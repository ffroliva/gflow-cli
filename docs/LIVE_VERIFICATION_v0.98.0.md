# Live verification — v0.98.0

Release ledger for v0.98.0: `gflow capabilities` and the MCP resource `capabilities://self`
(#1197), `gflow models list` / `show` (#1196), error envelopes that say whether Flow may have
billed and what to do next (#1201), billed requests and image submits sent once (#1260, #1274,
#1275), the caption heal on flow.google.com (#546), classic-editor selectors that no longer depend
on the UI language (#1273), and the `VideoGenerationMixin` split (#1249 wave 2).

## Environment

| | |
|---|---|
| Profiles | `denon82` (release runs, the paid t2v, the e2e); `ci-probe` (signed out: the error envelope) |
| Host Flow served | `flow.google.com` on every profile here; `labs.google/fx/tools/flow` answers HTTP 308 to it |
| Entrypoint | the real `gflow` CLI (`uv run gflow` in the release worktree); the e2e drive the CLI and the MCP tools in-process |
| OS | Windows 11 |
| Tree | `chore/release-v0.98.0`, cut from `ef213b87` (develop after #1280) |
| Veo credits spent | 1 veo-lite clip on `denon82` (the release-tree t2v) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Plain text-to-video on the release tree (step 0) | `gflow video t2v "a ceramic cup on a wooden table, soft morning light" --profile denon82 --model veo-lite --json` | ✅ exit 0 in 81 s; `MEDIA_GENERATION_STATUS_SUCCESSFUL`; 2,802,078 bytes; magic `ftyp`; H.264 720×1280 (portrait), 7.9 s | 1 veo-lite clip |
| `gflow capabilities` (#1197) | `gflow capabilities --json` and the table form | ✅ exit 0, no call to Flow; `schema: 1`, `adapter: flow-browser`, 12 capabilities (e.g. `cancel.server: no`, `idempotent_submit: no`, `generate.video: partial`), each with a note | $0 |
| `capabilities://self` MCP twin (#1197) | `tests/e2e/test_capabilities_e2e.py` | ✅ 3 passed (5.3 s) | $0 |
| `gflow models list` / `show` (#1196) | `gflow models list --json`, `gflow models list`, `gflow models show veo-lite --json` | ✅ exit 0; 4 image models, 6 video models (Omni 1.2 Flash up to 15 s); `show veo-lite`: `veo_3_1_lite`, durations 4/6/8, ref cap 3 | $0 |
| Error envelope with `charged` / `next_action` (#1201) | `gflow credits user --profile ci-probe --json` on a signed-out profile | ✅ exit 31 as expected; problem details carry `charged: "no"` and `next_action: "reauth"`, plus `remediation_hint` | $0 |
| `VideoGenerationMixin` split (#1249 wave 2, #1270) | t2v above through `UiAutomationTransport.generate_video`; PR #1270's runs | ✅ t2v above; on #1270: $0 tiers 105 passed, credits MCP 4/4, project-aware t2v passed | 1 clip (above) |
| Image submit sent once (#1275) | `test_image_batch_e2e`, `test_t2i_batch_project_e2e`, `test_image_download_failure_records_bdd` on PR #1279 | ✅ 4 passed: image generation on flow.google.com unchanged (the page-owned path) | $0 |
| Duration matcher shared with the classic editor (#1273) | `tests/e2e/test_migrated_locale_e2e.py` on PR #1280 | ✅ 1 passed: the duration binds in a Vietnamese pane on flow.google.com | $0 |
| Version | `gflow --version` | recorded after the bump in this release commit | $0 |

### What these runs do not prove

- **The billed sent-once branches (#1260, #1274) and the HTTP image transports (#1275).**
  `billed=True` sits on the direct-wire POSTs (`batchAsyncGenerateVideoExtendVideo`,
  `upsampleImage`), and the single-send image path runs on the HTTP transports. All of these
  run only for accounts Flow serves on labs. Blocker: every profile here gets a 308 to
  flow.google.com, and a real 5xx, timeout or reset cannot be forced on demand. Covered offline:
  `tests/api/test_billed_post_no_retry.py`, `tests/api/transports/test_migrated_images.py`.
- **The classic-editor selectors (#1273): include action, upload button, frame slots, classic
  duration tabs.** Same blocker (labs redirects with a 308). Covered by real-Chromium tests in
  `tests/api/transports/test_ui_automation_video.py`, including the uniqueness guard and the
  hidden-button case.
- **The caption heal (#546).** Its e2e (`refresh_on_miss`) passed on denon82 when #1259 was
  verified; it was not re-run for this ledger. The `$0` tier run on #1270 hit one
  `mention_miss` in `test_manifest_refs_bdd` (row 3), one of the 17 e2e failures already on
  develop (#1254).
- **macOS:** no macOS machine here.

### Found by this cycle

- Two xhigh reviews per PR (`/code-review` + `ponytail-review`) found real defects in every fix PR
  before merge, all fixed and tested: a signed media link leaking through the HAR summary's
  `location` header (#1276), a second test seam in `mcp/server.py` (#1277), a catch wide enough
  to mislabel a token-fetch timeout as maybe-billed (#1278), the HTTP transports' own submit
  timeout escaping the single-send (#1279), and a uniqueness guard that counted hidden buttons
  (#1280). CodeRabbit found a closed-browser case on #1278 that turned the reconcile error back
  into a retryable one; fixed with a test that was red first.
- `docker/Dockerfile` on Python 3.15 (#1281, Dependabot) does not build: `lz4` has no cp315 wheel
  and the slim image has no compiler. Green CI does not build this image. Left open.
- A concurrent profile clone of `denon82` drives flow.google.com alongside the original and both
  stay signed in (`docs/superpowers/spikes/2026-11-12-profile-clone-concurrency.md`), the basis for
  sharding live e2e (`/gflow:optimize`).

## Gates

| Gate | Result |
|---|---|
| `check_repo_hygiene.py`, `check_size.py`, `check_doc_links.py`, `check_website_docs_pii.py`, `generate_website_docs.py --check`, `check_council_memory.py`, `generate_plugin_skills.py --check` | ✅ clean |
| ruff, ruff format, pyright strict | ✅ clean, 0 errors |
| `pytest --cov` (`-n 8 --dist worksteal`) on the release tree | ✅ 6,791 passed, 32 skipped, 93% |
| `check_release_artifacts.py` | ✅ release artifacts OK |
| `/gflow:doc-review` | _Council verdict: YELLOW across all 3 auditors (completeness YELLOW, cross-reference YELLOW, drift YELLOW); no fictional or false claim, nothing blocking. Mechanical pass clean (versions, CHANGELOG footer, links, website mirror and PII, release artifacts, memory). 12 findings, all fixed in the release commit: issue numbers instead of PR numbers for #1196/#1197/#1201 in PROJECT_STATUS and this ledger; `refresh_resolver.py` added to the AGENTS.md module list; a CHANGELOG note for the #1249 split; the milestone row names #546 and #1273; USER_GUIDE §12.2 cites #1274; the #546 heal documented in REFERENCE_STRATEGIES; `charged`/`next_action` in the gflow-cli skill's error table (plugin copy regenerated); `gflow models` and the error fields in `llms.txt` and the README quick start; `optimize` listed in `skills/README.md`. Deferred (long-standing): ledgers v0.7–v0.59 are not named in INDEX. Reports at `tmp/council/0{1,2,3}-*.md` (local-only)._ |
