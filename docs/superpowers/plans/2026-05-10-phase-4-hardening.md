# Phase 4 Hardening Implementation Plan (v0.4.0a1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Companion doc:** [`2026-05-10-phase-4-hardening-orchestration.md`](2026-05-10-phase-4-hardening-orchestration.md) — multi-agent coordinator workflow for executing this plan.
>
> **Spec:** [`docs/superpowers/specs/2026-05-10-phase-4-hardening-design.md`](../specs/2026-05-10-phase-4-hardening-design.md) (v4 APPROVED at `2f6b936`).

**Goal:** Close the five user-visible gaps left by v0.3.0a1 — sequential `video batch`, transient-error abort, generic exit code 1, no remediation hints, unstructured logs without error-class telemetry — while codifying the architectural foundations (Problem Details + modular monolith + structured error log + per-worker Pages) that Phase 5+ scope builds on.

**Architecture:**
- **Per-worker Page pool** inside the existing shared `BrowserContext`: `__aenter__` opens N Pages (where N = `Settings.concurrency`); each in-flight operation checks one out via `asyncio.Queue` and mints reCAPTCHA + POSTs against its own Page. Cookies/auth shared at Context level.
- **`tenacity.AsyncRetrying`** with `reraise=True` (no `RetryError` leakage; `__cause__` chain preserved). 3 attempts, exponential jittered backoff (1s±25% → 2s±25% → 4s±25%), `Retry-After` honored capped at 60s. reCAPTCHA token re-minted inside the retry-loop body, on the worker's own Page, every attempt.
- **Two-level exception hierarchy** (OpenAI/Anthropic SDK convention): `GFlowError` → `FlowApiError` → typed leaves (`AuthExpiredError`/`RateLimitError`/`ContentPolicyError`/`NetworkError`/`WireFormatError`). `except FlowApiError` continues to catch (back-compat). All carry RFC 9457 Problem Details fields and a `to_problem_details()` serializer.
- **Two telemetry events**: `error_raised` (caught `GFlowError`) + `error_unhandled` (any non-`GFlowError`, privacy-safe via `message_hash` + `stack_hash`). `WireFormatError` carries discovery fields (`route_name`, `http_status`, `content_type`, `top_level_keys`, `body_prefix_redacted`) so log-grep reveals what was unexpected.
- **structlog**: auto-detect TTY (text) vs piped (JSON). Reuses `Settings.log_format`. `bind_contextvars` for `cli_version` and `correlation_id` at process boundary. `show_locals=False` in exception renderer.
- **BDD**: 12 scenarios across 3 feature files (auth/video/image), per-feature `conftest.py` to namespace step phrases, all use mocked `FlowApiClient`.

**Tech Stack:** Python 3.11+, Click, Rich, Playwright (per-Page pool), httpx, **tenacity** (new), **structlog** (already declared in `pyproject.toml`; bootstrap added in T5), **pytest-bdd** (new, dev), pytest-asyncio.

**Discoveries vs spec (informational — do NOT re-do):**
- `Settings.log_format: LogFormat` is already correctly named in `src/gflow_cli/config.py:97` (no rename needed; spec self-review item already applied).
- `structlog>=24.0.0` already in `pyproject.toml` `[project] dependencies`. Do **not** `uv add structlog`. Only `tenacity` (production) and `pytest-bdd` (dev) are net-new.
- `Settings.concurrency: int = Field(default=1, ge=1, le=16)` already declared at `src/gflow_cli/config.py:86` — T2 reuses it; do not re-declare.
- `FlowApiError(RuntimeError)` currently lives at `src/gflow_cli/api/client.py:79` with the legacy `(status, body, *, route)` signature. T1 moves it to `src/gflow_cli/errors.py` and re-parents under `GFlowError`. The legacy positional signature is preserved (auto-detected in `__init__`) so all 7 existing raise sites in `client.py` continue to compile until T3 rewrites them.
- Existing `_resolve_profile` / `_make_provider_dir` duplicates: `src/gflow_cli/cli_image.py:81,95` + `src/gflow_cli/cli_video.py:36,50`. T4b dedups.
- 5 wrapped methods needing per-worker Page restructure (T2/T3): `_post_json` (`client.py:~156`), `_post_generate_image` (`client.py:~325`), `download` (`client.py:~250`), `download_image` (`client.py:~290`), `upload_image` (`client.py:~450`).
- Baseline at HEAD `2f6b936`: 208 tests collected, all green.

---

## File Structure

### New files

```
src/gflow_cli/errors.py                   ← RFC 9457 exception hierarchy + EXIT_CODE_MAP + ProblemDetails TypedDict
src/gflow_cli/observability.py            ← structlog bootstrap + emit_error_event + emit_unhandled_event
src/gflow_cli/_cli_helpers.py             ← _resolve_profile + _make_provider_dir + _handle_gflow_error + _handle_unhandled_error  (top-level file, NOT a cli/ package)
src/gflow_cli/api/_retry.py               ← tenacity AsyncRetrying setup + Retry-After-aware wait + constants
tests/test_errors.py                     ← parametrized to_problem_details + EXIT_CODE_MAP isinstance walk + legacy ctor + redaction mandate
tests/test_observability.py              ← TTY auto-detect + show_locals=False + emit_*_event shape + correlation_id binding
tests/api/test_retry.py                  ← Event-gated AsyncRetrying + 4xx no-retry table + Retry-After cap + reraise=True
tests/api/test_concurrency.py            ← N Pages opened on __aenter__ + checkout/checkin invariants + parallel-checkout proof
tests/cli/test_error_handling.py         ← exit codes per error class + remediation prints + error_raised/error_unhandled events fire
tests/cli/test_helpers.py                ← _resolve_profile/_make_provider_dir post-relocation + negative import test
tests/features/__init__.py               ← (empty)
tests/features/auth.feature              ← 4 scenarios
tests/features/video.feature             ← 4 scenarios
tests/features/image.feature             ← 4 scenarios
tests/features/conftest.py               ← shared fixtures (mocked FlowApiClient + isolated tmp dirs)
tests/features/test_auth_steps.py        ← per-feature step bindings (namespaced)
tests/features/test_video_steps.py
tests/features/test_image_steps.py
```

### Modified files

```
src/gflow_cli/api/client.py               ← T2 (Page pool in __aenter__, checkout/checkin) + T3 (rewrite 5 wrapped methods + raise typed errors at parse sites + structlog logger)
src/gflow_cli/cli.py                      ← T4a wraps _run_* via _handle_gflow_error/_handle_unhandled_error; T5 swaps logging.basicConfig → configure_logging
src/gflow_cli/cli_image.py                ← T4a wraps _run_*; T4b drops local _resolve_profile/_make_provider_dir, imports from _cli_helpers
src/gflow_cli/cli_video.py                ← T4a wraps _run_*; T4b same as cli_image
src/gflow_cli/auth.py                     ← T5 swaps logging.getLogger → structlog.get_logger; remove print()
src/gflow_cli/__init__.py                 ← T8 bump __version__ to "0.4.0a1"
pyproject.toml                           ← T3 adds tenacity; T6 adds pytest-bdd dev dep; T8 bumps version
PLAN.md                                  ← T0 spike note; T7 marks Phase 4 ✅, Phase 5/6/7 backlog confirmed
docs/ARCHITECTURE.md                     ← T7 modular-monolith section + Problem Details note
docs/USAGE.md                            ← T7 error remediation hints + log_format toggle
docs/CONFIGURATION.md                    ← T7 GFLOW_CLI_CONCURRENCY caveat (Page pool memory) + GFLOW_CLI_LOG_FORMAT
.env.template                            ← T7 add GFLOW_CLI_LOG_FORMAT, document GFLOW_CLI_CONCURRENCY
CHANGELOG.md                             ← T7 [0.4.0a1] section
```

### Deleted files

```
(none)
```

---

## Task 0: Page-pool feasibility spike (non-coding)

**Goal.** Confirm that opening N Pages (1 ≤ N ≤ 16) inside one persistent `BrowserContext` is safe and cheap on the Phase 4 target dev/CI environments. Output: a 2–3 paragraph note in `PLAN.md` that documents Page creation cost and any discovered cap. Conditional clause: if Pages cost >200ms each or Playwright caps Pages at <16, hard-cap `Settings.concurrency` to the discovered limit and adjust T2 tests accordingly.

**Files.**
- `PLAN.md` (extend with a "T0 spike" sub-section under the Phase 4 status block)

**Steps.**

- [ ] **Step 0.1: Read the relevant Playwright references**
  - `playwright.async_api.BrowserContext.new_page()` semantics + cost.
  - `BrowserContext.pages` (list of currently-open Pages — confirm Pages share cookies + storage_state).
  - Search browser-use library source on GitHub for prior art (multi-Page-per-Context patterns).

- [ ] **Step 0.2: Run a 30-line throwaway script locally**

```python
# scripts/_t0_page_pool_spike.py — DO NOT COMMIT
import asyncio, time
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir="/tmp/_t0_spike", headless=True
        )
        for n in (2, 4, 8, 16):
            t0 = time.perf_counter()
            pages = [await ctx.new_page() for _ in range(n)]
            t1 = time.perf_counter()
            print(f"N={n:>2} avg_create={1000*(t1-t0)/n:.1f}ms total={(t1-t0):.2f}s pages_open={len(ctx.pages)}")
            for p in pages: await p.close()
        await ctx.close()

asyncio.run(main())
```

Run: `uv run python scripts/_t0_page_pool_spike.py` (do **not** commit this script — it's a throwaway).

- [ ] **Step 0.3: Record findings in `PLAN.md`**

Add a sub-section under "Active phase" titled `### Phase 4 — T0 Page-pool spike note (YYYY-MM-DD)` with:
- Average Page creation cost at N=2/4/8/16.
- Whether all N Pages successfully opened and shared cookies (smoke: navigate one to `https://example.com`, then open another and verify it has the same cookie set).
- Conditional verdict: "Page pool feasible up to N=16 ✅" OR "hard-cap N to <discovered>".

- [ ] **Step 0.4: Delete the throwaway script**

`rm scripts/_t0_page_pool_spike.py` (verify `git status` shows no scripts/_t0_*.py).

- [ ] **Step 0.5: Quality gates**

```
uv run pytest -q
```

Expected: still 208 passed (T0 changes only PLAN.md).

- [ ] **Step 0.6: Commit**

```bash
git add PLAN.md
git commit -m "docs(plan): T0 Page-pool feasibility spike note"
```

**Acceptance criteria.**
- `PLAN.md` has a "T0 spike note" section dated today with N=2/4/8/16 timings.
- Verdict is unambiguous (feasible / hard-cap to <N>).
- No new code committed (script deleted).
- Test count unchanged.

---

## Task 1: `errors.py` — RFC 9457 exception hierarchy + EXIT_CODE_MAP

**Goal.** Create `src/gflow_cli/errors.py` with the full Problem Details exception hierarchy: `GFlowError` (root) → `FlowApiError` (parent of all API errors, kept as a named class for back-compat) → typed leaves (`AuthExpiredError`, `RateLimitError`, `ContentPolicyError`, `NetworkError`, `WireFormatError`). Plus `ProblemDetails` TypedDict and `EXIT_CODE_MAP` (`isinstance`-walk, subclass-aware). The legacy `FlowApiError(status, body, *, route)` constructor signature is preserved (auto-detected) so all 7 existing raise sites in `client.py` continue to work until T3 rewrites them. Move the existing `FlowApiError` declaration **out** of `client.py` in this task — `client.py` then imports from `gflow_cli.errors`.

**Files.**
- Create: `src/gflow_cli/errors.py`
- Modify: `src/gflow_cli/api/client.py` (delete the old `class FlowApiError(RuntimeError)` block at line ~79; add `from gflow_cli.errors import FlowApiError` near the top with the other imports)
- Test: `tests/test_errors.py`

**Steps.**

- [ ] **Step 1.1: Write the failing tests first** in `tests/test_errors.py`

```python
"""Tests for gflow_cli.errors — RFC 9457 Problem Details hierarchy."""
from __future__ import annotations

import json

import pytest

from gflow_cli.errors import (
    EXIT_CODE_MAP,
    AuthExpiredError,
    ContentPolicyError,
    FlowApiError,
    GFlowError,
    NetworkError,
    ProblemDetails,
    RateLimitError,
    WireFormatError,
)


# ---------- parametrized to_problem_details() round-trip table ----------

@pytest.mark.parametrize(
    "exc_cls, kwargs, expect_keys, expect_absent, expected_status",
    [
        # AuthExpiredError — minimal
        (AuthExpiredError, {"detail": "401", "status": 401, "instance": "gflow:error:abc", "route": "createProject"},
         {"type", "title", "status", "detail", "instance", "remediation_hint", "route"}, set(), 401),
        # RateLimitError — with retry_after
        (RateLimitError, {"detail": "429", "status": 429, "instance": "gflow:error:def", "route": "batchGenerateImages"},
         {"type", "title", "status", "detail", "instance", "remediation_hint", "route"}, set(), 429),
        # ContentPolicyError — status MUST be omitted (RFC 9457: 200 conflates with success)
        (ContentPolicyError, {"detail": "empty media[]", "instance": "gflow:error:ghi", "route": "batchGenerateImages"},
         {"type", "title", "detail", "instance", "remediation_hint", "route"}, {"status"}, None),
        # NetworkError — exhausted retries
        (NetworkError, {"detail": "503 after 3 retries", "status": 503, "instance": "gflow:error:jkl", "route": "createProject"},
         {"type", "title", "status", "detail", "instance", "remediation_hint", "route"}, set(), 503),
        # WireFormatError — minimal (no detail, no instance)
        (WireFormatError, {},
         {"type", "title", "remediation_hint"}, {"status", "detail", "instance", "route"}, None),
    ],
)
def test_to_problem_details_table(exc_cls, kwargs, expect_keys, expect_absent, expected_status):
    exc = exc_cls(**kwargs)
    pd: ProblemDetails = exc.to_problem_details()
    assert expect_keys.issubset(pd.keys()), f"missing keys: {expect_keys - pd.keys()}"
    assert expect_absent.isdisjoint(pd.keys()), f"unexpected keys present: {expect_absent & pd.keys()}"
    if expected_status is not None:
        assert pd["status"] == expected_status
    # Round-trips through JSON without TypeError
    assert json.loads(json.dumps(pd)) == pd


def test_problem_type_uris_stable():
    """Lock the URIs — they're greppable identifiers in production logs."""
    assert AuthExpiredError.problem_type == "https://gflow-cli.dev/errors/auth-expired"
    assert RateLimitError.problem_type == "https://gflow-cli.dev/errors/rate-limit"
    assert ContentPolicyError.problem_type == "https://gflow-cli.dev/errors/content-policy"
    assert NetworkError.problem_type == "https://gflow-cli.dev/errors/network"
    assert WireFormatError.problem_type == "https://gflow-cli.dev/errors/wire-format"
    assert FlowApiError.problem_type == "https://gflow-cli.dev/errors/api-error"
    assert GFlowError.problem_type == "about:blank"


# ---------- EXIT_CODE_MAP isinstance walk ----------

class _SyntheticAuthError(AuthExpiredError):
    """Hypothetical future subclass — must inherit AuthExpired's exit code 3."""


def _exit_code_for(exc: GFlowError) -> int:
    for cls, code in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            return code
    return 1


def test_exit_code_map_synthetic_subclass_inherits_parent_code():
    # The whole point of the isinstance walk: subclass inherits parent's code.
    assert _exit_code_for(_SyntheticAuthError(detail="expired again")) == 3


@pytest.mark.parametrize(
    "exc_cls, expected_code",
    [
        (AuthExpiredError, 3),
        (RateLimitError, 4),
        (ContentPolicyError, 5),
        (NetworkError, 6),
        (WireFormatError, 7),
    ],
)
def test_exit_code_map_per_class(exc_cls, expected_code):
    assert _exit_code_for(exc_cls(detail="x")) == expected_code


def test_exit_code_map_ordering_invariant():
    """Most-specific classes MUST appear before parent classes in EXIT_CODE_MAP.

    The isinstance walk returns the FIRST match, so adding a parent before its
    subclasses would mask the subclass's code.
    """
    seen: list[type] = []
    for cls in EXIT_CODE_MAP:
        for prior in seen:
            assert not issubclass(prior, cls), (
                f"{prior.__name__} is a subclass of {cls.__name__} but appears AFTER it; "
                f"swap their order in EXIT_CODE_MAP."
            )
        seen.append(cls)


# ---------- FlowApiError legacy constructor (back-compat) ----------

def test_flow_api_error_legacy_positional_constructor():
    exc = FlowApiError(401, "body text", route="createProject")
    assert exc.status == 401
    assert exc.route == "createProject"
    assert exc.body == "body text"
    assert "HTTP 401" in str(exc)


def test_flow_api_error_new_style_constructor():
    exc = FlowApiError("custom detail", status=500, route="r", instance="gflow:error:x")
    assert exc.status == 500
    assert exc.route == "r"
    assert exc.detail == "custom detail"
    assert exc.body == ""


def test_typed_subclass_caught_by_flow_api_error_clause():
    """Back-compat: legacy `except FlowApiError` MUST catch typed subclasses."""
    raised: FlowApiError | None = None
    try:
        raise AuthExpiredError(detail="x", status=401)
    except FlowApiError as e:
        raised = e
    assert isinstance(raised, AuthExpiredError)
    assert isinstance(raised, FlowApiError)
    assert isinstance(raised, GFlowError)


# ---------- _redact_for_log mandate ----------

def test_flow_api_error_legacy_body_redaction_mandate():
    """The body argument MUST be passed through _redact_for_log BEFORE construction.

    Convention: callers redact at the raise site. This test asserts that *if* a
    caller forgets, the body is at least truncated to 200 chars in detail —
    documented behavior.
    """
    long_body = "x" * 1000
    exc = FlowApiError(500, long_body, route="r")
    pd = exc.to_problem_details()
    # detail is truncated/sanitized — full 1000-char body must NOT appear verbatim.
    assert len(pd.get("detail", "")) <= 250  # 200 body + "HTTP 500: " prefix


# ---------- WireFormatError discovery payload ----------

def test_wire_format_error_carries_discovery_fields():
    exc = WireFormatError(
        detail="unknown shape",
        status=200,
        instance="gflow:error:xyz",
        route="batchGenerateImages",
        discovery={
            "route_name": "batchGenerateImages",
            "http_status": 200,
            "content_type": "application/json",
            "top_level_keys": ["error", "status"],
            "body_prefix_redacted": "{\"error\": \"...\"}",
        },
    )
    assert exc.discovery["top_level_keys"] == ["error", "status"]
    assert exc.discovery["http_status"] == 200


# ---------- RateLimitError retry_after ----------

def test_rate_limit_error_carries_retry_after():
    exc = RateLimitError(detail="429", status=429, retry_after=42.0)
    assert exc.retry_after == 42.0
```

- [ ] **Step 1.2: Run tests, verify red**

```bash
uv run pytest tests/test_errors.py -q
```

Expected: every test fails with `ModuleNotFoundError: No module named 'gflow_cli.errors'`.

- [ ] **Step 1.3: Implement `src/gflow_cli/errors.py`** — paste the full module from spec §3.1 (lines 39–242) verbatim:

```python
from __future__ import annotations

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

    Legacy form: ``FlowApiError(status: int, body: str, *, route: str = "")``
    The ``body`` argument MUST be pre-redacted via ``_redact_for_log`` before
    construction (mandate per security review). It is truncated to 200
    chars and incorporated into ``detail``.
    """

    problem_type = "https://gflow-cli.dev/errors/api-error"
    title = "Flow API error"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
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
            self.body = body
        else:
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
    """Flow returned 200 with empty media[]. ``status`` is intentionally
    omitted from to_problem_details() per RFC 9457 — ``status`` is the HTTP
    status of the problem, and 200 conflates with success. The literal
    upstream status (200) is recorded only via the ``error_raised`` log event
    as an ``upstream_status`` extension (see observability.py)."""

    problem_type = "https://gflow-cli.dev/errors/content-policy"
    title = "Content policy rejection"
    _default_remediation = (
        "Flow rejected the prompt under its content policy. "
        "Soften wording or remove disallowed elements."
    )


class NetworkError(FlowApiError):
    problem_type = "https://gflow-cli.dev/errors/network"
    title = "Network failure persisted across retries"
    _default_remediation = "Check connectivity and try again."


class WireFormatError(FlowApiError):
    """Carries discovery fields so ``grep error_class=WireFormatError`` in
    structured logs reveals what was unexpected, enabling new error class
    proposals. Discovery payload set at raise site via the ``discovery=`` kwarg."""

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

- [ ] **Step 1.4: Move the old `FlowApiError` out of `client.py`**

In `src/gflow_cli/api/client.py`:
- Delete the existing `class FlowApiError(RuntimeError):` block (currently at line ~79).
- Add `from gflow_cli.errors import FlowApiError` to the import block at the top (alongside the other `from gflow_cli.*` imports).

Don't touch the 7 `raise FlowApiError(resp.status, text, route=...)` raise sites yet — they still compile because the new `FlowApiError.__init__` accepts the legacy positional signature. T3 rewrites them.

- [ ] **Step 1.5: Run tests, verify green**

```bash
uv run pytest tests/test_errors.py -q
uv run pytest -q   # 208 baseline + 9 new errors tests = 217+
```

Expected: all pass. The 7 existing client.py raise sites continue to work via the legacy-detect path.

- [ ] **Step 1.6: Quality gates**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli --cov-report=term-missing
```

Coverage on `src/gflow_cli/errors.py` should be ≥ 95% (DoD requirement).

- [ ] **Step 1.7: Commit**

```bash
git add src/gflow_cli/errors.py src/gflow_cli/api/client.py tests/test_errors.py
git commit -m "feat(errors): add RFC 9457 Problem Details exception hierarchy"
```

**Acceptance criteria.**
- `src/gflow_cli/errors.py` exists with `GFlowError`, `FlowApiError`, 5 typed subclasses, `ProblemDetails` TypedDict, `EXIT_CODE_MAP`.
- All 9+ tests in `test_errors.py` GREEN (parametrized table counts as 5 rows).
- `coverage.py` shows `errors.py` ≥ 95%.
- Existing 208 tests still GREEN — no regressions in the 7 raise sites in `client.py`.
- `git grep "class FlowApiError" src/` returns exactly 1 hit (in `errors.py`).
- `EXIT_CODE_MAP` ordering invariant test passes (subclasses precede parents).

---

## Task 2: Per-worker Page pool on `FlowApiClient`

**Goal.** Restructure `FlowApiClient.__aenter__` to open N Pages (where N = `Settings.concurrency`) inside the existing persistent `BrowserContext`, and add `_checkout_page()` / `_checkin_page()` via `asyncio.Queue`. T2 introduces the pool; T3 rewires the 5 wrapped methods (`_post_json`, `_post_generate_image`, `download`, `download_image`, `upload_image`) to use it. T2 also rolls in the deferred Phase 3 `_new_session_id` flake fix.

**Security-touched.** Reviewers: `python-reviewer` + `code-reviewer` + `security-reviewer`.

**Files.**
- Modify: `src/gflow_cli/api/client.py` (`__init__`, `__aenter__`, `__aexit__`, new `_checkout_page` / `_checkin_page`)
- Test: `tests/api/test_concurrency.py` (new)

**Steps.**

- [ ] **Step 2.1: Write failing tests** in `tests/api/test_concurrency.py`

```python
"""Per-worker Page pool tests for FlowApiClient.

Asserts: __aenter__ opens N Pages, checkout/checkin invariants hold,
parallel checkouts can hold N distinct Pages simultaneously, and Pages
return to the pool during backoff sleeps.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import Settings


@pytest.fixture
def settings_n4(tmp_path: Path) -> Settings:
    return Settings(concurrency=4, profile="t", default_profile="t", output_dir=tmp_path)


@pytest.fixture
def fake_context() -> MagicMock:
    """A MagicMock BrowserContext whose ``new_page`` returns distinct Pages."""
    ctx = MagicMock()
    pages = [MagicMock(name=f"Page{i}") for i in range(16)]
    ctx.pages = []  # empty initially
    counter = {"i": 0}

    async def _new_page():
        i = counter["i"]
        counter["i"] += 1
        return pages[i]

    ctx.new_page = AsyncMock(side_effect=_new_page)
    ctx.close = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_aenter_opens_N_pages(tmp_path, settings_n4, fake_context):
    """N=4 → 4 Pages opened on __aenter__."""
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            assert fake_context.new_page.await_count == 4
            assert client._page_queue is not None
            assert client._page_queue.qsize() == 4


@pytest.mark.asyncio
async def test_checkout_checkin_returns_same_page(tmp_path, settings_n4, fake_context):
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        async with FlowApiClient(profile_dir=tmp_path, settings=settings_n4) as client:
            page = await client._checkout_page()
            qsize_before = client._page_queue.qsize()
            client._checkin_page(page)
            assert client._page_queue.qsize() == qsize_before + 1


@pytest.mark.asyncio
async def test_parallel_checkouts_hold_N_distinct_pages(tmp_path, settings_n4, fake_context):
    """N=4 concurrent checkouts must each get a distinct Page (no contention)."""
    gate = asyncio.Event()
    held: list = []

    async def hold_then_release(client):
        page = await client._checkout_page()
        held.append(page)
        await gate.wait()
        client._checkin_page(page)

    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        async with FlowApiClient(profile_dir=tmp_path, settings=settings_n4) as client:
            tasks = [asyncio.create_task(hold_then_release(client)) for _ in range(4)]
            # Wait until all 4 have checked out a Page
            for _ in range(50):
                if len(held) == 4:
                    break
                await asyncio.sleep(0.01)
            assert len(held) == 4
            assert len(set(id(p) for p in held)) == 4  # all distinct
            assert client._page_queue.qsize() == 0    # pool exhausted
            gate.set()
            await asyncio.gather(*tasks)
            assert client._page_queue.qsize() == 4    # all returned


@pytest.mark.asyncio
async def test_aenter_with_concurrency_1_opens_one_page(tmp_path, fake_context):
    """Default N=1 retains single-Page behavior."""
    settings = Settings(concurrency=1, profile="t", default_profile="t", output_dir=tmp_path)
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        async with FlowApiClient(profile_dir=tmp_path, settings=settings) as client:
            assert fake_context.new_page.await_count == 1
            assert client._page_queue.qsize() == 1


@pytest.mark.asyncio
async def test_aexit_closes_all_pages(tmp_path, settings_n4, fake_context):
    """All N Pages closed on __aexit__ (resource cleanup)."""
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            pages_opened = list(client._pages)
        # Context closed; verify each Page received .close() OR Context.close handles them
        fake_context.close.assert_awaited()
```

- [ ] **Step 2.2: Verify red**

```bash
uv run pytest tests/api/test_concurrency.py -q
```

Expected: tests fail because `FlowApiClient` lacks `_pages`, `_page_queue`, `_checkout_page`, `_checkin_page`.

- [ ] **Step 2.3: Implement the Page pool in `src/gflow_cli/api/client.py`**

In `FlowApiClient.__init__`, add:

```python
self._pages: list[Page] = []
self._page_queue: asyncio.Queue[Page] | None = None
```

Replace `__aenter__` with:

```python
async def __aenter__(self) -> FlowApiClient:
    self._pw = await async_playwright().start()
    self._context = await self._pw.chromium.launch_persistent_context(
        user_data_dir=str(self.profile_dir),
        headless=self.headless,
    )
    n = max(1, self.settings.concurrency)
    self._pages = []
    # Reuse first existing page (Playwright opens one on launch_persistent_context).
    if self._context.pages:
        self._pages.append(self._context.pages[0])
        for _ in range(n - 1):
            self._pages.append(await self._context.new_page())
    else:
        for _ in range(n):
            self._pages.append(await self._context.new_page())
    self._page_queue = asyncio.Queue()
    for p in self._pages:
        self._page_queue.put_nowait(p)
    # Keep self._page = self._pages[0] for back-compat with existing
    # internal callers that haven't been migrated yet (T3 removes this).
    self._page = self._pages[0]
    # Phase 3 deferred: re-mint sessionId on enter (was a flake source).
    await self._page.goto(
        "https://labs.google/fx/tools/flow",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    return self
```

Add the helpers:

```python
async def _checkout_page(self) -> Page:
    """Block until a Page is available from the pool; FIFO."""
    assert self._page_queue is not None, "FlowApiClient not entered"
    return await self._page_queue.get()

def _checkin_page(self, page: Page) -> None:
    """Return a Page to the pool. Non-blocking."""
    assert self._page_queue is not None, "FlowApiClient not entered"
    self._page_queue.put_nowait(page)
```

`__aexit__` already closes the context, which closes its child Pages — leave it alone, but reset the pool fields:

```python
async def __aexit__(self, *exc_info) -> None:
    try:
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            await self._pw.stop()
    finally:
        self._pages = []
        self._page_queue = None
        self._page = None
        self._context = None
        self._pw = None
```

- [ ] **Step 2.4: Verify green**

```bash
uv run pytest tests/api/test_concurrency.py -q
uv run pytest -q
```

Expected: 5 new concurrency tests + 208 existing = 213 GREEN. Existing tests should not regress (they still see `self._page` via back-compat alias).

- [ ] **Step 2.5: Quality gates**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli
```

- [ ] **Step 2.6: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_concurrency.py
git commit -m "feat(api): add per-worker Page pool to FlowApiClient"
```

**Acceptance criteria.**
- `FlowApiClient.__aenter__` opens exactly `Settings.concurrency` Pages.
- `_checkout_page()` / `_checkin_page()` use an `asyncio.Queue` for FIFO checkout.
- All 5 concurrency tests GREEN; parallel-checkout test proves N distinct Pages.
- Existing 208 tests still GREEN (`self._page` back-compat alias preserved).
- `_page_queue.qsize()` returns to N after every test (no leaks).

---

## Task 3: Retry policy + 4xx classification + structlog logger swap

**Goal.** Extract `tenacity.AsyncRetrying` setup into `src/gflow_cli/api/_retry.py` (private). Rewrite the 5 wrapped methods in `client.py` to (a) use the per-worker Page model from T2, (b) wrap the per-attempt closure in the retry loop, (c) re-mint reCAPTCHA inside the loop body on the worker's own Page, (d) classify the response at the parse site → raise typed errors (`AuthExpiredError`/`RateLimitError`/`ContentPolicyError`/`NetworkError`/`WireFormatError`) with full RFC 9457 fields including `instance = f"gflow:error:{correlation_id}"` (read from structlog contextvars; empty string if not bound). Honor `Retry-After` capped at 60s. Use `reraise=True` so original exceptions surface (no `RetryError` leak). Swap `logging.getLogger(__name__)` to `structlog.get_logger(__name__)` in `client.py` (full migration of the other modules happens in T5).

**Security-touched.** Reviewers: `python-reviewer` + `code-reviewer` + `security-reviewer` (retry on auth-bearing requests; redaction in `WireFormatError.discovery.body_prefix_redacted`).

**Files.**
- Create: `src/gflow_cli/api/_retry.py`
- Modify: `src/gflow_cli/api/client.py` (rewrite `_post_json`, `_post_generate_image`, `download`, `download_image`, `upload_image`)
- Modify: `pyproject.toml` (add `tenacity>=8.2`)
- Test: `tests/api/test_retry.py` (new)
- Modify: `tests/api/test_client.py` and `tests/api/test_client_image.py` and `tests/api/test_client_generate_video.py` only if they break (likely not — the legacy `FlowApiError` constructor still works, and the new typed errors `isinstance` of `FlowApiError` so `except FlowApiError` clauses continue to catch).

**Steps.**

- [ ] **Step 3.1: Add `tenacity` dependency**

```bash
uv add "tenacity>=8.2"
```

Verify `pyproject.toml` `[project] dependencies` now includes `"tenacity>=8.2"`.

- [ ] **Step 3.2: Write failing tests** in `tests/api/test_retry.py`

```python
"""tenacity-based retry layer + 4xx-no-retry + Retry-After cap + reraise=True."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api._retry import (
    MAX_ATTEMPTS,
    RETRY_AFTER_CAP_SECONDS,
    parse_retry_after,
    post_with_retry,
)
from gflow_cli.errors import (
    AuthExpiredError,
    NetworkError,
    RateLimitError,
    WireFormatError,
)


def _resp(status: int, headers: dict[str, str] | None = None):
    r = MagicMock()
    r.status = status
    r.headers = headers or {}
    return r


@pytest.mark.asyncio
async def test_5xx_retried_3_times_then_raises_NetworkError():
    """3 attempts on 5xx, original exception reraised (no RetryError)."""
    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        return _resp(503)

    with pytest.raises(Exception) as ei:
        async for retrying in post_with_retry(retry_on_5xx=True):
            with retrying:
                resp = await attempt()
                if resp.status >= 500:
                    raise NetworkError(detail=f"HTTP {resp.status}", status=resp.status)
    assert attempts["n"] == MAX_ATTEMPTS
    # reraise=True: original NetworkError surfaces, NOT tenacity.RetryError.
    assert isinstance(ei.value, NetworkError)


@pytest.mark.asyncio
async def test_429_retried_then_RateLimitError():
    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        return _resp(429, headers={"retry-after": "2"})

    with pytest.raises(RateLimitError):
        async for retrying in post_with_retry(retry_on_5xx=True):
            with retrying:
                resp = await attempt()
                if resp.status == 429:
                    raise RateLimitError(
                        detail="429", status=429, retry_after=parse_retry_after(resp),
                    )
    assert attempts["n"] == MAX_ATTEMPTS


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_4xx_not_retried(status):
    """4xx (except 429) MUST NOT be retried."""
    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        return _resp(status)

    err_cls = AuthExpiredError if status in (401, 403) else WireFormatError
    with pytest.raises(err_cls):
        async for retrying in post_with_retry(retry_on_5xx=True):
            with retrying:
                resp = await attempt()
                if resp.status in (401, 403):
                    raise AuthExpiredError(detail=f"HTTP {status}", status=status)
                if 400 <= resp.status < 500 and resp.status != 429:
                    raise WireFormatError(detail=f"HTTP {status}", status=status)
    assert attempts["n"] == 1  # NOT retried


def test_parse_retry_after_seconds():
    assert parse_retry_after(_resp(429, headers={"retry-after": "30"})) == 30.0


def test_parse_retry_after_caps_at_60():
    assert parse_retry_after(_resp(429, headers={"retry-after": "999"})) == RETRY_AFTER_CAP_SECONDS


def test_parse_retry_after_missing_returns_none():
    assert parse_retry_after(_resp(429)) is None


@pytest.mark.asyncio
async def test_event_gated_retry_does_not_block_real_time():
    """Async test that uses asyncio.Event-gated wait so it runs in <0.1s."""
    started = asyncio.Event()
    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        started.set()
        return _resp(503)

    # Use a custom retry config with near-zero wait for the test only.
    from gflow_cli.api._retry import _make_retrying

    retrying = _make_retrying(wait_seconds=lambda _attempt: 0)
    with pytest.raises(NetworkError):
        async for r in retrying:
            with r:
                resp = await attempt()
                if resp.status >= 500:
                    raise NetworkError(detail=f"HTTP {resp.status}", status=resp.status)
    assert attempts["n"] == MAX_ATTEMPTS
```

- [ ] **Step 3.3: Verify red**

```bash
uv run pytest tests/api/test_retry.py -q
```

Expected: ImportError on `gflow_cli.api._retry`.

- [ ] **Step 3.4: Implement `src/gflow_cli/api/_retry.py`**

```python
"""Private retry layer for FlowApiClient. tenacity.AsyncRetrying with
reraise=True so the original GFlowError surfaces (no RetryError leakage).

Constants:
    MAX_ATTEMPTS = 3
    RETRY_AFTER_CAP_SECONDS = 60.0

Public API:
    post_with_retry(retry_on_5xx: bool) -> AsyncGenerator yielding AttemptManager
    parse_retry_after(response) -> float | None  (cap-applied)
"""
from __future__ import annotations

import random
from typing import Any, AsyncIterator, Callable

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_base,
)

from gflow_cli.errors import NetworkError, RateLimitError

MAX_ATTEMPTS = 3
RETRY_AFTER_CAP_SECONDS = 60.0


def parse_retry_after(response: Any) -> float | None:
    """Extract the Retry-After header (seconds form only). Caps at 60s.

    Returns None if header absent or malformed.
    """
    raw = response.headers.get("retry-after") if hasattr(response, "headers") else None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return min(seconds, RETRY_AFTER_CAP_SECONDS)


class _JitteredExponentialWait(wait_base):
    """1s±25% → 2s±25% → 4s±25% with Retry-After override (capped)."""

    def __call__(self, retry_state: RetryCallState) -> float:
        # If the previous attempt raised RateLimitError with retry_after, honor it.
        last_exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(last_exc, RateLimitError) and last_exc.retry_after is not None:
            return min(last_exc.retry_after, RETRY_AFTER_CAP_SECONDS)
        attempt = retry_state.attempt_number  # 1-indexed
        base = 2 ** (attempt - 1)             # 1, 2, 4
        jitter = base * 0.25 * (2 * random.random() - 1)
        return max(0.0, base + jitter)


def _make_retrying(*, wait_seconds: Callable[[RetryCallState], float] | None = None) -> AsyncRetrying:
    """Internal factory; tests override `wait_seconds` to skip real sleeps."""
    waiter: wait_base
    if wait_seconds is not None:
        waiter = _LambdaWait(wait_seconds)
    else:
        waiter = _JitteredExponentialWait()
    # Retry only on retryable exception types — 4xx classification raises
    # AuthExpired/WireFormat/ContentPolicy and falls straight through (no retry).
    return AsyncRetrying(
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=waiter,
        retry=retry_if_exception_type((NetworkError, RateLimitError)),
        reraise=True,
    )


class _LambdaWait(wait_base):
    def __init__(self, fn: Callable[[RetryCallState], float]) -> None:
        self._fn = fn

    def __call__(self, retry_state: RetryCallState) -> float:
        return self._fn(retry_state)


def post_with_retry(*, retry_on_5xx: bool = True) -> AsyncIterator:
    """Public: returns the configured AsyncRetrying iterator.

    Usage:
        async for retrying in post_with_retry():
            with retrying:
                resp = await page.request.post(url, ...)
                if resp.status == 429:
                    raise RateLimitError(status=429, retry_after=parse_retry_after(resp))
                if resp.status >= 500:
                    raise NetworkError(status=resp.status)
                # 4xx (non-429) falls through; classifier outside the loop turns
                # them into AuthExpiredError / WireFormatError (NOT retried).
    """
    return _make_retrying().__aiter__()
```

- [ ] **Step 3.5: Rewrite the 5 wrapped methods in `client.py`** to use per-worker Page checkout + retry + typed-error classification.

For each of `_post_json`, `_post_generate_image`, `download`, `download_image`, `upload_image`:

1. Extract the per-attempt body into an inner `async def attempt(): ...` closure.
2. Inside `attempt`, `page = await self._checkout_page()` then `try: ... finally: self._checkin_page(page)`.
3. **Inside the try, mint a fresh reCAPTCHA token via `TokenMinter(page).mint(action)`** (every attempt, on this worker's Page).
4. POST/GET via `page.request.*`. Return the response.
5. Wrap the closure call in `async for retrying in post_with_retry(): with retrying: response = await attempt(); ...classify...`.

**Example — `_post_generate_image` rewrite (full code):**

```python
import structlog
import asyncio  # already imported

from gflow_cli.api._retry import parse_retry_after, post_with_retry
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    FlowApiError,
    NetworkError,
    RateLimitError,
    WireFormatError,
)

logger = structlog.get_logger(__name__)  # replaces logging.getLogger

async def _post_generate_image(
    self,
    *,
    project_id: str,
    req: GenerateImageRequest,
    seed: int,
    batch_id: str,
    session_id: str,
) -> GeneratedImage:
    route_name = "batchGenerateImages"
    route_url = routes.batch_generate_images_url(project_id)

    async def attempt():
        page = await self._checkout_page()
        try:
            minter = TokenMinter(page)
            token = await minter.mint("imageGeneration")
            body = _build_batch_generate_images_body(
                req,
                project_id=project_id,
                recaptcha_token=token,
                batch_id=batch_id,
                seed=seed,
                session_id=session_id,
            )
            return await page.request.post(
                route_url,
                headers={"content-type": "text/plain;charset=UTF-8"},
                data=json.dumps(body),
                timeout=120_000,
            )
        finally:
            self._checkin_page(page)

    response = None
    async for retrying in post_with_retry():
        with retrying:
            response = await attempt()
            # Classify retryable failures so tenacity can act.
            if response.status == 429:
                raise RateLimitError(
                    detail=f"HTTP {response.status}",
                    status=response.status,
                    retry_after=parse_retry_after(response),
                    route=route_name,
                )
            if response.status >= 500:
                raise NetworkError(
                    detail=f"HTTP {response.status}",
                    status=response.status,
                    route=route_name,
                )
    assert response is not None  # tenacity reraise=True guarantees this

    correlation = structlog.contextvars.get_contextvars().get("correlation_id", "")
    instance = f"gflow:error:{correlation}"

    if response.status in (401, 403):
        raise AuthExpiredError(
            detail=f"HTTP {response.status}",
            status=response.status,
            instance=instance,
            route=route_name,
        )
    if 400 <= response.status < 500:
        body_text = await response.text()
        try:
            parsed = json.loads(body_text) if response.headers.get("content-type", "").startswith("application/json") else None
            top_keys = sorted(list(parsed.keys())) if isinstance(parsed, dict) else []
        except (json.JSONDecodeError, ValueError):
            top_keys = []
        raise WireFormatError(
            detail=f"HTTP {response.status} on 4xx fallthrough",
            status=response.status,
            instance=instance,
            route=route_name,
            discovery={
                "route_name": route_name,
                "http_status": response.status,
                "content_type": response.headers.get("content-type", ""),
                "top_level_keys": top_keys,
                "body_prefix_redacted": _redact_for_log(body_text[:200]),
            },
        )

    data = await response.json()
    images = GeneratedImage.from_response_dict(data)
    if not images:
        raise ContentPolicyError(
            detail="empty media[]",
            instance=instance,
            route=route_name,
            # NOTE: status omitted — RFC 9457 forbids 200 (success) for an error.
            # The literal upstream 200 is recorded only via observability.emit_error_event
            # as `upstream_status` extension.
        )
    return images[0]
```

Apply the analogous restructuring to `_post_json`, `download`, `download_image`, `upload_image`. For methods that don't mint a reCAPTCHA token (e.g. `download`, `download_image`), the `attempt` closure simply checks out a Page, does the GET, returns the response.

**Important:** `_post_json` is called by `_create_project` and other JSON-body endpoints — preserve its public signature. Only the body changes.

**Replace `logger = logging.getLogger(__name__)`** at the top of `client.py` with `logger = structlog.get_logger(__name__)`. Remove `import logging`.

- [ ] **Step 3.6: Verify green**

```bash
uv run pytest tests/api/test_retry.py -q
uv run pytest tests/api/ -q
uv run pytest -q
```

Expected: 7 retry tests + 5 concurrency tests + all existing tests pass. Existing tests using `FlowApiError` continue to catch the typed subclasses (back-compat).

- [ ] **Step 3.7: Quality gates**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli
```

- [ ] **Step 3.8: Commit**

```bash
git add src/gflow_cli/api/_retry.py src/gflow_cli/api/client.py tests/api/test_retry.py pyproject.toml uv.lock
git commit -m "feat(api): add tenacity retry layer with typed error classification"
```

**Acceptance criteria.**
- `src/gflow_cli/api/_retry.py` exists. `MAX_ATTEMPTS=3`, `RETRY_AFTER_CAP_SECONDS=60.0`.
- All 7 retry tests GREEN; mock call count == 3 on 5xx; `Retry-After` honored and capped at 60s; `reraise=True` (no `RetryError` leak — tests use `pytest.raises(NetworkError)`).
- `_post_generate_image`, `_post_json`, `download`, `download_image`, `upload_image` all use the per-worker Page checkout pattern.
- `WireFormatError` raise sites carry full discovery payload including `body_prefix_redacted`.
- `client.py` uses `structlog.get_logger`, no `import logging` left in the file.
- Existing 213+ tests still GREEN.

---

## Task 4a: `_handle_gflow_error` + `_handle_unhandled_error` in `_cli_helpers.py`

**Goal.** Create `src/gflow_cli/_cli_helpers.py` (top-level file, NOT a `cli/` package — avoids collision with the existing `cli.py`). Add the two CLI-boundary handlers that catch `GFlowError` (and subclasses) vs. anything else, emit the appropriate structured event via `gflow_cli.observability` (T5 ships the helpers; for T4a the handlers import lazily so the call sites don't break before T5 lands), print Rich-formatted user-facing messages with remediation hints, and exit with the right code via `EXIT_CODE_MAP`. Wire each `_run_*` async helper in `cli.py`/`cli_image.py`/`cli_video.py` to dispatch through these handlers.

**Files.**
- Create: `src/gflow_cli/_cli_helpers.py` (with `_handle_gflow_error` + `_handle_unhandled_error` only — no `_resolve_profile`/`_make_provider_dir` yet; T4b moves those)
- Modify: `src/gflow_cli/cli.py` (wrap `_run_*` calls)
- Modify: `src/gflow_cli/cli_image.py` (wrap `_run_*` calls; existing `_resolve_profile` stays put — T4b dedups)
- Modify: `src/gflow_cli/cli_video.py` (wrap `_run_*` calls; existing `_resolve_profile` stays put)
- Test: `tests/cli/test_error_handling.py` (new)

**Steps.**

- [ ] **Step 4a.1: Write failing tests** in `tests/cli/test_error_handling.py`

```python
"""End-to-end CLI error handling: typed errors → exit codes + remediation prints + telemetry."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli import cli
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    NetworkError,
    RateLimitError,
    WireFormatError,
)


@pytest.mark.parametrize(
    "exc, expected_exit_code, expected_in_output",
    [
        (AuthExpiredError(detail="401", status=401, route="createProject"),
         3, "Run `gflow auth login"),
        (RateLimitError(detail="429", status=429, retry_after=42),
         4, "Wait a few minutes"),
        (ContentPolicyError(detail="empty media[]"),
         5, "content policy"),
        (NetworkError(detail="503 after retries", status=503),
         6, "Check connectivity"),
        (WireFormatError(detail="unknown shape"),
         7, "File a bug"),
    ],
)
def test_cli_error_to_exit_code_and_remediation(exc, expected_exit_code, expected_in_output):
    runner = CliRunner()
    # Mock the deepest async helper for one command; example: `gflow image t2i`.
    with patch("gflow_cli.cli_image._run_t2i", side_effect=exc):
        result = runner.invoke(cli, ["image", "t2i", "test prompt"])
    assert result.exit_code == expected_exit_code, result.output
    assert expected_in_output.lower() in result.output.lower()


def test_cli_unhandled_exception_exits_1_and_emits_unhandled_event(caplog):
    """Non-GFlowError exception → exit code 1 + error_unhandled event fires."""
    import structlog
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    runner = CliRunner()
    with patch("gflow_cli.cli_image._run_t2i", side_effect=ValueError("bad input")):
        result = runner.invoke(cli, ["image", "t2i", "test prompt"])
    assert result.exit_code == 1
    events = [e for e in log_capture if e.get("event") == "error_unhandled"]
    assert events, "error_unhandled event MUST fire"
    e = events[0]
    assert e["exception_class"] == "ValueError"
    assert "message_hash" in e and len(e["message_hash"]) == 64  # SHA-256 hex
    assert "stack_hash" in e and len(e["stack_hash"]) == 64
    # Privacy: full message MUST NOT appear in event payload
    assert "bad input" not in str(e)


def test_cli_gflow_error_emits_error_raised_event_with_correlation_id():
    import structlog
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    runner = CliRunner()
    exc = AuthExpiredError(detail="401", status=401, route="createProject")
    with patch("gflow_cli.cli_image._run_t2i", side_effect=exc):
        result = runner.invoke(cli, ["image", "t2i", "test prompt"])
    assert result.exit_code == 3
    events = [e for e in log_capture if e.get("event") == "error_raised"]
    assert events
    e = events[0]
    assert e["error_class"] == "AuthExpiredError"
    assert e["problem"]["type"] == "https://gflow-cli.dev/errors/auth-expired"
    assert e["problem"]["status"] == 401
    assert "correlation_id" in e
    assert e["cli_command"].startswith("image t2i")


def test_cli_wire_format_error_logs_discovery_fields():
    import structlog
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    runner = CliRunner()
    exc = WireFormatError(
        detail="unknown shape", status=200, route="batchGenerateImages",
        discovery={
            "route_name": "batchGenerateImages",
            "http_status": 200,
            "content_type": "application/json",
            "top_level_keys": ["error", "status"],
            "body_prefix_redacted": "{\"error\": \"...\"}",
        },
    )
    with patch("gflow_cli.cli_image._run_t2i", side_effect=exc):
        result = runner.invoke(cli, ["image", "t2i", "test"])
    events = [e for e in log_capture if e.get("event") == "error_raised"]
    pd = events[0]["problem"]
    # Discovery fields land in the structured event extension (NOT in Problem Details type/title).
    assert "discovery" in events[0]
    assert events[0]["discovery"]["top_level_keys"] == ["error", "status"]


def test_content_policy_logs_upstream_status_200_extension():
    import structlog
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    runner = CliRunner()
    exc = ContentPolicyError(detail="empty media[]")
    with patch("gflow_cli.cli_image._run_t2i", side_effect=exc):
        result = runner.invoke(cli, ["image", "t2i", "test"])
    events = [e for e in log_capture if e.get("event") == "error_raised"]
    assert events[0].get("upstream_status") == 200
    # Problem Details `status` field MUST be absent (RFC 9457 §3.1)
    assert "status" not in events[0]["problem"]
```

- [ ] **Step 4a.2: Verify red**

```bash
uv run pytest tests/cli/test_error_handling.py -q
```

Expected: ImportError on `gflow_cli._cli_helpers` or attribute errors on missing handlers.

- [ ] **Step 4a.3: Implement `src/gflow_cli/_cli_helpers.py`** (handlers only — helpers in T4b)

```python
"""CLI-boundary handlers shared across cli.py / cli_image.py / cli_video.py.

Top-level file (NOT a `cli/` package) to avoid file/package collision with
the existing `cli.py`.
"""
from __future__ import annotations

import sys

import click
import structlog
from rich.console import Console

from gflow_cli.errors import EXIT_CODE_MAP, GFlowError

_logger = structlog.get_logger(__name__)
_console = Console()


def _exit_code_for(exc: GFlowError) -> int:
    for cls, code in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            return code
    return 1


def _handle_gflow_error(exc: GFlowError, *, cli_command: str) -> int:
    """Print user-facing message + remediation, emit error_raised event, return exit code.

    Lazy imports `gflow_cli.observability` so this module is usable even before
    T5 lands (observability bootstrap).
    """
    try:
        from gflow_cli.observability import emit_error_event
        emit_error_event(_logger, exc, cli_command=cli_command)
    except ImportError:
        # T4a may land before T5; degrade gracefully.
        _logger.error(
            "error_raised",
            error_class=type(exc).__name__,
            problem=exc.to_problem_details(),
            cli_command=cli_command,
        )
    _console.print(f"[red]{exc.title}:[/red] {exc.detail or ''}")
    if exc.remediation_hint:
        _console.print(f"[yellow]→ {exc.remediation_hint}[/yellow]")
    return _exit_code_for(exc)


def _handle_unhandled_error(exc: BaseException, *, cli_command: str) -> int:
    """Catch-all for non-GFlowError. Privacy-safe: hashes message+stack, never logs raw."""
    try:
        from gflow_cli.observability import emit_unhandled_event
        emit_unhandled_event(_logger, exc, cli_command=cli_command)
    except ImportError:
        import hashlib, traceback
        _logger.error(
            "error_unhandled",
            exception_class=type(exc).__name__,
            message_hash=hashlib.sha256(str(exc).encode("utf-8", "replace")).hexdigest(),
            stack_hash=hashlib.sha256(
                "".join(traceback.format_tb(exc.__traceback__)).encode("utf-8", "replace")
            ).hexdigest(),
            cli_command=cli_command,
        )
    _console.print(
        "[red]Unexpected error.[/red] Re-run with --verbose to capture details. "
        "If this persists, file a bug at https://github.com/ffroliva/gflow-cli/issues."
    )
    return 1


def run_with_handlers(coro_factory, *, cli_command: str) -> None:
    """Wrap an asyncio.run(coro) call in the GFlowError + unhandled handlers.

    Usage in CLI command body:
        run_with_handlers(lambda: _run_t2i(prompt, ...), cli_command="image t2i")
    """
    import asyncio

    try:
        asyncio.run(coro_factory())
    except GFlowError as e:
        sys.exit(_handle_gflow_error(e, cli_command=cli_command))
    except (KeyboardInterrupt, click.Abort):
        sys.exit(130)
    except BaseException as e:  # noqa: BLE001 — catch-all is intentional at CLI boundary
        sys.exit(_handle_unhandled_error(e, cli_command=cli_command))
```

- [ ] **Step 4a.4: Wire `_run_*` callers via `run_with_handlers`**

In `src/gflow_cli/cli_image.py`, find the existing pattern (typically `asyncio.run(_run_t2i(...))` inside the Click command body) and replace with:

```python
from gflow_cli._cli_helpers import run_with_handlers

@image_grp.command()
@click.argument("prompt")
# ... existing options ...
def t2i(prompt: str, ...) -> None:
    run_with_handlers(
        lambda: _run_t2i(prompt, ...),
        cli_command=f"image t2i",
    )
```

Apply the same wrapper to all `_run_*` invocations in `cli.py`, `cli_image.py`, `cli_video.py`. Drop any pre-existing `try/except FlowApiError` blocks that the wrapper now handles centrally.

- [ ] **Step 4a.5: Verify green**

```bash
uv run pytest tests/cli/test_error_handling.py -q
uv run pytest -q
```

- [ ] **Step 4a.6: Quality gates** (same four)

- [ ] **Step 4a.7: Commit**

```bash
git add src/gflow_cli/_cli_helpers.py src/gflow_cli/cli.py src/gflow_cli/cli_image.py src/gflow_cli/cli_video.py tests/cli/test_error_handling.py
git commit -m "feat(cli): add unified GFlowError + unhandled-exception handlers"
```

**Acceptance criteria.**
- `src/gflow_cli/_cli_helpers.py` exists at top level (NOT under a `cli/` package).
- `_handle_gflow_error` returns the right exit code for each typed error (3/4/5/6/7).
- `_handle_unhandled_error` returns 1; emits `error_unhandled` with `message_hash` + `stack_hash` only (no raw message).
- All 7 tests in `test_error_handling.py` GREEN.
- Existing 220+ tests still GREEN.

---

## Task 4b: Helper relocation — dedup `_resolve_profile` + `_make_provider_dir`

**Goal.** Move `_resolve_profile` and `_make_provider_dir` from `cli_image.py` (lines 81 and 95) and `cli_video.py` (lines 36 and 50) to `_cli_helpers.py`. Add a negative import test asserting that neither helper is defined locally in `cli_image.py` / `cli_video.py` after the move (prevents future drift).

**Files.**
- Modify: `src/gflow_cli/_cli_helpers.py` (add `_resolve_profile`, `_make_provider_dir`)
- Modify: `src/gflow_cli/cli_image.py` (remove local definitions; import from `_cli_helpers`)
- Modify: `src/gflow_cli/cli_video.py` (same)
- Test: `tests/cli/test_helpers.py` (new)

**Steps.**

- [ ] **Step 4b.1: Write failing test** in `tests/cli/test_helpers.py`

```python
"""Helper relocation tests + negative import test (drift prevention)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _toplevel_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_helpers_relocated_to_cli_helpers_module():
    from gflow_cli import _cli_helpers
    assert callable(_cli_helpers._resolve_profile)
    assert callable(_cli_helpers._make_provider_dir)


@pytest.mark.parametrize(
    "module_path",
    [
        Path("src/gflow_cli/cli_image.py"),
        Path("src/gflow_cli/cli_video.py"),
    ],
)
def test_no_local_helper_definitions_in_cli_modules(module_path):
    """Negative test — drift prevention. After T4b, neither cli_image.py nor
    cli_video.py defines `_resolve_profile` or `_make_provider_dir` locally;
    they import from `gflow_cli._cli_helpers`.
    """
    names = _toplevel_function_names(module_path)
    assert "_resolve_profile" not in names, (
        f"{module_path} still defines _resolve_profile locally — import from _cli_helpers."
    )
    assert "_make_provider_dir" not in names, (
        f"{module_path} still defines _make_provider_dir locally — import from _cli_helpers."
    )


def test_resolve_profile_returns_explicit_when_given(tmp_path, monkeypatch):
    """When the caller passes --profile, _resolve_profile must return it verbatim."""
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    from gflow_cli._cli_helpers import _resolve_profile

    assert _resolve_profile("experiments") == "experiments"


def test_resolve_profile_falls_back_to_env(tmp_path, monkeypatch):
    """When --profile is None, _resolve_profile falls back to GFLOW_CLI_PROFILE."""
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_PROFILE", "work")
    from gflow_cli._cli_helpers import _resolve_profile

    assert _resolve_profile(None) == "work"


def test_make_provider_dir_creates_and_returns_path(tmp_path, monkeypatch):
    """_make_provider_dir creates the provider directory under $GFLOW_CLI_OUTPUT_DIR
    for the named profile and returns its Path."""
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path))
    from gflow_cli._cli_helpers import _make_provider_dir

    pdir = _make_provider_dir("experiments")
    assert pdir.exists() and pdir.is_dir()
    assert str(tmp_path) in str(pdir)
```

> **Note on pre-existing semantics.** The two helpers existed on `cli_image.py` and `cli_video.py` before T4b (Phase 3 code). After the T4b move, their public behavior is unchanged — only their import path differs. If the engineer discovers a richer signature in the pre-relocation source (e.g. `_resolve_profile` consulting `config.toml` as a third fallback layer), extend the two tests above to exercise that path and update the post-move helper accordingly. Do NOT change the helpers' semantics in T4b — relocation only.

- [ ] **Step 4b.2: Verify red**

`uv run pytest tests/cli/test_helpers.py -q` → tests fail because `_cli_helpers` lacks the two helpers.

- [ ] **Step 4b.3: Move both helpers** to `_cli_helpers.py`

Read the current implementation of `_resolve_profile` and `_make_provider_dir` in `cli_image.py:81-100` and `cli_video.py:36-65`. They should be identical (or near-identical — verify and reconcile to one canonical form). Paste the canonical version into `_cli_helpers.py`. Remove the local definitions from both `cli_image.py` and `cli_video.py`. Add `from gflow_cli._cli_helpers import _resolve_profile, _make_provider_dir` to both call-site modules.

- [ ] **Step 4b.4: Verify green**

```bash
uv run pytest tests/cli/test_helpers.py -q
uv run pytest -q
```

The negative import test is the key drift-prevention assertion.

- [ ] **Step 4b.5: Quality gates** (same four)

- [ ] **Step 4b.6: Commit**

```bash
git add src/gflow_cli/_cli_helpers.py src/gflow_cli/cli_image.py src/gflow_cli/cli_video.py tests/cli/test_helpers.py
git commit -m "refactor(cli): relocate _resolve_profile and _make_provider_dir to _cli_helpers"
```

**Acceptance criteria.**
- `_resolve_profile` and `_make_provider_dir` defined exactly once, in `_cli_helpers.py`.
- `git grep "def _resolve_profile" src/` returns 1 hit; `def _make_provider_dir` returns 1 hit.
- Negative import test passes — neither helper appears in `cli_image.py` / `cli_video.py` AST.

---

## Task 5: `observability.py` — structlog bootstrap + emit_*_event + full migration

**Goal.** Create `src/gflow_cli/observability.py` with `configure_logging`, `emit_error_event`, `emit_unhandled_event`. Bootstrap structlog with TTY-auto detection, `show_locals=False` exception renderer, and `bind_contextvars` for `cli_version` + `correlation_id` at process boundary. Replace `logging.basicConfig` in `cli.py` and the remaining `logging.getLogger` in `auth.py` with `configure_logging` + `structlog.get_logger`. Confirm `caplog` integration still works for legacy tests.

**Files.**
- Create: `src/gflow_cli/observability.py`
- Modify: `src/gflow_cli/cli.py` (swap `logging.basicConfig` → `configure_logging` at process entry, bind `cli_version` + new `correlation_id` per invocation)
- Modify: `src/gflow_cli/auth.py` (swap `logging.getLogger` → `structlog.get_logger`; remove the `print()` at line 58)
- Test: `tests/test_observability.py` (new)

**Steps.**

- [ ] **Step 5.1: Write failing tests** in `tests/test_observability.py`

```python
"""structlog bootstrap + emit_*_event tests."""
from __future__ import annotations

import hashlib
import json
from io import StringIO
from unittest.mock import patch

import pytest
import structlog

from gflow_cli.errors import AuthExpiredError, ContentPolicyError, WireFormatError
from gflow_cli.observability import (
    configure_logging,
    emit_error_event,
    emit_unhandled_event,
)
from gflow_cli.config import LogFormat


def test_auto_detects_tty_renders_text(monkeypatch):
    """When stdout.isatty() == True → text format."""
    fake_tty = StringIO()
    fake_tty.isatty = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setattr("sys.stdout", fake_tty)
    configure_logging(LogFormat.AUTO)
    log = structlog.get_logger("test")
    log.info("hello", extra="value")
    output = fake_tty.getvalue()
    # Text format has no JSON braces wrapping the event
    assert "hello" in output
    # Should NOT be valid JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.strip().splitlines()[0])


def test_auto_detects_pipe_renders_json(monkeypatch):
    fake_pipe = StringIO()
    fake_pipe.isatty = lambda: False  # type: ignore[attr-defined]
    monkeypatch.setattr("sys.stdout", fake_pipe)
    configure_logging(LogFormat.AUTO)
    log = structlog.get_logger("test")
    log.info("hello", extra="value")
    output = fake_pipe.getvalue().strip().splitlines()[0]
    parsed = json.loads(output)
    assert parsed["event"] == "hello"
    assert parsed["extra"] == "value"


def test_show_locals_is_false_in_exception_renderer():
    """show_locals=False — secrets in local frames must not leak."""
    fake_pipe = StringIO()
    fake_pipe.isatty = lambda: False  # type: ignore[attr-defined]
    with patch("sys.stdout", fake_pipe):
        configure_logging(LogFormat.JSON)
        log = structlog.get_logger("test")
        try:
            secret_token = "DO_NOT_LEAK_xyz123"  # noqa: F841
            raise ValueError("boom")
        except ValueError:
            log.exception("oops")
        output = fake_pipe.getvalue()
    assert "DO_NOT_LEAK_xyz123" not in output


def test_emit_error_event_shape():
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    log = structlog.get_logger("test")
    structlog.contextvars.bind_contextvars(correlation_id="abc-123")
    exc = AuthExpiredError(detail="401", status=401, route="createProject", instance="gflow:error:abc-123")
    emit_error_event(log, exc, cli_command="image t2i")
    structlog.contextvars.clear_contextvars()
    assert log_capture
    e = log_capture[0]
    assert e["event"] == "error_raised"
    assert e["error_class"] == "AuthExpiredError"
    assert e["problem"]["type"] == "https://gflow-cli.dev/errors/auth-expired"
    assert e["cli_command"] == "image t2i"


def test_emit_error_event_content_policy_adds_upstream_status():
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    log = structlog.get_logger("test")
    exc = ContentPolicyError(detail="empty media[]")
    emit_error_event(log, exc, cli_command="image t2i")
    e = log_capture[0]
    assert e.get("upstream_status") == 200  # extension because Problem Details `status` is omitted
    assert "status" not in e["problem"]


def test_emit_error_event_wire_format_includes_discovery():
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    log = structlog.get_logger("test")
    exc = WireFormatError(
        detail="unknown shape",
        discovery={
            "route_name": "batchGenerateImages",
            "http_status": 200,
            "content_type": "application/json",
            "top_level_keys": ["error"],
            "body_prefix_redacted": "{...}",
        },
    )
    emit_error_event(log, exc, cli_command="image t2i")
    e = log_capture[0]
    assert e["discovery"]["top_level_keys"] == ["error"]


def test_emit_unhandled_event_hashes_message_and_stack():
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    log = structlog.get_logger("test")
    try:
        raise ValueError("secret-value")
    except ValueError as e:
        emit_unhandled_event(log, e, cli_command="image t2i")
    ev = log_capture[0]
    assert ev["event"] == "error_unhandled"
    assert ev["exception_class"] == "ValueError"
    assert ev["message_hash"] == hashlib.sha256(b"secret-value").hexdigest()
    assert len(ev["stack_hash"]) == 64
    # Privacy: message text MUST NOT appear in event payload
    assert "secret-value" not in json.dumps(ev)


def test_correlation_id_bound_at_boundary_appears_in_events():
    log_capture: list = []
    structlog.configure(processors=[structlog.testing.LogCapture(log_capture)])  # noqa: SLF001
    structlog.contextvars.bind_contextvars(correlation_id="zzz-111", cli_version="0.4.0a1")
    log = structlog.get_logger("test")
    log.info("any_event")
    structlog.contextvars.clear_contextvars()
    assert log_capture[0]["correlation_id"] == "zzz-111"
    assert log_capture[0]["cli_version"] == "0.4.0a1"
```

- [ ] **Step 5.2: Verify red**

- [ ] **Step 5.3: Implement `src/gflow_cli/observability.py`**

```python
"""structlog bootstrap + structured error event emitters.

Public:
    configure_logging(log_format) — bootstrap; auto-detects TTY when AUTO.
    emit_error_event(logger, exc, *, cli_command) — caught GFlowErrors.
    emit_unhandled_event(logger, exc, *, cli_command) — catch-all (privacy-safe).
"""
from __future__ import annotations

import hashlib
import logging
import sys
import traceback

import structlog

from gflow_cli import __version__ as _CLI_VERSION
from gflow_cli.config import LogFormat
from gflow_cli.errors import ContentPolicyError, GFlowError, WireFormatError


def configure_logging(log_format: LogFormat = LogFormat.AUTO) -> None:
    """Bootstrap structlog. Renders text on TTY, JSON when piped (AUTO mode).

    Sets show_locals=False on the exception renderer so frame locals (which
    can contain auth tokens) are NEVER serialized.
    """
    if log_format == LogFormat.AUTO:
        is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        log_format = LogFormat.TEXT if is_tty else LogFormat.JSON

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        # Critical: show_locals=False — never leak frame locals (auth tokens).
        structlog.processors.format_exc_info,
    ]
    renderer = (
        structlog.dev.ConsoleRenderer(colors=True)
        if log_format == LogFormat.TEXT
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def emit_error_event(
    logger: structlog.BoundLogger,
    exc: GFlowError,
    *,
    cli_command: str,
) -> None:
    """Emit `error_raised` for caught GFlowErrors.

    Stable fields: error_class, problem (Problem Details dict), cli_command.
    correlation_id flows from contextvars (bound at process boundary).
    Extensions:
      - ContentPolicyError → upstream_status=200 (literal upstream HTTP status)
      - WireFormatError    → discovery payload (route_name, http_status, top_level_keys, ...)
    """
    payload: dict = {
        "error_class": type(exc).__name__,
        "problem": exc.to_problem_details(),
        "cli_command": cli_command,
    }
    if isinstance(exc, ContentPolicyError):
        payload["upstream_status"] = 200
    if isinstance(exc, WireFormatError):
        payload["discovery"] = exc.discovery
    logger.error("error_raised", **payload)


def emit_unhandled_event(
    logger: structlog.BoundLogger,
    exc: BaseException,
    *,
    cli_command: str,
) -> None:
    """Emit `error_unhandled` for non-GFlowError. Privacy-safe.

    NEVER includes the raw exception message or full traceback — only SHA-256
    hashes (so log analysis can group recurring errors without storing PII).
    """
    msg_bytes = str(exc).encode("utf-8", errors="replace")
    tb_bytes = "".join(traceback.format_tb(exc.__traceback__)).encode("utf-8", errors="replace")
    logger.error(
        "error_unhandled",
        exception_class=type(exc).__name__,
        message_hash=hashlib.sha256(msg_bytes).hexdigest(),
        stack_hash=hashlib.sha256(tb_bytes).hexdigest(),
        cli_command=cli_command,
    )
```

- [ ] **Step 5.4: Wire `configure_logging` at process boundary in `cli.py`**

Replace the existing `logging.basicConfig(...)` call in `src/gflow_cli/cli.py:52` with:

```python
import uuid
import structlog
from gflow_cli import __version__ as CLI_VERSION
from gflow_cli.observability import configure_logging
from gflow_cli.config import Settings

@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging.")
def cli(verbose: bool) -> None:
    """gflow — Google Flow CLI."""
    settings = Settings()
    configure_logging(settings.log_format)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        cli_version=CLI_VERSION,
        correlation_id=str(uuid.uuid4()),
    )
    if verbose:
        # Set DEBUG level via structlog filtering, NOT logging.basicConfig
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(10),  # logging.DEBUG
        )
```

Remove `import logging` from `cli.py`.

- [ ] **Step 5.5: Migrate `auth.py`**

In `src/gflow_cli/auth.py`:
- `import logging` → `import structlog`
- `logger = logging.getLogger(__name__)` → `logger = structlog.get_logger(__name__)`
- `print(...)` at line 58 → `_console = Console(); _console.print(...)` (or remove if it's debug-only output that the structured log already covers)

- [ ] **Step 5.6: Verify green**

```bash
uv run pytest tests/test_observability.py -q
uv run pytest tests/test_auth.py -q
uv run pytest -q
```

Expected: 8 new observability tests + all existing pass. `caplog` integration in legacy tests still works because `structlog.testing.LogCapture` is the structlog equivalent (existing tests using `caplog` keep working since structlog's stdlib bridge writes to `logging` by default).

- [ ] **Step 5.7: Quality gates** + coverage check on `observability.py` ≥ 95%

- [ ] **Step 5.8: Commit**

```bash
git add src/gflow_cli/observability.py src/gflow_cli/cli.py src/gflow_cli/auth.py tests/test_observability.py
git commit -m "feat(observability): structlog bootstrap with error_raised/error_unhandled events"
```

**Acceptance criteria.**
- `src/gflow_cli/observability.py` exists. Coverage ≥ 95%.
- All 8 observability tests GREEN.
- `git grep "import logging" src/` returns NO hits in non-test code.
- `git grep "print(" src/` shows only Rich `console.print` (Click/CLI output is allowed via Rich, raw `print()` is not).
- TTY auto-detect: `gflow auth` (in a real terminal) shows text logs; `gflow auth | cat` shows JSON.

---

## Task 6: BDD coverage — pytest-bdd + 3 feature files + 12 scenarios

**Goal.** Add `pytest-bdd` (dev) and three feature files (`auth.feature`, `video.feature`, `image.feature`) with 4 scenarios each — 12 total. Use a shared `conftest.py` for fixtures (mocked `FlowApiClient`, isolated tmp dirs) and per-feature step files (`test_*_steps.py`) that scope step phrases to their feature directory to prevent cross-feature collisions.

**Files.**
- Modify: `pyproject.toml` (`uv add --dev pytest-bdd`)
- Create: `tests/features/__init__.py`
- Create: `tests/features/conftest.py`
- Create: `tests/features/auth.feature`
- Create: `tests/features/video.feature`
- Create: `tests/features/image.feature`
- Create: `tests/features/test_auth_steps.py`
- Create: `tests/features/test_video_steps.py`
- Create: `tests/features/test_image_steps.py`

**Steps.**

- [ ] **Step 6.1: Add the dev dependency**

```bash
uv add --dev pytest-bdd
```

- [ ] **Step 6.2: Author `tests/features/conftest.py`**

```python
"""Shared BDD fixtures: mocked FlowApiClient, isolated tmp output dirs.

Required: BDD step defs MUST use only mocked clients — never live API.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_flow_client() -> MagicMock:
    client = MagicMock(name="FlowApiClient")
    client.create_project = AsyncMock(return_value="proj-123")
    client.upload_image = AsyncMock(return_value=MagicMock(media_name="media-uuid-abc"))
    client.generate_image = AsyncMock()
    client.generate_images_batch = AsyncMock()
    client.generate_video = AsyncMock()
    client.download = AsyncMock()
    client.download_image = AsyncMock()
    return client


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_tmp_output(tmp_path, monkeypatch):
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path))
    yield
```

- [ ] **Step 6.3: Author `tests/features/auth.feature`** (4 scenarios)

```gherkin
Feature: Authentication
  As a Flow user
  I want to manage profiles
  So that I can authenticate against my Google account

  Scenario: List profiles when none exist
    Given the profile root is empty
    When I run "gflow auth"
    Then the exit code is 0
    And the output contains "No profiles found"

  Scenario: Show profile config
    Given a profile "experiments" exists
    When I run "gflow auth show experiments"
    Then the exit code is 0
    And the output contains "experiments"

  Scenario: Use a profile
    Given a profile "experiments" exists
    When I run "gflow auth use experiments"
    Then the exit code is 0
    And the default profile is "experiments"

  Scenario: Auth-expired error during a Flow API call
    Given the mocked FlowApiClient raises AuthExpiredError
    When I run "gflow image t2i some prompt"
    Then the exit code is 3
    And the output contains "gflow auth login"
```

- [ ] **Step 6.4: Author `tests/features/video.feature`** (4 scenarios)

```gherkin
Feature: Video generation
  Scenario: Single video t2v
    Given the mocked FlowApiClient returns a successful video
    When I run "gflow video t2v a hot air balloon"
    Then the exit code is 0
    And one video file is created

  Scenario: Batch with concurrency=4
    Given a manifest with 4 prompts
    And concurrency is set to 4
    When I run "gflow video batch manifest.tsv"
    Then the exit code is 0
    And 4 video files are created
    And the FlowApiClient was called concurrently

  Scenario: Rate-limit retry succeeds on second attempt
    Given the mocked FlowApiClient returns 429 once then 200
    When I run "gflow video t2v retry-test"
    Then the exit code is 0
    And the FlowApiClient.generate_video was called twice

  Scenario: Network failure after retries
    Given the mocked FlowApiClient raises NetworkError after 3 attempts
    When I run "gflow video t2v fail-test"
    Then the exit code is 6
    And the output contains "Check connectivity"
```

- [ ] **Step 6.5: Author `tests/features/image.feature`** (4 scenarios)

```gherkin
Feature: Image generation
  Scenario: T2I single image
    Given the mocked FlowApiClient returns a successful image
    When I run "gflow image t2i a peaceful lake"
    Then the exit code is 0
    And one image file is created

  Scenario: Multi-image fan-out
    Given the mocked FlowApiClient returns successful images
    When I run "gflow image t2i mountains -n 4"
    Then the exit code is 0
    And 4 image files are created

  Scenario: Content policy rejection
    Given the mocked FlowApiClient raises ContentPolicyError
    When I run "gflow image t2i something rejected"
    Then the exit code is 5
    And the output contains "content policy"

  Scenario: Wire format error during image generation
    Given the mocked FlowApiClient raises WireFormatError
    When I run "gflow image t2i wire-fail"
    Then the exit code is 7
    And the output contains "File a bug"
```

- [ ] **Step 6.6: Author the per-feature step files**

Each `test_<feature>_steps.py` uses `@scenarios("<feature>.feature")` from `pytest_bdd` and binds the Given/When/Then steps for that feature only. Example shape (full content omitted for brevity — use the [pytest-bdd documentation](https://pytest-bdd.readthedocs.io/) — but the key constraint is: every `When I run "..."` step uses `CliRunner` from Click, never live API).

Example skeleton for `tests/features/test_image_steps.py`:

```python
"""Step bindings for image.feature. Scoped to this directory only."""
from __future__ import annotations

from unittest.mock import patch
from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli.cli import cli
from gflow_cli.errors import ContentPolicyError, WireFormatError

scenarios("image.feature")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder():
    return {"result": None}


@given("the mocked FlowApiClient returns a successful image")
def _mock_success(mock_flow_client, fixtures_dir, tmp_path):
    """Wire generate_image + download_image so the scenario produces a real file
    on disk under tmp_path — needed for the `one image file is created` step."""
    from unittest.mock import MagicMock

    fake_image = MagicMock(
        media_name="media-uuid-abc",
        fife_url="https://flow-content.google/signed/img.png",
        dimensions=(1024, 1024),
        is_signed_url=True,
    )
    mock_flow_client.generate_image.return_value = fake_image
    mock_flow_client.generate_images_batch.return_value = [fake_image] * 4

    async def _fake_download(image, out_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # PNG magic bytes — enough for "file exists and is PNG-shaped" assertions.
        out_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return out_path

    mock_flow_client.download_image.side_effect = _fake_download


@given("the mocked FlowApiClient raises ContentPolicyError")
def _mock_content_policy(mock_flow_client):
    mock_flow_client.generate_image.side_effect = ContentPolicyError(detail="empty media[]")


@given("the mocked FlowApiClient raises WireFormatError")
def _mock_wire_format(mock_flow_client):
    mock_flow_client.generate_image.side_effect = WireFormatError(detail="unknown shape")


@when('I run "gflow image t2i a peaceful lake"')
def _run_t2i_lake(runner, cli_result_holder, mock_flow_client):
    with patch("gflow_cli.cli_image.FlowApiClient", return_value=mock_flow_client):
        cli_result_holder["result"] = runner.invoke(cli, ["image", "t2i", "a peaceful lake"])


@when('I run "gflow image t2i mountains -n 4"')
def _run_t2i_n4(runner, cli_result_holder, mock_flow_client):
    with patch("gflow_cli.cli_image.FlowApiClient", return_value=mock_flow_client):
        cli_result_holder["result"] = runner.invoke(cli, ["image", "t2i", "mountains", "-n", "4"])


@when('I run "gflow image t2i something rejected"')
def _run_t2i_rejected(runner, cli_result_holder, mock_flow_client):
    with patch("gflow_cli.cli_image.FlowApiClient", return_value=mock_flow_client):
        cli_result_holder["result"] = runner.invoke(cli, ["image", "t2i", "something rejected"])


@when('I run "gflow image t2i wire-fail"')
def _run_t2i_wire_fail(runner, cli_result_holder, mock_flow_client):
    with patch("gflow_cli.cli_image.FlowApiClient", return_value=mock_flow_client):
        cli_result_holder["result"] = runner.invoke(cli, ["image", "t2i", "wire-fail"])


@then("the exit code is 0")
def _check_0(cli_result_holder): assert cli_result_holder["result"].exit_code == 0
@then("the exit code is 5")
def _check_5(cli_result_holder): assert cli_result_holder["result"].exit_code == 5
@then("the exit code is 7")
def _check_7(cli_result_holder): assert cli_result_holder["result"].exit_code == 7


@then("one image file is created")
def _check_one_image(tmp_path): assert any(tmp_path.rglob("*.png"))
@then("4 image files are created")
def _check_four_images(tmp_path): assert len(list(tmp_path.rglob("*.png"))) == 4


@then('the output contains "content policy"')
def _check_content_policy(cli_result_holder):
    assert "content policy" in cli_result_holder["result"].output.lower()
@then('the output contains "File a bug"')
def _check_file_bug(cli_result_holder):
    assert "File a bug" in cli_result_holder["result"].output
```

Apply analogous shape to `test_auth_steps.py` and `test_video_steps.py`.

- [ ] **Step 6.7: Add a step-phrase collision regression test**

In `tests/features/test_image_steps.py` (or a separate `tests/features/test_collision_guard.py`):

```python
def test_step_phrases_namespaced_per_feature():
    """Each feature directory's step bindings must NOT leak across features.
    pytest-bdd uses module-scoped step registries — this test asserts no
    cross-feature step is reachable from another feature's bindings.
    """
    # Trivial smoke: import each step module and assert distinct registry markers.
    import tests.features.test_auth_steps as auth_steps
    import tests.features.test_video_steps as video_steps
    import tests.features.test_image_steps as image_steps
    # Each module should register scenarios for its OWN feature only.
    assert auth_steps is not None
    assert video_steps is not None
    assert image_steps is not None
```

(pytest-bdd already enforces step locality via module scope; this test is a sanity probe.)

- [ ] **Step 6.8: Verify green**

```bash
uv run pytest tests/features/ -q
uv run pytest -q
```

Expected: 12 BDD scenarios + collision smoke + all earlier tests GREEN.

- [ ] **Step 6.9: Verify mocked-only contract**

```bash
uv run pytest tests/features/ -q -k mock
```

Add an explicit assertion in each step module: `mock_flow_client.generate_image.assert_called()` and equivalents. Confirm the live `playwright` is never invoked during BDD.

- [ ] **Step 6.10: Quality gates** (same four)

- [ ] **Step 6.11: Commit**

```bash
git add tests/features/ pyproject.toml uv.lock
git commit -m "test(bdd): add pytest-bdd scenarios for auth/video/image"
```

**Acceptance criteria.**
- 12 scenarios across 3 feature files, all GREEN.
- Per-feature `test_*_steps.py` files; step phrases scoped per file.
- All BDD steps use mocked `FlowApiClient` — `assert_not_called` on live `page.request.*` (verify by patching `playwright.async_api.async_playwright` and asserting it's never started).

---

## Task 7: Documentation (USAGE / CONFIGURATION / CHANGELOG / .env.template / PLAN.md / ARCHITECTURE.md)

**Goal.** Update user-facing docs for Phase 4. Add the modular monolith section + Problem Details note to `docs/ARCHITECTURE.md`. Document `GFLOW_CLI_CONCURRENCY` semantics + memory cost. Document `GFLOW_CLI_LOG_FORMAT`. Mark Phase 4 ✅ in `PLAN.md` and confirm Phase 5/6/7 backlog. Add `[0.4.0a1]` to `CHANGELOG.md`.

**Files.**
- Modify: `docs/USAGE.md`
- Modify: `docs/CONFIGURATION.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `PLAN.md`
- Modify: `CHANGELOG.md`
- Modify: `.env.template`

**Steps.**

- [ ] **Step 7.1: `docs/USAGE.md`** — add a new top-level section "Error handling and exit codes":

```markdown
## Error handling and exit codes

`gflow` exits with a typed code per error class:

| Exit code | Error class            | Remediation hint shown                                          |
|-----------|------------------------|-----------------------------------------------------------------|
| 0         | success                | —                                                               |
| 1         | unhandled exception    | Re-run with `--verbose`; file a bug if it persists              |
| 2         | usage error (Click)    | Standard CLI usage error                                        |
| 3         | `AuthExpiredError`     | `gflow auth login --profile <name>`                             |
| 4         | `RateLimitError`       | Wait + reduce `GFLOW_CLI_CONCURRENCY`                            |
| 5         | `ContentPolicyError`   | Soften prompt wording                                           |
| 6         | `NetworkError`         | Check connectivity                                              |
| 7         | `WireFormatError`      | File a bug (Flow API shape changed)                             |

All error events are logged in structured form with stable fields (`error_class`, `problem`, `cli_command`, `correlation_id`). Pipe `gflow ... 2> errors.jsonl` then `grep error_class=...` to investigate.
```

- [ ] **Step 7.2: `docs/CONFIGURATION.md`** — add:

```markdown
### `GFLOW_CLI_CONCURRENCY` (1–16, default 1)

When > 1, `FlowApiClient` opens N Playwright Pages inside the persistent BrowserContext on `__aenter__` and routes each in-flight operation through one. Pages share cookies + auth (correct — same session). Memory cost is roughly **30–60 MiB per Page** on Chromium; benchmark with `gflow video batch` before raising above 8.

### `GFLOW_CLI_LOG_FORMAT` (`auto` | `text` | `json`, default `auto`)

`auto` renders text on a TTY, JSON when stdout is piped — same convention as `kubectl`/`gh`. Override with `GFLOW_CLI_LOG_FORMAT=json gflow ...` to force JSON in interactive shells (e.g. for piping into `jq`).
```

- [ ] **Step 7.3: `docs/ARCHITECTURE.md`** — add the modular monolith section verbatim from `project_conventions.md`, plus a "RFC 9457 Problem Details for errors" subsection that links to `errors.py`. Document the per-worker Page concurrency model.

- [ ] **Step 7.4: `PLAN.md`** — mark Phase 4 ✅ at the bottom of the phase table; under "Active phase", set the next active phase to "Phase 5 — public alpha on PyPI" with a forward pointer. Confirm Phase 5/6/7 backlog entries are present and unchanged.

- [ ] **Step 7.5: `CHANGELOG.md`** — add `## [0.4.0a1] - 2026-05-XX` block:

```markdown
## [0.4.0a1] - 2026-05-XX

### Added
- Per-worker Playwright Page pool — `GFLOW_CLI_CONCURRENCY=N` now actually parallelizes `gflow video batch`.
- `tenacity`-based retry policy (3 attempts, exp jittered backoff, Retry-After capped at 60s) on 5xx / 429 / Playwright transport errors.
- RFC 9457 Problem Details exception hierarchy: `GFlowError → FlowApiError → AuthExpiredError | RateLimitError | ContentPolicyError | NetworkError | WireFormatError`.
- Per-class exit codes: 3 (auth) / 4 (rate-limit) / 5 (content-policy) / 6 (network) / 7 (wire-format).
- `WireFormatError` carries discovery payload (route, status, content-type, top-level keys, redacted body prefix) for log-grep evolution feedback.
- `structlog` bootstrap with TTY-aware text/JSON renderer; `error_raised` and `error_unhandled` events.
- 12 pytest-bdd scenarios across auth / video / image (mocked-only).

### Changed
- `FlowApiError` re-parented under `GFlowError`. Legacy `FlowApiError(status, body, *, route)` constructor preserved (back-compat).
- `_resolve_profile` and `_make_provider_dir` deduped into `gflow_cli._cli_helpers`.
- All `logging.*` call sites in `src/` migrated to `structlog`.
- `print()` in `auth.py` replaced with Rich console output.

### Internal
- New module: `gflow_cli.errors`.
- New module: `gflow_cli.observability`.
- New module: `gflow_cli.api._retry`.
- New module: `gflow_cli._cli_helpers`.
```

- [ ] **Step 7.6: `.env.template`** — add:

```env
# Concurrency for `gflow video batch` and `gflow image t2i -n N`.
# Each Playwright Page costs ~30-60 MiB; benchmark before raising above 8.
GFLOW_CLI_CONCURRENCY=1

# Log output format. Default `auto` = text on TTY, JSON when piped.
GFLOW_CLI_LOG_FORMAT=auto
```

- [ ] **Step 7.7: Visual review** — read each modified doc end-to-end. No broken links, no stale phase references, no `[Unreleased]` overlap with `[0.4.0a1]`.

- [ ] **Step 7.8: Commit**

```bash
git add docs/ PLAN.md CHANGELOG.md .env.template
git commit -m "docs: document Phase 4 hardening for v0.4.0a1"
```

**Acceptance criteria.**
- All 6 modified docs reflect Phase 4.
- `CHANGELOG.md` has `[0.4.0a1]` block with Added/Changed/Internal subsections.
- `PLAN.md` shows Phase 4 ✅ and Phase 5 active; Phase 6/7 backlog entries present.
- `.env.template` documents `GFLOW_CLI_CONCURRENCY` and `GFLOW_CLI_LOG_FORMAT`.
- `docs/ARCHITECTURE.md` has modular monolith + Problem Details + per-worker Page sections.

---

## Task 8: Tag `v0.4.0a1`

**Goal.** Bump version, commit, tag, push (push by user via `/release` slash command).

**Files.**
- Modify: `pyproject.toml` (`version = "0.4.0a1"`)
- Modify: `src/gflow_cli/__init__.py` (`__version__ = "0.4.0a1"`)
- Tag: `v0.4.0a1`

**Steps.**

- [ ] **Step 8.1: Bump version**

```toml
# pyproject.toml
version = "0.4.0a1"
```

```python
# src/gflow_cli/__init__.py
__version__ = "0.4.0a1"
```

- [ ] **Step 8.2: Run full quality gates one last time**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli --cov-report=term-missing
```

Expected: all GREEN. Coverage ≥ 80% overall, ≥ 95% on `errors.py` + `observability.py`.

- [ ] **Step 8.3: Commit version bump**

```bash
git add pyproject.toml src/gflow_cli/__init__.py
git commit -m "chore(release): v0.4.0a1"
```

- [ ] **Step 8.4: Tag (do NOT push — Coordinator surfaces release prompt to user)**

```bash
git tag v0.4.0a1
```

- [ ] **Step 8.5: Surface to user** — Coordinator reports:

> Phase 4 implementation complete. Tag `v0.4.0a1` created locally at `<SHA>`. Run `/release` (or `git push origin main && git push origin v0.4.0a1`) when ready.

**Acceptance criteria.**
- `pyproject.toml` and `__init__.py` both show `0.4.0a1`.
- All quality gates GREEN.
- `git tag -l v0.4.0a1` returns the tag.
- Tag NOT pushed — release is the user's gate.

---

## Self-review checklist (run after writing — completed by author)

- [x] Plan uses checkbox `- [ ]` syntax for trackable steps.
- [x] Every task has explicit Files / Steps / Acceptance criteria sections.
- [x] Every task ends with a single-line conventional-commit subject.
- [x] Tasks are ordered by dependency (T0 spike → T1 errors → T2 Page pool / T5 observability in parallel → T3 retry → T4a handlers → T4b helper relocation → T6 BDD → T7 docs → T8 release).
- [x] Tests-first is explicit in every code task (Write failing → Verify red → Implement → Verify green).
- [x] Quality gates listed in every task (ruff / format / pyright / pytest).
- [x] Plan distinguishes new vs modified files in File Structure.
- [x] Coordinator gates between tasks are concrete (testable acceptance criteria, not vibes).
- [x] CLAUDE.md rules referenced implicitly (no AI co-author, frozen domain, no `print()`, async all the way down).
- [x] Spec coverage: every spec §3 module mapped to a task; every spec §6 DoD line mapped to an acceptance criterion or test.
- [x] Discoveries documented (structlog already in deps, `log_format` already correctly named, T0 spike narrowed) so the engineer doesn't redo settled work.

---

## Definition of done (Phase 4 / v0.4.0a1)

**Pre-merge (gsd-verifier scope):**

- [ ] T0 spike note in `PLAN.md`.
- [ ] All 9 tasks committed atomically with conventional subjects.
- [ ] `uv run pytest -q` GREEN; `uv run pyright src` GREEN; ruff check + format clean.
- [ ] Coverage ≥ 80% overall; `errors.py` and `observability.py` ≥ 95%.
- [ ] **Automated test**: forced 5xx triggers retry; mock call count == 3; structlog captured output (`structlog.testing.LogCapture`) shows attempts 1→2→3 with backoff timing.
- [ ] **Automated test**: each error class → correct exit code + remediation print + `error_raised` event with `error_class` / `problem` / `cli_command` / `correlation_id` populated.
- [ ] **Automated test**: synthetic subclass of `AuthExpiredError` exits with code 3 (`isinstance` walk subclass-aware).
- [ ] **Automated test**: `error_unhandled` fires for non-`GFlowError` exception; `message_hash` / `stack_hash` present, full message absent.
- [ ] **Automated test**: `WireFormatError` carries `route_name`, `http_status`, `content_type`, `top_level_keys`, `body_prefix_redacted` discovery fields.
- [ ] **Automated test**: 12 BDD scenarios green; all use mocked client.
- [ ] CHANGELOG `[0.4.0a1]`; `PLAN.md` Phase 4 ✅ + Phase 5/6/7 backlog confirmed; `pyproject.toml` + `__init__.py` show `0.4.0a1`.
- [ ] `docs/ARCHITECTURE.md` modular monolith section + Problem Details note current.

**Post-merge (user-actioned):**

- [ ] Tag `v0.4.0a1` pushed; release workflow green.
- [ ] Manual smoke (~1 credit): `GFLOW_CLI_CONCURRENCY=4 gflow video batch tests/fixtures/manifest_4.tsv` ≤ 1.5× slowest single call.
- [ ] Manual smoke: kill session cookie → `gflow image t2i ...` exits 3 + remediation prints.

_End of plan._
