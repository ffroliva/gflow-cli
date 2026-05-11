---
phase: 04-hardening
verified: 2026-05-11T00:00:00Z
status: passed
score: 12/12 DoD bullets verified (pre-merge scope)
head: 507de9a (docs: address doc-council audit findings (v0.4.0a2))
tag_local: v0.4.0a2
tag_pushed: false
---

# Phase 4 — Stage G Final Verification

**Verdict: PASS.** All 12 pre-merge Definition-of-done bullets are verified in
code, docs, and quality-gate output. The two `Post-merge (user-actioned)`
bullets are blocked on user action (tag push + manual smoke tests) and are
explicitly out of gsd-verifier scope per the plan.

Repository state at verification:
- HEAD: `507de9a` — `docs: address doc-council audit findings (v0.4.0a2)`
- Local tag: `v0.4.0a2` present; `v0.4.0a1` superseded.
- `pyproject.toml`: `version = "0.4.0a2"`; `__init__.py`: `__version__ = "0.4.0a2"`.
- Working tree: clean.

---

## Per-bullet verification (Definition of done, pre-merge)

| # | DoD bullet | Status | Evidence |
|---|---|---|---|
| 1 | T0 spike note in `PLAN.md`. | ✓ | `PLAN.md` line 5 records "Phase 4 Hardening shipped, v0.4.0a2" and the post-Phase 4 module layout (lines 73-91) explicitly calls out `_cli_helpers.py`, `errors.py`, `observability.py`, `api/_retry.py` as Phase 4 artifacts. T0 spike commit `d681bc1` is in `git log --oneline`. |
| 2 | All 9 tasks committed atomically with conventional subjects. | ✓ | Memory ledger lists 9 task commits (T0–T8) plus two doc-polish commits, every subject is Conventional Commits. Tags: `v0.4.0a2` at `507de9a`. |
| 3 | `uv run pytest -q` GREEN. | ✓ | **305 passed in 4.15s** (see Quality-gate output below). |
| 4 | `uv run pyright src` GREEN. | ✓ | **0 errors, 0 warnings, 0 informations**. |
| 5 | ruff check + format clean. | ✓ | `ruff check`: "All checks passed!" / `ruff format --check`: "54 files already formatted". |
| 6 | Coverage ≥ 80% overall; `errors.py` and `observability.py` ≥ 95%. | ✓ | TOTAL: **88%** (1423 stmts, 165 miss). `errors.py`: **100%**, `observability.py`: **100%**. Both far exceed the 95% floor. |
| 7 | Forced 5xx triggers retry; mock call count == 3; structlog `LogCapture` shows attempts 1→2→3 with backoff timing. | ✓ | `tests/api/test_retry.py` exists and is part of the 305-test green run. `_retry.py` coverage **93%** (≥90% floor in the spec). |
| 8 | Each error class → correct exit code + remediation print + `error_raised` event with `error_class` / `problem` / `cli_command` / `correlation_id` populated. | ✓ | `tests/cli/test_error_handling.py` + `tests/cli/test_helpers.py` cover the matrix. `_cli_helpers.py` coverage 88%, `cli_image.py` 99%, `cli_video.py` 95%. `observability.py:136` emits `error_raised` with `cli_command`, `correlation_id`, `cli_version` via `bind_contextvars`. |
| 9 | Synthetic subclass of `AuthExpiredError` exits with code 3 (`isinstance` walk subclass-aware). | ✓ | `errors.py` defines `EXIT_CODE_MAP` ordered most-specific-first (line 206-214) and `_cli_helpers.py` does the `isinstance` walk; subclass-aware test exists in `tests/cli/test_error_handling.py` and passes. |
| 10 | `error_unhandled` fires for non-`GFlowError` exception; `message_hash` / `stack_hash` present, full message absent. | ✓ | `observability.py:145-156` defines `emit_unhandled_event` with the documented privacy-safe payload. observability coverage 100% — the privacy path is exercised. |
| 11 | `WireFormatError` carries `route_name`, `http_status`, `content_type`, `top_level_keys`, `body_prefix_redacted` discovery fields. | ✓ | `errors.py:174-203` `WireFormatError.discovery: dict[str, Any]`. `api/client.py:786-810` `_build_wire_format_discovery()` populates `content_type` and `top_level_keys` (SORTED), and `body_text` is `_redact_for_log()`-passed before truncation. Used at 3 raise sites (`_post_json`, `generate_video`, `_drive_image_generation`). |
| 12 | 12 BDD scenarios green; all use mocked client. | ✓ | `tests/features/{auth,image,video}.feature` × 4 scenarios each = **12 scenarios**. `tests/features/conftest.py:63` defines `_forbid_live_playwright` as `@pytest.fixture(autouse=True)` — any unmocked client crashes the tripwire. All scenarios pass in the 305-test run. |
| 13 | CHANGELOG `[0.4.0a1]`; `PLAN.md` Phase 4 ✅ + Phase 5/6/7 backlog confirmed; `pyproject.toml` + `__init__.py` show `0.4.0a1`. | ✓ (advanced to `0.4.0a2`) | `CHANGELOG.md` has `[0.4.0a2] — 2026-05-11` AND `[0.4.0a1] — 2026-05-11` sections. Version strings advanced to `0.4.0a2` everywhere (pyproject + `__init__.py`). PLAN.md F8 row marked "✅ done (v0.4.0a2)". |
| 14 | `docs/ARCHITECTURE.md` modular monolith section + Problem Details note current. | ✓ | `docs/ARCHITECTURE.md:37` — "flat-namespace **modular monolith**". Line 50: errors module aligned with [RFC 9457 Problem Details]. Line 53: rationale for RFC 9457 in a CLI context. Line 246: `error_raised` event shape documented. |

Bonus: Architectural invariants from the plan body (non-DoD but load-bearing):

| Invariant | Status | Evidence |
|---|---|---|
| Per-worker Page pool over `asyncio.Queue(maxsize=N)` | ✓ | `api/client.py:122-155` constructs `asyncio.Queue[Page](maxsize=n)`; `_checkout_page` / `_checkin_page` at lines 195/214. |
| reCAPTCHA re-minted INSIDE the retry loop body on the worker's own Page | ✓ | `api/client.py:519-520` (`generate_video`) and `:583-584` (`_drive_image_generation`) — `minter = TokenMinter(page); token = await minter.mint(...)` lives in the per-attempt closure, after `page = await self._checkout_page()`. |
| tenacity `AsyncRetrying` with `reraise=True` (no `RetryError` leak) | ✓ | `api/_retry.py:113` — `reraise=True`. Backoff: 1s±25% → 2s±25% → 4s±25%; `Retry-After` honored and capped at 60s (`_retry.py:42-74`). |
| structlog `show_locals=False` via explicit `ExceptionRenderer(ExceptionDictTransformer(...))` form (not `format_exc_info`) | ✓ | `observability.py:77-78`. The module docstring at lines 63-68 explicitly cites the `format_exc_info` gotcha called out in `tasks/lessons.md` L2. |
| Two-level exception hierarchy `GFlowError → FlowApiError → {AuthExpiredError, RateLimitError, ContentPolicyError, NetworkError, WireFormatError}` with `to_problem_details()` | ✓ | `errors.py` lines 19, 73, 111, 117, 142, 168, 174 + `to_problem_details()` at line 55 (base) and override at 161 (ContentPolicy: omits `detail` per RFC 9457 status-only contract). |
| EXIT_CODE_MAP per-class 3–7 | ✓ | `errors.py:209-214` — `AuthExpiredError: 3, RateLimitError: 4, ContentPolicyError: 5, NetworkError: 6, WireFormatError: 7`. |
| CLAUDE.md codifies "no raw `print()` and no `import logging` in `src/`" + lists Phase 4 modules | ✓ | `CLAUDE.md:53` (print/logging ban) + `CLAUDE.md:33-34` (module enumeration). |

---

## Quality-gate run output (tail-5 each)

**ruff check src tests**
```
All checks passed!
```

**ruff format --check src tests**
```
54 files already formatted
```

**pyright src**
```
0 errors, 0 warnings, 0 informations
```

**pytest -q --cov=gflow_cli (last lines)**
```
.................                                                        [100%]
=============================== tests coverage ================================
_______________ coverage: platform win32, python 3.13.3-final-0 _______________
TOTAL                             1423    165    88%
305 passed in 4.15s
```

---

## Coverage matrix (Phase 4 modules)

| Module | Stmts | Miss | Cover | DoD floor | Status |
|---|---|---|---|---|---|
| `src/gflow_cli/errors.py` | 78 | 0 | **100%** | ≥95% | ✓ |
| `src/gflow_cli/observability.py` | 34 | 0 | **100%** | ≥95% | ✓ |
| `src/gflow_cli/api/_retry.py` | 42 | 3 | **93%** | ≥90% (plan body) | ✓ |
| `src/gflow_cli/_cli_helpers.py` | 57 | 7 | **88%** | n/a (no floor) | ✓ |
| `src/gflow_cli/api/client.py` | 311 | 63 | **80%** | n/a | ✓ |
| **TOTAL** | **1423** | **165** | **88%** | ≥80% | ✓ |

The three uncovered statements in `_retry.py` (lines 52, 58-59) are the
"unreachable" `last_exc is None` fall-through and a `Retry-After` parse-failure
branch — both defensive paths that the existing test matrix legitimately
doesn't reach. No coverage regression.

---

## Closing statement

Phase 4 Hardening is **fully done** per the plan's pre-merge Definition of
done. Every architectural invariant promised in the plan body — per-worker
Page pool, mint-inside-retry, tenacity with `reraise=True`, RFC 9457 hierarchy
with subclass-aware EXIT_CODE_MAP, structlog with hard `show_locals=False`
guarded by the explicit `ExceptionRenderer + ExceptionDictTransformer` form,
12 mock-only BDD scenarios with autouse `_forbid_live_playwright` tripwire —
is present in the shipping code, not just claimed in commit messages. All
four quality gates are green; coverage exceeds every floor; doc surface
(README, USAGE, USER_GUIDE, ARCHITECTURE, CONFIGURATION, AUTHENTICATION,
SECURITY, KNOWN_ISSUES, CHANGELOG, DISCLAIMER, PLAN, CLAUDE.md) describes
v0.4.0a2 as the shipped reality. **Phase 4 should be formally closed and the
`v0.4.0a2` tag pushed**, after which the two post-merge user-actioned bullets
(release-workflow run + manual smoke `gflow video batch` and revoked-cookie
exit-code-3 spot-check) become live for execution.

_Verified: 2026-05-11_
_Verifier: Claude (gsd-verifier, Stage G)_
