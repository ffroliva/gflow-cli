# Live verification — v0.103.1

Release ledger for v0.103.1, a patch release. It covers the flow.google.com editor gate's
recovery from Flow's agent mode and agent panel (#1384, #1374; PRs #1388 and #1389), the
move of agent-mode handling to its own mixin, and the #1369 spike that closed that issue as
stale. Documentation-only changes in the same range (#1376) touch no Flow surface.

## Environment

| | |
|---|---|
| Profile | A funded profile, served Flow's 2026-11-20 build (Agent switch, `right_panel_close`) |
| Host Flow served | `flow.google.com`, classic composer, with agent mode turned on and off by the tests |
| Entrypoint | CLI & MCP |
| OS | Windows 11, system Google Chrome (`channel=chrome`) |
| Trees | `bugfix/1374-agent-sidebar-recovery` at the commits named per row; `docs/spike-1369-agent-only-driver-new-build` at `4d58e07f`; `chore/release-v0.103.1`, cut from `develop` `22e7d882` |
| Engine | playwright (default) |
| Veo credits spent | 1 video generation (the release-tree `video t2v`); every other run is $0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Agent panel open: the gate closes it, switches agent mode off, returns `classic`, and the state persists across a reload (#1384, #1374) | `tests/e2e/test_agent_panel_recovery_bdd.py` "The agent panel is open and Flow remembers it" | ✅ On the release tree. The Given step proves the panel is open after a reload before the When step runs. Red on the unfixed gate with `UiSelectorDriftError` | $0 |
| Agent mode on, panel closed: the Agent switch is the way out (#749 on the new build) | same file, "Agent mode is on with the panel closed" | ✅ On the release tree; red on the unfixed gate | $0 |
| CLI door: a `video t2v` client run reaches the submit from the agent panel | same file, the CLI scenario, with the submit aborted in Chrome | ✅ On the release tree; red on the unfixed gate | $0 |
| MCP door: `gflow_generate_video` (`wait=true`, the queued worker path) reaches the submit from the agent panel | same file, the MCP scenario; asserts a submit was attempted and the job did not complete | ✅ On the release tree; red on the unfixed gate (no submit attempted) | $0 |
| The #749 recovery with its A/B control arm (the toggle constant neutered → drift; restored → classic) | `tests/e2e/test_migrated_host_e2e.py::test_e2e_agent_mode_is_left_before_the_readiness_gate` | ✅ On the release tree. The control arm patches the constant in its new home, `_mc_agent_mode` | $0 |
| Agent-mode handling moved to `AgentModeMixin` (no behaviour change) | the five e2e above, plus the migrated-composer contract (signatures and source hashes) passing without a re-record | ✅ 5/5 at `4d58e07f` and again on the release tree | $0 |
| #1369 closed as stale | `scripts/dev/spike_agent_only_driver_new_build.py` | ✅ The agent-only driver's `_open_pane` raises "Agent settings button (…'tune'…) missing", with the panel closed and with it open, so the driver never reaches the chat ([spike](superpowers/spikes/2026-11-20-agent-only-driver-on-the-new-build.md)) | $0 |
| Release tree, default engine (live-verify step 0) | `python -m gflow_cli video t2v --model veo-lite --aspect 16:9 --out-file <path> --json` on `chore/release-v0.103.1` | ✅ see "Release-tree run" below | 1 veo-lite |

### Release-tree run

`gflow, version 0.103.1`, funded profile, no `--project` (a fresh project). Exit 0 in 92 s,
`MEDIA_GENERATION_STATUS_SUCCESSFUL`, model `veo_3_1_lite`. The file is MP4 (`ftyp isom`),
2,851,490 bytes, h264 1280x720, 7.93 s. Stderr was empty: nothing beside the JSON document.

## Pre-tag gates

- `/gflow:check` on the release tree: every repository gate passes (hygiene, size, website
  PII, website mirror, council memory, plugin skills); `ruff` clean; `pyright` `0 errors`;
  `7413 passed, 16 skipped`, coverage 93.5%. The doc-link gate's two broken links were this
  ledger, which did not exist yet.
- `tests/test_smoke.py::test_imports_succeed` failed in that run only because the release
  tree's source (0.103.1) ran against another checkout's installed metadata (0.103.0). In
  the release worktree's own environment it passes (2 passed).
- **PR #1388 CI:** 22 checks passed, SonarCloud green. **PR #1389 CI:** 22 checks passed.
- **Reviews:**
  - PR #1388: a fresh `/code-review xhigh` and `/ponytail-review xhigh` with one fix round;
    `/gflow:pr-council-review` (YELLOW, 7 Must-fix), all addressed in #1389.
  - #1389's ambiguous-close fix was test-first; an ablation of the prompt-box exclusion
    turned its tests red.
  - CodeRabbit's findings were triaged in the council report.
- `/gflow:doc-review`: _three auditors, YELLOW, no FAILs and no Tier 1 findings. Fixed in
  the release prep:_
  - the `MigratedComposer` mixin chain in ARCHITECTURE;
  - the #1369 milestone label in PROJECT_STATUS;
  - the #1387 caveat for the agent-only driver on the new build, in KNOWN_ISSUES, MCP and
    USAGE;
  - the #749 entry's affected versions, tracking and wording;
  - a host-membership phrasing.

  Tier 3 is left for later: leftover "chip" wording in code comments, and FLOW_WIRE_RPCS's
  trigger column.

## Post-tag evidence

- **Signed tag** `v0.103.1` on `a0c97c1e` (SSH signature present).
- **`Release` workflow** run 44809926268: `build-and-publish` success, `mcp-registry / publish` success.
- **PyPI:** `gflow-cli 0.103.1` is the latest version, with `gflow_cli-0.103.1-py3-none-any.whl` (20:17:27Z) and `gflow_cli-0.103.1.tar.gz` (20:17:30Z).
- **GitHub Release:** https://github.com/ffroliva/gflow-cli/releases/tag/v0.103.1, not a prerelease.

### What these runs do not prove

- **Flow's 2026-11-19 build** (the Agent chip, `close`, `tune`) is covered offline only,
  against a real CSS engine. On 2026-11-20 no available profile was served it.
- **An account with no classic arm** on the new build. It fails closed as drift (exit 23);
  the agent-only driver on that build is #1387.
- **Windows and `en` accounts only.**
