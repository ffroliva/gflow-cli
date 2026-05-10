# Phase 4 — Hardening — Design Spec

**Status:** Draft v3 (post 1st council audit + user evolution; pending 2nd council audit + Codex/Gemini external review)
**Target version:** v0.4.0a1
**Date:** 2026-05-10
**Author:** Coordinator (collaborative brainstorm + 5-agent council audit + user-driven evolutions)

> **For agentic workers:** This is the SPEC, not the implementation plan. The plan (task-by-task TDD steps with checkboxes) will be derived via `superpowers:writing-plans` after this spec is approved.
>
> **Revision log:**
> - v1 → v2 incorporated 19 CRITICAL+HIGH findings from a 5-agent Claude council. Major: status+shape error classification (no body heuristic); module renamed to `log_config.py`; Semaphore in `__aenter__`; `tenacity.AsyncRetrying` context manager; `Retry-After` capped at 60s; `show_locals=False`; Playwright exception types; `errors.py` at top-level; `FlowApiError` retained as alias; reuse existing `LogFormat` StrEnum + `concurrency` cap.
> - v2 → v3 incorporates user-driven evolutions: (a) error class shape now follows **RFC 9457 Problem Details** vocabulary; (b) error raise sites emit a structured `error_raised` log event so untracked error classes can be discovered via log analysis; (c) **modular monolith** codified as an architectural rule (also added to ARCHITECTURE.md); (d) backlog: local SQLite persistence (Phase 6) + pluggable object storage (Phase 7) — both added to PLAN.md, NOT in v0.4.0a1 scope; (e) helper dedup between cli_image.py / cli_video.py folded into T4.

## 1. Why this phase

Phase 3 (v0.3.0a1) shipped the image MVP. Five user-visible weaknesses remain:

1. `gflow video batch` runs strictly sequentially — a 50-prompt manifest takes hours.
2. Transient 5xx and rate-limit errors abort the entire batch on first failure.
3. Every error exits with code 1 (or `FlowApiError` traceback), so shell scripts cannot branch on failure type.
4. Error messages give no remediation hint (e.g. expired auth produces a 401 traceback rather than "run `gflow auth login`").
5. Logs use stdlib `logging` with bespoke formatting — not parseable as JSON, no contextvars, no error-class telemetry.

Phase 4 closes all five plus introduces BDD coverage as a regression net for user journeys, and lays the architectural foundation (Problem Details + modular monolith + structured error log) for the Phase 6/7 backlog.

## 2. Design choices (locked in via brainstorm + 1st council + user evolution)

| # | Choice | Decision |
|---|---|---|
| C1 | Concurrency model | Single `asyncio.Semaphore(N)` on `FlowApiClient`, **created in `__aenter__`** (matches existing `_pw`/`_context`/`_page` lifecycle). N from existing `Settings.concurrency` field (already declared in `config.py`, range `ge=1, le=16`). Wraps every `page.request.*` call inside the retry loop body. Hard cap retained from existing config. |
| C2 | Retry policy | Retry HTTP 5xx + 429, plus `playwright.async_api.Error` and `playwright.async_api.TimeoutError` (not `httpx.*` — Playwright wraps transport differently). 3 attempts. `tenacity.AsyncRetrying` **context manager** (not `@retry` decorator) so the response object is in scope when computing `Retry-After`-aware wait. Exponential jittered backoff (1s±25% → 2s±25% → 4s±25%). `Retry-After` header honored when present, **capped at 60s** to prevent self-DoS. reCAPTCHA token re-minted inside the retry loop body, so each attempt uses a fresh token. After exhausting retries: `RateLimitError` (was 429) or `NetworkError` (was 5xx/connection). |
| C3 | Error classification | **HTTP status + response shape only — no body-substring heuristic.** Mapping: 401/403 → `AuthExpiredError`; 429 (after retry) → `RateLimitError`; 200 with empty `media[]` → `ContentPolicyError` (Flow's documented signal for content-policy rejection); exhausted-retries 5xx/network → `NetworkError`; anything else unexpected → `WireFormatError`. Five exit codes (3–7). |
| C4 | Error class shape | **Aligned with [RFC 9457 Problem Details](https://datatracker.ietf.org/doc/html/rfc9457).** Each `GFlowError` carries Problem Details fields: `type` (stable URI identifier per error class), `title` (human summary), `status` (HTTP status when applicable), `detail` (free-form per-raise text), `instance` (the URI/route that failed), plus our extension `remediation_hint`. `GFlowError.to_problem_details()` returns the JSON-shape dict. `FlowApiError` retained as a subclass alias for backwards-compat. |
| C5 | Error-tracking telemetry | Every `GFlowError` raised at the CLI boundary emits a structlog **`error_raised`** event with stable fields (`error_class`, `problem` (the Problem Details dict), `cli_command`, `correlation_id`). Users can `grep error_class=WireFormatError` in logs to discover untracked error patterns and propose new error classes. This closes the feedback loop: the error taxonomy is empirical, not aspirational. |
| C6 | structlog | Auto-detect TTY → text; piped → JSON. Reuses existing `LogFormat` StrEnum in `config.py` + `Settings.log_format` field. Full migration of every `logging.*` call site in `src/`. `show_locals=False` in exception renderer (prevents reCAPTCHA token leak via traceback locals). `bind_contextvars` used for process-global fields (`cli_version`, `correlation_id`). |
| C7 | BDD coverage | One feature file per command group (`auth.feature`, `video.feature`, `image.feature`). 3–5 scenarios each (~12 total): happy path + most-common error + one edge case. Per-feature `conftest.py` with namespaced step phrases. |
| C8 | Modular monolith | Codify as architectural rule (see §3.5). gflow-cli converges to a flat-namespace modular monolith: each top-level package under `src/flow_cli/` is a module with a defined public interface. Phase 4 introduces `errors` and `observability` (logging + future metrics) modules. The flat `cli.py` / `cli_image.py` / `cli_video.py` files share helpers via a new `cli/_helpers.py` (shared dedup, no full package promotion yet — defer to Phase 5). |
| C9 | Orchestration | Same multi-agent pattern as Phase 3 (gsd-executor + gsd-nyquist-auditor + python-reviewer + code-reviewer; security-reviewer for security-touched tasks). |

## 3. Architecture

### 3.1 New modules

#### `src/flow_cli/errors.py` (top-level — both `cli.py` and `api/client.py` import)

```python
from __future__ import annotations
from typing import Any


class GFlowError(Exception):
    """Base class for all gflow domain errors.

    Field shape follows RFC 9457 Problem Details:
    https://datatracker.ietf.org/doc/html/rfc9457

    Class-level vars (`type`, `title`, `_default_remediation`) define the
    stable identity of each error class. Instance attributes (`detail`,
    `status`, `instance`, `remediation_hint`) are populated per-raise.
    """

    # Class-level Problem Details defaults; subclasses override.
    type: str = "about:blank"           # stable URI identifier
    title: str = "Error"                 # human-readable summary
    _default_remediation: str = ""

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        remediation_hint: str | None = None,
    ) -> None:
        message = self.title if not detail else f"{self.title}: {detail}"
        super().__init__(message)
        self.detail = detail
        self.status = status
        self.instance = instance
        self.remediation_hint = (
            remediation_hint
            if remediation_hint is not None
            else self._default_remediation
        )

    def to_problem_details(self) -> dict[str, Any]:
        """Serialize to RFC 9457 JSON shape (with `remediation_hint` extension).

        Used by structlog event emitters and any future HTTP-style error
        rendering. Stable contract — downstream consumers may grep by
        `type` URI to count occurrences of a specific error class."""
        out: dict[str, Any] = {"type": self.type, "title": self.title}
        if self.status is not None:
            out["status"] = self.status
        if self.detail:
            out["detail"] = self.detail
        if self.instance:
            out["instance"] = self.instance
        if self.remediation_hint:
            out["remediation_hint"] = self.remediation_hint
        return out


class AuthExpiredError(GFlowError):
    type = "https://gflow-cli.dev/errors/auth-expired"
    title = "Authentication expired"
    _default_remediation = "Run `gflow auth login --profile <name>` to refresh the session."


class RateLimitError(GFlowError):
    type = "https://gflow-cli.dev/errors/rate-limit"
    title = "Rate limit or quota hit"
    _default_remediation = "Wait a few minutes; reduce FLOW_CLI_CONCURRENCY if persistent."

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        remediation_hint: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(
            detail,
            status=status,
            instance=instance,
            remediation_hint=remediation_hint,
        )
        self.retry_after = retry_after


class ContentPolicyError(GFlowError):
    type = "https://gflow-cli.dev/errors/content-policy"
    title = "Content policy rejection"
    _default_remediation = "Flow rejected the prompt under its content policy. Soften wording or remove disallowed elements."


class NetworkError(GFlowError):
    type = "https://gflow-cli.dev/errors/network"
    title = "Network failure persisted across retries"
    _default_remediation = "Check connectivity and try again."


class WireFormatError(GFlowError):
    type = "https://gflow-cli.dev/errors/wire-format"
    title = "Unexpected response shape from Flow"
    _default_remediation = (
        "File a bug at https://github.com/ffroliva/gflow-cli/issues "
        "(do NOT include captured tokens or signed URLs)."
    )


class FlowApiError(GFlowError):
    """Backwards-compat subclass alias.

    Phase 3 raised `FlowApiError(status, body, route=...)` for any 4xx/5xx.
    Phase 4 retains this constructor signature so `except FlowApiError`
    clauses in skills and smoke scripts continue to catch the new typed
    subclasses (which all derive from GFlowError, of which FlowApiError
    is also a subclass).

    New raise sites should use the typed subclasses (`AuthExpiredError`,
    `RateLimitError`, etc.). v0.5.0+ may deprecate this alias.
    """

    type = "https://gflow-cli.dev/errors/legacy-flow-api-error"
    title = "Flow API error"

    def __init__(
        self,
        status: int,
        body: str,
        *,
        route: str = "",
        remediation_hint: str | None = None,
    ) -> None:
        super().__init__(
            f"HTTP {status}: {body[:200]}",
            status=status,
            instance=route,
            remediation_hint=remediation_hint,
        )
        self.body = body
        # Compat alias kept for code that reads `.route` directly.
        self.route = route


EXIT_CODE_MAP: dict[type[GFlowError], int] = {
    AuthExpiredError: 3,
    RateLimitError: 4,
    ContentPolicyError: 5,
    NetworkError: 6,
    WireFormatError: 7,
}
```

#### `src/flow_cli/observability.py` (renamed from `log_config.py` to align with the modular-monolith naming convention — `observability` is the future home for metrics + tracing too)

```python
def configure_logging(format: LogFormat = LogFormat.AUTO) -> None:
    """Configure structlog. Called once at CLI entry point.

    AUTO resolves to TEXT on TTY, JSON when piped or in CI.
    Sets show_locals=False on the exception renderer to prevent
    reCAPTCHA token leakage via traceback local variables.
    Binds `cli_version` and `correlation_id` once via
    `structlog.contextvars.bind_contextvars`.

    The processor chain includes `structlog.stdlib.ProcessorFormatter`
    so pytest's `caplog` fixture continues to capture log records.
    """


def emit_error_event(logger: structlog.BoundLogger, exc: GFlowError, *, cli_command: str) -> None:
    """Emit a structured `error_raised` event with Problem Details.

    Stable contract — downstream telemetry tools rely on these field names:
        event = "error_raised"
        error_class = type(exc).__name__   (e.g., "AuthExpiredError")
        problem = exc.to_problem_details() (RFC 9457 dict)
        cli_command = "<command-group> <subcommand>"
    """
    logger.error(
        "error_raised",
        error_class=type(exc).__name__,
        problem=exc.to_problem_details(),
        cli_command=cli_command,
    )
```

#### `src/flow_cli/api/_retry.py` (private — extracted from client.py to keep cohesion)

Houses the `tenacity.AsyncRetrying` setup, the exception-classification predicate, and the `Retry-After`-aware wait function.

```python
RETRY_AFTER_CAP_SECONDS = 60.0  # security: prevent self-DoS via malicious header value

async def post_with_retry(
    func: Callable[[], Awaitable[Response]],
    *,
    on_attempt: Callable[[int], None] | None = None,
) -> Response:
    """Run `func()` under tenacity AsyncRetrying with project-standard policy.

    Retries 5xx, 429, and Playwright transport errors. Honors Retry-After
    (capped at RETRY_AFTER_CAP_SECONDS). Yields between attempts so other
    coroutines progress. Re-mints reCAPTCHA inside `func` on every attempt
    (caller responsibility — `func` is the per-attempt closure).
    """
```

Callers wrap their per-attempt body (mint reCAPTCHA + build body + POST + parse response) in a closure passed to `post_with_retry`.

#### `src/flow_cli/cli/_helpers.py` (NEW — shared CLI helpers, dedup target for T4)

The functions `_resolve_profile` and `_make_provider_dir` are currently duplicated verbatim in `cli_image.py` and `cli_video.py`. T4 moves them here. The existing flat files `cli.py` / `cli_image.py` / `cli_video.py` import from `flow_cli.cli._helpers`. Full package promotion of `cli/` is deferred to Phase 5.

> **Note:** Strictly speaking, `flow_cli/cli/_helpers.py` requires `flow_cli/cli/__init__.py` to exist. T4 creates the empty `__init__.py` and moves the helpers; the flat files import from the new package. Click sub-command registration in `cli.py` is unchanged.

### 3.2 Modified modules

#### `src/flow_cli/api/client.py`

- Constructor sets `self._sem: asyncio.Semaphore | None = None` (alongside existing `_pw`, `_context`, `_page`).
- `__aenter__` creates `self._sem = asyncio.Semaphore(self.settings.concurrency)` after Page open succeeds.
- `_post_json`, `_post_generate_image`, `download`, `download_image`, `upload_image` are restructured so the body-build/POST/parse path runs inside `async with self._sem:` inside `await post_with_retry(...)`. Concrete shape (per-attempt closure):

  ```python
  async def _post_generate_image(self, ...) -> GeneratedImage:
      route = routes.batch_generate_images_url(project_id)

      async def attempt() -> Response:
          async with self._sem:
              token = await TokenMinter(self.page).mint(recaptcha_action)  # fresh per attempt
              body = _build_batch_generate_images_body(..., recaptcha_token=token, ...)
              return await self.page.request.post(route, headers=..., data=...)

      response = await post_with_retry(attempt)

      # Status-based 4xx classification (after retry exhausted)
      if response.status in (401, 403):
          raise AuthExpiredError(detail=f"HTTP {response.status}", status=response.status, instance=route)
      if response.status == 429:
          raise RateLimitError(detail=f"HTTP {response.status}", status=response.status, instance=route, retry_after=parse_retry_after(response))
      if 400 <= response.status < 500:
          raise WireFormatError(detail=f"HTTP {response.status} on 4xx classification fallthrough", status=response.status, instance=route)
      if response.status >= 500:
          raise NetworkError(detail=f"HTTP {response.status} after retries", status=response.status, instance=route)

      data = await response.json()
      images = GeneratedImage.from_response_dict(data)
      if not images:
          raise ContentPolicyError(detail="empty media[]", status=200, instance=route)
      return images[0]
  ```

  Order locked: `retry-loop → semaphore-acquire → mint-fresh-token → POST → release-semaphore-on-exit-of-async-with`. Backoff sleeps happen with the semaphore released and no token in scope.

- Logger call sites (`logging.getLogger(...)` → `structlog.get_logger(...)`) updated as part of T3 to avoid T5 merge churn.
- The deferred Phase 3 `_new_session_id` flake fix is folded into T2 or T3 (extract `_new_session_id()` returning `f";{uuid.uuid4().hex}"`).

#### `src/flow_cli/config.py` — already done, no changes

The existing `Settings` already has `concurrency: int = Field(default=1, ge=1, le=16)` and `log_format: LogFormat = LogFormat.AUTO`. Phase 4 reuses these without redefining.

#### `src/flow_cli/cli.py`, `cli_image.py`, `cli_video.py`

Top-level error handler. Pseudocode:

```python
# cli/_helpers.py (NEW)
def _handle_gflow_error(exc: GFlowError, *, cli_command: str) -> int:
    # Telemetry: emit error event so log-grep can find untracked patterns.
    emit_error_event(get_logger(), exc, cli_command=cli_command)

    # isinstance walk (not `type(exc)` lookup) so subclasses inherit
    # parent class's exit code if they don't have their own entry.
    code = next(
        (c for cls, c in EXIT_CODE_MAP.items() if isinstance(exc, cls)),
        1,
    )
    console.print(f"[red]{exc}[/red]")
    if exc.remediation_hint:
        console.print(f"[dim]{exc.remediation_hint}[/dim]")
    return code

# cli_image.py / cli_video.py — each `_run_*` async helper:
async def _run_t2i(...) -> int:
    try:
        # ... existing body ...
        return 0
    except GFlowError as exc:
        return _handle_gflow_error(exc, cli_command="image t2i")

# Click sync entry:
sys.exit(asyncio.run(_run_t2i(...)))
```

Click's own `UsageError` (exit 2) flows through unmodified.

### 3.3 New tests

- `tests/test_errors.py` — exception classes; `EXIT_CODE_MAP`; `to_problem_details()` round-trip; class-level `type`/`title` defaults; instance-level `detail`/`status`/`instance`/`remediation_hint` overrides; `RateLimitError.retry_after` attribute; `FlowApiError` back-compat alias (existing `(status, body, route=...)` constructor still works).
- `tests/api/test_retry.py` — retry behaviour. Uses `asyncio.Event` (NOT timing-based counters) for deterministic in-flight assertions. Injects `wait`/`stop` parameters via `_make_retrying()` factory so tests use `tenacity.wait_none()` and run instantly. Asserts: 3 attempts on 5xx; 4xx other than 429 not retried; `Retry-After` honored; capped at 60s; `__cause__` chain preserved.
- `tests/api/test_concurrency.py` — Semaphore enforces N. Uses `asyncio.Event` to gate in-flight assertions deterministically.
- `tests/test_observability.py` — TTY auto-detection (mock `sys.stdout.isatty`); format override; `caplog` integration verified (T5 first test); `show_locals=False` verified by deliberately raising with a local variable named "token" and asserting the value does not appear in JSON output; **`emit_error_event` produces the documented `error_raised` shape** (stable contract test).
- `tests/cli/test_error_handling.py` — exit code per error class; remediation hint printed; `error_raised` event emitted with correct fields; back-compat for `FlowApiError`.
- `tests/cli/test_helpers.py` — `_resolve_profile` and `_make_provider_dir` work after relocation to `cli/_helpers.py`.
- `tests/features/auth.feature`, `video.feature`, `image.feature` — Gherkin scenarios.
- `tests/features/conftest.py` (or per-feature directory conftests) — pytest-bdd step definitions.
- `tests/features/test_*.py` — scenario collection wiring.

### 3.4 Pre-flight verification (T0 — before T1 work begins)

The Phase 3 deferred concern (Playwright `page.request` re-entrancy beyond N=1) is now in scope.

- Read Playwright Python API docs for `page.request`.
- If unsafe at N>1, hard-cap `concurrency` at 1 in `config.py`. Phase 4 still ships value via retry + structured errors + observability + BDD; concurrency knob becomes a placeholder for Phase 5.
- If safe (current best understanding: yes), keep `le=16` and let T2 tests verify with N=1, 2, 4.

T0 produces a note in PLAN.md or KNOWN_ISSUES.md, no commit.

### 3.5 Architectural rule: Modular monolith (codified by Phase 4, applies to Phase 5+)

> gflow-cli is a **modular monolith** — a single deployable artifact organized as a flat namespace of clearly-bounded modules.
>
> **Per-module rules:**
> - Each top-level package or file under `src/flow_cli/` is a module with one clear domain (`auth`, `api`, `cli`, `errors`, `observability`, `manifest`, `paths`, `config`, `profile_store`).
> - Each module exposes a public interface via `__init__.py` and (where applicable) explicit `__all__`.
> - Internals are prefixed with `_` (single leading underscore) and never imported across modules.
> - Cross-module communication goes through public interfaces, never private internals.
> - Modules don't share global mutable state. Configuration is read-only at the boundary (`Settings`).
> - When a module file grows past 400 lines, prefer extraction to a sub-package over inline growth.
>
> **Phase 4 module additions:**
> - `flow_cli.errors` — Problem Details exception taxonomy.
> - `flow_cli.observability` — structlog config + `error_raised` event emitter.
> - `flow_cli.cli._helpers` — dedup target for shared CLI helpers (full `cli/` package promotion deferred).
>
> **Phase 4 does NOT:**
> - Restructure existing modules beyond minimal dedup.
> - Introduce dependency-injection containers, command/query buses, or any DDD/CQRS scaffolding (deferred per ADR #2).
>
> This rule is also added to `docs/ARCHITECTURE.md` as the canonical reference.

### 3.6 New dependencies

Added via `uv add`:
- `tenacity` — retry primitive (`AsyncRetrying` context manager).
- `structlog` — structured logging.
- `pytest-bdd` (dev) — Gherkin scenarios.

No production dep is added except `tenacity` and `structlog`. `pytest-bdd` is dev-only.

## 4. Task breakdown — proposed 8 tasks (plus T0 spike)

Order chosen so dependencies are explicit. T2/T3 may run in parallel after T1.

| # | Task | Depends on | Tests | Files | Sec? |
|---|---|---|---|---|---|
| T0 | Playwright re-entrancy spike (no commit) | none | none | PLAN.md or KNOWN_ISSUES.md note | no |
| T1 | `errors.py` taxonomy (Problem Details shape) + `EXIT_CODE_MAP` + `FlowApiError` alias + `to_problem_details()` | T0 | unit (incl. round-trip) | new `src/flow_cli/errors.py`, `tests/test_errors.py` | no |
| T2 | `FLOW_CLI_CONCURRENCY` Semaphore on FlowApiClient (in `__aenter__`) + `_new_session_id` extraction | T0 | asyncio.Event-gated unit | `api/client.py`, `tests/api/test_concurrency.py` | **yes** |
| T3 | `_retry.py` + `tenacity.AsyncRetrying` + 4xx classification at parse sites + `Retry-After` cap + reCAPTCHA re-mint per attempt + structlog logger swap in client.py | T1 | unit + parametrized 4xx table | new `api/_retry.py`, `api/client.py`, `tests/api/test_retry.py` | **yes** |
| T4 | CLI top-level error handler + `emit_error_event` integration + remediation print + `cli/_helpers.py` extraction + helper dedup | T1, T3 | unit per class + back-compat + helper relocation | `cli.py`, `cli_image.py`, `cli_video.py`, new `cli/_helpers.py`, `cli/__init__.py`, `tests/cli/test_error_handling.py`, `tests/cli/test_helpers.py` | no |
| T5 | `observability.py` bootstrap + full migration of remaining `logging.*` calls + `show_locals=False` + caplog compat + `emit_error_event` wired | none (parallel with T2/T3) | format-detection + caplog + token-not-in-traceback unit | new `src/flow_cli/observability.py`, every remaining `src/` file with a logger; `pyproject.toml`; `tests/test_observability.py` | no |
| T6 | pytest-bdd setup + `tests/features/{auth,video,image}.feature` + per-feature conftests | T1–T5 | scenarios run via pytest | new test surface; `pyproject.toml` | no |
| T7 | Documentation: USAGE.md (new flags + exit codes + Problem Details `type` URI table), CONFIGURATION.md (`FLOW_CLI_CONCURRENCY`, `FLOW_CLI_LOG_FORMAT`, exit-code table), `.env.template`, CHANGELOG `[0.4.0a1]`, PLAN.md Phase 4 ✅ DONE + Phase 6/7 backlog confirmed | T1–T6 | none | docs | no |
| T8 | Bump version 0.3.0a1 → 0.4.0a1; tag v0.4.0a1 | T1–T7 | none | `pyproject.toml`, `__init__.py`, tag | no |

Security-touched tasks: T2, T3.

## 5. Out of scope (deferred / non-goals)

- **Multi-account orchestration.** A higher-level pool that runs batches across multiple `--profile` values in parallel.
- **Multiple Playwright Pages per profile.** Single Page; parallelism comes from Semaphore-guarded `page.request.*`.
- **Provider-aware retry policies.** Same retry policy applies to all routes.
- **Auto-archive on rate limit.** No automatic archiving / cleanup.
- **Live retry-budget telemetry.** No counter exposed; users see retry events via structlog DEBUG only.
- **Body-content heuristic for content policy.** Replaced by HTTP-status + response-shape classification.
- **Local SQLite/DuckDB persistence layer.** See PLAN.md Phase 6 (operations history, cost tracking, `gflow history` command surface).
- **Pluggable object-storage backend** (S3 / GCS / local filesystem). See PLAN.md Phase 7 (`--storage-backend s3://bucket/prefix/`).
- **Full `cli/` package promotion** beyond `_helpers.py`. Deferred to Phase 5 to keep Phase 4 scope honest.
- **Dependency-injection containers, command/query buses, full DDD/CQRS layered refactor.** Deferred indefinitely per ADR #2.

## 6. Definition of done (Phase 4 / v0.4.0a1)

**Pre-merge (verified locally + by gsd-verifier):**

- [ ] T0 spike conclusion recorded in PLAN.md or KNOWN_ISSUES.md.
- [ ] All 8 tasks committed atomically with conventional subjects.
- [ ] Total coverage ≥ 80%; new modules `errors.py` and `observability.py` ≥ 95%.
- [ ] `uv run pytest -q` GREEN (208+ tests, no flakes); `uv run pyright src` GREEN; ruff check + format clean.
- [ ] **Automated** test in `test_retry.py`: forced 5xx response triggers retry; mock call count == 3; structlog DEBUG output (captured via `caplog` or `structlog.testing.LogCapture`) shows attempt 1 → 2 → 3 with backoff timing.
- [ ] **Automated** test in `test_error_handling.py`: each error class produces the documented exit code; remediation hint prints; **`error_raised` event emitted with `error_class` + `problem` (Problem Details dict) fields**.
- [ ] BDD: `pytest tests/features/` runs all 12 scenarios green.
- [ ] CHANGELOG `[0.4.0a1]` lists every user-visible change; PLAN.md Phase 4 marked ✅ DONE; Phase 6 + 7 backlog confirmed; pyproject + `__init__.py` show 0.4.0a1.
- [ ] `docs/ARCHITECTURE.md` includes the modular monolith section + Problem Details note.

**Post-merge (out of gsd-verifier scope; user-actioned):**

- [ ] Tag `v0.4.0a1` pushed; release workflow ran green.
- [ ] **Manual smoke** (optional, burns ~1 credit): `FLOW_CLI_CONCURRENCY=4 gflow video batch tests/fixtures/manifest_4.tsv` completes in ≤ 1.5× the slowest single call.
- [ ] **Manual smoke** (optional): kill the session cookie, run `gflow image t2i ...`, assert exit code 3 and stderr contains "run `gflow auth login`".

## 7. Open risks (post-audit)

| Risk | Mitigation |
|---|---|
| Playwright `page.request` may not be coroutine-safe beyond N=1 | T0 spike resolves before any code lands. Hard-cap N=1 in `config.py` if unsafe. |
| `tenacity.AsyncRetrying` API surface in target version | Pin tenacity ≥ 8.2. Verify in T3. |
| `pytest-bdd` step-phrase collision across feature files | Per-feature directory conftests with namespaced phrases; explicit collision test in T6's first scenario. |
| `structlog` exception renderer default `show_locals` | Explicitly set `False` in `observability.py`; T5 test asserts via deliberate exception with a "token"-named local. |
| Problem Details `type` URI registration | URIs use `https://gflow-cli.dev/errors/...` placeholder host. Domain may not resolve; that's fine — `type` URIs in RFC 9457 are identifiers, not dereferenceable. Document explicitly in §3.1 docstring. If we later host a documentation page at that domain, the URIs become dereferenceable for free. |

## 8. Self-review — checks performed by author after writing v3

- [x] All 19 v1 council CRITICAL+HIGH findings remain closed.
- [x] All 5 v2→v3 user evolutions reflected: Problem Details shape (C4), `error_raised` event (C5), modular monolith (C8), backlog forward-pointer to Phase 6/7 (§5), helper dedup (T4 + new `cli/_helpers.py`).
- [x] No "TBD" / "TODO" / placeholder language outside §7.
- [x] Internal consistency: every choice in §2 matches its expansion in §3.
- [x] Scope check: 8 tasks + T0 spike. T4 grew (added helper dedup) but stays focused.
- [x] Ambiguity check: every concrete value (N=1 default, le=16 cap, retry curve, exit codes 3–7, `type` URIs) is explicit.
- [x] Distinguishes new vs modified vs unchanged files in §3.
- [x] Acceptance criteria in §6 split pre-merge / post-merge; each is testable.
- [x] Out-of-scope items in §5 reference PLAN.md backlog phases by number.
- [x] No body-substring heuristics anywhere.
- [x] No module name shadows stdlib (renamed `log_config.py` → `observability.py`).
- [x] No spec contradicts existing `config.py` (`LogFormat` StrEnum, `concurrency` field with `le=16`).
- [x] `FlowApiError` rename is non-breaking (kept as subclass alias).
- [x] reCAPTCHA token mint is inside the retry loop (re-minted per attempt).
- [x] `_handle_gflow_error` uses `isinstance` walk for subclass-aware EXIT_CODE_MAP lookup.
- [x] Architecture rule for modular monolith codified (§3.5) with clear scope (Phase 4 minimal additions, Phase 5+ for full restructure).

---

_End of design spec v3._
