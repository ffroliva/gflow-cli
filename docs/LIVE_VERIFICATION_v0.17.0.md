# Live verification — v0.17.0

> Evidence record for the v0.17.0 release. v0.17.0 ships one feature — cookie-store
> Flow-session verification (`verify_flow_profile`, PR #168, contributed by @3mora2)
> plus two hardening fixes to it — and one behavior change: the issue-#174
> entity-attach exit-7 remediation hint + `entity_attach_context` telemetry (PR #177).

## Summary

- **Verified by:** ffroliva (Claude Code)
- **Date:** 2026-06-12 (fresh run on the release tree, `develop @ 5444de8`)
- **gflow-cli version:** 0.17.0 (pre-tag verification)
- **Status:** 🟢 Green — cookie verification live-verified credit-free; the #174 hint
  is intentionally not live-triggerable this cycle (reason recorded below)

All verification below is **credit-free**: session verification probes
`/fx/api/auth/session` and spends no Veo credits; no generation was exercised.

## 1. Pre-tag gates

- [x] **Repo hygiene + doc links** — `check_repo_hygiene.py` + `check_doc_links.py` green.
- [x] **Lint/format** — `ruff check --fix` + `ruff format`: 219 files, nothing to fix.
- [x] **Type check** — `pyright src`: 0 errors.
- [x] **Unit + BDD tests** — full suite green in CI on the exact release tree
  (`develop @ 5444de8`, CI run 2026-06-12 13:58 UTC, success).
- [x] **`/gflow:doc-review`** — mechanical pass + 3-auditor council. *Council verdict:
  GREEN / YELLOW / GREEN. 5 findings; 0 Tier 1 (release-blocking); 4 fixed in the
  release-prep commit (KNOWN_ISSUES → AUTHENTICATION link path, AGENTS.md exit-code
  range 3–22, new AUTHENTICATION § Session verification section, INDEX routing row);
  1 Tier 3 deferred (dedicated `verify_flow_profile` API reference doc). Council
  reports at `tmp/council/0{1,2,3}-*.md` (local-only).*

## 2. PR #168 — cookie-store session verification (`verify_flow_profile`)

`gflow_cli.auth.verification.verify_flow_profile` verifies a Flow session directly
from the Chrome cookie store via `browser_cookie3` + `httpx` (fast path), falling
back to a marker-gated Playwright probe when the store is encrypted/locked. The two
Fixed entries harden the same path: Windows DPAPI `RuntimeError` now triggers the
fallback instead of propagating, and transient HTTP failures (429/503/504) retry
with backoff.

### E2E evidence (live Google endpoint, credit-free)

Fresh run on the release tree, 2026-06-12 15:5x (UTC+1), profile **denon82**:

```
$env:GFLOW_CLI_E2E_PROFILE='denon82'
.venv\Scripts\python.exe -m pytest tests/e2e/test_auth_verification_e2e.py -m e2e_auth -v
```

| Case | Criterion | Outcome |
|---|---|---|
| `test_e2e_verify_flow_profile_authenticated` | Logged-in profile → `AUTHENTICATED` with a non-empty `user_email`, **and** the same profile passes `FlowApiClient.health_check()` | **PASS** |
| `test_e2e_verify_flow_profile_no_session` | Sessionless store → typed non-authenticated outcome (no false positive, no crash) | **PASS** |
| `test_e2e_verify_flow_profile_falls_back_to_playwright` | Cookie-store read failure → Playwright fallback engages and verifies | **PASS** |

```
3 passed in 17.20s
```

The same three cases were also verified green live on denon82 during the PR #168
review council (pre-merge, same day) — this run re-confirms on the post-merge
release tree.

## 3. PR #177 — issue #174 entity-attach exit-7 hint + telemetry

Entity-attach `WireFormatError` failures (exit 7) now carry `ENTITY_ATTACH_DRIFT_HINT`
(dialog-vs-navigate self-diagnosis + link to #174) and both submit backstops emit an
`entity_attach_context` discovery field (`video`/`image`).

### Not live-triggerable this cycle — reason

Triggering the hint live requires an account on Flow's new full-page media-library
UI (the #174 A/B variant where staged entities are dropped from the submit). The
A/B has **rolled back off both available accounts**: the variant re-probe on
2026-06-12 15:48 (UTC+1) via `scripts/dev/spike_issue174_library_ui_recon.py`
returned `variant=dialog` on **denon82** (15 ms) and **promo-denon82** (8 ms) —
evidence posted on [#174](https://github.com/ffroliva/gflow-cli/issues/174).
With no affected account, the backstop cannot fire live without a real wire
regression to provoke it.

Coverage in lieu of a live trigger:

- The hint text, exit code, and `entity_attach_context` field are covered by the
  PR #177 unit/BDD suites (green in CI on the release tree).
- The underlying backstops themselves were live-verified for v0.16.0 (the image
  backstop's first live encounter is what *opened* #174 — see
  [LIVE_VERIFICATION_v0.16.0](LIVE_VERIFICATION_v0.16.0.md)).
- The credit-free variant probe above exercised the real composer Add-Media path
  on both accounts at the release tree without drift.

## Post-tag evidence

*(filled after the tag push and PyPI publish)*
