# Live verification — v0.18.0

> Evidence record for the v0.18.0 release. v0.18.0 ships one feature — typed
> `UiSelectorDriftError` (exit code 23) for UI-automation selector-probe failures
> (issue #183, PR #184) — and one fix: the `gflow image` command family now wires
> its output directory into the API client so debug screenshots are actually
> captured on UI-automation failures.

## Summary

- **Verified by:** ffroliva (Claude Code)
- **Date:** 2026-06-12 (live runs on the PR #184 head `45ae442`, merged unchanged
  into `develop @ 9f1d80b`)
- **gflow-cli version:** 0.18.0 (pre-tag verification)
- **Status:** 🟢 Green — both the failure path (drift simulation) and the happy
  path live-verified, **credit-free** (image generation spends no Veo credits)

## 1. Pre-tag gates

- `ruff check src tests` — clean; `ruff format --check` — 220 files formatted
- `pyright src` — 0 errors (7 pre-existing `browser_cookie3` resolution errors in
  the local env only, untouched `auth/cookies.py`; CI green)
- Scoped pytest (errors + transports + cli surfaces) — 475 passed, 1 skipped
- PR #184 CI — 7/7 checks green on `45ae442` (test matrix 3.11/3.12/3.13,
  gitleaks, SonarCloud)

## 2. Failure path — live drift simulation (the issue-#183 condition)

`MODE_SWITCH_TRIGGER_SELECTORS` was patched (in both transport module namespaces)
to a guaranteed-miss selector, reproducing exactly the reporter's DOM condition —
the crop_* mode-switch trigger absent from the Flow editor. The real CLI t2i path
was then run against live Flow (profile `denon82`, project created, $0 credits —
the failure fires before any generation).

| Ledger layer | Evidence |
|---|---|
| Exit code | **23** (`UiSelectorDriftError` via `EXIT_CODE_MAP`) |
| File count | 1 — `debug_no_mode_trigger.png` written to the `--out` dir |
| Magic bytes | `\x89PNG\r\n\x1a\n` ✅ (valid PNG) |
| Dimensions | 1280×720 (Pillow) |
| structlog invariants | `selector_miss` ×1 (bogus selector) → `selector_probe_failed` → `error_raised` with full RFC 9457 problem payload; **zero** `error_unhandled` / `message_hash` events |
| User-visible artifact | `Flow UI selector drift: probe=mode_switch_trigger: no matching element found on the Flow editor. Screenshot: <out-dir>/debug_no_mode_trigger.png` + remediation hint (no `--verbose` claim, PII warning present) |

This is the user-facing contract the release exists for: the issue-#183 reporter
saw `Unexpected error` (exit 1) with the real message hashed away; on v0.18.0 the
same condition produces an actionable, screenshot-carrying exit-23 error.

## 3. Happy path — mode-switch unregressed

`gflow --verbose image t2i "<prompt>" --profile denon82 --aspect 9:16 --out <dir>`
against live Flow:

| Ledger layer | Evidence |
|---|---|
| Exit code | 0 |
| File count | 1 (`ec262f4c-….jpg`) |
| Magic bytes | `\xff\xd8\xff` ✅ (valid JPEG) |
| Dimensions | 768×1376 (portrait — matches `--aspect 9:16`) |
| structlog invariants | `image_mode_entered` fired (mode-switch dropdown found and used); no `UiSelectorDriftError` in the run |
| User gallery | image present in the Flow project gallery (denon82) |

## 4. Not verified this cycle (recorded, not omitted)

- **The drift error on a genuinely affected account:** the issue-#183 reporter's
  account is in the (suspected) issue-#174 new-UI A/B cohort; both maintainer
  accounts probed as the old dialog UI on 2026-06-12, so the organic condition is
  not reproducible on our side. The simulation in § 2 exercises the identical
  code path (selector cascade miss → typed raise → CLI boundary → exit 23).
  Reporter confirmation is requested on #183.
- **`cli_character.py` screenshot wiring:** intentionally out of scope for this
  release (tracked as a follow-up); its drift errors correctly omit the
  `Screenshot:` clause.
