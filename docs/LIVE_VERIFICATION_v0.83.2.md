# Live verification — v0.83.2

> Evidence for this release, gathered on 2026-10-10/11 (PRs #962, #964, #965). Each row is a
> run someone watched, not an inference. Where a check could not be run, the blocker is
> named.

## Environment

| | |
|---|---|
| Profile | `denon82` (real-browser Chrome strategy) |
| Host Flow served | `flow.google.com` (classic composer) |
| UI locale | `pt` by default; forced to `vi` with `?hl=vi` + `Accept-Language: vi` for #963, read back from `document.documentElement.lang` |
| Transport | `ui_automation` → migrated composer |
| OS | Windows 11 (Google Chrome installed) |
| Veo credits spent | 0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Duration radio matched on its number, not the English unit (#963, PR #964) | `apply_video_settings` in a Vietnamese settings pane | ✅ e2e `test_e2e_duration_binds_in_a_vietnamese_pane`: **failed** with `develop`'s composer (exit 11, the reporter's error), **passed** on the branch and again on the release tree; `4 giây` and `8 giây` read back checked | $0 (no submit) |
| Notice-only cookie bar acknowledged when it has no reject (PR #964) | `_dismiss_cookie_bar` under `hl=vi` | ✅ the spike first stopped on this bar (exit 23); the same e2e now passes through it | $0 |
| Image tile and download button clicked through locators (#957, PR #965) | `gflow image upscale` (2K), CLI door | ✅ e2e `test_migrated_image_upscale_2k_e2e` passed on the branch and the release tree | quota only |
| Same, MCP door | `gflow_upscale_image` | ✅ e2e `test_mcp_upscale_image_2k_e2e` passed on the branch and the release tree | quota only |
| `auth status` migrated-host probe opens the profile with its own channel (#962) | `gflow auth status` | ⚠️ no regression only: on the release tree, `gflow auth status --profile denon82` exited 0, *Flow session verified*. That output does not prove the migrated fallback ran, and this host has Chrome, so the fixed branch (no Chrome) was not reached | $0 |

## 5-layer ledger (release tree, 2026-10-11)

| Layer | Locale e2e | Image upscale e2e (CLI + MCP) |
|---|---|---|
| File count | n/a (settings only, no submit) | 1 upscaled image per door, asserted by the tests |
| Magic bytes | n/a | CLI: asserted PNG or JPEG header and > 100 kB; MCP: not asserted (file exists and opens as an image) |
| Dimensions | n/a | long side ≥ the 2K floor, asserted on both doors; CLI also asserts larger than the source |
| structlog | no `UiSelectorDriftError`, no exit 11 | no `Element is not attached to the DOM` |
| User-confirmable artifact | the pane's `N giây` radio read back checked for N = 4 and 8 | the upscaled file in the project's newest catalogued image |

`pytest -m e2e tests/e2e/test_migrated_locale_e2e.py` + the two image-upscale tests on the
release tree: **3 passed in 101.93 s**.

## Not verified

- **#962 on a host without Google Chrome.** Blocker: every host we drive has Chrome
  installed, so `channel_for_profile` returns `chrome` either way. Covered offline by
  `9ea59653` (a chrome-marked profile on a host without Chrome); the contributor reported
  the original failure on such a host.
- **Video upscale 1080p.** It raised `UpscaleUnavailableError` on `develop` as well,
  because the newest clip's 1080p option is disabled on this account. Filed as #967; the
  shared download-menu helper is covered by the image runs above.
- **Accounts served labs.** Blocker: every profile we hold is redirected to
  flow.google.com.

## Pre-tag gates

| Gate | Result |
|---|---|
| `/gflow:changelog` | `[Unreleased]` → `[0.83.2] — 2026-10-11`: Fixed (#963, notice-only cookie bar, #957, #962) |
| `/gflow:check` | ruff, format, pyright 0, hygiene, doc links, PII, mirror (regenerated after the KNOWN_ISSUES fix), council memory, release artifacts green; pytest 4923 passed, 24 skipped, coverage 93% |
| SonarCloud | PR #964 and PR #965 gates passed; #962 was a fork PR (skipped, maintainer-checked) |
| `/gflow:doc-review` | council: Auditor 1 YELLOW (KNOWN_ISSUES sent a non-English `--duration` exit-11 user to the cohort workaround; PROJECT_STATUS omitted video upscale from *Not verified*) → KNOWN_ISSUES now names #963 and says upgrade first, PROJECT_STATUS lists #967. Auditor 2 YELLOW (CHANGELOG `auth status` entry had no PR number) → now cites PR #962; 7 version sites, footer, `<details>` balance, mirror in sync. Auditor 3 GREEN (every claim matched to code, `_duration` regex run against decoys). Deferred: "a miss raises exit 11" holds only when the pane renders option groups (exit 23 otherwise, unchanged). Reports local at `tmp/council/` |

## Post-tag evidence

| Evidence | Result |
|---|---|
| Signed tag | `v0.83.2` (SSH signature) on `a486f831` |
| Release workflow | [run 38109515205](https://github.com/ffroliva/gflow-cli/actions/runs/38109515205): `build-and-publish` success, `mcp-registry / publish` success |
| GitHub Release | https://github.com/ffroliva/gflow-cli/releases/tag/v0.83.2 (not a prerelease) |
| PyPI | `uvx --refresh --from gflow-cli==0.83.2 gflow --version` → `gflow, version 0.83.2` |
| Release PR | #968 merged into `main` (`b12a1730`, merge commit; all checks incl. SonarCloud green); back-merged into `develop` (`98ea3232`) |
