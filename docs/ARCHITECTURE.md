# Architecture

This document is the steady-state reference for how `gflow-cli` is organised. The intent and sequencing live in [PLAN.md](../PLAN.md) — this file describes the **shape** that PLAN converges on.

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

## Modular monolith (current shape)

The hexagonal target above is the steady state. The **current** package — and the shape Phase 4+ work converges to before any DDD restructure — is a flat-namespace **modular monolith**: one deployable artifact, organized as a flat namespace of clearly-bounded modules.

**Per-module rules:**

- Each top-level package or file under `src/gflow_cli/` is a module with one clear domain (`auth`, `api`, `cli`, `errors`, `observability`, `manifest`, `paths`, `config`, `profile_store`).
- Each module exposes a public interface via `__init__.py` and (where applicable) explicit `__all__`.
- Internals are prefixed with `_` (single leading underscore) and never imported across modules.
- Cross-module communication goes through public interfaces, never private internals.
- Modules don't share global mutable state. Configuration is read-only at the boundary (`Settings`).
- When a module file grows past 400 lines, prefer extraction to a sub-package over inline growth.

**Phase 4 module additions (in v0.4.0a1):**

- `gflow_cli.errors` — exception taxonomy aligned with [RFC 9457 Problem Details](https://datatracker.ietf.org/doc/html/rfc9457). Each `GFlowError` subclass carries `type` (URI), `title`, `status`, `detail`, `instance`, `remediation_hint`. The `to_problem_details()` method serializes to the RFC 9457 JSON shape and is the stable contract for telemetry consumers.
- `gflow_cli.observability` — structlog configuration + the `error_raised` event emitter. This is the future home for metrics + tracing (Phase 5+) too.

**Why RFC 9457 for errors:** Problem Details is the IETF-standard shape for machine-readable HTTP error responses. Even though gflow-cli is a CLI (not an HTTP server), adopting the same vocabulary means: (a) the error log shape is greppable by stable `type` URI, (b) future cloud-edge integrations (e.g., a `gflow serve` HTTP front-end) can return our errors directly without translation, (c) downstream telemetry tools recognize the shape immediately.

**Phase 4 does NOT:**

- Restructure existing modules beyond minimal dedup (shared CLI helpers were promoted to `gflow_cli._cli_helpers` at the package top level — kept flat to avoid a `cli.py` file / `cli/` package collision).
- Introduce dependency-injection containers, command/query buses, or any DDD/CQRS scaffolding (deferred per [PLAN ADR #2](../PLAN.md#5-decision-log-adrs-in-miniature)).

When the project converges on the hexagonal target above, modules graduate to layers: e.g., today's `gflow_cli.api` becomes `gflow_cli.infrastructure.flow_api`, `gflow_cli.cli` becomes `gflow_cli.interfaces.cli`, and so on. The modular-monolith shape is the staging area, not the destination.

## Folder layout

> **Note: this document describes the TARGET architecture, not the current
> package layout.** The current shape (per [PLAN.md § 2](../PLAN.md#2-architecture-steady-state)
> and [ADR #2](../PLAN.md#5-decision-log-adrs-in-miniature)) is the simpler
> `src/gflow_cli/{api/, cli.py, cli_image.py, cli_video.py, auth.py,
> config.py, paths.py, profile_store.py}`. The DDD layout below was deferred
> indefinitely; converge toward it incrementally if/when a second `Provider`
> or a `gflow serve` HTTP front-end justifies the split.

```text
src/gflow_cli/
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

- **Target DDD names** (not yet implemented): `RateLimitExceededError`, `QuotaExhaustedError`, `InvalidPromptError`, `ProjectNotFoundError`, `JobNotFoundError`, `ProviderUnavailableError`.
- **Current Phase 4 classes** (shipped in v0.4.0a2, see `gflow_cli.errors`): `AuthExpiredError`, `RateLimitError`, `ContentPolicyError`, `NetworkError`, `WireFormatError`. All inherit from `FlowApiError → GFlowError`; `EXIT_CODE_MAP` walks them in subclass-first order so `except FlowApiError` still catches every typed leaf. Per-class exit codes: 3 (auth), 4 (rate-limit), 5 (content-policy), 6 (network), 7 (wire-format).

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
    auth_session = PlaywrightSession(profile_dir=settings.profile_subdir(profile_name))
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
@click.argument("prompt")
def generate(prompt: str) -> None:
    bus = build_bus(load_settings())
    cmd = GenerateImageCommand(prompt=Prompt(prompt), aspect=AspectRatio("1:1"), count=OutputCount(1))
    asyncio.run(bus.dispatch(cmd))
```

## Concurrency model (shipped, Phase 4 / v0.4.0a2)

- **Single-process, single-event-loop** (`asyncio`). No multiprocessing, no application-level threads (only what Playwright uses internally).
- **Within one `gflow video batch`:** `FlowApiClient.__aenter__` opens `Settings.concurrency` Playwright Pages inside **one shared persistent `BrowserContext`**. Operations check out a Page via an `asyncio.Queue` (FIFO, bounded by `maxsize=N`); a double-checkin raises `QueueFull` loudly rather than corrupting the pool. `gflow video batch` fans out manifest entries via `asyncio.gather`. See `gflow_cli.api.client.FlowApiClient._checkout_page` / `_checkin_page`.
- **Across profiles:** parallelism by spawning one shell per profile. Chromium refuses two persistent contexts on the same `user-data-dir`, so cross-profile parallelism is a process-level concern, not a coroutine one. See [KNOWN_ISSUES § Same profile can't be used in parallel](../KNOWN_ISSUES.md#same-profile-cant-be-used-in-parallel).
- **Why a Page pool and not a `Semaphore`?** A semaphore over a single shared Page would serialize at `page.request.post` anyway (Playwright doesn't make a Page thread-safe). N Pages inside one Context let Chromium pipeline the requests while still sharing the auth cookies. T0 of the Phase 4 spike measured ~45 ms per Page creation at N=16 — well under the 200 ms/Page threshold.
- **What's backlog (post-v0.5):** account-pool aware scheduling across multiple signed-in profiles (round-robin), and a separate-process driver so two `gflow video batch` invocations against the same profile can serialize properly via OS file lock.

## Observability (shipped, Phase 4 / v0.4.0a2)

`structlog` is configured at startup via `gflow_cli.observability.configure(...)`, called by `_cli_helpers.run_with_handlers(...)` at the CLI boundary.

Stable event names emitted at the boundary:

- **`error_raised`** — caught `GFlowError` (and subclasses). Carries `error_class`, `problem` (the full RFC 9457 Problem Details dict including `type`, `title`, `status`, `detail`, `instance`, `remediation_hint`, `route`), and `cli_command`.
- **`error_unhandled`** — any non-`GFlowError` reaching the boundary. Privacy-safe: hashes the exception message + stack trace with SHA-256; never logs raw payload. Carries `error_class`, `message_hash`, `stack_hash`, `cli_command`.

Process-boundary contextvars bind once: `correlation_id` (UUID4 per CLI invocation) and `cli_version`. Both ride along on every event line.

Format defaults to **human-readable on TTY**, **JSON when piped or `GFLOW_CLI_LOG_FORMAT=json`**. The exception renderer is constructed as `structlog.processors.ExceptionRenderer(structlog.tracebacks.ExceptionDictTransformer(show_locals=False))` — the verbose form makes the privacy guarantee visible at the call site (frame locals may contain auth cookies and signed URLs, never log them).

Pipes cleanly into `jq` / Loki / Datadog without configuration. See [`docs/USER_GUIDE.md` § Journey 6](USER_GUIDE.md#journey-6--read-the-structured-logs) for `jq` recipes.

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
