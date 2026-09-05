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
│                     (Playwright), AssetStorage (local/cloud).│
└──────────────────────────────────────────────────────────────┘
```

**Dependency rule (hexagonal):** `interfaces → application → domain ← infrastructure`. Domain depends on nothing. Application depends on domain + ports (Protocols). Infrastructure implements the ports. Compile this rule into the import graph: `domain/*` must not import from `application/`, `infrastructure/`, or `interfaces/`.

## Modular monolith (current shape)

The hexagonal target above is the steady state. The **current** package — and the shape Phase 4+ work converges to before any DDD restructure — is a flat-namespace **modular monolith**: one deployable artifact, organized as a flat namespace of clearly-bounded modules.

**Per-module rules:**

- Each top-level package or file under `src/gflow_cli/` is a module with one clear domain (`auth`, `api`, `cli`, `errors`, `observability`, `manifest`, `paths`, `storage`, `config`, `profile_store`, `data`).
- Each module exposes a public interface via `__init__.py` and (where applicable) explicit `__all__`.
- Internals are prefixed with `_` (single leading underscore) and never imported across modules.
- Cross-module communication goes through public interfaces, never private internals.
- Modules don't share global mutable state. Configuration is read-only at the boundary (`Settings`).
- When a module file grows past 400 lines, prefer extraction to a sub-package over inline growth.

**Phase 4 module additions (in v0.4.0a1):**

- `gflow_cli.errors` — exception taxonomy aligned with [RFC 9457 Problem Details](https://datatracker.ietf.org/doc/html/rfc9457). Each `GFlowError` subclass carries `type` (URI), `title`, `status`, `detail`, `instance`, `remediation_hint`. The `to_problem_details()` method serializes to the RFC 9457 JSON shape and is the stable contract for telemetry consumers.
- `gflow_cli.observability` — structlog configuration + the `error_raised` event emitter. This is the future home for metrics + tracing (Phase 5+) too.

**Data layer module (shipped in v0.9.0):**

- `gflow_cli/data/` — local SQLite persistence layer (`DataStore` + `DataRepository` + `OperationRecorder` + redaction + read-only `queries` for `gflow data list`). Records all new image/video operations, asset provenance, and final asset locations (local paths or cloud URIs). Default DB path is resolved from `$GFLOW_CLI_DB_PATH` (default: `~/.local/share/gflow-cli/data.db` on POSIX, the equivalent platformdirs user-data dir on Windows). Migrations versioned via SHA-256 checksums; safe forward-only semantics with newer-schema detection. T2V now flows through `FlowApiClient.generate_video`, sharing the client boundary with image commands.

**External storage module (shipped after v0.9.1):**

- `gflow_cli/storage.py` — target resolver and writer for generated assets. With `GFLOW_CLI_STORAGE_URI` unset it returns normal local `Path` targets under `GFLOW_CLI_OUTPUT_DIR`; with `gs://` or `s3://` it returns a cloud-backed `UPath` and writes through the matching fsspec backend. The module owns key sanitisation, cloud URI metadata extraction, and event-loop-safe writes. User-facing setup lives in [EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md).

**Why RFC 9457 for errors:** Problem Details is the IETF-standard shape for machine-readable HTTP error responses. Even though gflow-cli is a CLI (not an HTTP server), adopting the same vocabulary means: (a) the error log shape is greppable by stable `type` URI, (b) future cloud-edge integrations (e.g., a `gflow serve` HTTP front-end) can return our errors directly without translation, (c) downstream telemetry tools recognize the shape immediately.

**Phase 4 does NOT:**

- Restructure existing modules beyond minimal dedup (shared CLI helpers were promoted to `gflow_cli._cli_helpers` at the package top level — kept flat to avoid a `cli.py` file / `cli/` package collision).
- Introduce dependency-injection containers, command/query buses, or any DDD/CQRS scaffolding (deferred per [PLAN ADR #2](../PLAN.md#5-decision-log-adrs-in-miniature)).

**v0.6.0a2 module additions — auth strategy package:**

`auth.py` was promoted to a sub-package `gflow_cli.auth/` with the strategy pattern to support multiple browser backends for the `gflow auth login` command:

```text
src/gflow_cli/auth/
├── __init__.py          # public: login(), AuthStatus, get_status(), list_profiles()
├── base.py              # AuthStrategy Protocol — the shared contract
├── factory.py           # AuthStrategyFactory — routes auto/chrome/internal modes
├── internal_chromium.py # InternalChromiumStrategy — Playwright bundled Chromium (legacy)
├── real_chrome.py       # RealChromeStrategy — system Google Chrome with stealth flags
└── strategies.py        # (internal) shared Playwright helpers
```

**Why:** Google's bot-detection ("G12 block") rejects Playwright's bundled Chromium during `gflow auth login`. Launching the user's installed Google Chrome with `--disable-blink-features=AutomationControlled` plus a JS `add_init_script` that overrides `navigator.webdriver` bypasses detection. Two strategies are needed because the setup (persistent-context flags, Chrome binary path, stealth init) differs fundamentally between them.

**AuthStrategy Protocol** (`base.py`):
```python
class AuthStrategy(Protocol):
    name: str
    async def login(self, profile_dir: Path, headless: bool) -> None: ...
```

**AuthStrategyFactory** (`factory.py`):
- `mode="auto"` — probes `is_chrome_available()` → `RealChromeStrategy` if found, else `InternalChromiumStrategy` with a warning.
- `mode="chrome"` — explicit `RealChromeStrategy`; raises `ConfigurationError` if Chrome binary missing.
- `mode="internal"` — explicit `InternalChromiumStrategy`.

**RealChromeStrategy stealth design** (`real_chrome.py`):
- Uses a **Passive Capture** pattern: launches system Chrome via `subprocess.Popen` without any automation flags or remote-debugging ports.
- Provides a 100% clean browser process that Google's G12 block cannot detect.
- The CLI blocks on `proc.wait()`, prompting the user to complete the sign-in and **close the browser completely**.
- Post-close: performs a fast, headless `launch_persistent_context` probe to verify the `SAPISID` cookie was successfully captured.
- Privacy guard: raises `SecurityError` if the resolved `profile_dir` is outside `GFLOW_CLI_HOME` — protects the user's primary system Chrome profile from being used as a session store.

**UiAutomationTransport (UI Mimicry)**:
- Drives the Flow editor via real DOM interactions (clicks, keyboard events).
- **Project Setup Evolution:** Due to REST 401 blockers, this transport is moving toward "UI-First" setup, where it handles its own project creation by clicking the "+ New Project" CTA in the browser instead of relying on the `FlowApiClient.create_project` REST call.

**CLI surface:** `gflow auth login [--browser auto|chrome|internal]` (env: `GFLOW_CLI_AUTH_BROWSER`).

When the project converges on the hexagonal target above, modules graduate to layers: e.g., today's `gflow_cli.api` becomes `gflow_cli.infrastructure.flow_api`, `gflow_cli.cli` becomes `gflow_cli.interfaces.cli`, and so on. The modular-monolith shape is the staging area, not the destination.

## Folder layout

> **Note: this document describes the TARGET architecture, not the current
> package layout.** The current shape (per [PLAN.md § 2](../PLAN.md#2-architecture-steady-state)
> and [ADR #2](../PLAN.md#5-decision-log-adrs-in-miniature)) is the simpler
> `src/gflow_cli/{api/, auth/, data/, mcp/, services/, tools/, ui/, worker/}`
> packages plus flat modules — the `cli*.py` adapters, `browser_manager.py`,
> `chain*.py`, `composition.py`, `config.py`, `diagnostics.py`, `errors.py`,
> `image_batch.py`, `json_output.py`, `media.py`, `movie_manifest.py`,
> `observability.py`, `paths.py`, `profile_lease.py`, `profile_store.py`,
> `storage.py`, `update_check.py` (the once-a-day notice and `gflow update`'s
> `run_update`; its Click surface is `cli_update.py`), and `winsec.py` (inventory refreshed for
> v0.56.0, #507 — it had drifted a dozen modules behind).
> The DDD layout below was deferred indefinitely; converge toward it incrementally
> if/when a second `Provider` justifies the split (`gflow serve` shipped as a
> thin adapter over the same core rather than forcing it).

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
│   │   └── asset_storage.py
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
- **Module alias:** `gflow_cli.exceptions` is a complete re-export of `gflow_cli.errors`. Both module paths resolve to identical class objects. The alias exists so downstream integrators can use the conventional `exceptions` name without a special import path.

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
    storage = AssetStorage.from_settings(settings)

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
- **Page pool:** `FlowApiClient.__aenter__` opens `Settings.concurrency` Playwright Pages inside **one shared persistent `BrowserContext`**. Operations check out a Page via an `asyncio.Queue` (FIFO, bounded by `maxsize=N`); a double-checkin raises `QueueFull` loudly rather than corrupting the pool. See `gflow_cli.api.client.FlowApiClient._checkout_page` / `_checkin_page`. **No current CLI command drives more than one generation concurrently through this pool** — the one caller that used to fan out manifest entries via `asyncio.gather` (a manifest-driven video batch runner) never worked end-to-end and was removed as a nonfunctional stub; `gflow image batch` processes its manifest sequentially.
- **Across profiles:** parallelism by spawning one shell per profile. Chromium refuses two persistent contexts on the same `user-data-dir`, so cross-profile parallelism is a process-level concern, not a coroutine one. See [KNOWN_ISSUES § Same profile can't be used in parallel](../KNOWN_ISSUES.md#same-profile-cant-be-used-in-parallel).
- **Why a Page pool and not a `Semaphore`?** A semaphore over a single shared Page would serialize at `page.request.post` anyway (Playwright doesn't make a Page thread-safe). N Pages inside one Context let Chromium pipeline the requests while still sharing the auth cookies. T0 of the Phase 4 spike measured ~45 ms per Page creation at N=16 — well under the 200 ms/Page threshold.
- **What's backlog (post-v0.5):** a concurrent caller that actually exercises the Page pool above `N=1`, account-pool aware scheduling across multiple signed-in profiles (round-robin), and a separate-process driver so two concurrent invocations against the same profile can serialize properly via OS file lock.

## Observability (shipped, Phase 4 / v0.4.0a2)

`structlog` is configured at startup via `gflow_cli.observability.configure(...)`, called by `_cli_helpers.run_with_handlers(...)` at the CLI boundary.

Stable event names emitted at the boundary:

- **`error_raised`** — caught `GFlowError` (and subclasses). Carries `error_class`, `problem` (the full RFC 9457 Problem Details dict including `type`, `title`, `status`, `detail`, `instance`, `remediation_hint`, `route`), and `cli_command`.
- **`error_unhandled`** — any non-`GFlowError` reaching the boundary. Privacy-safe: hashes the exception message + stack trace with SHA-256; never logs raw payload. Carries `error_class`, `message_hash`, `stack_hash`, `cli_command`.

Process-boundary contextvars bind once: `correlation_id` (UUID4 per CLI invocation) and `cli_version`. Both ride along on every event line.

Format defaults to **human-readable on TTY**, **JSON when piped or `GFLOW_CLI_LOG_FORMAT=json`**. The exception renderer is constructed as `structlog.processors.ExceptionRenderer(structlog.tracebacks.ExceptionDictTransformer(show_locals=False))` — the verbose form makes the privacy guarantee visible at the call site (frame locals may contain auth cookies and signed URLs, never log them).

Pipes cleanly into `jq` / Loki / Datadog without configuration. See [`docs/USER_GUIDE.md` § Journey 6](USER_GUIDE.md#journey-6--read-the-structured-logs) for `jq` recipes.

## Incident diagnostics (shipped, v0.43.0)

`FlowApiClient` owns one session-scoped `IncidentRecorder`
(`gflow_cli.diagnostics`) because it already owns the persistent
`BrowserContext`, the Page pool, teardown ordering, settings, and the
profile lease. Lifecycle, in order:

1. **Construct before lease acquisition** (plus best-effort retention
   pruning), so `ProfileLockedError` contention can produce a metadata-only
   bundle without ever launching Chrome.
2. **Attach listeners after context launch, before the bootstrap
   navigation:** context-level `request`/`response`/`requestfailed` plus a
   `page` hook, and `console`/`pageerror` on every pooled page. Callbacks do
   synchronous primitive extraction into bounded ring journals only — they
   never retain Playwright objects, read bodies, touch the DOM, or perform
   I/O.
3. **Capture at the generation boundaries** (`generate_image` /
   `generate_images_batch` / `generate_video`) while the page is still
   alive: structural DOM + sensitive screenshot + journal snapshots are
   *staged*; the manifest is not yet written. Capture is observation-only
   and serialized by a recorder-local lock; repeats of one failure
   fingerprint only bump a suppression counter (max 3 bundles/command).
4. **Teardown order (load-bearing):** detach + freeze journals → bounded
   context close → resolve HAR state honestly from a pre-launch file
   snapshot → atomically finalize staged manifests (`manifest.json` last)
   → driver stop → lease release. Finalization runs through the same
   bounded/shielded teardown-step helper as everything else and can never
   mask a close/driver/lease failure or swallow a cancellation.

The legacy `capture_ui_diagnostics` debug dump consumes the same structural
DOM engine (`STRUCTURAL_DOM_JS` + `validate_structural_dom`) — there is one
DOM/screenshot engine, not two artifact formats. See
[DEBUGGING § Automatic incident bundles](DEBUGGING.md#automatic-incident-bundles)
and [SECURITY § Automatic incident bundles](SECURITY.md#automatic-incident-bundles-gflow_cli_incident_capture-default-on).

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

---

## System overview

```text
┌─────────────────┐
│  gflow CLI      │ ← Click + Rich
└────────┬────────┘
         │
┌────────▼────────┐
│  Provider       │ ← protocol (planned abstraction — see ROADMAP; not yet a `providers/` package)
│  abstraction    │
└────────┬────────┘
         │
   ┌─────┴─────┬───────────────┐
   │           │               │
┌──▼──┐    ┌────────┐       ┌───────┐
│Flow │    │Official│       │ Mock  │
│(now)│    │ Veo    │       │(tests)│
│     │    │(planned│       │       │
│     │    │ later) │       │       │
└──┬──┘    └────────┘       └───────┘
   │
   │   POST /v1/flow/uploadImage
   │   POST /v1/video:batchAsyncGenerateVideoText
   │   POST /v1/video:batchCheckAsyncVideoGenerationStatus
   │   PATCH /v1/flowWorkflows/{id}
   ▼
aisandbox-pa.googleapis.com  (Google's private Flow API)
```

The `Provider` interface keeps backends interchangeable. v0.1 shipped `FlowProvider`. A future release may add `OfficialVeoProvider` (via [`googleapis/python-genai`](https://github.com/googleapis/python-genai) against `generativelanguage.googleapis.com`) — same code path, swap with `GFLOW_CLI_PROVIDER=official`.

## Auth strategy

`gflow-cli` does **not** reverse-engineer Google's OAuth flow. Instead it piggybacks on Playwright's persistent context: `gflow auth login --browser chrome` opens a real Chrome window, the user signs in normally, and the resulting cookie jar is saved to a per-OS user-data dir via [`platformdirs`](https://github.com/platformdirs/platformdirs):

- Windows: `%LOCALAPPDATA%\gflow-cli\profile_default\`
- macOS: `~/Library/Application Support/gflow-cli/profile_default/`
- Linux: `~/.local/share/gflow-cli/profile_default/`

Subsequent commands launch a Playwright context using that profile and call REST endpoints via Playwright's HTTP client — which auto-attaches the cookies. No tokens to refresh manually, no SSO scraping. Auth is the only browser interaction users see, and it is a one-time event.

See [docs/AUTHENTICATION.md](AUTHENTICATION.md) for the full lifecycle, multi-account workflows, and recovery paths.

## Headed-browser dependency — current limitation

This is the project's defining trade-off and the most valuable place an external contributor could move the needle.

### Why headed

Google's auth + reCAPTCHA stack on `aisandbox-pa.googleapis.com` rejects:

1. **Playwright's bundled Chromium** — flagged by Google's bot detection on first request. The CLI fails fast with `AuthBrowserRejectedError` (exit code 14) when this happens.
2. **Headless browsers** — same fingerprinting trips during the OAuth-consent flow.
3. **Bare HTTP clients** without the cookies + tokens minted by a real Chrome session — most endpoints return HTTP 401 or a reCAPTCHA challenge that can only be solved interactively.

We attempted three pure-HTTP transport strategies before settling on `ui_automation`:

- **`evaluate_fetch`** — call `fetch()` from inside the Page context to inherit cookies. Worked for some endpoints, failed on video upload (HTTP 401 with no actionable message).
- **`bearer`** — extract bearer tokens from the live page and replay them via `httpx`. Tokens rotate too aggressively; rate-limited within minutes.
- **`sapisidhash`** — compute Google's SAPISIDHASH header from session cookies and replay. Works for read endpoints; rejected for any mutation endpoint (including all generation calls).

All three now live as standalone modules under `src/gflow_cli/api/transports/experimental/` (`evaluate_fetch.py` / `bearer.py` / `sapisidhash.py`), preserved for reference and future iteration. None survives Google's anti-bot stack for mutation/generation endpoints, so the production path is `ui_automation`.

**The migrated `flow.google.com` host (#639, v0.67.0).** Google is moving accounts off `labs.google` onto `flow.google.com`, a rewritten frontend with a different widget toolkit and a `batchexecute` wire instead of `aisandbox-pa` REST. `src/gflow_cli/api/transports/migrated_composer.py` drives that editor for text-to-video — still through the same real-Chrome Playwright session — by operating the composer UI and then *observing* the app's own `batchexecute` replies (`YhhmEf` submit, `jwpduf` status, `as29s` result) before downloading the clip. `GFLOW_CLI_FLOW_HOST` routes between the two (`auto` = migrated host for every request it can serve, `--project` required there); the rest of the command matrix keeps `ui_automation` on unmoved accounts and exits 36 on moved ones. Recon: `docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md`.

**Standalone-only transports.** `bearer` and `sapisidhash` discard any caller-supplied Playwright page and launch their own browser under a fresh `ProfileLease`, so they are **standalone-only** — they cannot run inside a `FlowApiClient` that already holds the profile lease (the second acquire would self-lock with `ProfileLockedError`). Selecting either via `GFLOW_CLI_TRANSPORT` (or the Python API) while the client owns the profile now fails fast with a clear `ConfigurationError` naming the transport, rather than the opaque lock error. `evaluate_fetch` is exempt: it reuses the client's shared page and takes no second lease. The standalone-only set lives in `STANDALONE_ONLY_TRANSPORTS` (`api/transports/__init__.py`); to drive `bearer`/`sapisidhash`, run them outside an owning client.

### What this costs users

- A **persistent Chrome profile** on disk (~150 MB after `playwright install chromium`).
- A **display server** for the one-time `gflow auth login --browser chrome` (Linux/WSL need X or Wayland; profile transplants freely between machines after).
- **Per-account horizontal concurrency** is capped by what one warm Page pool can drive — `GFLOW_CLI_CONCURRENCY=N` fans out N Playwright Pages on the same profile, but cannot scale infinitely.
- **No native serverless deployment.** Running on Lambda / Cloud Run / Fly requires a transplanted profile + a desktop-like environment.

### How contributors can help

The biggest single improvement to gflow-cli's scalability would be a **working pure-HTTP transport for video generation** that survives Google's anti-bot stack. Specific contributions we welcome:

- Network traffic captures from a successful headed video generation (with personally-identifying data scrubbed) — the [Keysight HAR analysis](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis) is the public reference; we want our own.
- A working pure-HTTP transport (extend the existing `api/transports/experimental/` subpackage) that produces a video against the live API without a browser.
- Insight into Google's reCAPTCHA-mint flow for `aisandbox-pa.googleapis.com` (especially how `Authorization: SAPISIDHASH ...` interacts with `X-Goog-Visitor-Id`).
- An adapter to the **official** Veo API (`googleapis/python-genai`) as a parallel provider — that bypasses the headed-browser dependency entirely for users who have direct API access.

If you ship any of the above, open an issue or a PR — this is the most strategic contribution any new collaborator can make to the project.
