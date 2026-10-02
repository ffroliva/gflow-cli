# Live verification — v0.81.0

> Evidence for the user-facing changes in this release, run against real Flow on
> 2026-09-30. Where a check could not be run, the blocker is named. Each row is a run
> that someone watched, not an inference.

## Environment

| | |
|---|---|
| Profile | `ci-probe` (real-browser Chrome strategy) |
| Host Flow served | `flow.google.com` |
| Transport | `ui_automation` → migrated composer |
| OS | Windows 11 |
| Date | 2026-09-30 |
| Veo credits spent | 3 × `veo-lite` clips (two measurement arms, one final-code run) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Generation browser off-screen, `GFLOW_CLI_BROWSER_WINDOW_POSITION` (#923) | `FlowApiClient` launch (CLI and MCP); the standalone `UiAutomationTransport` launch is unit-tested only | ✅ Yes: image e2e, video run, window bounds, throttling | 3 images, 3 clips |
| `ProfileAccessError` for a write-denied profile (#919) | generation client launch | ✅ at merge (PR #919): real Chrome on a write-denied profile raises `ProfileAccessError`, exit 11 | $0 |
| `urllib3` 2.8.0 CVE lock (#920) | none (transitive) | n/a: no Flow surface; `pip-audit` clean in CI | — |

## Off-screen generation browser — #923

**Measured before adopting the change** (the contributor's branch, then the final code).

| Check | Off-screen | Visible control |
|---|---|---|
| `gflow image t2i` (PR branch) | exit 0, image saved, 34 s | exit 0, image saved, 33 s |
| `gflow video t2v --model veo-lite` (PR branch) | exit 0, mp4 saved, 53 s, 8 status polls, 0 stall events | exit 0, mp4 saved, 65 s, 11 polls, 0 stall events |
| `document.visibilityState` | `visible` | `visible` |
| animation frames / 100 ms timer ticks in 5 s | 300 / 50 | 295 / 50 |

**Final code (merge `1f42b222`), default setting:**

- `pytest -m e2e tests/e2e/test_json_output_e2e.py` → **3 passed, 1 skipped** (the image
  t2i JSON shape among them). The skipped video test is covered below.
- `gflow video t2v --model veo-lite --aspect 9:16 --json`, five layers:
  1. **Count:** 1 mp4 in the output directory.
  2. **Magic bytes:** `ftypisom` at offset 4.
  3. **Shape:** h264, 720×1280, 8.0 s.
  4. **Structlog:** `migrated.submit_clicked` 1, `migrated.status` 9, `migrated.result` 1;
     zero stall, `bring_to_front` or reCAPTCHA-failure events.
  5. **User-confirmable:** JSON `status: "ok"` with the clip's `media_id`.

**Window placement, final setting, read back from Chrome (CDP `Browser.getWindowForTarget`):**

| `GFLOW_CLI_BROWSER_WINDOW_POSITION` | Result |
|---|---|
| unset | `-21845,-21845` on every launch (Windows clamps -30000); off every monitor |
| `100,100` | exactly `100,100` on 8 of 10 launches. Twice it was found elsewhere, once seen moving mid-run: a visible window can be moved once open |
| empty | Chrome's own placement |

**Focus:** Chrome took the foreground on 5 of 5 launches with `--no-focus-on-init` and on 3
of 4 without it. The flag was dropped, and the docs state that Chrome can still take
keyboard focus at launch.

## Blockers (named, not waived)

- **macOS / Linux:** no machine here. The flag is a standard Chromium switch; occlusion
  behaviour there is unmeasured.
- **Polls longer than ~5 minutes** (Chrome's intensive throttling): every clip here
  finished in about a minute. The page reports `visible` off-screen, so throttling should
  not engage, but it was not observed.
- **Video e2e test:** `test_e2e_video_t2v_json_shape` passes `--duration 8`, which this
  account's composer does not offer (KNOWN_ISSUES "Video duration control is absent").
  It is refused before submit (exit 11), unrelated to this release. The same command
  without `--duration` is the video row above.

## Pre-tag gates

- **PR #923:** CI 15/15 (SonarCloud skips fork PRs; it runs on the develop push).
- **`/gflow:doc-review`:** mechanical sections PASS (version sites, links, website mirror, release artifacts).
  - Council: Completeness **YELLOW → fixed**. The setting was unreachable from troubleshooting: added rows to the CONFIGURATION troubleshooting table and a pointer from the KNOWN_ISSUES chooser → consent gap. An unverified remedy for the first-upload rights dialog was removed in favour of the documented one. Tracked, not fixed: no error message names the setting yet.
  - Council: Cross-reference **GREEN**. `ProfileAccessError` scope added in KNOWN_ISSUES and USAGE; an unmeasured causal claim about focus removed; this ledger now says the standalone launch is unit-tested only.
  - Council: Drift **YELLOW → fixed**. The CHANGELOG and CONFIGURATION listed a "CDP launch" site that does not exist in `src/`.
- **`/gflow:check`:** offline suite 4694 passed, 24 skipped, coverage 93%; ruff, format, pyright, hygiene, doc links, PII, council memory, website mirror all green.

## Post-tag evidence

- **Release workflow:** [run 36786572400](https://github.com/ffroliva/gflow-cli/actions/runs/36786572400) — success (build-and-publish, mcp-registry publish).
- **PyPI:** `gflow_cli-0.81.0-py3-none-any.whl` and `gflow_cli-0.81.0.tar.gz`, uploaded 2026-09-30T22:37Z.
- **GitHub Release:** [v0.81.0](https://github.com/ffroliva/gflow-cli/releases/tag/v0.81.0), published 2026-09-30T22:37Z.
- **Release PR:** #924, merged to `main` with a merge commit (`8fa5ea0c`).
- **Back-merge:** `main` → `develop` (`be334758`).
