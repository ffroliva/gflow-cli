# Live verification — v0.56.0

> Hand-run on `develop` (post-#504 merge, tree of the v0.56.0 tag) against real
> Flow on Windows 11, profile `denon82` (chrome strategy), 2026-08-13. Raw logs
> under `tmp/live-verify/` (gitignored); artifact images under
> `tmp/live-verify/out/v0.56.0/`.

## Environment

| | |
|---|---|
| Branch | `develop` @ post-#504 (release tree) |
| Local version | `0.55.0` editable (pre-bump) |
| Profile | `denon82` (chrome strategy) |
| Date | 2026-08-13 |
| OS | Windows 11 |

## Pre-tag gates

- `/gflow:check`: hygiene + doc links + website PII + mirror sync + ruff check +
  format-check all green; pyright 0 errors; duplication proxy: 2 findings, both
  in code untouched by this release (experimental transports 401-retry; #477
  guard block) — develop's SonarCloud analysis green post-merge.
- Full test suite: green on develop CI (3.11/3.12/3.13 + ProfileLease matrix on
  3 OSes) for the exact merged tree; touched suites re-run locally (66 passed).
- `/code-review 504 xhigh` (post-merge): no functional bugs; 4 confirmed
  doc/test-hygiene findings fixed on the release branch (commit `9fb53c2`).
- `/ponytail:ponytail-review`: lean, no cuts.
- `/gflow:doc-review`: council verdict **YELLOW / YELLOW / GREEN** across the 3
  auditors — zero Tier-1 (release-blocking) findings; 9 Tier-2 fixes applied in
  the release-prep commit (USAGE lease-wait + jq-pipe note + exit-row updates,
  root CONFIGURATION table, INDEX routing rows, PROJECT_STATUS develop marker +
  #479-deferral note, CHANGELOG section order); Tier-3 deferred (llms.txt blurb,
  ARCHITECTURE module inventory, website-mirror relative-link rewrite). Council
  reports at `tmp/council/0{1,2,3}-*.md` (local-only).

## Matrix

| # | Feature | Variation | Result |
|---|---|---|---|
| 1 | #493 fix — mode-switch path | `image t2i`, fresh project, 16:9 | ✅ exit 0 |
| 2 | #477 downgrade guard | scratch profile, `Last Version` = 999.0.0.0 | ✅ exit 11 |
| 3 | #478 lease wait — control | contended t2i, `GFLOW_CLI_LEASE_WAIT_SECONDS=0` | ✅ exit 11 fail-fast |
| 4 | #478 lease wait — waiter | contended t2i, `GFLOW_CLI_LEASE_WAIT_SECONDS=180` | ✅ waited ≈11 s, then exit 0 |
| 5 | #479 update notice | — | ⏳ deferred post-release (see below) |

## 1. #493 — mode-switch drift fix (happy path)

The changed function (`ui_automation_video.py` mode-switch fall-through) sits on
the live mode-switch path; the *drift branch itself is unreachable on our
accounts* — it fires only on the unrecognized new editor variant, which no
available profile has (that is exactly why #493 awaits an affected reporter's
`diag_mode_switch_miss.json`). Named-skip for the branch; the surrounding path
was exercised live, and the new message text is pinned by unit tests
(`test_agentic_cohort_detection.py`, `test_errors.py`).

5-layer ledger (run: `t2i-v0.56.0-493-smoke.log`):

| Layer | Evidence |
|---|---|
| File count | 1 new file `7ae94544-…_1.jpg` in a fresh project |
| Magic bytes | `FF D8 FF E0` (valid JPEG) |
| Dimensions | 1376×768 — matches requested `--aspect 16:9` |
| Structlog | `ui_automation.image_mode_entered` → `gen_settings_opened` → `batch_response_captured` |
| Artifact | 1,019,459 B image, human-viewable |

Note: first two attempts failed `AuthExpiredError` (401 at
`project.createProject`) — an expired denon82 session, not a code failure;
`gflow auth login` re-mint fixed it and the same command then passed unchanged.
Recorded per failure-routing as external state, not a flake of the code under
test.

## 2. #477 — Chromium downgrade guard

Scratch profile `profile_scratch477` with `Last Version` = `999.0.0.0` and a
stub cookies DB; `gflow image t2i … --profile scratch477` (playwright engine,
`channel=None`) refused **before** launching Chromium:

- Exit code **11** (`ProfileEngineDowngradeError`), pre-auth, pre-credits.
- Error names both versions and prints the documented remediation (upgrade
  engine / chrome channel exemption / re-login recovery path).
- Log: `477-downgrade-guard-t2i.log`.

(`auth status` on the same profile takes the browser_cookie3 fast path and
never reaches the guarded Playwright open — the guard is still covered there by
unit tests; the command-level e2e above is the generation-client call site,
`api/client.py:529`.)

## 3–4. #478 — bounded lease wait

Three concurrent `image t2i` runs on `denon82` (logs: `478-holder.log`,
`478-control-nowait.log`, `478-waiter.log`):

| Time (UTC) | Event |
|---|---|
| 19:44:50 | holder launches browser, holds `ProfileLease` |
| 19:45:07 | control (`WAIT=0`) → `ProfileLockedError`, **exit 11** — historical fail-fast preserved |
| 19:45:08 | waiter (`WAIT=180`) starts, lease contended → polls |
| 19:45:15 | holder finishes naturally, releases lease |
| 19:45:19 | waiter acquires, launches browser |
| 19:45:58 | waiter completes generation, **exit 0**, real JPEG written |

Holder and waiter each produced a valid image (649.7 K / 818.7 K JPEGs in
`out/v0.56.0/`); the waiter's takeover happened only after the holder's natural
release, matching the no-early-release design.

## 5. #479 — once-a-day PyPI update notice (deferred, with reason)

Cannot be honestly verified pre-release: the notice fires only when PyPI
carries a **newer** version than the running install, and it is skipped by
design for editable/local installs (PEP 610) and under `CI`. Post-release plan
(recorded here per the never-silently-omit rule): after v0.56.0 publishes,
install `gflow-cli==0.55.0` in a scratch venv and run any command — expect the
one-line stderr notice naming 0.56.0. Result to be appended below.

## Post-tag evidence

_(filled after the v0.56.0 PyPI publish)_

- #479 update-notice e2e: install `gflow-cli==0.55.0` in a scratch venv, run a
  command, expect the one-line stderr notice naming 0.56.0 — result pending.
- Released-wheel smoke: install `gflow-cli==0.56.0` from PyPI, run a live t2i —
  result pending.
