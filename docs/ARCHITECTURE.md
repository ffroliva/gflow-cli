# Architecture

This document is the steady-state reference for how `flow-cli` is organised. The intent and sequencing live in [PLAN.md](../PLAN.md) — this file describes the **shape** that PLAN converges on.

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│  interfaces/   ← CLI (Click)                                 │
│                  one entry point per user-facing command     │
└──────────────────────┬───────────────────────────────────────┘
                       │ dispatches Command/Query objects
┌──────────────────────▼───────────────────────────────────────┐
│  application/  ← Use cases                                   │
│                  Commands (writes) + Queries (reads),        │
│                  one Handler per Command/Query.              │
│                  Handlers depend on PORTS only.              │
└──────────────────────┬───────────────────────────────────────┘
                       │ depends on Protocol (port)
┌──────────────────────▼───────────────────────────────────────┐
│  domain/       ← Pure business logic                         │
│                  Entities, value objects, domain events,     │
│                  domain errors. No I/O, no frameworks.       │
└──────────────────────▲───────────────────────────────────────┘
                       │ implements
┌──────────────────────┴───────────────────────────────────────┐
│  infrastructure/  ← Adapters (driven side)                   │
│                     FlowProvider (REST), AuthSession         │
│                     (Playwright), LocalStorage (filesystem). │
└──────────────────────────────────────────────────────────────┘
```

**Dependency rule (hexagonal):** `interfaces → application → domain ← infrastructure`. Domain depends on nothing. Application depends on domain + ports (Protocols). Infrastructure implements the ports. Compile this rule into the import graph: `domain/*` must not import from `application/`, `infrastructure/`, or `interfaces/`.

## Folder layout

```text
src/flow_cli/
├── domain/
│   ├── models.py            # Asset, GenerationJob, GenerationProject
│   ├── value_objects.py     # AspectRatio, Prompt, OutputCount, etc.
│   ├── events.py            # AssetUploaded, JobStarted, …
│   └── errors.py            # AuthExpiredError, RateLimitExceededError, …
├── application/
│   ├── ports/
│   │   ├── image_provider.py    # Protocol
│   │   ├── video_provider.py    # Protocol
│   │   ├── asset_storage.py     # Protocol
│   │   └── auth_session.py      # Protocol
│   ├── commands/
│   │   ├── upload_image.py
│   │   ├── generate_image.py
│   │   ├── generate_image_batch.py
│   │   ├── generate_video.py
│   │   ├── generate_video_batch.py
│   │   └── download_asset.py
│   ├── queries/
│   │   ├── get_job_status.py
│   │   └── list_generations.py
│   ├── handlers/            # one file per command/query handler
│   └── bus.py               # CommandBus + QueryBus
├── infrastructure/
│   ├── flow/                # adapter for aisandbox-pa
│   │   ├── client.py
│   │   ├── image_provider.py
│   │   ├── video_provider.py
│   │   └── routes.py        # captured route shapes
│   ├── auth/
│   │   └── playwright_session.py
│   ├── storage/
│   │   └── local.py
│   └── observability/
│       └── logging.py
├── interfaces/
│   └── cli/
│       ├── main.py          # Click entry
│       ├── image.py         # `gflow image …`
│       ├── video.py         # `gflow video …`
│       └── auth.py          # `gflow auth …`
└── shared/
    ├── config.py            # pydantic-settings, .env loader
    └── paths.py             # platformdirs default dirs
```

## DDD pieces

**Aggregates**

- `GenerationProject` — owns a Flow project lifecycle, the assets uploaded into it, and the jobs spawned from it.
- `GenerationJob` — async work (Veo/Imagen) with status, progress, and outputs.

**Entities**

- `Asset` — uploaded image OR generated image OR generated video. Has stable UUID, media URL, kind, source-job (if generated).

**Value objects** (frozen dataclasses)

- `AspectRatio` (`9:16` | `16:9` | `1:1` | `4:3` | `3:4`) — validated at construction.
- `Prompt`, `MotionPrompt` — non-empty, length-bounded strings.
- `OutputCount` — integer 1–4.
- `JobId`, `AssetId`, `ProjectId` — `NewType` over `str` (UUID4 format).
- `OutputPath` — `Path` with directory-vs-file role tagged.

**Domain events** (in-process for now, eventable later)

- `AssetUploaded`, `JobStarted`, `JobProgressed`, `JobCompleted`, `JobFailed`, `AssetDownloaded`.

**Domain errors** (typed exceptions; no stringly-typed HTTP codes leaking out)

- `AuthExpiredError`, `RateLimitExceededError`, `QuotaExhaustedError`, `InvalidPromptError`, `ProjectNotFoundError`, `JobNotFoundError`, `ProviderUnavailableError`.

## CQRS

Every state-changing intent is a **Command**. Every read is a **Query**. Both are immutable dataclasses; their handlers are async.

```python
# application/commands/generate_image.py
@dataclass(frozen=True)
class GenerateImageCommand:
    prompt: Prompt
    aspect: AspectRatio
    count: OutputCount
    output_path: Optional[OutputPath] = None
    profile: ProfileName = ProfileName("default")

# application/handlers/generate_image_handler.py
class GenerateImageHandler:
    def __init__(self, image_provider: ImageProvider, storage: AssetStorage): ...
    async def handle(self, cmd: GenerateImageCommand) -> list[Asset]: ...
```

A tiny in-process **CommandBus** dispatches `cmd → handler.handle(cmd)`. No middleware, no event sourcing — that would be theatre for a CLI.

```python
# application/bus.py
class CommandBus:
    def __init__(self): self._handlers = {}
    def register(self, cmd_type, handler): self._handlers[cmd_type] = handler
    async def dispatch(self, cmd): return await self._handlers[type(cmd)].handle(cmd)
```

**Why CQRS in a CLI?** Two real wins:

1. **Testability** — handlers are easy to test in isolation; CLI is just thin Click glue.
2. **Future-proof** — when a `gflow serve` HTTP front-end ships, the command/query layer is reused as-is.

## Ports & adapters

A **port** is a `Protocol` (PEP 544) in `application/ports/`. An **adapter** is a concrete class in `infrastructure/` that implements one. Handlers depend only on ports.

```python
# application/ports/image_provider.py
class ImageProvider(Protocol):
    async def start_generation(self, prompt: Prompt, aspect: AspectRatio, count: OutputCount) -> GenerationJob: ...
    async def poll(self, job_id: JobId) -> GenerationJob: ...
    async def list_outputs(self, job_id: JobId) -> list[Asset]: ...

# infrastructure/flow/image_provider.py
class FlowImageProvider:                                        # implements ImageProvider
    async def start_generation(self, prompt, aspect, count): ...   # POST /v1/imagen:generate
    async def poll(self, job_id): ...                              # POST /v1/imagen:checkStatus
    async def list_outputs(self, job_id): ...
```

Tests substitute fakes/mocks for the same Protocol. The handler doesn't change.

## Composition root

A single function in `interfaces/cli/main.py` wires the dependency graph. No global registry, no DI framework — explicit construction is short and readable.

```python
def build_bus(settings: Settings) -> CommandBus:
    auth_session = PlaywrightSession(profile_dir=settings.profile_dir())
    image_provider = FlowImageProvider(auth_session)
    video_provider = FlowVideoProvider(auth_session)
    storage = LocalStorage(settings.output_dir)

    bus = CommandBus()
    bus.register(GenerateImageCommand, GenerateImageHandler(image_provider, storage))
    bus.register(GenerateVideoCommand, GenerateVideoHandler(video_provider, storage))
    # ... etc
    return bus
```

Click commands then become tiny:

```python
@click.command()
@click.option("-p", "--prompt", required=True)
def generate(prompt: str) -> None:
    bus = build_bus(load_settings())
    cmd = GenerateImageCommand(prompt=Prompt(prompt), aspect=AspectRatio("1:1"), count=OutputCount(1))
    asyncio.run(bus.dispatch(cmd))
```

## Concurrency model

- **Single-process, single-event-loop** (`asyncio`).
- Within a profile: serial per-Chromium-context (Chromium can't open the same profile dir twice).
- Across profiles: parallelism is enabled by spawning multiple browser contexts (one per profile). Coordination via `asyncio.Semaphore(FLOW_CLI_CONCURRENCY)`.
- No multiprocessing, no threading except what Playwright uses internally.

This caps the parallelism at `min(concurrency_limit, profile_count)`. v0.4 will add account-pool aware scheduling (round-robin across logged-in profiles).

## Observability

`structlog` configured at startup via `infrastructure/observability/logging.py`. Each log line carries:

- `event` — canonical key (e.g. `image.generation.started`)
- `command_id` — UUID4 per dispatched command
- `provider`, `profile`
- domain context (`prompt_chars`, `aspect`, `count`, etc.)
- timing (`duration_ms`) where relevant

Format defaults to **human-readable on TTY**, **JSON when piped or `FLOW_CLI_LOG_FORMAT=json`**. Pipes cleanly into `jq` / Loki / Datadog without configuration.

## Testing topology

| Layer | Test type | Where | Network? |
|---|---|---|---|
| `domain/*` | Unit | `tests/domain/` | no |
| `application/*` (handlers) | Unit (mock ports) | `tests/handlers/` | no |
| `application/*` ↔ `infrastructure/*` | Integration (mocked HTTP) | `tests/integration/` | no |
| `infrastructure/flow/*` | Contract (replayed HTTP fixtures) | `tests/providers/` | no (replay) |
| `infrastructure/flow/*` | Live opt-in (`@pytest.mark.live` + `GFLOW_LIVE=1`) | `tests/providers/test_flow_live.py` | yes |
| `interfaces/cli/*` | BDD (`pytest-bdd` Gherkin) | `tests/features/` | no (mocked provider) |

CI runs everything except `live`. Live tests run on the maintainer's machine before each release.

## When to break the rules

The dependency direction is inviolate. Everything else is preference, not religion:

- A 5-line helper that doesn't quite belong in `domain/` is fine in a `shared/` module.
- A pragmatic synchronous shortcut in `interfaces/` that calls `asyncio.run(...)` once is fine — handlers stay async, CLI stays simple.
- If a Provider implementation needs to call into another Provider for some compound operation, do it from a *handler* (orchestration belongs in `application/`), not from the adapter itself.

## See also

- [PLAN.md](../PLAN.md) — phasing, exit criteria, ADRs
- [CONTRIBUTING.md](../CONTRIBUTING.md) — TDD discipline, test categories
- [AUTHENTICATION.md](AUTHENTICATION.md) — full auth flow lifecycle
