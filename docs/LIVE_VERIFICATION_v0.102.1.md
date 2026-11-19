# Live verification — v0.102.1

Release ledger for v0.102.1, a patch release. It covers one fix merged into `develop` since v0.102.0: #1354 (PR #1368).

## Environment

| | |
|---|---|
| Profile | `ffroliva` (funded) |
| Host Flow served | `flow.google.com`. The account was served the classic composer at 10:11Z and the agent-only composer from about 10:20Z on (see "What these runs do not prove") |
| Entrypoint | CLI & MCP |
| OS | Windows 11, system Google Chrome (`channel=chrome`) |
| Trees | `bugfix/1354-agent-only-asb-video` at each commit named per row; `chore/release-v0.102.1`, cut from `develop` (`cf8063ac`), for the plain `video t2v` |
| Engine | playwright (default) |
| Veo credits spent | 16 video generations: 15 while verifying #1354, 1 for the release-tree `video t2v` |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Spike: an `/asb/` tile names its listing record (#1354) | `scripts/dev/spike_agent_only_asb_join.py`; [spike note](superpowers/spikes/2026-11-19-agent-only-asb-listing-join.md) | ✅ On an agent-only page, every finished tile and chat option was `/asb/`, with no `flow-content.google` URL (the reported DOM). Each tile's token named exactly one `Zzl0ze` record (5 of 5), and 3 of 3 on the classic grid | $0 |
| Agent-only t2v names an all-`/asb/` clip from the listing (#1354, PR #1368) | Direct `run_agent_video` call on the classic composer driven through its agent arm, with the driver's `flow-content.google` pattern disabled (`dd2ef270`) | ✅ Clip named and downloaded in 59 s, `VideoStarted` with the same id. MP4 (`ftyp`), h264 1280x720, 7.92 s; size equal to the record's `size_bytes` (3,337,551 B), so the `/asb/` download is the stored asset | 1 veo-lite |
| Same, unmodified CLI on the affected surface | `gflow video t2v --model veo-lite --aspect 16:9 --json` on an agent-only page (`b3d9cd19`) | ✅ Exit 0; `migrated.agent_only.video_named_by_listing` logged; MP4 h264 1280x720, 7.92 s | 1 veo-lite |
| Same, CLI and MCP twin | `GFLOW_CLI_E2E_RUN_VIDEO=1 pytest -m "e2e_video or e2e_image" tests/e2e/test_agent_only_composer_bdd.py` | ✅ 4/4 at `b3d9cd19`; the MCP t2v worker (`cli_command='worker t2v'`) logged `video_named_by_listing`. At `e1f54d74`: 3/4. The MCP t2i failed in the full run because the agent answered with the previous scenario's count and aspect (#1369, image path, unchanged); it passed when re-run alone | 4 Omni (agent default) |
| Review rounds | Same e2e, `-m e2e_video` | At `7e463005`: MCP t2v passed, **CLI t2v failed** with exit 9 after one approval. Its clip (`f316080b`) was finished in Flow, and the run's cause is not established (see below). The identical command then passed. At `de293205` (the merged tree): **6/6**, three consecutive runs of CLI + MCP t2v, each logging `video_named_by_listing` | 9 (2 + 1 + 6) |
| Release tree, default engine (live-verify step 0) | `gflow video t2v --model veo-lite --aspect 16:9 --out-file <path> --json` on `chore/release-v0.102.1` | ✅ in a fresh project (see "Release-tree run" below) | 1 veo-lite |

### Release-tree run

`gflow, version 0.102.1`, profile served the agent-only composer.

- **First attempt, in the project the e2e runs used:** exit 9 after 621 s, `approvals=0`, nothing billed. The new timeout detail read `last poll: the newest finished tile is the one from before the submit; pending seen: False; listing reads: 0`. The agent had answered the directive, which named no duration, as if it had asked for 4 seconds. Earlier requests in that chat had, and the agent asked which model to use instead of generating. That is #1369's context carry-over on the video path (recorded there); #1354's code was never reached.
- **Second attempt, in a fresh project (no `--project`):** exit 0 in 98 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, model `veo_3_1_lite`, and `migrated.agent_only.video_named_by_listing` logged. The file is MP4 (`ftyp`), h264 1280x720, 7.92 s.

## Pre-tag gates

- `/gflow:check` on the release tree: every repository gate passes (hygiene, size, doc links, website PII, website mirror, council memory, plugin skills, release artifacts); `ruff` clean; `pyright` `0 errors`; `7354 passed, 36 skipped` in 361 s, coverage 93%.
- **PR #1368 CI** at `de293205`: 21 checks passed, 1 skipped (CodeRabbit on the last commit), with SonarCloud green.
- **Offline suite** at `e1f54d74`: 7353 passed, 36 skipped.
- **Reviews:** one fresh `/code-review xhigh` and one `/ponytail-review xhigh`, with their important findings fixed in `e1f54d74`. CodeRabbit's 3 findings were fixed in `7e463005` and answered on the PR.
- `/gflow:doc-review`: _Council verdict: YELLOW, YELLOW, YELLOW; no Tier 1 findings. Fixed in the release-prep commit: the CHANGELOG entry names the new exit-9 detail; the run count reads 14 of 15 video runs in the ledger and `docs/INDEX.md`; the milestone row sits newest-first; the spike note's Decision and `KNOWN_ISSUES.md` state the pre-submit and pending guards added in review. Tier 3, not changed: a `docs/MCP.md` row for an agent-only video exit 9. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

## Post-tag evidence

Recorded after the tag is published: the `Release` workflow run, the PyPI upload and the GitHub Release.

### What these runs do not prove

- **The runs cover Windows only and one account.** That account is `en`; the reporter's account is `ja`, and the token join was not measured on it.
- **One video run in 15 failed** across the fix's trees (14 passed). That failure, on `7e463005`, happened before a video timeout carried any diagnostics. Its clip had finished in Flow, and the incident screenshot showed the grid scrolled past the newest tiles. Whether the newest tile was unmounted, or a guard blocked a mounted clip, is not established. Since `de293205`, a video timeout names the last poll's verdict, so a recurrence explains itself.
- **The account changed composer mid-session.** `ffroliva` was served the classic composer at 10:11Z. Verification then pressed its agent-mode chip to drive the agent arm, and within seconds the chip disappeared and the account was served the agent-only composer on every later load. Whether the click caused that is not established.
