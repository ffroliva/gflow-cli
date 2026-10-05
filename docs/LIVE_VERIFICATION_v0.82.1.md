# Live verification — v0.82.1

> Evidence for the fixes in this patch release, run during development on 2026-10-01 and
> 2026-10-02 (PRs #939, #940, #942). Where a check could not be run, the blocker is named.
> Each row is a run that someone watched, not an inference.

## Environment

| | |
|---|---|
| Profile | `ci-probe` (real-browser Chrome strategy) |
| Host Flow served | `flow.google.com` |
| Transport | `ui_automation` → migrated composer |
| OS | Windows 11 |
| Veo credits spent | 1 × `veo-lite` clip (#898, consented); everything else $0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| A reCAPTCHA mint failure is a typed error with a measured retry flag (#915, PR #939) | token mint on the real page pool | ✅ BDD e2e `test_recaptcha_error_shape_bdd.py`: **2 passed** | $0 |
| A busy catalog after a successful generation is a warning, not a failed run (#900, PR #940) | local SQLite | n/a: no Flow surface — verified on a real DB file with a real second connection | $0 |
| A video's Flow workflow id is recorded (#898, PR #942) | `gflow video t2v` → catalog | ✅ one clip on flow.google.com | 1 veo-lite |
| A repeated video start is a no-op (#898) | catalog | n/a: no Flow surface — offline | $0 |

## #915 — reCAPTCHA mint failures

Spike `scripts/dev/spike_recaptcha_error_shape.py`
([findings](superpowers/spikes/2026-10-01-recaptcha-error-shape.md)), three runs, real page
pool:

- **Pool page at `about:blank`, client path:** `RecaptchaError` 3/3, identical → not retryable.
- **flow.google.com root grid:** exit 36 before the mint (guarded).
- **Project page, steady:** 5/5 and 3/3 mints OK.
- **Mint racing a navigation:** "context destroyed" 3/3, every re-mint OK → retryable.
- **Site-key read alone, 0–0.2 s into a navigation:** "context destroyed" at 0.02/0.05 s and
  "no site key" at 0.1/0.2 s; every re-read OK. `document.readyState` is already `complete`
  at 0.2–0.3 s with no key (Flow injects the script after load), so it cannot tell the two
  apart; the page's scheme can (`about:` never gets the script). → a missing key is
  retryable iff the page is `http(s)`.

E2E (`@e2e @e2e_auth`, ci-probe): **2 passed** (30.9 s) — `about:blank` → typed,
`retryable=False`, message names the page; a raced mint → typed, `retryable=True`, and the
re-mint on the settled page succeeds.

## #900 — busy catalog

No Flow surface. The real dependency is SQLite, driven for real:
`tests/data/test_store_transaction_errors.py` (lock past `busy_timeout`, nested BEGIN,
`IntegrityError` still raw, non-sqlite error passes through with rollback) and
`tests/cli/test_video_recording_busy.py` (a real recorder and DB file, the lock taken after
the generation returns: exit 0, one `data.persistence_failed_after_success` warning with
`detail` "database is locked", the STARTED row intact). The CLI test fails on the
pre-fix `store.py` (exit 1, `error_unhandled OperationalError`) and passes with the fix.

## #898 — the video workflow id

`gflow video t2v --model veo-lite --profile ci-probe` (1 clip, consented):

1. **Count:** 1 file, `7ec5466f-….mp4`.
2. **Magic bytes:** `ftypisom` (MP4).
3. **Shape:** 2,171,384 bytes.
4. **Structlog:** `migrated.submit_observed` (`YhhmEf`) named workflow `dbb0737e…`; all 10
   `migrated.status` replies matched it (the driver drops any that do not).
5. **User-confirmable:** the catalog row has `flow_workflow_id = dbb0737e…` (NULL for every
   video before the fix) and a lookup by that id returns the clip.

The labs arm reads `media[0].workflowId`, present in every committed capture (02, 08, 09);
it is covered offline against those captures.

## Blockers (named, not waived)

- **Accounts served labs:** labs answers 308 on the three profiles here that hold a live
  Flow session, so the labs arms of #915 (where a user meets the retryable shapes) and #898
  (`media[0].workflowId`) are unobserved live.

## Pre-tag gates

- **PRs** #939, #940, #942: CI 16/16 each, including SonarCloud.
- **Councils:** #939 three reviewers (no must-fix; the site-key-read arm they asked for
  falsified a first reading and changed the rule); #940 one reviewer (three should-fixes,
  all fixed); #942 one reviewer (one must-fix — the labs workflow id was in the committed
  captures — and two should-fixes, all fixed).
- **`/gflow:doc-review`:** one auditor over completeness, cross-reference and drift — YELLOW,
  no FAIL; both WARNs (MCP.md retry-override list, DATA_LAYER.md lock row) and the nits fixed
  before the tag.
- **`/gflow:check`:** all gates green on the release branch.

## Post-tag evidence

- **Release workflow:** [run 36989594319](https://github.com/ffroliva/gflow-cli/actions/runs/36989594319) — success (build-and-publish, mcp-registry publish).
- **PyPI:** `gflow_cli-0.82.1` wheel and sdist published 2026-10-02.
- **GitHub Release:** [v0.82.1](https://github.com/ffroliva/gflow-cli/releases/tag/v0.82.1), published 2026-10-02T09:25Z.
- **Release PR:** #943, merged to `main` with a merge commit (`c1341cb8`).
- **Back-merge:** `main` → `develop` (`7dba9418`).
- **Install from PyPI:** `uvx --from gflow-cli==0.82.1 gflow --version` → `gflow, version 0.82.1`.
