# Live verification — v0.7.0 release gate

> Hand-run on `develop` against the real Flow API. Each row below is a
> credit-spending live test that succeeded end-to-end (prompt → batch
> response captured → image downloaded). All outputs land in
> `tmp/live-verify-0.7.0/`. This document is a reference for anyone
> writing integration tests or wanting concrete evidence that the
> `ui_automation` transport works against the production Flow UI.

## Environment

| | |
|---|---|
| Branch | `develop` |
| Local version | `0.6.0a6` (pre-bump; release tag will be `v0.7.0`) |
| Profile | `ffroliva` (real-browser Chrome strategy — required per project memory `real-browser-auth-mandatory.md`) |
| Date | 2026-05-20 |
| Transport | `ui_automation` (the only non-experimental transport in v0.7.0) |
| Model | `nano2` (default, → `NARWHAL`) |
| Count | 1 |

## What was tested

`gflow image t2i` was run against every aspect ratio the CLI exposes.
Each row corresponds to one CLI invocation. "Captured" means the
`batchGenerateImages` HTTP response was observed by the new
`ui_automation.batch_response_seen` listener log.

| # | Aspect | Result | Submit → captured | PNG size | Saved as |
|---|---|---|---|---|---|
| 1 | `1:1` (pre-fix) | FAIL — `aspect_ratio_set_failed`, then `TimeoutError` after 3 min | n/a | — | — |
| 2 | `9:16` (default) | FAIL — flake; no `batch_response_seen` | n/a | — | — |
| 3 | `9:16` | OK | ~35 s | 1.09 MB | `3d952b40-1271-4d69-9657-c871deaf608d_1.png` |
| 4 | `16:9` | OK | ~37 s | 1.02 MB | `45dd32f2-fb9e-44cf-9f48-78f8ba578796_1.png` |
| 5 | `4:3` | OK | ~29 s | 853 KB | `9b2a224d-b2d6-41c4-9eae-15fbcfec5cb1_1.png` |
| 6 | `1:1` (post-fix, cascade) | OK | ~26 s | 1.11 MB | `c7f0fb40-50cf-47c4-b0b1-d3a77d6e68f7_1.png` |

`3:4` was not run live; the selector is structurally identical to `4:3`
and the cascade fallthrough covers it. Open a follow-up live test for
parity if you depend on it.

## What changed because of this verification

Two improvements landed on `develop` as a direct result of these runs:

1. **`1:1` aspect-ratio selector cascade**
   (`src/gflow_cli/api/transports/ui_automation.py`).
   The previous selector `[role="tab"]:has-text("1:1")` substring-matched
   against an invisible parent on Flow's current UI, causing a 3 s
   timeout and a silent fallback to Flow's default aspect. The fix
   replaces the single substring match with an ordered cascade of
   exact-match (`:text-is`) selectors:
   ```
   _ASPECT_TAB_CANDIDATES["1:1"] = ("1:1", "Square", "1×1", "1x1")
   ```
   `:text-is(label)` only matches when the trimmed text content equals
   `label` exactly, sidestepping the parent-string ambiguity.

2. **Listener black-hole elimination**
   (`src/gflow_cli/api/transports/ui_automation.py`,
   `_attach_batch_response_listener`).
   The listener previously dropped any non-matching response silently,
   hiding listener-miss bugs. Now every `batchGenerateImages` URL is
   logged via `ui_automation.batch_response_seen` BEFORE the
   per-project filter, and any filter-drop emits
   `ui_automation.batch_response_dropped_project_id_mismatch`.

Both changes are covered by the existing unit tests on
`test_ui_automation.py` (58 passing) and were live-validated by the
runs above.

## Open follow-ups (not blocking v0.7.0)

- **First-attempt flake** — sometimes the listener observes no
  `batchGenerateImages` URL at all even though the UI clicks succeed.
  Hit once in 4 non-`1:1` attempts (row 2 above). Hypothesis: a
  transient overlay or stale UI state eats the arrow-forward click,
  but the click event itself returns "success". Tracked for v0.8.0.
- **`3:4` live test** — covered by the cascade but not exercised on
  this run.
- **Live image-gen smoke as a permanent guarded CI job** — would catch
  any future Flow UI regression. Requires a long-lived authenticated
  test profile in CI, which currently isn't safe.

## How to reproduce

```powershell
# Pre-req: a logged-in Chrome profile.
$env:PYTHONUTF8 = "1"
mkdir -p tmp\live-verify

uv run gflow --verbose image t2i `
  "A neon-lit cyberpunk cat reading a book in a rainy alley" `
  --profile <your-profile-name> `
  --count 1 `
  --aspect 9:16 `
  --out tmp\live-verify
```

Per profile rule (`branch-workflow.md`): use a real Chrome strategy
(`--browser chrome` on `auth login`). Playwright's bundled Chromium is
rejected by Google and falls under issue #17's exit-code-14 path.

## Linkage

- Pre-existing memory referenced: `image-generation-401-next.md` —
  this verification did NOT reproduce the historical 401. The
  feat/ui-automation-onboarding-bypass branch (#27) appears to have
  resolved that path.
- Open issue once #18 lands: track the first-attempt flake.
