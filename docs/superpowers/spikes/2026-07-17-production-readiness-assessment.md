# Production-Readiness Assessment — gflow-cli

**Date:** 2026-07-17 · **Commit base:** `develop` (post #327/#328) · **Assessor:** autonomous run

## Executive summary

**The codebase is in strong production-ready shape.** Every automated gate is green,
the full test suite passes with zero failures, dependencies have no known
vulnerabilities, and the SonarCloud quality gate is **OK**. There were **no errors to
fix** — no failing tests, no type errors, no lint violations. The work this pass
delivers is therefore *hardening + code-smell cleanup*, not defect repair.

## Gate results

| Gate | Result |
|---|---|
| **Full test suite** | ✅ **2357 passed, 7 skipped, 0 failed** (255s) |
| **pyright `src`** (CI type gate) | ✅ 0 errors, 0 warnings, 0 informations |
| **ruff check + format** (CI lint gate) | ✅ clean |
| **repo hygiene + doc links** | ✅ 578 files, all links resolved |
| **SonarCloud quality gate** | ✅ **OK** (new-code conditions pass) |
| **SonarCloud hotspots** | ✅ 0 to review |
| **pip-audit** (dependency CVEs) | ✅ no known vulnerabilities |

## Security scan (bandit — advisory, not a CI gate)

| ID | Sev | Location | Verdict |
|---|---|---|---|
| **B324** SHA-1 for security | HIGH | `api/_sapisidhash.py:14` | **FIXED** — added `usedforsecurity=False`. SHA-1 is *mandated* by Google's SAPISIDHASH scheme (not a chosen primitive); the flag marks it a protocol hash **and** makes it FIPS-mode-safe (previously would raise on FIPS Python). |
| **B113** httpx `timeout=None` | MED (low-conf) | `transports/experimental/sapisidhash.py:319` | **Accept (false-positive)** — deliberate: the docstring documents that `asyncio.wait_for` in `_call_once` is the single wall-clock guard (HIGH #7). Adding a client timeout would double-guard. |
| B311 non-crypto `random` | LOW | jitter/pacing sites | Accept — already `# noqa: S311`, pacing not crypto. |
| B101 `assert` | LOW | various | Accept — asserts in non-security paths; stripped under `-O` but not used for control flow at trust boundaries. |
| B105 hardcoded "password" | LOW | `--password-store=basic`, empty strings | Accept — false positives (flag/field names, not secrets). |
| B112 try/except/continue | LOW | best-effort loops | Accept — intentional best-effort continues, logged. |

Net security action: **one real hardening fix** (B324); the rest are justified accepts.

## SonarCloud issues — 29 open (all CODE_SMELL; gate stays OK because none are new-code)

Triaged into three buckets:

### Fixed this pass (6 — safe, clear value)
- `python:S5655` `mcp/tools.py` — `_card_dict(replace(...))` type lost by Sonar → wrapped in `cast(...)` per the repo's `sonar-dataclasses-replace` pattern.
- `python:S1192` `drivers/agentic.py` — `"agentic:await_images"` ×3 → module constant `_ROUTE_AWAIT_IMAGES`.
- `python:S7500` `tools/invocation.py` — dict comprehension → `dict(self.params)`.
- `python:S108` `mcp/server.py` — removed the empty `if TYPE_CHECKING: pass` block + now-unused import.
- `python:S1186` `ui/app.py` — added the explanatory comment to the intentional no-op `AlreadySentResponse.__call__`.
- (`python:S324` was the same SHA-1 site — covered by the B324 hardening fix.)

### False-positives / not safely fixable (3 — recommend "Won't Fix" in Sonar with justification)
- `python:S1192` `ui_automation_video.py:2194` — the duplicated literal is the **type string** `"dict[str, object]"` inside `cast(...)`; extracting it to a variable breaks pyright (cast needs a literal type). Keep.
- `python:S7503` `ui/app.py:115` — `wrapped_receive` **must** be `async` (ASGI receive-callable contract); Sonar's "remove async" is wrong here.
- `python:S7632` ×3 (`factory.py:173`, `image_batch.py:537`, `cli_video.py:257`) — flags the `# noqa: <code> - reason` suppression syntax. These are **ruff** suppressions that ruff parses correctly; reformatting risks breaking the suppression. Low value, high fiddle-risk. Recommend accept.

### Deferred — risky refactors, NOT done autonomously (19)
**15× `python:S3776` cognitive complexity** + **4× `python:S107` too-many-parameters**. These are on **verified, hard-to-unit-test** surfaces:
- Highest complexity is live browser-automation and the daemon: `ui_automation_video.py` (CC **62**, 36, 26, 23, 21), `worker/daemon.py` (CC **45**), `browser_manager.py`, `client.py`, `mcp/tools.py`.
- The S107 cases are **Click CLI command signatures** (`t2i` 18 params, `i2i`/`i2v` 15) — the "parameters" *are* the CLI options; bundling them into a params object also touches CLI→MCP parity (`cli-param-changes-need-mcp-parity`).

**Why deferred:** restructuring a CC-62 live-automation function (or the daemon) to satisfy a metric changes control flow that only **live e2e** can verify. Doing that in an unattended overnight batch and auto-merging is a **net production-readiness risk** — the likely outcome is a subtle regression in exactly the selector/flow paths that unit tests can't cover. The responsible path is **individually-reviewed, e2e-verified refactor PRs** (or accept-with-justification for the inherently-complex automation ones). Recommend one dedicated PR per hotspot, each with a live e2e run — not a blind sweep.

## Recommendations

1. **Ship** the 6 safe fixes + B324 hardening (this pass — PR to `develop`).
2. **Mark the 3 false-positives "Won't Fix"** in SonarCloud (dashboard action; keeps the count honest without unsafe edits).
3. **Schedule the 19 refactors** as individual reviewed PRs, prioritising the pure-logic, well-tested ones (`expander.py`, `movie_manifest.py`, `_cli_helpers.py`, `client.py:549`) and treating the live-automation/daemon ones as accept-or-carefully-refactor-with-e2e.
4. The interaction-seam / `SettleKind` work (issue #315 follow-up) folds naturally into #299 and would *reduce* several of these complexity scores as a side effect — another reason to do it there, with e2e, rather than as isolated metric-chasing.

## Bottom line

No production blockers. One real hardening fix shipped (FIPS-safe SHA-1). Code-smell count
is maintainability debt concentrated in known-complex automation code, best paid down with
reviewed, e2e-verified refactors rather than an unattended sweep.
