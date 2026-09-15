# Agent-Only Composer Driver Implementation Plan (#799)

> **For agentic workers:** Run `/gflow:status --feature agent-only-composer-driver` to find
> the next unchecked task. One task at a time. `/gflow:check` before every commit.

**Status:** APPROVED WITH DECISIONS (2026-09-15, account owner):
1. **Model / aspect / count are set in Agent settings, not asked for in the directive.**
   Measured $0 (`scripts/dev/spike_agent_settings_defaults.py`, capture `…_190331.json`):
   the video model menu is `[role=menuitem]` (Omni 1.1 Flash, Veo 3.1 Lite/Fast/Quality — the
   existing `VIDEO_MODEL_MENU_MATCHERS` apply), aspect/count are `button[role=radio]
   [aria-checked]` in `mat-button-toggle-group`s (DOM order: image aspect, image count, video
   aspect, video count), Save closes the pane and **persists across reload**, and a
   set → Save → restore → Save round trip returned the account to its originals. The driver
   snapshots the pane, applies the request, Saves, generates, and **restores in `finally`**.
   This supersedes Predict's "never click Save" mitigation.
2. **Count > 1:** measure in the image e2e, enable if it holds, refuse otherwise.
3. **Credit gate is a user choice:** `GFLOW_CLI_AGENT_CONFIRM=account|always|never`
   (default `account` = leave as found). Applied to the pane before a run and **not
   restored** — it is a standing preference. With a gate present gflow approves exactly one
   its own submit produced and stops on a second; with `never` there is no gate and the
   post-hoc count check is the only guard. Setting shipped in Task 0 (config + docs + test).

**Goal:** `gflow image t2i` and `gflow video t2v` (CLI and MCP) succeed on accounts Flow serves
the agent-only composer, instead of exiting 25.

**Architecture:** `MigratedComposer.ensure_editor` returns which composer it found
(`"classic"` / `"agent_only"`) instead of raising for the agent-only case; `run_images` /
`run_video` branch to a new `AgentOnlyComposer` in `api/transports/agent_only_composer.py`.
Completion is observed in the DOM (new `flow-content.google/{image,video}/<uuid>` tiles vs a
pre-submit baseline), not the wire. Return types are unchanged, so recorder, CLI, MCP and
worker are untouched.

**Predict verdict:** CAUTION — 6/10 ([PREDICT.md](PREDICT.md)) · Scenarios: [SCENARIO.md](SCENARIO.md)

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Approving a stale or extra credit gate | Baseline-diffed gate; one approval per run; 2nd gate → fail |
| High | Agent ignores count/aspect/model | Post-hoc count + orientation checks; model per Q1 |
| High | 30 s dead wait before detection | Race trigger-visible vs agent-prompt-box-visible |
| Medium | Directive phrasing on non-en accounts unmeasured | Record in KNOWN_ISSUES; e2e on en only (named blocker: no non-en cohort profile) |

## New risks from decision 1
| Severity | Risk | Mitigation |
|---|---|---|
| High | Run dies between Save and restore → account left on the requested defaults | Restore in `finally`; log `migrated.agent_only.defaults_restore_failed` with the originals so the user can reset by hand |
| Medium | User edits settings in the web UI mid-run; restore overwrites it | Accepted; documented in KNOWN_ISSUES |
| Medium | Image model menu entries unmeasured (video only was opened) | Measure in Task 4's e2e before enabling image `--model` |

---

## File structure

### New files
```
src/gflow_cli/api/transports/agent_only_composer.py
  AgentOnlyComposer: directive, send, submit, gate, observe tiles, build results
tests/api/transports/test_agent_only_composer_driver.py
  fixture-DOM (real Chromium, set_content) tests for gate/baseline/dedupe/poster
tests/features/agent_only_composer.feature + test_agent_only_composer_steps.py
tests/e2e/agent_only_composer_e2e.feature + test_agent_only_composer_bdd.py
```

### Modified files
```
src/gflow_cli/api/transports/migrated_composer.py
  ensure_editor returns composer kind; anchor race; run_images/run_video branch
tests/api/transports/test_agent_only_composer.py
  assert routing instead of the exit-25 raise
KNOWN_ISSUES.md, docs/MCP.md (if it mentions the cohort), CHANGELOG.md, src/gflow_cli/errors.py docstring
```

---

## Task 1 — Red tests: readiness returns the composer kind

**Files:** `tests/api/transports/test_agent_only_composer.py`
- [x] Agent-only fixture → `ensure_editor` returns `"agent_only"` after `AGENT_ONLY_EARLY_S` (5 s), not 30 s (real Chromium via `page.route`)
- [x] Classic fixture → `"classic"`; #749 pressed-chip fixture → recovered, `"classic"`
- [x] Trigger absent from DOM → still `UiSelectorDriftError` (existing tests unchanged)
- [x] Callers keep exit 25 via `_agent_only_not_driven` until Task 4 routes to the driver

## Task 2 — Red tests: driver behaviour on fixture DOM

**Files:** `tests/api/transports/test_agent_only_composer_driver.py`, BDD offline feature
- [x] Stale live gate in baseline is not clicked (S1)
- [x] Second gate → `FlowAgentUiError`, one click recorded (S2)
- [x] Image produced count > requested → typed failure (S3)
- [x] Video poster-only is not complete; `<video>` is (S4)
- [x] Duplicate uuid (grid + chat) → one result (S5); pre-existing media excluded (S6)
- [x] Turn idle with no gate and no tile → fast typed failure (S7)
- [x] Unported forms → exit 36 before touching the page (S11)
- [x] Signed URL query absent from error text (S19)
- [x] ~~No Save click~~ superseded by decision 1: apply → Save → restore, confirm kept; restore runs when generation fails
- [x] Mutation check: 8/8 guards killed (stale gate, second gate, poster, media baseline, over-count, idle, confirm, restore-on-failure)

## Task 3 — `AgentOnlyComposer` (green Task 2)

**Files:** `agent_only_composer.py`
- [x] `compose_image_directive` / `compose_video_directive`
- [x] `AgentOnlyComposer.generate`: clear ProseMirror, `insert_text`, submit, gate handling + tile uuid diff
- [x] results → `list[GeneratedImage]` (fife_url = signed src) / `GenerationRecord` for the existing `MigratedComposer.download`
- [x] Slice-1 limits: video count must be 1 (`VideoResult` carries one clip)
- [x] Video readiness re-measured after a live timeout (spike § "Readiness, measured"): ready =
      no `flow-pending-tile` + newest finished tile changed + a uuid named by the tile or the
      chat option; opaque `/asb/` tiles are hovered for the `<video>` src; `download_video`
      follows redirects only to `*.googlevideo.com` / allowed Google hosts and checks `ftyp`

## Task 4 — Wire readiness + routing (green Task 1)

**Files:** `migrated_composer.py`
- [x] Early wait in `ensure_editor`; return kind (Task 1)
- [x] `run_images` / `run_video` route to `run_agent_images` / `run_agent_video` (routing test)
- [x] `apply_defaults` / `restore_defaults` reusing the `ModelMenuMatcher` tables; `finally` restore

## Task 5 — CLI surface

No new options. `--help` unchanged. Verify exit-code mapping only.

## Task 6 — MCP surface mirror

- [ ] Six mirror axes (`skills/check/SKILL.md` step 1b): no new keys; confirm docstrings of
      `gflow_generate_image` / `gflow_generate_video` make no "cannot drive" claim
- [ ] MCP e2e: one image through the MCP tool on the cohort profile (S20)

## Task 7 — Docs

- [ ] KNOWN_ISSUES: move t2i/t2v out of Mitigated; list still-unported forms + non-en caveat
- [ ] `errors.py` / remediation text no longer says "no driver"
- [ ] CHANGELOG `[Unreleased]`

## Task 8 — Gates, e2e, live-verify

- [ ] `/gflow:check` green
- [ ] `pytest -m e2e` image BDD (quota) + video BDD (`GFLOW_CLI_E2E_RUN_VIDEO=1`, ~7 credits)
- [ ] `/gflow:branch-review`
- [ ] Ask the user before opening the PR (`Refs #799` unless every form is covered)

## Definition of done
- [ ] All tasks checked · `/gflow:check` green · CHANGELOG updated
- [ ] BDD covers all Must-cover scenarios · e2e image + video + MCP run and pasted
- [ ] No `# TODO` without an issue link
