# flow-cli — Implementation Plan

> **Status:** Living document — updated as phases complete.
> **Owner:** [@ffroliva](https://github.com/ffroliva)
> **Last revised:** 2026-05-09

This plan turns the v0.1 scaffold into a production-grade CLI that lets Google AI Ultra/Pro subscribers generate **images** and **videos** through Flow's REST API, individually or in batch, from a single executable.

The plan is intentionally opinionated — it treats this repo as a **portfolio-grade benchmark** of how to structure a small but serious Python CLI: DDD-shaped domain, CQRS for write/read separation, Clean Architecture for dependency direction, BDD for behaviour specs, TDD for the tight loop. Where a pattern would be theatre instead of value, it's omitted on purpose and the rationale is noted.

---

## 1. Goals

### Functional

| # | Goal | Phase |
|---|---|---|
| F1 | Authenticate once via browser, persist session | 1 |
| F2 | Generate **a single image** from a prompt with chosen aspect ratio + count | **2** |
| F3 | Generate **a batch of images** from a manifest (file or stdin) | 2 |
| F4 | Generate **a single Veo video** (I2V) from a start image + motion prompt + aspect | 3 |
| F5 | Generate **a batch of videos** from a manifest | 3 |
| F6 | Download all outputs to a configurable, well-known directory | 2 (images), 3 (videos) |
| F7 | Per-account profiles (`--profile`) for multi-account usage | 1 |
| F8 | Concurrency across accounts (pool) | 4 |

### Non-functional

| # | Goal | Where it shows up |
|---|---|---|
| N1 | **Maintainable** — clear boundaries, small files, no god modules | Layer separation (§3) |
| N2 | **Testable** — every behaviour has an automated check | TDD (§5), BDD (§6) |
| N3 | **Observable** — what failed, where, why, in one log line | Structured logging via `structlog` (§7) |
| N4 | **Configurable** — env vars > flags > sane defaults | `pydantic-settings` (§4) |
| N5 | **Vitrine-grade** — code a senior engineer would put on their CV | DDD + CQRS + Clean Architecture (§2) |
| N6 | **Cross-platform** — Windows, macOS, Linux | OS-native paths via `pathlib` + `platformdirs` |

### Explicit non-goals (this version)

- ❌ A GUI. CLI only.
- ❌ Hosting / multi-tenancy. Single user, local CLI.
- ❌ Re-implementing Google's auth. Playwright cookie jar is good enough.
- ❌ Re-selling Flow. See [DISCLAIMER](DISCLAIMER.md).

---

## 2. Architecture

### 2.1 Layered (Clean / Hexagonal)

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

**Dependency direction** (hexagonal rule): `interfaces → application → domain ← infrastructure`. Domain depends on nothing. Application depends on domain + ports (Protocols). Infrastructure implements the ports.

### 2.2 CQRS

Every state-changing intent is a **Command**. Every read is a **Query**. They're both immutable dataclasses; their handlers are sync facades over async work.

```python
# application/commands/generate_image.py
@dataclass(frozen=True)
class GenerateImageCommand:
    prompt: Prompt
    aspect: AspectRatio
    count: OutputCount
    output_path: OutputPath

class GenerateImageHandler:
    def __init__(self, provider: ImageProvider, storage: AssetStorage): ...
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

Why CQRS in a CLI? Two real wins:
1. **Testability** — handlers are easy to test in isolation; CLI is just thin Click glue.
2. **Future-proof** — when v0.3 adds a `gflow serve` HTTP front-end, the command/query layer is reused as-is.

### 2.3 DDD pieces

**Aggregates**
- `GenerationProject` — owns a Flow project lifecycle, the assets uploaded into it, and the jobs spawned from it.
- `GenerationJob` — async work (Veo/Imagen) with status, progress, and outputs.

**Entities**
- `Asset` — uploaded image OR generated image OR generated video. Has stable UUID, media URL, kind, source-job (if generated).

**Value objects** (`@dataclass(frozen=True)`)
- `AspectRatio` (`"9:16" | "16:9" | "1:1" | "4:3" | "3:4"`) — validated on construction.
- `Prompt` — non-empty, length-checked.
- `MotionPrompt` — same as Prompt but with semantic intent.
- `OutputCount` — integer 1–4.
- `JobId`, `AssetId`, `ProjectId` — `NewType` over `str` (UUID4 format).
- `OutputPath` — Path with directory-vs-file role tagged.

**Domain events** (in-process for now, eventable later)
- `AssetUploaded`, `JobStarted`, `JobProgressed`, `JobCompleted`, `JobFailed`, `AssetDownloaded`.

**Domain errors** (typed exceptions, no stringly typed HTTP codes leaking out)
- `AuthExpiredError`, `RateLimitExceededError`, `QuotaExhaustedError`, `InvalidPromptError`, `ProjectNotFoundError`, `JobNotFoundError`, `ProviderUnavailableError`.

### 2.4 Folder layout (target end-of-Phase-1)

```
src/flow_cli/
├── domain/
│   ├── __init__.py
│   ├── models.py            # Asset, GenerationJob, GenerationProject
│   ├── value_objects.py     # AspectRatio, Prompt, OutputCount, etc.
│   ├── events.py            # domain events
│   └── errors.py            # typed exceptions
├── application/
│   ├── __init__.py
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
│   ├── __init__.py
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
    └── paths.py             # XDG / platformdirs default dirs
```

---

## 3. Configuration

### 3.1 `.env.template` (committed)

```dotenv
# Auth profile root — Playwright persistent contexts live here.
# Default: %USERPROFILE%/.flow-cli (Windows) or ~/.flow-cli (POSIX)
FLOW_CLI_HOME=

# Default profile name (multi-account: pass --profile to override)
FLOW_CLI_PROFILE=default

# Where downloaded assets land. Subfolders created per kind/date.
# Default: <user-downloads-dir>/flow-cli  (via platformdirs)
#   - Windows: %USERPROFILE%\Downloads\flow-cli
#   - macOS:   ~/Downloads/flow-cli
#   - Linux:   $XDG_DOWNLOAD_DIR/flow-cli  (falls back to ~/Downloads/flow-cli)
FLOW_CLI_OUTPUT_DIR=

# Provider — "flow" (default, reverse-engineered) or "official" (v0.3+)
FLOW_CLI_PROVIDER=flow

# Optional Gemini API key for the official Veo 3.1 provider (v0.3+)
# Get one at https://aistudio.google.com/apikey
FLOW_CLI_GEMINI_API_KEY=

# Per-request HTTP timeout (seconds)
FLOW_CLI_TIMEOUT_SECONDS=600

# Logging — DEBUG | INFO | WARNING | ERROR
FLOW_CLI_LOG_LEVEL=INFO

# Concurrency for batch operations (v0.4+)
FLOW_CLI_CONCURRENCY=1
```

### 3.2 Resolution order

1. **CLI flag** (highest precedence, e.g. `--output ./my-out`)
2. **Environment variable** (`FLOW_CLI_OUTPUT_DIR`)
3. **`.env` file** in CWD
4. **`.env` file** in `$FLOW_CLI_HOME`
5. **Built-in default** (lowest precedence)

Implemented via `pydantic-settings` `BaseSettings` — single source of truth, validated at startup, fails loudly on bad values.

### 3.3 Default output paths

```text
<output_root>/
├── images/<YYYY-MM-DD>/<job_id>_<index>.png
├── videos/<YYYY-MM-DD>/<job_id>.mp4
└── manifests/<YYYY-MM-DD>/<batch_id>.tsv   # batch input snapshot
```

Predictable, sorted, easy to clean up.

---

## 4. Feature specs

### 4.1 Image generation (Phase 2)

#### CLI surface

```bash
# Single image
gflow image generate \
  -p "a serene mountain lake at sunset" \
  [--aspect 1:1 | 9:16 | 16:9 | 4:3 | 3:4]   # default: 1:1
  [--count 1..4]                              # default: 1
  [--output PATH]                             # default: $FLOW_CLI_OUTPUT_DIR/images/<date>/<job_id>_<i>.png
  [--profile NAME]                            # default: $FLOW_CLI_PROFILE or "default"

# Batch (manifest = TSV: prompt\tcount\taspect\toutput_path? )
gflow image batch <manifest.tsv> \
  [--out-dir DIR]                             # default: $FLOW_CLI_OUTPUT_DIR/images/<date>/
  [--concurrency N]                           # default: 1 (v0.4+: account-pool aware)
  [--profile NAME]
```

#### Manifest format (TSV — terse, scriptable, vim-friendly)

```tsv
# prompt	count	aspect	output_path
a serene mountain lake at sunset	2	1:1	./out/lake-{i}.png
black-and-white portrait of a fisherman	1	3:4	./out/fisherman.png
```

`{i}` is replaced by 1-based index when `count > 1`. Empty `output_path` falls back to the default scheme.

#### Routes to capture (Phase 2 prerequisite — discovery run)

We have video routes captured but **not** image routes. Phase 2 starts with a focused discovery session against the Flow Imagen flow. Expected:

```text
POST /v1/flow/uploadImage                            (already known — for I2I; not needed for T2I)
POST /v1/flow/createImage  or  /v1/imagen:generate   (TBD — capture in P2.1)
POST /v1/flow/createImage:checkStatus  or similar   (TBD)
GET  signed URL pattern for downloading PNG          (likely same getMediaUrlRedirect)
```

#### Domain & handlers

```python
# application/commands/generate_image.py
@dataclass(frozen=True)
class GenerateImageCommand:
    prompt: Prompt
    aspect: AspectRatio
    count: OutputCount
    output_path: Optional[OutputPath] = None        # None → use default scheme
    profile: ProfileName = ProfileName("default")

# application/handlers/generate_image_handler.py
class GenerateImageHandler:
    def __init__(
        self,
        image_provider: ImageProvider,         # port
        storage: AssetStorage,                 # port
        clock: Clock,                          # port (for testable date-based folders)
    ):
        ...
    async def handle(self, cmd: GenerateImageCommand) -> list[Asset]:
        job = await self.image_provider.start_generation(...)
        while not job.is_terminal():
            await asyncio.sleep(2)
            job = await self.image_provider.poll(job.id)
        if job.failed():
            raise job.error
        outputs = await self.image_provider.list_outputs(job.id)
        paths = []
        for i, asset in enumerate(outputs, 1):
            path = self.storage.resolve_path(cmd.output_path, kind="image",
                                              job_id=job.id, index=i)
            await self.storage.download(asset.media_url, path)
            paths.append(asset.with_local_path(path))
        return paths
```

### 4.2 Video generation (Phase 3)

#### CLI surface

```bash
# Single video (I2V)
gflow video generate \
  -i ./input.png \
  -p "Slow cinematic push-in"
  [--end-image PATH]                          # optional end-frame
  [--aspect 9:16 | 16:9 | 1:1]                # default: 9:16
  [--output PATH]                             # default: <output_root>/videos/<date>/<job_id>.mp4
  [--profile NAME]

# Batch (TSV: start_image\tprompt\tend_image?\taspect\toutput_path?)
gflow video batch <manifest.tsv> \
  [--out-dir DIR]
  [--concurrency N]
  [--profile NAME]

# Convenience alias for Phase-2 ergonomics
gflow video i2v <image> "<prompt>" -o out.mp4
```

#### Routes (already captured — see `samples/captured_requests.json`)

```text
POST  /v1/flow/uploadImage
POST  /v1/video:batchAsyncGenerateVideoText
POST  /v1/video:batchCheckAsyncVideoGenerationStatus
PATCH /v1/flowWorkflows/{id}                   (archive — not user-facing)
```

#### Notes

- A video generation always needs an uploaded start frame → upload happens implicitly inside the handler if `--start-uuid` isn't passed.
- End-frame is optional (Veo I2V supports "transition between two frames").
- Default aspect 9:16 because Flow's primary Veo use case is short-form vertical reels.

---

## 5. TDD discipline

Already documented in [CONTRIBUTING.md](CONTRIBUTING.md). Summary:

- **Red → Green → Refactor → Commit.**
- Every command/query handler has a test file under `tests/handlers/`.
- Every Provider port has contract tests under `tests/providers/` (red until route is wired, then mocked HTTP, then live-opt-in).
- Coverage floor: **80% overall**, **90% on `domain/` and `application/`**.

Failure to follow → CI rejects the PR.

---

## 6. BDD scenarios

Behaviour specs live in `tests/features/` as Gherkin `.feature` files, executed by [`pytest-bdd`](https://pytest-bdd.readthedocs.io/). They describe user-visible behaviour and double as living documentation.

```gherkin
# tests/features/image_generation.feature
Feature: Image generation

  Background:
    Given a signed-in profile "default"

  Scenario: Single image with default settings
    When I run "gflow image generate -p 'a serene mountain lake'"
    Then a PNG is saved under "<output_dir>/images/<today>/"
    And exit code is 0

  Scenario: Multiple images with explicit aspect
    When I run "gflow image generate -p 'forest' --aspect 16:9 --count 3"
    Then 3 PNGs are saved under "<output_dir>/images/<today>/"
    And each PNG has aspect ratio 16:9

  Scenario: Output path override via flag
    Given env "FLOW_CLI_OUTPUT_DIR=/tmp/env-default"
    When I run "gflow image generate -p 'sky' --output /tmp/flag-override/sky.png"
    Then the file "/tmp/flag-override/sky.png" exists
    And nothing was written to "/tmp/env-default"

  Scenario: Output dir from env var
    Given env "FLOW_CLI_OUTPUT_DIR=/tmp/env-out"
    When I run "gflow image generate -p 'sky'"
    Then a PNG is saved under "/tmp/env-out/images/<today>/"
```

```gherkin
# tests/features/video_generation.feature
Feature: Video generation (I2V)

  Background:
    Given a signed-in profile "default"
    And an input image "tests/fixtures/start.png"

  Scenario: Single I2V clip
    When I run "gflow video generate -i tests/fixtures/start.png -p 'push-in'"
    Then an MP4 is saved under "<output_dir>/videos/<today>/"
    And the first frame of the MP4 visually matches "tests/fixtures/start.png"
```

The "first frame visually matches" step uses the same Pillow MAE check that already lives in this monorepo's `verify_first_frames.py`.

---

## 7. Observability

`structlog` configured at startup via `shared/observability/logging.py`. Every log line has:

- `event` (canonical key e.g. `image.generation.started`)
- `command_id` (UUID4 per command invocation)
- `provider` / `profile`
- domain context (`prompt_chars`, `aspect`, `count`, etc.)
- timing where relevant (`duration_ms`)

Output format defaults to **human-readable on TTY**, **JSON when piped or `FLOW_CLI_LOG_FORMAT=json`**. Lets the CLI feel friendly interactively but pipe cleanly into `jq`/`Loki` in pipelines.

---

## 8. Phasing

### Phase 1 — Foundation (target: 1–2 days)
1. Refactor scaffold into `domain/`, `application/`, `infrastructure/`, `interfaces/`, `shared/` per §2.4.
2. Add `pydantic-settings` config (§3) + `.env.template` (committed).
3. Implement `CommandBus` + `QueryBus` (§2.2).
4. Move existing CLI commands to dispatch via the bus.
5. Wire `structlog` (§7).
6. Migrate existing red tests to the new layout.
7. **Exit criteria:** all current tests still pass, no functional regression, `gflow --help` works, layered structure complete.

### Phase 2 — Image generation (target: 2–3 days)
1. **Discovery run** (P2.1): capture image-gen routes from a real Flow session, dump to `samples/captured_image_requests.json`.
2. Domain models for image generation: `Asset(kind="image")`, `GenerationJob`, value objects.
3. `ImageProvider` port + `FlowImageProvider` adapter using captured routes.
4. Commands: `GenerateImageCommand`, `GenerateImageBatchCommand`, `DownloadAssetCommand`.
5. Handlers + tests (red, then green).
6. CLI: `gflow image generate`, `gflow image batch`.
7. BDD feature file `image_generation.feature` — passes against live API (opt-in).
8. **Exit criteria:** `gflow image generate -p "..."` produces a real PNG end-to-end, batch works, output path resolution honours flag → env → default.

### Phase 3 — Video generation (target: 2–3 days)
1. Domain models for video: `GenerationJob(kind="video")`, `MotionPrompt`.
2. `VideoProvider` port + `FlowVideoProvider` adapter (uses already-captured routes).
3. Commands: `GenerateVideoCommand`, `GenerateVideoBatchCommand`.
4. CLI: `gflow video generate`, `gflow video batch`, `gflow video i2v` (alias).
5. BDD `video_generation.feature` with first-frame MAE assertion.
6. Migrate Compiled Growth's worker to call `gflow` instead of in-tree Playwright.
7. **Exit criteria:** F002 short produces 12/12 correct clips via `gflow`.

### Phase 4 — Hardening (target: 1–2 days)
1. Per-account pool + `FLOW_CLI_CONCURRENCY > 1` for batch ops.
2. Retry / backoff on rate limits and transient 5xx.
3. Domain-error → exit-code mapping (so shell scripts can branch).
4. Verbose-by-default error messages with remediation hints.
5. Architecture doc (`docs/ARCHITECTURE.md`) — diagrams + decision records.
6. **Exit criteria:** can drive 12 clips concurrently across 2 accounts in ≤ half wall time.

### Phase 5 — Public alpha release
1. Set up PyPI Trusted Publishing for `flow-cli`.
2. Tag `v0.2.0a1`.
3. Verify `uvx --from flow-cli gflow --help` works end-to-end on a fresh machine.
4. Announce (LinkedIn / X / dev.to / Hacker News "Show HN").

---

## 9. Decision log (ADRs in miniature)

| # | Decision | Rationale |
|---|---|---|
| 1 | DDD + CQRS + Clean Arch | Vitrine-grade structure; future REST/gRPC adapter is a free lunch. |
| 2 | Use `pydantic-settings`, not `python-dotenv` directly | Validated config, single source, fails fast. |
| 3 | TSV manifests over JSON/YAML | Editable in any tool, scriptable, friendly to `awk`/`cut`. |
| 4 | `structlog` over stdlib `logging` | Structured-by-default, human-readable on TTY. |
| 5 | `pytest-bdd` over `behave` | Same runner as unit tests; one CI command. |
| 6 | Default aspect 1:1 (image), 9:16 (video) | Imagen's natural default; Flow's primary use case is reels. |
| 7 | Output path defaults under `Downloads/flow-cli/` | OS-native, discoverable, easy to clean. |
| 8 | No event-sourcing, no message queue, no SaaS dependencies | YAGNI for a local CLI. |
| 9 | Playwright for auth + REST transport (`page.request`) | Avoids re-implementing Google OAuth; cookie jar auto-attaches. |
| 10 | Both `gflow` and `flow` binary names installed | `flow` is friendlier; `gflow` avoids conflicts. |

---

## 10. Open questions (need confirmation before Phase 2)

| # | Question | Default if no answer |
|---|---|---|
| Q1 | Do you want a full layered refactor (Phase 1) before Phase 2, or grow into it as features land? | Full refactor first — clean foundation is cheaper now than retrofitted later. |
| Q2 | Image batch: TSV manifest format above OK, or prefer YAML/JSON? | TSV (terse, scriptable). |
| Q3 | Default image count when not specified? | `1` |
| Q4 | Should `gflow image generate` block until done (default) or return job_id for later polling? | Block by default; add `--async` flag to return job_id. |
| Q5 | Where should the `.env` be loaded from by default? CWD only, or also `$FLOW_CLI_HOME/.env`? | Both, with CWD taking precedence. |
| Q6 | Are you OK with `pydantic-settings`, `structlog`, `pytest-bdd`, `platformdirs` as new dependencies? | Yes — all small, all stable, all heavily-used. |

---

## 11. Definition of done (per phase)

A phase ships when:

- [ ] All exit criteria above are met.
- [ ] CI is green (lint + type + test + coverage ≥ 80%).
- [ ] CHANGELOG.md `[Unreleased]` block lists every user-visible change.
- [ ] README is updated if user-facing surface changed.
- [ ] One BDD feature file exists for any new user-visible command.
- [ ] No `# TODO` left without a tracked issue link.

---

_End of plan. Updates to this file ship as part of the phase that motivated them._
