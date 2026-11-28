# Live verification — v0.108.0

Release ledger for v0.108.0, a minor release. It covers the headless reCAPTCHA mint and direct
HTTP as the default transport for the measured shapes (#1371, #1545, #1546, #1547, PR #1561):
direct text-to-image and image-to-image, direct text-to-video at a measured key including a bare
`video t2v` that reads the account's selected model, direct image-to-video from a local start
frame, direct 1080p video upscale of a catalogued clip, the single headless retry on a refused
submit, the route decided before launch, `--browser-visibility` and its MCP twin, and the
bonus-credit promotion dialog that blocked project creation (#1557, PR #1558). It also covers
the changes merged to `develop` since v0.107.0 that are not a Flow surface (#1536, #1543, #1556).

## Environment

| | |
|---|---|
| Profile | A funded profile served `flow.google.com` for every live run; a second profile served `flow.google.com` (with a low balance) as the probe profile for the refusal e2e, which deliberately sends a corrupted token and so must not run on the main account |
| Entrypoint | CLI, the MCP server (in-process), the MCP worker, and the BDD-bound e2e scenarios |
| OS | Windows 11 (NTFS), system Google Chrome (`channel=chrome`); headless and headed launches |
| Tree | `chore/release-v0.108.0`, cut from `develop` `91d4ea4b` (`gflow_cli.__version__` reads 0.108.0 from the release worktree); the e2e matrix ran on the feature branch head before the merge, the post-fix re-run on the head after both review fix rounds |
| Engine | playwright (default) |
| Veo credits spent | 84 for the e2e matrix (both visibilities), 22 for the post-fix re-run, 8 for the release-tree runs; images cost daily quota only, upscales 0 credits, refusals are not billed |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Quote probe before the release-tree run | `video t2v "<prompt>" --model omni-1.2-flash --duration 4 --resolution 360p --count 1 --aspect 16:9 --max-credits 0 --json` | ✅ Exit 45, `CreditCapExceededError`, `quoted: 4`, `max_credits: 0`, `charged: no` | $0 |
| Release tree, credit-capped (live-verify step 0) | the same command with `--max-credits 4 --out-file t2v.mp4` | ✅ Exit 0 in 64 s, `route: ui`, `visibility: headed` — a capped video takes the UI path, the only path that reads Flow's quote; MP4 (`ftyp mp42`), 1,527,884 bytes, 640x360 | 4 credits |
| Release tree, uncapped — direct and headless (#1371) | the same command without `--max-credits` | ✅ Exit 0 in 41 s, `route: direct`, `visibility: headless`, `retried: false`; log events `direct.minted`, `direct.video_post`, `direct.video_poll`, `direct.video`; MP4 1,521,550 bytes, 640x360 | 4 credits |
| Direct images, headless default (#1371, #1549) | `tests/e2e/test_direct_image_e2e.py` | ✅ headless 14 passed (3 opt-in character tests skipped); headed 17 passed (character tests included) | $0 (daily image quota) |
| Direct text-to-video and image-to-video (#1546), bare t2v (#1371) | `tests/e2e/test_direct_video_e2e.py` | ✅ 3 passed in each visibility: t2v at the measured Omni key, default i2v (Veo 3.1 Lite, 9:16), bare t2v reading an Omni 1.2 Flash selection. The bare-t2v fallback (a Veo 3.1 Lite selection, no measured key) passed separately: it falls back to the UI path without minting a token | 4–10 credits per clip |
| Direct 1080p upscale (#1547) | `tests/e2e/test_direct_upscale_e2e.py` | ✅ 1 passed in each visibility: an Omni 1.2 Flash 4 s 16:9 clip upscaled to 1920x1080 over `p0UkFb` | 4 credits (source clip); upscale 0 |
| Routing, retry and visibility scenarios (#1545) | `tests/e2e/test_headless_direct_bdd.py` | ✅ 4 passed in each visibility | credits per clip |
| MCP twin (`browser_visibility`, route fields) | `tests/e2e/test_direct_video_mcp_e2e.py` | ✅ 2 passed in each visibility | credits per clip |
| Refusal retry (#1545) | `tests/e2e/test_refusal_retry_e2e.py` on the probe profile | ✅ 1 passed headless and 1 passed headed (each run skips the other variant); a refused submit is retried once from a new browser process and is not billed | $0 |
| Model-catalog drift check | `tests/e2e/test_model_catalog_drift_e2e.py` | ✅ 1 passed in each visibility | $0 |
| Promotion dialog blocking project creation (#1557) | `tests/e2e/test_project_create_slow_link_e2e.py` | ✅ 5 passed in each visibility, including a deterministic test that injects the promotion over the new-project button | $0 |
| After both review fix rounds (relaunch counter, shared proxy and route rules, signed-out mark, wire-read gate) | the image, video, upscale, promotion and refusal suites, headless | ✅ 11 passed, 0 failed | 22 credits |
| CJK caption font (#1536), Windows WAL/SHM ACL (#1543), wire signed-out mark TTL (#1556) | local media and the wire read path | Not a generation surface changed by this release; covered by their own PRs' tests and the offline suite | — |

Note on the headed column: the direct video, upscale and MCP video suites set the visibility
inside the test during the matrix run, so their headed rows repeated the headless run; this was
corrected afterwards (the suites now honour the outer setting). Headed evidence for the direct
path comes from the headed image suite, the BDD headed example and the headed refusal variant.

## Pre-tag gates

- `/gflow:check` on the release tree: repository hygiene, size, doc links, website PII, website mirror, council memory, plugin skills, commit trailers, release artifacts, `ruff check`, `ruff format --check` and `pyright src` (0 errors) pass; the offline suite passes (9431 passed, 52 skipped, total coverage 94 %).
- `/gflow:doc-review`: completeness, cross-reference and drift lenses all YELLOW, no release-blocking finding; fixes in the release commit (the module list in `AGENTS.md`, a CHANGELOG entry for `docs/FLOW_TIPS.md`, `RelaunchFailed` in `BROWSER_MODES.md`, `USAGE.md`, `DEBUGGING.md`, a `gflow run` example that set the kill switch, `--browser-visibility` on four commands, `llms.txt`, `.env.template`).
- `scripts/ci/check_release_artifacts.py` passes on the release commit.

## Not measured

macOS, Linux and accounts served `labs.google`. Headless for the remaining UI surfaces is
tracked in #1554; credit-price tracking (which would allow a direct credit-capped video) in
#1555.
