# Phase 4 — Hardening — Design Spec

**Status:** Draft v2 (post council audit; pending user approval)
**Target version:** v0.4.0a1
**Date:** 2026-05-10
**Author:** Coordinator (collaborative brainstorm + 5-agent council audit)

> **For agentic workers:** This is the SPEC, not the implementation plan. The plan (task-by-task TDD steps with checkboxes) will be derived via `superpowers:writing-plans` after this spec is approved.
>
> **Revision log:** v1 → v2 incorporates 19 CRITICAL+HIGH findings from a 5-agent council (architect, planner, python-reviewer, security-reviewer, tdd-guide). Major changes: error classification by HTTP status + response shape (no body-substring heuristic); module renamed to `log_config.py` to avoid stdlib shadow; Semaphore moved to `__aenter__`; `tenacity.AsyncRetrying` context manager replaces decorator (so `Retry-After` header is in scope); reCAPTCHA re-mint inside retry loop; `Retry-After` capped at 60s; structlog `show_locals=False`; Playwright exception types (not httpx); `errors.py` at top-level (not under `api/`); `FlowApiError` kept as alias for back-compat; existing `LogFormat` StrEnum + `concurrency` cap reused (not redefined); spec acknowledges Playwright Page re-entrancy must be verified before N>1 ships.

## 1. Why this phase

Phase 3 (v0.3.0a1) shipped the image MVP. Five user-visible weaknesses remain:

1. `gflow video batch` runs strictly sequentially — a 50-prompt manifest takes hours.
2. Transient 5xx and rate-limit errors abort the entire batch on first failure.
3. Every error exits with code 1 (or `FlowApiError` traceback), so shell scripts cannot branch on failure type.
4. Error messages give no remediation hint (e.g. expired auth produces a 401 traceback rather than "run `gflow auth login`").
5. Logs use stdlib `logging` with bespoke formatting — not parseable as JSON, no contextvars.

Phase 4 closes all five plus introduces BDD coverage as a regression net for user journeys.

## 2. Design choices (locked in via brainstorm + council audit)

| # | Choice | Decision |
|---|---|---|
| C1 | Concurrency model | Single `asyncio.Semaphore(N)` on `FlowApiClient`, **created in `__aenter__`** (matches existing `_pw`/`_context`/`_page` lifecycle). N from existing `Settings.concurrency` field (already declared in `config.py`, range `ge=1, le=16`). Wraps every `page.request.*` call inside the retry loop body. Hard cap retained from existing config. |
| C2 | Retry policy | Retry HTTP 5xx + 429, plus `playwright.async_api.Error` and `playwright.async_api.TimeoutError` (not `httpx.*` — Playwright wraps transport differently). 3 attempts. `tenacity.AsyncRetrying` **context manager** (not `@retry` decorator) so the response object is in scope when computing `Retry-After`-aware wait. Exponential jittered backoff (1s±25% → 2s±25% → 4s±25%). `Retry-After` header honored when present, **capped at 60s** to prevent self-DoS. reCAPTCHA token re-minted inside the retry loop body, so each attempt uses a fresh token. After exhausting retries: `RateLimitError` (was 429) or `NetworkError` (was 5xx/connection). |
| C3 | Error classification | **HTTP status + response shape only — no body-substring heuristic.** Mapping: 401/403 → `AuthExpiredError`; 429 (after retry) → `RateLimitError`; 200 with empty `media[]` → `ContentPolicyError` (Flow's documented signal for content-policy rejection — already detected in Phase 3's `_post_generate_image`); exhausted-retries 5xx/network → `NetworkError`; anything else unexpected → `WireFormatError`. Five exit codes (3–7). |
| C4 | Error class shape | Each error subclasses `GFlowError`. `remediation_hint` is an `__init__` parameter with a class-level default (per-raise customizable, pyright-clean, instance-attribute). `FlowApiError` retained as a subclass alias of `GFlowError` for backwards-compat. |
| C5 | structlog | Auto-detect TTY → text; piped → JSON. Reuses existing `LogFormat` StrEnum in `config.py` + the existing `Settings.log_format` field. Full migration of every `logging.*` call site in `src/`. `show_locals=False` in exception renderer (prevents reCAPTCHA token leak via traceback locals). `bind_contextvars` used for process-global fields (e.g., `cli_version`). |
| C6 | BDD coverage | One feature file per command group (`auth.feature`, `video.feature`, `image.feature`). 3–5 scenarios each (~12 total): happy path + most-common error + one edge case. Per-feature `conftest.py` with namespaced step phrases to avoid collisions across files. |
| C7 | Orchestration | Same multi-agent pattern as Phase 3 (gsd-executor + gsd-nyquist-auditor + python-reviewer + code-reviewer; security-reviewer for security-touched tasks). |

## 3. Architecture

### 3.1 New modules

#### `src/flow_cli/errors.py` (top-level — both `cli.py` and `api/client.py` import from here)

```python
from __future__ import annotations

class GFlowError(Exception):
    """Base class for all gflow domain errors. Carries a remediation hint."""

    _default_hint: str = ""

    def __init__(self, message: str = "", *, remediation_hint: str | None = None) -> None:
        super().__init__(message)
        self.remediation_hint = (
            remediation_hint if remediation_hint is not None else self._default_hint
        )


class AuthExpiredError(GFlowError):
    _default_hint = "Run `gflow auth login --profile <name>` to refresh the session."


class RateLimitError(GFlowError):
    _default_hint = "Quota or rate limit hit. Wait a few minutes; reduce FLOW_CLI_CONCURRENCY if persistent."

    def __init__(
        self,
        message: str = "",
        *,
        remediation_hint: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, remediation_hint=remediation_hint)
        self.retry_after = retry_after


class ContentPolicyError(GFlowError):
    _default_hint = "Flow rejected the prompt under its content policy. Soften wording or remove disallowed elements."


class NetworkError(GFlowError):
    _default_hint = "Network failure persisted across retries. Check connectivity and try again."


class WireFormatError(GFlowError):
    _default_hint = (
        "Unexpected response shape from Flow. File a bug at "
        "https://github.com/ffroliva/gflow-cli/issues "
        "(do NOT include captured tokens or signed URLs)."
    )


# Backwards-compat: existing `FlowApiError` becomes a subclass alias.
# v0.4.0a1 keeps the name so `except FlowApiError` clauses in skills + smoke
# scripts continue to work; v0.5.0+ may rename.
class FlowApiError(GFlowError):
    """Legacy name. New code should use the typed subclasses above."""

    def __init__(
        self,
        status: int,
        body: str,
        *,
        route: str = "",
        remediation_hint: str | None = None,
    ) -> None:
        message = f"Flow API {route} -> HTTP {status}: {body[:200]}"
        super().__init__(message, remediation_hint=remediation_hint)
        self.status = status
        self.body = body
        self.route = route


EXIT_CODE_MAP: dict[type[GFlowError], int] = {
    AuthExpiredError: 3,
    RateLimitError: 4,
    ContentPolicyError: 5,
    NetworkError: 6,
    WireFormatError: 7,
}
```

`api/client.py` imports `from flow_cli.errors import ...`. `cli.py` and the cli_*.py files do the same.

#### `src/flow_cli/log_config.py` (renamed from `logging.py` to avoid shadowing stdlib `logging`)

```python
def configure_logging(format: LogFormat = LogFormat.AUTO) -> None:
    """Configure structlog. Called once at CLI entry point.
    AUTO resolves to TEXT on TTY, JSON when piped or in CI.
    Sets show_locals=False on the exception renderer to prevent
    reCAPTCHA token leakage via traceback local variables.
    Binds `cli_version` once via `structlog.contextvars.bind_contextvars`.
    """
```

The processor chain includes `structlog.stdlib.ProcessorFormatter` so `pytest`'s `caplog` fixture continues to capture log records — this is verified in T5's first test.

#### `src/flow_cli/api/_retry.py` (private — extracted from client.py to keep cohesion)

Houses the `tenacity.AsyncRetrying` setup, the exception-classification predicate, and the `Retry-After`-aware wait function. `client.py` imports the helper but does not host the implementation.

```python
RETRY_AFTER_CAP_SECONDS = 60.0  # security: prevent self-DoS via malicious header value

async def post_with_retry(
    func: Callable[[], Awaitable[Response]],
    *,
    on_attempt: Callable[[int], None] | None = None,
) -> Response:
    """Run `func()` under tenacity AsyncRetrying with project-standard policy.
    Retries 5xx, 429, and Playwright transport errors. Honors Retry-After
    (capped). Yields between attempts so other coroutines progress."""
```

Callers wrap their per-attempt body (mint reCAPTCHA + build body + POST + parse response) in a closure passed to `post_with_retry`. This guarantees fresh token per attempt.

### 3.2 Modified modules

#### `src/flow_cli/api/client.py`

- Constructor sets `self._sem: asyncio.Semaphore | None = None` (alongside existing `_pw`, `_context`, `_page`).
- `__aenter__` creates `self._sem = asyncio.Semaphore(self.settings.concurrency)` after Page open succeeds.
- `_post_json`, `_post_generate_image`, `download`, `download_image`, `upload_image` are restructured so the body-build/POST/parse path runs inside `async with self._sem:` inside `await post_with_retry(...)`. Concrete shape:

  ```python
  async def _post_generate_image(self, ...) -> GeneratedImage:
      async def attempt() -> Response:
          async with self._sem:
              token = await TokenMinter(self.page).mint(recaptcha_action)  # fresh per attempt
              body = _build_batch_generate_images_body(..., recaptcha_token=token, ...)
              return await self.page.request.post(url, headers=..., data=...)

      response = await post_with_retry(attempt)
      data = await response.json()
      images = GeneratedImage.from_response_dict(data)
      if not images:
          raise ContentPolicyError(f"Flow returned 200 with empty media[] for {url}")
      return images[0]
  ```

  Order locked: `retry-loop → semaphore-acquire → mint-fresh-token → POST → release-semaphore-on-exit-of-async-with`. Backoff sleeps happen with the semaphore released and no token in scope.

- 4xx classification at the response-parse site: 401/403 → `AuthExpiredError`; 429 → `RateLimitError(retry_after=parse_retry_after(response))` (raised so the retry policy can read it); other 4xx → `WireFormatError(...)`.
- Empty-media on 200 → `ContentPolicyError` (replaces the existing `FlowApiError(200, ...)` raise).
- Logger call sites (`logging.getLogger(...)` → `structlog.get_logger(...)`) updated as part of T3 (since T3 already touches this file). Avoids T5 merge churn.

#### `src/flow_cli/config.py` — already done, no changes

The existing `Settings` already has `concurrency: int = Field(default=1, ge=1, le=16)` and `log_format: LogFormat = LogFormat.AUTO`. Phase 4 reuses these without redefining. Spec v1 incorrectly proposed redefining; v2 corrects this.

#### `src/flow_cli/cli.py`, `cli_image.py`, `cli_video.py`

Top-level error handler. Pseudocode:

```python
def _handle_gflow_error(exc: GFlowError) -> int:
    code = EXIT_CODE_MAP.get(type(exc), 1)
    console.print(f"[red]{exc}[/red]")
    if exc.remediation_hint:
        console.print(f"[dim]{exc.remediation_hint}[/dim]")
    return code

# Each `_run_*` async helper wrapped:
async def _run_t2i(...) -> int:
    try:
        # ... existing body ...
        return 0
    except GFlowError as exc:
        return _handle_gflow_error(exc)

# Click sync entry:
sys.exit(asyncio.run(_run_t2i(...)))
```

Click's own `UsageError` (exit 2) flows through unmodified.

### 3.3 New tests

- `tests/test_errors.py` — exception classes, `EXIT_CODE_MAP`, `remediation_hint` instance + class attr access, `RateLimitError.retry_after` attribute, `FlowApiError` back-compat alias.
- `tests/api/test_retry.py` — retry behaviour. Uses an `asyncio.Event` (NOT timing-based counters) for deterministic in-flight assertions. Injects `wait`/`stop` parameters via a `_make_retrying()` factory so tests use `tenacity.wait_none()` and run instantly. Asserts: 3 attempts on 5xx; 4xx other than 429 not retried (`@pytest.mark.parametrize` table 400/401/403/422 → no retry); `Retry-After` honored when present; `Retry-After` capped at 60s when oversized; `__cause__` chain preserved (`RetryError.__cause__` is the original tenacity `RetryError`).
- `tests/api/test_concurrency.py` — Semaphore enforces N. Uses `asyncio.Event` to gate in-flight assertion: each fake call sets a "started" event and waits on a "release" event before completing; the test asserts `concurrency` events are set before any are released, and no more than `concurrency` are set at any time.
- `tests/test_log_config.py` — TTY auto-detection (mock `sys.stdout.isatty`); format override; `caplog` integration verified (T5 first test); `show_locals=False` verified by deliberately raising with a local variable named "token" and asserting the value does not appear in JSON output.
- `tests/cli/test_error_handling.py` — exit code per error class; remediation hint printed; back-compat for `FlowApiError`.
- `tests/features/auth.feature`, `video.feature`, `image.feature` — Gherkin scenarios.
- `tests/features/conftest.py` (or per-feature directory conftests if step phrases collide) — pytest-bdd step definitions reusing the existing CliRunner + mock patterns.
- `tests/features/test_*.py` — scenario collection wiring (`pytest-bdd` requires test functions with `@scenario(...)` or `scenarios(...)` to be discoverable).

### 3.4 Pre-flight verification (T0 — before T1 work begins)

The Phase 3 deferred concern (Playwright `page.request` re-entrancy beyond N=1) is now in scope. T0 is a 30-minute spike, not a coding task:

- Read the Playwright Python API docs for `page.request` (Playwright vN currently bundled with the project per `pyproject.toml`).
- If the docs explicitly state per-Page concurrency is unsafe, **the spec hard-caps `concurrency` at 1 in `config.py`** and Phase 4 still ships value via retry + structured errors + structlog + BDD; the concurrency knob becomes a placeholder for Phase 5 multi-Page work.
- If the docs allow concurrent `page.request.*` calls (current best understanding: yes, since Playwright APIRequestContext is documented as thread/coroutine-safe), the `le=16` cap stands; T2 tests verify with N=1, 2, 4.

T0 does not produce a commit; its result is recorded in PLAN.md or KNOWN_ISSUES.md.

### 3.5 New dependencies

Added via `uv add`:
- `tenacity` — retry primitive (`AsyncRetrying` context manager).
- `structlog` — structured logging.
- `pytest-bdd` (dev) — Gherkin scenarios.

No production dep is added except `tenacity` and `structlog`. `pytest-bdd` is dev-only.

## 4. Task breakdown — proposed 8 tasks (plus T0 spike)

Order chosen so that dependencies are explicit. T2/T3 may run in parallel after T1 (both touch `client.py` but different methods/sections; merge conflicts are mechanical).

| # | Task | Depends on | Tests | Files | Sec? |
|---|---|---|---|---|---|
| T0 | Playwright re-entrancy spike (no commit) | none | none | none — produces a note in PLAN.md or KNOWN_ISSUES.md | no |
| T1 | `errors.py` taxonomy + `EXIT_CODE_MAP` + `FlowApiError` alias | T0 | unit | new `src/flow_cli/errors.py`, `tests/test_errors.py` | no |
| T2 | `FLOW_CLI_CONCURRENCY` Semaphore on FlowApiClient (in `__aenter__`) | T0 | asyncio.Event-gated unit | `api/client.py`, `tests/api/test_concurrency.py` | **yes** |
| T3 | `_retry.py` + `tenacity.AsyncRetrying` context manager applied to 5 client methods + 4xx classification at parse sites + Retry-After cap + reCAPTCHA re-mint per attempt + structlog logger swap in client.py | T1 | unit + parametrized 4xx table | new `api/_retry.py`, `api/client.py`, `tests/api/test_retry.py` | **yes** |
| T4 | CLI top-level error handler + remediation print + back-compat for `FlowApiError` callers | T1, T3 | unit per class + back-compat | `cli.py`, `cli_image.py`, `cli_video.py`, `tests/cli/test_error_handling.py` | no |
| T5 | structlog bootstrap (new `log_config.py`) + full migration of remaining `logging.*` calls + `show_locals=False` + caplog compatibility test | none | format-detection unit + caplog integration | new `src/flow_cli/log_config.py`, every remaining `src/` file with a logger; `pyproject.toml`; `tests/test_log_config.py` | no |
| T6 | pytest-bdd setup + `tests/features/{auth,video,image}.feature` (3–5 scenarios each) + per-feature conftest | T1–T5 | scenarios run via pytest | new test surface; `pyproject.toml` | no |
| T7 | Documentation: USAGE.md (new flags + exit codes), CONFIGURATION.md (`FLOW_CLI_CONCURRENCY`, `FLOW_CLI_LOG_FORMAT`, exit-code table), `.env.template`, CHANGELOG `[0.4.0a1]`, PLAN.md Phase 4 ✅ DONE | T1–T6 | none | docs | no |
| T8 | Bump version 0.3.0a1 → 0.4.0a1; tag v0.4.0a1 | T1–T7 | none | `pyproject.toml`, `__init__.py`, tag | no |

Security-touched tasks: T2, T3.

### Pre-flight item also folded into T2/T3

While `client.py` is open in T2 and T3, also fix the deferred Phase 3 sessionId timestamp flake (extract `_new_session_id()` returning `f";{uuid.uuid4().hex}"` or similar, replacing `int(time.time() * 1000)`). This eliminates the millisecond-collision flake permanently.

## 5. Out of scope (deferred / non-goals)

- **Multi-account orchestration.** A higher-level pool that runs batches across multiple `--profile` values in parallel is not part of this phase.
- **Multiple Playwright Pages per profile.** Single Page; parallelism comes from N concurrent `page.request.*` calls guarded by the Semaphore. If T0 spike concludes Playwright is not safely re-entrant, hard-cap N=1 and defer multi-Page work to Phase 5.
- **Provider-aware retry policies.** Same retry policy applies to all routes.
- **Auto-archive on rate limit.** No automatic archiving / cleanup; pure observation + retry.
- **Live retry-budget telemetry.** No counter exposed to the CLI; users see retry events via structlog DEBUG only.
- **Body-content heuristic for content policy.** Replaced by HTTP-status + response-shape classification (C3). If Flow ever changes the empty-media signal, we'll revisit.

## 6. Definition of done (Phase 4 / v0.4.0a1)

**Pre-merge (verified locally + by gsd-verifier):**

- [ ] T0 spike conclusion recorded in PLAN.md or KNOWN_ISSUES.md.
- [ ] All 8 tasks committed atomically with conventional subjects.
- [ ] Total coverage ≥ 80%; new modules `errors.py` and `log_config.py` ≥ 95%.
- [ ] `uv run pytest -q` GREEN (208+ tests, no flakes); `uv run pyright src` GREEN (0 errors); ruff check + format clean.
- [ ] **Automated** test in `test_retry.py`: forced 5xx response triggers retry; mock call count == 3; structlog DEBUG output (captured via `caplog` or `structlog.testing.LogCapture`) shows attempt 1 → 2 → 3 with backoff timing.
- [ ] **Automated** test in `test_error_handling.py`: each error class produces the documented exit code; remediation hint prints to console.
- [ ] BDD: `pytest tests/features/` runs all 12 scenarios green.
- [ ] CHANGELOG `[0.4.0a1]` lists every user-visible change; PLAN.md Phase 4 marked ✅ DONE; pyproject + `__init__.py` show 0.4.0a1.

**Post-merge (out of gsd-verifier scope; user-actioned):**

- [ ] Tag `v0.4.0a1` pushed; release workflow ran green.
- [ ] **Manual smoke** (optional, burns ~1 credit): `FLOW_CLI_CONCURRENCY=4 gflow video batch tests/fixtures/manifest_4.tsv` (a small 4-row fixture, NOT `large.tsv`) completes in ≤ 1.5× the slowest single call. Manual; not in CI.
- [ ] **Manual smoke** (optional): kill the session cookie, run `gflow image t2i ...`, assert exit code 3 and stderr contains "run `gflow auth login`".

## 7. Open risks (post-audit; reduced from v1)

| Risk | Mitigation |
|---|---|
| Playwright `page.request` may not be coroutine-safe beyond N=1 | T0 spike resolves before any code lands. Hard-cap N=1 in `config.py` if unsafe. |
| `tenacity.AsyncRetrying` API surface in target version | Pin a recent tenacity version (≥ 8.2). Verify in T3. |
| `pytest-bdd` step-phrase collision across feature files | Per-feature directory conftests with namespaced phrases; explicit collision test in T6's first scenario. |
| `structlog` exception renderer default `show_locals` | Explicitly set `False` in `log_config.py`; T5 test asserts via deliberate exception with a "token"-named local. |

## 8. Self-review — checks performed by author after writing v2

- [x] All 19 CRITICAL+HIGH council findings addressed in §2 / §3 / §6 / §7.
- [x] No "TBD" / "TODO" / placeholder language outside the explicit "open risks" section (§7).
- [x] Internal consistency: every choice in §2 matches its expansion in §3.
- [x] Scope check: 8 tasks + T0 spike is reasonable (Phase 3 had 12).
- [x] Ambiguity check: every concrete value (N=1 default, le=16 cap from existing config, retry curve, exit codes 3–7, log-format keys via existing StrEnum) is explicit.
- [x] Distinguishes new vs modified vs unchanged files in §3.
- [x] Acceptance criteria in §6 are testable and split pre-merge / post-merge.
- [x] References Phase 3 orchestration so this phase doesn't duplicate process docs.
- [x] No body-substring heuristics anywhere (replaced by status + shape classification).
- [x] No module name shadows stdlib.
- [x] No spec contradicts existing `config.py` (`LogFormat` StrEnum, `concurrency` field with `le=16`).
- [x] `FlowApiError` rename is non-breaking (kept as subclass alias).
- [x] reCAPTCHA token mint is inside the retry loop (re-minted per attempt).

---

_End of design spec v2._
