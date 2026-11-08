# Live verification — v0.96.0

Release ledger for v0.96.0: Omni 1.2 Flash with 3–15 second durations (#1168), Omni r2v at 4, 6 or
10 seconds (#1167), and reloads past the agent-only composer before refusing (#1142, PR #1177).

## Environment

| | |
|---|---|
| Profile | `denon82` (real-browser Chrome strategy, no Google AI plan: the Omni 1.2 slider stops at 10s) |
| Host Flow served | `flow.google.com` |
| Entrypoint | the real `gflow` CLI (`uv run gflow` in the release worktree); the e2e rows drive the composer, the CLI through `CliRunner`, and the MCP tool functions |
| OS | Windows 11 |
| Tree | `chore/release-v0.96.0` (cut from `cb6cae81`, the PR #1177 merge head) |
| Veo credits spent | 1 lite clip on the release tree; 9 credits on the PR #1174 head (below) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Plain text-to-video on the release tree (step 0) | `gflow video t2v "a ceramic cup on a wooden table, soft morning light" --profile denon82 --model veo-lite` | ✅ exit 0 in 118s, 1,437,001 bytes, `ftypisom`, 7.93s, 720x1280 h264 + aac, 24 fps | 1 lite clip |
| Omni 1.2 Flash durations (#1168) | `tests/e2e/test_migrated_omni12_duration_bdd.py` on the release tree | ✅ every second from 3 to 15: 3–10 set on the live slider and read back before Send; 11–15 refused before submit on this 10s-max account | $0 |
| Omni 1.2 over MCP (#1168) | `tests/e2e/test_omni12_mcp_twin_e2e.py` on the release tree | ✅ `gflow_generate_video` → queue → worker → the live slider reads back the requested length | $0 |
| Omni r2v 4/6/10s (#1167) | `tests/e2e/test_migrated_omni_r2v_duration_bdd.py` on the release tree | ✅ each length applied on the live composer without submitting | $0 |
| Agent-only reload at the reference-upload gate, CLI and MCP (#1142, PR #1177) | `tests/e2e/test_agent_only_reload_e2e.py` on the release tree | ✅ 2 passed: first verdict forced to agent-only, the page reloads, the live classic composer takes the local `--ref` / `reference_images` upload, stopped before generation | $0 |
| Paid Omni clips (#1167, #1168) | `gflow video r2v --model omni-flash --duration 4` and `--model omni-1.2-flash --duration 5`, PR #1174 head | ✅ 4.032s and 5.035s MP4s, the reference visible in both | 9 credits |
| Version | `gflow --version` | ✅ `gflow, version 0.96.0` after the bump | $0 |

### What these runs do not prove

- **A real agent-only serve.** No account here is served the agent-only composer, so the reload
  e2e forces the first verdict, on `image i2i --ref` (CLI) and `reference_images` (MCP). It proves
  the reference-upload gate reloads and then uploads on the live classic composer, not that a
  reload gets an affected account past the agent-only one. The `run_images` and `run_video` gates
  call the same `ensure_editor_for` helper and are covered offline (PR #1177), not forced live. Blocker: no
  account in the cohort; the one observation came from the #1142 reporter.
- **15-second Omni 1.2 on a Google AI plan.** No account here has one (`denon82` stops at 10s);
  above 10s is verified only as the pre-submit refusal.
- **The paid Omni clips ran on the PR #1174 head**, before PR #1177 changed `run_video`'s
  refusal gate. On the release tree that gate is covered by the plain t2v above and the $0 Omni
  rows, which run the real `apply_video_settings`.

### Found by this run

The first release run of `test_cli_ref_reloads_past_the_agent_only_composer` failed: it saw no
`migrated.agent_only_reload` event. It had passed on PR #1177 only because the shell exported
`GFLOW_CLI_LOG_LEVEL=INFO`; the CLI defaults to WARNING. The test now sets INFO itself (commit
`491579ac`) and passed with the variable unset. Three Omni 1.2 setup errors and one r2v `[10]`
failure in that first run came from a second pytest run sharing `tmp/pytest` and the profile; all
four passed when re-run alone.

## Gates

| Gate | Result |
|---|---|
| `check_repo_hygiene.py`, `check_doc_links.py`, `check_website_docs_pii.py`, `generate_website_docs.py --check`, `check_council_memory.py`, `generate_plugin_skills.py --check` | ✅ all clean |
| ruff, ruff format, pyright strict | ✅ clean, 614 files formatted, 0 errors |
| `pytest --cov` (`-n 8`) | 6027 passed, 12 skipped, 1 failed: `test_packaging.py::test_built_distributions_contain_sql_migrations`, which passed alone (2/2) and with `tests/data` under `-n 8` (173/173); the full-suite run kept no traceback |
| `check_release_artifacts.py` | ✅ release artifacts OK |
| `/gflow:doc-review` | _Council verdict: RED across 3 auditors (completeness RED, cross-reference YELLOW, drift RED). All findings were docs lagging #1174/#1177, none code: `docs/USAGE.md` called 10s "exclusively omni-flash"; the `video chain` help said "10s remains omni-flash-only"; resolution was "omni-flash only" in USAGE, the MCP docstring and `VideoRequest`; Omni 1.2's reference cap of 7 and its chain refusal were missing from the cap and chain lists; the video-production skill, KNOWN_ISSUES and MOVIE.md omitted the 3–15 range; no user doc described the #1142 reloads before exit 36 (now in USAGE, KNOWN_ISSUES, MCP.md and the gflow-cli skill); this ledger over-claimed that the forced e2e covers every gate (it covers the reference-upload gate). All fixed in the release-prep commit, website and plugin mirrors regenerated. Deferred: README's "5-model Veo picker" count, 720p on Omni 1.2 (unmeasured), the video-production skill's claim that `--duration` without `--model` exits 2 (pre-existing). Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._ |
