# Phase 4 — Hardening — Design Spec

**Status:** Draft v4 (post 1st + 2nd Claude council, Gemini APPROVED, Codex REQUEST_CHANGES — all findings applied)
**Target version:** v0.4.0a1
**Date:** 2026-05-10

> **For agentic workers:** This is the SPEC, not the implementation plan. After approval, `superpowers:writing-plans` derives the task-by-task TDD plan.
>
> **Revision log:**
> - v1: initial draft.
> - v2: closed 19 CRITICAL+HIGH from 1st Claude council (status+shape error classification, `tenacity.AsyncRetrying`, `Retry-After` cap, `show_locals=False`, Playwright exception types, `errors.py` top-level, `FlowApiError` alias, reuse existing `LogFormat`/`concurrency`).
> - v3: user evolutions — RFC 9457 Problem Details, `error_raised` telemetry, modular monolith codified, Phase 6/7 backlog (PLAN.md), helper dedup folded into T4.
> - v4: applied 2nd-round 7-reviewer findings (5 Claude + Gemini APPROVED + Codex REQUEST_CHANGES). Major: **per-worker Page concurrency model** (Option A from research) replaces shared-Page-with-Semaphore; **exception hierarchy refactored** to OpenAI/Anthropic SDK convention (`GFlowError → FlowApiError → typed subclasses`) so `except FlowApiError` actually catches typed errors; **`error_unhandled` event added** + `WireFormatError` enriched with discovery fields; **`gflow_cli/_cli_helpers.py`** (top-level, no package) replaces broken `cli/_helpers.py`; status/instance fields tightened for RFC 9457 semantics; T4 split into T4a + T4b.

## 1. Why this phase

Phase 3 (v0.3.0a1) shipped image MVP. Five user-visible weaknesses remain: sequential `video batch`, transient-error abort, generic exit code 1, no remediation hints, unstructured logs with no error-class telemetry. Phase 4 closes all five and codifies the architectural foundations (Problem Details + modular monolith + structured error log + per-worker Pages) that Phase 5+ scope (PyPI release, Phase 6 SQLite history, Phase 7 pluggable storage) builds on.

## 2. Design choices (locked via brainstorm + 2 council rounds + Codex/Gemini external review + research)

| # | Choice | Decision |
|---|---|---|
| C1 | Concurrency model | **Per-worker Page within shared BrowserContext.** When `Settings.concurrency > 1`, FlowApiClient maintains a Page pool of size N inside the existing persistent BrowserContext. Each concurrent operation checks out a Page, mints reCAPTCHA + POSTs against ITS Page, returns the Page to the pool. Pages share cookies + auth at Context level (correct — same session). No state contention. Industry-standard pattern (Playwright docs + browser-use library). N from existing `Settings.concurrency: Field(default=1, ge=1, le=16)` — config already declared. |
| C2 | Retry policy | Retry HTTP 5xx + 429, plus `playwright.async_api.Error` and `playwright.async_api.TimeoutError`. 3 attempts. `tenacity.AsyncRetrying` context manager with `reraise=True` (no `RetryError` leakage; original exception's `__cause__` chain preserved). Exponential jittered backoff (1s±25% → 2s±25% → 4s±25%). `Retry-After` honored, **capped at 60s**. reCAPTCHA token re-minted **inside the retry loop body, on the worker's own Page**, every attempt. After exhaustion: `RateLimitError` (429) or `NetworkError` (5xx/transport). |
| C3 | Error classification | HTTP status + response shape. 401/403 → `AuthExpiredError`; 429 → `RateLimitError`; 200 + empty `media[]` → `ContentPolicyError` (Flow's documented signal); exhausted 5xx/network → `NetworkError`; anything else → `WireFormatError`. Five exit codes (3–7). |
| C4 | Exception hierarchy (Pythonic + OpenAI/Anthropic SDK convention) | Two-level: `GFlowError` (base, library-wide) → `FlowApiError` (parent of all API-related errors, kept as a NAMED parent class so `except FlowApiError` continues to catch in legacy callers) → typed leaves (`AuthExpiredError`, `RateLimitError`, `ContentPolicyError`, `NetworkError`, `WireFormatError`). Each carries RFC 9457 Problem Details fields: `problem_type` (URI; renamed from bare `type` to avoid ruff A003 builtin shadow), `title`, `status` (only when it's the literal HTTP status of the failed call), `detail`, `instance` (formatted as `gflow:error:<correlation_id>`, NOT the route URL — RFC 9457 §3.5), `remediation_hint`, plus `route` extension (sanitized route name like `batchGenerateImages`, NOT the full URL with query). `to_problem_details()` returns the RFC 9457 JSON shape with our two extension fields (`remediation_hint`, `route`). `FlowApiError.__init__` retains the legacy `(status, body, route=...)` constructor signature; its `body` argument MUST pass through `_redact_for_log` before being incorporated into `detail`. |
| C5 | Telemetry — two events | **`error_raised`** (caught `GFlowError` at CLI boundary): fields `error_class`, `problem` (Problem Details dict), `cli_command`, `correlation_id`. **`error_unhandled`** (any `Exception` not subclassing `GFlowError`): fields `exception_class`, `message_hash` (SHA-256 of `str(exc)`, no full message — bounded log size + privacy), `stack_hash` (SHA-256 of traceback), `cli_command`, `correlation_id`. **`WireFormatError` carries discovery fields**: `route_name`, `http_status`, `content_type`, `top_level_keys` (sorted JSON object keys), `shape_signature` (hash of sorted keys+types), `body_prefix_redacted` (first 200 chars after `_redact_for_log`). Together: `grep error_class=WireFormatError` reveals WHAT was unexpected, not just THAT something was. |
| C6 | structlog | Auto-detect TTY → text; piped → JSON. Reuses existing `LogFormat` StrEnum + `Settings.log_format`. Full migration of `logging.*` calls. `show_locals=False` in exception renderer. `bind_contextvars` for `cli_version` and `correlation_id` at process boundary; never inside async tasks (avoid cross-task leakage). |
| C7 | BDD coverage | One feature file per command group (`auth.feature`, `video.feature`, `image.feature`). 3–5 scenarios each. Per-feature directory `conftest.py` to namespace step phrases. Required: BDD step defs use only mocked `FlowApiClient` — never live API. |
| C8 | Modular monolith (architectural rule) | Codified in `docs/ARCHITECTURE.md` and §3.5 of this spec. Per-package public interface via `__init__.py`; per-file private helpers prefixed `_` (allowed within their own module; never imported across modules). NOT redefining single-file modules as packages — files are first-class modules. Phase 4 adds: `gflow_cli.errors`, `gflow_cli.observability`, `gflow_cli._cli_helpers` (top-level file, NOT a `cli/` package — prevents collision with existing `cli.py`). Full `cli/` promotion deferred to Phase 5. |
| C9 | Orchestration | Same multi-agent pattern as Phase 3. Security-touched tasks: T2 (per-worker Page model), T3 (retry on auth-bearing requests). |

## 3. Architecture

### 3.1 New modules

#### `src/gflow_cli/errors.py` (top-level — both `cli.py` and `api/client.py` import)

```python
from __future__ import annotations
from hashlib import sha256
from typing import Any, TypedDict


class ProblemDetails(TypedDict, total=False):
    """RFC 9457 Problem Details JSON shape (https://datatracker.ietf.org/doc/html/rfc9457).
    Two gflow extensions: `remediation_hint` and `route`."""
    type: str           # required
    title: str          # required
    status: int         # optional — only the literal HTTP status of the failed call
    detail: str         # optional
    instance: str       # optional — `gflow:error:<correlation_id>`
    remediation_hint: str  # gflow extension
    route: str          # gflow extension — sanitized route name, NOT full URL


class GFlowError(Exception):
    """Base class for all gflow domain errors. Library-wide root.

    Field shape: RFC 9457 Problem Details. Class-level (`problem_type`,
    `title`, `_default_remediation`) define stable identity per class.
    Instance-level (`detail`, `status`, `instance`, `remediation_hint`,
    `route`) populated per raise.

    `instance` is `gflow:error:<correlation_id>` (per-occurrence ID), NOT
    the failed route URL — RFC 9457 §3.5 says `instance` identifies the
    occurrence, not the endpoint. Route name lives in the `route` extension.
    """

    # Class-level defaults; subclasses override.
    problem_type: str = "about:blank"
    title: str = "Error"
    _default_remediation: str = ""

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
    ) -> None:
        message = self.title if not detail else f"{self.title}: {detail}"
        super().__init__(message)
        self.detail = detail
        self.status = status
        self.instance = instance or ""
        self.route = route
        self.remediation_hint = (
            remediation_hint
            if remediation_hint is not None
            else self._default_remediation
        )

    def to_problem_details(self) -> ProblemDetails:
        out: ProblemDetails = {
            "type": self.problem_type,
            "title": self.title,
        }
        if self.status is not None:
            out["status"] = self.status
        if self.detail:
            out["detail"] = self.detail
        if self.instance:
            out["instance"] = self.instance
        if self.remediation_hint:
            out["remediation_hint"] = self.remediation_hint
        if self.route:
            out["route"] = self.route
        return out


class FlowApiError(GFlowError):
    """Parent of all API-related errors. Retained as a named parent class so
    `except FlowApiError` continues to catch typed subclasses below.

    Constructor accepts BOTH the legacy 3-arg form (Phase 3 callers) AND
    the new GFlowError-style kwargs.

    Legacy form: `FlowApiError(status: int, body: str, *, route: str = "")`
    The `body` argument MUST be pre-redacted via `_redact_for_log` before
    construction (mandate per security review). It is truncated to 200
    chars and incorporated into `detail`.
    """

    problem_type = "https://gflow-cli.dev/errors/api-error"
    title = "Flow API error"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Detect legacy positional call: FlowApiError(status, body, route=...)
        if args and isinstance(args[0], int):
            status = args[0]
            body = args[1] if len(args) > 1 else ""
            route_kw = kwargs.pop("route", "")
            super().__init__(
                f"HTTP {status}: {body[:200]}",
                status=status,
                instance=kwargs.pop("instance", None),
                route=route_kw,
                remediation_hint=kwargs.pop("remediation_hint", None),
            )
            self.body = body  # legacy attribute kept for callers reading .body
        else:
            # New-style: FlowApiError(detail, status=..., instance=..., route=..., ...)
            super().__init__(*args, **kwargs)
            self.body = ""


class AuthExpiredError(FlowApiError):
    problem_type = "https://gflow-cli.dev/errors/auth-expired"
    title = "Authentication expired"
    _default_remediation = "Run `gflow auth login --profile <name>` to refresh the session."


class RateLimitError(FlowApiError):
    problem_type = "https://gflow-cli.dev/errors/rate-limit"
    title = "Rate limit or quota hit"
    _default_remediation = "Wait a few minutes; reduce GFLOW_CLI_CONCURRENCY if persistent."

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(
            detail,
            status=status,
            instance=instance,
            route=route,
            remediation_hint=remediation_hint,
        )
        self.retry_after = retry_after


class ContentPolicyError(FlowApiError):
    """Flow returned 200 with empty media[]. Note: `status` is intentionally
    omitted from to_problem_details() per RFC 9457 — `status` is the HTTP
    status of the problem, and 200 conflates with success. The literal
    upstream status (200) is recorded only via the `error_raised` log event
    as an `upstream_status` extension (see observability.py)."""

    problem_type = "https://gflow-cli.dev/errors/content-policy"
    title = "Content policy rejection"
    _default_remediation = "Flow rejected the prompt under its content policy. Soften wording or remove disallowed elements."


class NetworkError(FlowApiError):
    problem_type = "https://gflow-cli.dev/errors/network"
    title = "Network failure persisted across retries"
    _default_remediation = "Check connectivity and try again."


class WireFormatError(FlowApiError):
    """Carries discovery fields so `grep error_class=WireFormatError` in
    structured logs reveals what was unexpected, enabling new error class
    proposals. Discovery payload set at raise site via the `discovery=` kwarg."""

    problem_type = "https://gflow-cli.dev/errors/wire-format"
    title = "Unexpected response shape from Flow"
    _default_remediation = (
        "File a bug at https://github.com/ffroliva/gflow-cli/issues "
        "(do NOT include captured tokens or signed URLs)."
    )

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
        discovery: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            detail,
            status=status,
            instance=instance,
            route=route,
            remediation_hint=remediation_hint,
        )
        self.discovery = discovery or {}


# EXIT_CODE_MAP — most-specific class FIRST per isinstance walk semantics.
# Subclasses inherit their parent's exit code if they don't have their own
# entry. New entries MUST go BEFORE their parent class in this dict.
EXIT_CODE_MAP: dict[type[GFlowError], int] = {
    AuthExpiredError: 3,
    RateLimitError: 4,
    ContentPolicyError: 5,
    NetworkError: 6,
    WireFormatError: 7,
    # FlowApiError omitted — falls through to default 1
}
```

#### `src/gflow_cli/observability.py`

```python
def configure_logging(log_format: LogFormat = LogFormat.AUTO) -> None:
    """Bootstrap structlog. AUTO → text on TTY, JSON when piped. Sets
    show_locals=False on exception renderer. Binds `cli_version` and
    `correlation_id` via bind_contextvars at process boundary."""


def emit_error_event(logger: structlog.BoundLogger, exc: GFlowError, *, cli_command: str) -> None:
    """Emit `error_raised` event for caught GFlowErrors. Fields:
    error_class, problem (Problem Details dict), cli_command, correlation_id.
    For ContentPolicyError, adds upstream_status=200 extension."""


def emit_unhandled_event(
    logger: structlog.BoundLogger,
    exc: BaseException,
    *,
    cli_command: str,
) -> None:
    """Emit `error_unhandled` event for any non-GFlowError exception that
    reaches the CLI boundary. Fields: exception_class, message_hash
    (SHA-256 of str(exc)), stack_hash (SHA-256 of traceback), cli_command,
    correlation_id. Privacy: full message + traceback NOT logged."""
```

#### `src/gflow_cli/api/_retry.py` (private — extracted from client.py)

Houses `tenacity.AsyncRetrying` setup + `Retry-After`-aware wait function. Constants: `RETRY_AFTER_CAP_SECONDS = 60.0`, `MAX_ATTEMPTS = 3`. Uses `reraise=True` to surface the original exception (no `RetryError` leakage).

#### `src/gflow_cli/_cli_helpers.py` (top-level, NOT under a `cli/` package — prevents file/package collision with existing `cli.py`)

Houses `_resolve_profile`, `_make_provider_dir` (currently duplicated in `cli_image.py` + `cli_video.py`) plus the new `_handle_gflow_error` helper used by all CLI command groups.

### 3.2 Modified modules

#### `src/gflow_cli/api/client.py`

Per-worker Page model:

```python
class FlowApiClient:
    def __init__(self, ...) -> None:
        self._pages: list[Page] = []          # pool of N Pages
        self._page_queue: asyncio.Queue[Page] | None = None  # FIFO checkout
        ...

    async def __aenter__(self):
        # Existing: launch persistent context
        self._context = await self._pw.chromium.launch_persistent_context(...)
        # NEW: open N Pages within the Context (cookies + auth shared)
        N = self.settings.concurrency
        for _ in range(N):
            self._pages.append(await self._context.new_page())
        self._page_queue = asyncio.Queue()
        for p in self._pages:
            self._page_queue.put_nowait(p)
        return self

    async def _checkout_page(self) -> Page:
        return await self._page_queue.get()

    def _checkin_page(self, page: Page) -> None:
        self._page_queue.put_nowait(page)
```

The five wrapped methods (`_post_json`, `_post_generate_image`, `download`, `download_image`, `upload_image`) restructure so the per-attempt closure mints + POSTs against the worker's checked-out Page:

```python
async def _post_generate_image(self, ...) -> GeneratedImage:
    route_name = "batchGenerateImages"  # sanitized; never the full URL
    route_url = routes.batch_generate_images_url(project_id)

    async def attempt() -> Response:
        page = await self._checkout_page()
        try:
            token = await TokenMinter(page).mint(recaptcha_action)  # fresh, on this worker's Page
            body = _build_batch_generate_images_body(..., recaptcha_token=token, ...)
            return await page.request.post(route_url, headers=..., data=...)
        finally:
            self._checkin_page(page)

    response = await post_with_retry(attempt)
    correlation = structlog.contextvars.get_contextvars().get("correlation_id", "")
    instance = f"gflow:error:{correlation}"

    if response.status in (401, 403):
        raise AuthExpiredError(detail=f"HTTP {response.status}", status=response.status, instance=instance, route=route_name)
    if response.status == 429:
        raise RateLimitError(
            detail=f"HTTP {response.status}", status=response.status, instance=instance, route=route_name,
            retry_after=parse_retry_after(response),
        )
    if 400 <= response.status < 500:
        raise WireFormatError(
            detail=f"HTTP {response.status} on 4xx fallthrough",
            status=response.status, instance=instance, route=route_name,
            discovery={
                "route_name": route_name,
                "http_status": response.status,
                "content_type": response.headers.get("content-type", ""),
                "top_level_keys": sorted(list((await response.json()).keys())) if response.headers.get("content-type", "").startswith("application/json") else [],
                "body_prefix_redacted": _redact_for_log((await response.text())[:200]),
            },
        )
    if response.status >= 500:
        raise NetworkError(detail=f"HTTP {response.status} after retries", status=response.status, instance=instance, route=route_name)

    data = await response.json()
    images = GeneratedImage.from_response_dict(data)
    if not images:
        raise ContentPolicyError(detail="empty media[]", instance=instance, route=route_name)  # NOTE: status omitted (RFC 9457 semantics)
    return images[0]
```

Order: `retry-loop → checkout-page → mint-on-this-page → POST-on-this-page → checkin-page-in-finally`. Backoff sleeps between retries occur with the Page checked back in (other workers can use it). Logger calls swap to structlog as part of T3.

Phase 3 deferred `_new_session_id` flake fix is folded into T2 or T3.

#### `src/gflow_cli/config.py` — already correct; no changes

#### `src/gflow_cli/cli.py`, `cli_image.py`, `cli_video.py`

Each `_run_*` async helper wraps in try/except and dispatches via `_cli_helpers._handle_gflow_error` (catches `GFlowError`) or `_handle_unhandled_error` (catches anything else). Both emit the appropriate structured event before exiting.

### 3.3 New tests (parametrized tables explicit)

- `tests/test_errors.py`:
  - **Parametrized `to_problem_details()` round-trip table** covering every concrete subclass × every optional-field combination (present + absent). Each row asserts: `expected_keys ⊆ result.keys()`, `expected_absent ∩ result.keys() == ∅`, `json.dumps(result)` succeeds.
  - Subclass-aware `EXIT_CODE_MAP` test: synthetic `_MyAuthError(AuthExpiredError)` → exit code 3 (inherits parent's code).
  - `FlowApiError` legacy constructor: `FlowApiError(401, "body", route="/r")` works AND `isinstance(...)` of typed subclasses still hits `FlowApiError`.
  - `_redact_for_log` mandate: passing a body with a token to `FlowApiError(...)` produces a `to_problem_details()["detail"]` with the token redacted.
  - `EXIT_CODE_MAP` ordering invariant test: assert most-specific classes appear before parents.
- `tests/api/test_retry.py`: `asyncio.Event`-gated; injects `wait/stop` via `_make_retrying()`; verifies `__cause__` chain; parametrized 4xx no-retry table; `Retry-After` honor + cap at 60s; `reraise=True` (no `RetryError` leak).
- `tests/api/test_concurrency.py`: per-worker Page model. Asserts: N=2 → 2 Pages opened on `__aenter__`; checkout/checkin invariants; `asyncio.Event`-gated parallel checkout proves N concurrent attempts can hold N distinct Pages; backoff sleeps don't hold a Page.
- `tests/test_observability.py`: TTY auto-detect (mock `isatty`); `caplog` integration after `configure_logging`; `show_locals=False` verified via deliberate token-named local; `emit_error_event` shape; `emit_unhandled_event` shape (message_hash + stack_hash present, full message NOT present); `correlation_id` bound at boundary present in every event.
- `tests/cli/test_error_handling.py`: each error class produces correct exit code; remediation prints; `error_raised` and `error_unhandled` events fire correctly; `WireFormatError` discovery fields land in the `problem` dict.
- `tests/cli/test_helpers.py`: `_resolve_profile`, `_make_provider_dir`, `_handle_gflow_error` work after relocation; **negative import test** asserts `cli_image.py`/`cli_video.py` no longer define `_resolve_profile`/`_make_provider_dir` locally.
- `tests/features/{auth,video,image}.feature` + per-feature directory `conftest.py`: 12 scenarios via pytest-bdd. Required: all use mocked `FlowApiClient` — never live API. Step phrases namespaced per directory.

### 3.4 Pre-flight (T0 — non-coding spike)

T0 is now narrower since per-worker Pages eliminate the shared-Page concurrency question. T0 verifies: per-Context Page count limit (Playwright + Chromium) and confirms Page creation cost. If Pages are unexpectedly heavy (>200ms each), document and consider lazy creation. Output: note in PLAN.md.

### 3.5 Architectural rule: modular monolith

(Per docs/ARCHITECTURE.md — moved verbatim into the canonical reference. Summary: per-package public interface via `__init__.py`; per-file private helpers prefixed `_`; private helpers never imported across modules; modules don't share global mutable state; >400 line file → extract; Phase 4 keeps the flat structure with three additions: `gflow_cli.errors`, `gflow_cli.observability`, `gflow_cli._cli_helpers`.)

### 3.6 New dependencies (all via `uv add`)

- `uv add tenacity` (production)
- `uv add structlog` (production)
- `uv add --dev pytest-bdd`

## 4. Task breakdown — 9 tasks (T0 spike + 8 implementation)

| # | Task | Depends on | Sec? | Notes |
|---|---|---|---|---|
| T0 | Page-pool feasibility spike | — | no | Verify N Pages within one BrowserContext is safe + cheap. PLAN.md note. |
| T1 | `errors.py` taxonomy (RFC 9457 + `FlowApiError`-as-parent + subclass-aware EXIT_CODE_MAP + parametrized to_problem_details test) | T0 | no | `uv add` not needed |
| T2 | Per-worker Page pool on FlowApiClient (`__aenter__` opens N Pages; checkout/checkin via asyncio.Queue) + `_new_session_id` flake fix | T0 | **yes** | |
| T3 | `_retry.py` + tenacity AsyncRetrying with `reraise=True` + 4xx classification at parse sites + WireFormatError discovery fields + Retry-After cap + structlog logger swap in client.py | T1 | **yes** | `uv add tenacity` |
| T4a | `_handle_gflow_error` + `_handle_unhandled_error` in `_cli_helpers.py` | T1, T3 | no | |
| T4b | Helper relocation: `_resolve_profile`/`_make_provider_dir` from cli_image.py/cli_video.py to `_cli_helpers.py` + negative import test | T4a | no | |
| T5 | `observability.py` bootstrap + `emit_error_event` + `emit_unhandled_event` + full `logging.*` migration + `show_locals=False` + caplog compat | — (parallel with T2/T3) | no | `uv add structlog` |
| T6 | pytest-bdd setup + 3 feature files + per-feature conftests + scenario collection | T1–T5 | no | `uv add --dev pytest-bdd` |
| T7 | Documentation (USAGE/CONFIGURATION/CHANGELOG/.env.template/PLAN.md/ARCHITECTURE.md) + Phase 6/7 backlog confirmed | T1–T6 | no | |
| T8 | Bump 0.3.0a1 → 0.4.0a1 + tag | T1–T7 | no | |

T2 and T3 may run in parallel (both touch `client.py` but different sections — merge conflicts mechanical).

T0 outcome conditional clause: if Page pool is infeasible (e.g., Playwright caps Pages at <16), hard-cap `Settings.concurrency` to the discovered limit; T2 tests verify with the discovered cap.

## 5. Out of scope

- Multi-account orchestration, multi-Context per profile.
- Provider-aware retry policies, auto-archive, live retry-budget telemetry.
- Body-content heuristic for content policy (replaced by status+shape).
- **Phase 6: Local SQLite operations history + cost tracking** (PLAN.md).
- **Phase 7: Pluggable storage backend (S3/GCS/local)** (PLAN.md).
- Full `cli/` package promotion beyond `_cli_helpers.py` (Phase 5).
- DDD/CQRS layered refactor (deferred indefinitely per ADR #2).

## 6. Definition of done (Phase 4 / v0.4.0a1)

**Pre-merge (gsd-verifier scope):**

- [ ] T0 spike note in PLAN.md.
- [ ] All 9 tasks committed atomically with conventional subjects.
- [ ] Coverage ≥ 80% overall; `errors.py` and `observability.py` ≥ 95%.
- [ ] `uv run pytest -q` GREEN; `uv run pyright src` GREEN; ruff check + format clean.
- [ ] **Automated test**: forced 5xx triggers retry; mock call count == 3; structlog DEBUG output (`structlog.testing.LogCapture`) shows attempts 1→2→3 with backoff timing.
- [ ] **Automated test**: each error class → correct exit code + remediation print + `error_raised` event with `error_class`/`problem`/`cli_command`/**`correlation_id`** populated.
- [ ] **Automated test**: synthetic subclass of `AuthExpiredError` exits with code 3 (`isinstance` walk subclass-aware).
- [ ] **Automated test**: `error_unhandled` fires for non-GFlowError exception; `message_hash`/`stack_hash` present, full message absent.
- [ ] **Automated test**: WireFormatError carries `route_name`, `http_status`, `content_type`, `top_level_keys`, `body_prefix_redacted` discovery fields.
- [ ] BDD: 12 scenarios green; all use mocked client (verified via `assert_not_called` on `page.request.*`).
- [ ] CHANGELOG `[0.4.0a1]`; PLAN.md Phase 4 ✅ + Phase 6/7 backlog confirmed; pyproject + `__init__.py` show 0.4.0a1.
- [ ] `docs/ARCHITECTURE.md` modular monolith section + Problem Details note current.

**Post-merge (user-actioned):**

- [ ] Tag `v0.4.0a1` pushed; release workflow green.
- [ ] Manual smoke (~1 credit): `GFLOW_CLI_CONCURRENCY=4 gflow video batch tests/fixtures/manifest_4.tsv` ≤ 1.5× slowest single call.
- [ ] Manual smoke: kill session cookie → `gflow image t2i ...` exits 3 + remediation prints.

## 7. Open risks (post-v4)

| Risk | Mitigation |
|---|---|
| Playwright Page pool cost (memory, startup) at N=16 | T0 spike measures; hard-cap if infeasible. |
| `tenacity.AsyncRetrying` API churn | Pin ≥ 8.2; verify in T3. |
| pytest-bdd step-phrase collisions | Per-feature conftests + collision test. |
| Problem Details `type` URI registration | Document as identifier-only; non-resolvable today (acceptable per RFC 9457 §3.1). |
| `correlation_id` cross-task leakage | `bind_contextvars` only at process entry; document in observability.py. |

## 8. Self-review

- [x] Codex CRIT (cli.py/cli/ collision): `_cli_helpers.py` at top-level — no package created.
- [x] Codex CRIT (FlowApiError hierarchy): typed errors now inherit FROM FlowApiError.
- [x] Codex HIGH (Page concurrency): per-worker Page model — no shared-Page state contention.
- [x] Codex HIGH (telemetry): `error_unhandled` event + WireFormatError discovery fields.
- [x] Codex MED (status=200 on ContentPolicyError): omitted; `upstream_status` via observability extension.
- [x] Codex MED (instance as route): now `gflow:error:<correlation_id>`; route in extension field.
- [x] Codex MED (tenacity reraise): explicit `reraise=True`.
- [x] Codex MED (modular monolith wording): tightened in §3.5.
- [x] Claude TDD CRIT (parametrized to_problem_details): explicit table mandated.
- [x] Claude TDD HIGH (correlation_id in DoD): added.
- [x] Claude TDD HIGH (synthetic-subclass test): added.
- [x] Claude security HIGH (FlowApiError body redaction): mandated `_redact_for_log` in legacy constructor.
- [x] Claude planner HIGH (T4 split): T4a + T4b.
- [x] Claude planner HIGH (T0 conditional clause): added in §4.
- [x] Claude python HIGH (EXIT_CODE_MAP ordering): documented invariant, comment in code.
- [x] Claude python MED (`type` ruff A003): renamed to `problem_type`.
- [x] Claude python MED (`format` ruff A002): renamed to `log_format`.
- [x] Gemini APPROVED — design direction confirmed.

---

_End of design spec v4._
