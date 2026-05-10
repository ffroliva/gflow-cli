# Phase 4 — Hardening — Design Spec

**Status:** Draft (pending council audit)
**Target version:** v0.4.0a1
**Date:** 2026-05-10
**Author:** Coordinator (collaborative brainstorm with user)

> **For agentic workers:** This is the SPEC, not the implementation plan. The plan (task-by-task TDD steps with checkboxes) will be derived via `superpowers:writing-plans` after this spec is approved.

## 1. Why this phase

Phase 3 (v0.3.0a1) shipped the image MVP. Five user-visible weaknesses remain:

1. `gflow video batch` runs strictly sequentially — a 50-prompt manifest takes hours.
2. Transient 5xx and rate-limit errors abort the entire batch on first failure.
3. Every error exits with code 1 (or `FlowApiError` traceback), so shell scripts cannot branch on failure type.
4. Error messages give no remediation hint (e.g. expired auth produces a 401 traceback rather than "run `gflow auth login`").
5. Logs use stdlib `logging` with bespoke formatting — not parseable as JSON, no contextvars.

Phase 4 closes all five plus introduces BDD coverage as a regression net for user journeys.

## 2. Design choices (locked in via brainstorm 2026-05-10)

| # | Choice | Decision |
|---|---|---|
| C1 | Concurrency model | Single `asyncio.Semaphore(N)` on `FlowApiClient`. N = `FLOW_CLI_CONCURRENCY` (default 1, hard cap 8). Wraps every `page.request.*` call. |
| C2 | Retry policy | Retry HTTP 5xx, 429, `httpx.ConnectError`, `httpx.ReadTimeout`. 3 attempts. Exponential backoff with jitter (1s → 2s → 4s, ±25%). Never retry 4xx other than 429. |
| C3 | Error taxonomy | Project-specific exit codes (3–7). New exception classes subclassing a base `GFlowError` with a `remediation_hint: str` attribute. |
| C4 | structlog | Auto-detect TTY → text format; piped → JSON. `FLOW_CLI_LOG_FORMAT` env override (`auto`/`text`/`json`). Full migration of every `logging.*` call site in `src/`. Tests stay on stdlib logging (pytest captures it natively). |
| C5 | BDD coverage | One feature file per command group (`auth.feature`, `video.feature`, `image.feature`). 3–5 scenarios each (~12 total): happy path + most-common error + one edge case. |
| C6 | Orchestration | Same multi-agent pattern as Phase 3 (gsd-executor + gsd-nyquist-auditor + python-reviewer + code-reviewer; security-reviewer for security-touched tasks). |

## 3. Architecture

### 3.1 New modules

#### `src/flow_cli/api/errors.py`

Pure module. No imports outside stdlib + project enums. Defines:

```python
from __future__ import annotations
from dataclasses import dataclass

class GFlowError(Exception):
    """Base class for all gflow domain errors. Carries a remediation hint."""
    remediation_hint: str = ""

class AuthExpiredError(GFlowError):
    remediation_hint = "Run `gflow auth login --profile <name>` to refresh the session."

class RateLimitError(GFlowError):
    remediation_hint = "Wait a few minutes and retry; reduce FLOW_CLI_CONCURRENCY if persistent."

class ContentPolicyError(GFlowError):
    remediation_hint = "Flow rejected the prompt. Soften wording or remove disallowed content."

class NetworkError(GFlowError):
    remediation_hint = "Network failure after retries. Check connectivity and try again."

class WireFormatError(GFlowError):
    remediation_hint = "Unexpected response shape from Flow. File a bug at https://github.com/ffroliva/gflow-cli/issues."

EXIT_CODE_MAP: dict[type[GFlowError], int] = {
    AuthExpiredError: 3,
    RateLimitError: 4,
    ContentPolicyError: 5,
    NetworkError: 6,
    WireFormatError: 7,
}
```

The existing `FlowApiError` becomes a subclass of `GFlowError` (or is replaced by the more specific subclasses where the constructor knows which class applies). Migration: at every `raise FlowApiError(...)` site, classify by HTTP status / response body and raise the specific subclass.

#### `src/flow_cli/logging.py`

Pure-config module. Bootstrap function:

```python
def configure_logging(format: Literal["auto", "text", "json"] = "auto") -> None:
    """Configure structlog. Called once at CLI entry point.
    `auto` resolves to `text` on TTY, `json` when piped or in CI."""
```

Process: detect `sys.stdout.isatty()` for `auto`. Configure structlog with the corresponding processor chain (Rich/coloured for text, `structlog.processors.JSONRenderer()` for json). Bind a `cli_version` field once.

### 3.2 Modified modules

#### `src/flow_cli/api/client.py`

- Constructor adds `self._sem = asyncio.Semaphore(settings.concurrency)`.
- A new private decorator `_retry_transient` (or `tenacity.retry` with predicate) wraps `_post_json`, `_post_generate_image`, `download`, `download_image`, `upload_image`.
- Inside the wrapped methods, the body is re-entered via `async with self._sem:` so the semaphore counts concurrent in-flight HTTP requests.
- The retry predicate catches: response.status in {500, 502, 503, 504, 429}, `httpx.ConnectError`, `httpx.ReadTimeout`. Other exceptions propagate immediately.
- On `429`, honor the `Retry-After` response header if present, else use computed backoff.
- After exhausting retries, re-raise as `RateLimitError` (for 429) or `NetworkError` (for 5xx/connection).
- For 401/403, raise `AuthExpiredError`.
- For 400 with content-policy markers in the body, raise `ContentPolicyError`. Heuristic: response body contains `"safety"`, `"policy"`, or `"violates"`. (Verify against captured 4xx samples; if the markers don't match, fall back to generic `WireFormatError`.)

#### `src/flow_cli/config.py`

```python
class Settings(BaseSettings):
    concurrency: int = Field(default=1, ge=1, le=8, env="FLOW_CLI_CONCURRENCY")
    log_format: Literal["auto", "text", "json"] = Field(
        default="auto", env="FLOW_CLI_LOG_FORMAT"
    )
```

Existing fields unchanged.

#### `src/flow_cli/cli.py`, `cli_image.py`, `cli_video.py`

Top-level error handler. Pseudocode:

```python
def _handle_error(exc: Exception) -> int:
    if isinstance(exc, GFlowError):
        code = EXIT_CODE_MAP.get(type(exc), 1)
        console.print(f"[red]{exc}[/red]")
        if exc.remediation_hint:
            console.print(f"[dim]{exc.remediation_hint}[/dim]")
        return code
    raise  # let Click handle UsageError, etc.
```

Each `_run_*` async helper is wrapped (decorator or try/except) to invoke `_handle_error` and `sys.exit(code)`.

Every `logging.getLogger(__name__)` call site → `structlog.get_logger(__name__)`. Same call surface; no behavioural change beyond the structured output.

### 3.3 New tests

- `tests/api/test_errors.py` — exception classes, `EXIT_CODE_MAP`, remediation hints.
- `tests/api/test_client_retry.py` — retry behaviour (3 attempts, backoff timing, exception classification).
- `tests/api/test_client_concurrency.py` — Semaphore enforces N. Use `asyncio.gather` to fire 6 calls with `concurrency=2` and assert at most 2 are in-flight at any time (via a counter mock).
- `tests/test_logging.py` — TTY auto-detection, format override.
- `tests/cli/test_error_handling.py` — exit codes per error class, remediation print.
- `tests/features/auth.feature`, `video.feature`, `image.feature` — Gherkin scenarios.
- `tests/features/conftest.py` — pytest-bdd step definitions reusing existing CliRunner + mock patterns.

### 3.4 New dependencies

Added via `uv add`:
- `tenacity` — retry decorator (well-tested, supports async + jitter + custom predicates).
- `structlog` — structured logging.
- `pytest-bdd` (dev) — Gherkin scenarios.

No production dep is added except `tenacity` and `structlog` (both small, pure-Python). `pytest-bdd` is dev-only.

## 4. Task breakdown — proposed 8 tasks

Order chosen so that each task can complete and merge independently; later tasks depend on earlier ones for taxonomy + infrastructure but never block on shape choices that aren't yet locked.

| # | Task | Wire scope | Tests | Files | Sec? |
|---|---|---|---|---|---|
| T1 | `errors.py` taxonomy + `EXIT_CODE_MAP` | none | unit | new `api/errors.py`, `tests/api/test_errors.py` | no |
| T2 | `FLOW_CLI_CONCURRENCY` Semaphore on FlowApiClient | none | unit + counter-mock | `api/client.py`, `config.py`, new test | **yes** |
| T3 | `tenacity` retry decorator on the five wrapped methods | none | unit; mock 5xx/429/network | `api/client.py`, `pyproject.toml`, new test | **yes** |
| T4 | CLI top-level error handler + remediation hints + classify call sites | new error subclasses raised | unit per class | `cli.py`, `cli_image.py`, `cli_video.py`, `api/client.py` (raise sites) | no |
| T5 | structlog bootstrap + full migration of `logging.*` calls | none | format-detection unit | new `logging.py`, every `src/` file with a logger; `pyproject.toml` | no |
| T6 | pytest-bdd setup + `tests/features/{auth,video,image}.feature` (3–5 scenarios each) | none | scenarios run via pytest | new test surface; `pyproject.toml` | no |
| T7 | Documentation: USAGE.md (new flags + exit codes), CONFIGURATION.md (`FLOW_CLI_CONCURRENCY`, `FLOW_CLI_LOG_FORMAT`), `.env.template`, CHANGELOG `[0.4.0a1]` | n/a | none | docs | no |
| T8 | Bump version 0.3.0a1 → 0.4.0a1; tag v0.4.0a1 | n/a | none | `pyproject.toml`, `__init__.py`, tag | no |

Security-touched tasks (need security-reviewer per Phase 3 matrix conventions): T2 (concurrency on auth-bearing connection — defense-in-depth on the deferred T5 finding from Phase 3), T3 (retry on auth-bearing requests — must not retry 4xx/auth, must not amplify rate-limit through reCAPTCHA token reuse).

## 5. Out of scope (deferred / non-goals)

- **Multi-account orchestration.** A higher-level pool that runs batches across multiple `--profile` values in parallel is not part of this phase. Single profile, single account.
- **Multiple Playwright Pages per profile.** Concurrency stays bounded by one Page; parallelism comes from N concurrent `page.request.*` calls into the same Page guarded by the Semaphore. If Playwright's `page.request` is not safely re-entrant beyond N=4, we cap N at 4 in code; the env var still allows ≤8 for forward-compat.
- **Provider-aware retry policies.** Same retry policy applies to all routes. If a specific route needs a different policy, that's a future change.
- **Auto-archive on rate limit.** No automatic archiving / cleanup; pure observation + retry.
- **Live retry-budget telemetry.** No counter exposed to the CLI; users see retry events via structlog DEBUG only.

## 6. Definition of done (Phase 4 / v0.4.0a1)

- [ ] All 8 tasks committed atomically with conventional subjects.
- [ ] Total coverage ≥ 80%; new modules `api/errors.py` and `logging.py` ≥ 95%.
- [ ] `uv run pytest -q` GREEN; `uv run pyright src` GREEN.
- [ ] Smoke: `FLOW_CLI_CONCURRENCY=4 gflow video batch large.tsv` completes in ≤ 1.5× the slowest single call (manual sanity).
- [ ] Smoke: forced 5xx response triggers retry; structlog DEBUG output shows attempt 1 → 2 → 3 with backoff timing.
- [ ] Auth-expired path: kill the session cookie, run any `gflow image t2i` invocation, assert exit code is 3 and stderr contains "run `gflow auth login`".
- [ ] BDD: `pytest tests/features/` runs all 12 scenarios green.
- [ ] CHANGELOG `[0.4.0a1]` lists every user-visible change; PLAN.md Phase 4 marked ✅ DONE; pyproject + `__init__.py` show 0.4.0a1.
- [ ] Tag `v0.4.0a1` pushed; release workflow ran green.

## 7. Open risks / known unknowns

| Risk | Mitigation |
|---|---|
| `tenacity`'s async support has edge cases with `async with` blocks | Use the `tenacity.retry` decorator on the outermost method; the inner `async with self._sem:` is unaffected. Tested early in T3. |
| `httpx.ReadTimeout` may be raised inside `page.request.*` (Playwright wraps httpx differently) | Verify in T3 spike. If Playwright's exception types differ, extend the predicate to include them. |
| Content-policy heuristic ("safety"/"policy"/"violates") may be locale-dependent | If the markers don't match captured samples, fall back to generic `WireFormatError`. Document the heuristic in the docstring. |
| structlog full migration touches ~15 files; risk of breaking a test that captures `caplog` | Tests using `caplog` (pytest fixture) keep working because structlog's stdlib processor forwards to the root logger by default. Verify in T5. |
| pytest-bdd step-def collision across feature files | One shared `conftest.py` with cleanly-namespaced step phrases. If collision happens, split conftests by feature dir. |
| The hard cap N=8 may be too aggressive on slow networks | Conservative default of 1 ships unchanged; users opt in. Doc clarifies the cap is a sanity bound, not a tested ceiling. |

## 8. Self-review — checks performed by author after writing

- [x] No "TBD" / "TODO" / placeholder language outside the explicit "open risks" section.
- [x] Internal consistency: every choice in §2 matches its expansion in §3.
- [x] Scope check: 8 tasks is reasonable for one phase (Phase 3 had 12; Phase 2 had 13). No further decomposition needed.
- [x] Ambiguity check: every concrete value (default N, retry curve, exit codes 3–7, log-format keys) is explicit. The only intentional under-specification is the content-policy heuristic, which is documented as a known unknown.
- [x] Distinguishes new vs modified files in §3.
- [x] Acceptance criteria in §6 are concrete and testable, not vibes.
- [x] References the Phase 3 orchestration so this phase doesn't duplicate process docs.

---

_End of design spec._
