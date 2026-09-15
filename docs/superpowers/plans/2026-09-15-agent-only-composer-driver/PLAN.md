# Agent-Only Composer Driver Implementation Plan (#799)

> **For agentic workers:** Run `/gflow:status --feature agent-only-composer-driver` to find
> the next unchecked task. One task at a time. `/gflow:check` before every commit.

**Status:** DRAFT — awaiting answers to the open questions below.

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

## Open questions (answer before EXECUTE)

1. **`--model`**: the agent composer has no per-request model control. Refuse when the
   requested model is not the Agent-settings default (read-only, $0) — *recommended* — or put
   the model name in the directive and log it as unverified?
2. **`--count > 1`**: measure in the e2e and enable if it holds (*recommended*, costs quota
   only for images; video count>1 would cost 7 credits × N), or refuse count>1 in slice 1?
3. **Credit gate**: auto-approve one gate that our own submit produced (*recommended* — the
   classic path already spends without asking), or require an explicit opt-in flag?

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
- [ ] Agent-only fixture → `ensure_editor` returns `"agent_only"` within ≤5 s (no 30 s wait)
- [ ] Classic fixture → `"classic"`; #749 pressed-chip fixture → recovered, `"classic"`
- [ ] Trigger absent from DOM → still `UiSelectorDriftError`

## Task 2 — Red tests: driver behaviour on fixture DOM

**Files:** `tests/api/transports/test_agent_only_composer_driver.py`, BDD offline feature
- [ ] Stale live gate in baseline is not clicked (S1)
- [ ] Second gate → `FlowAgentUiError`, one click recorded (S2)
- [ ] Image produced count ≠ requested → typed failure (S3)
- [ ] Video poster-only is not complete; `<video>` is (S4)
- [ ] Duplicate uuid (grid + chat) → one result (S5); pre-existing media excluded (S6)
- [ ] Turn idle with no gate and no tile → fast typed failure (S7)
- [ ] Unported forms → exit 36 before typing (S11)
- [ ] Signed URL query stripped from logs/errors (S19)
- [ ] No `settings-save-button` click anywhere (grep-style assertion on the module)

## Task 3 — `AgentOnlyComposer` (green Task 2)

**Files:** `agent_only_composer.py`
- [ ] `compose_directive(kind, count, aspect, duration, prompt)`
- [ ] `send(page, directive)`: clear ProseMirror, `insert_text`
- [ ] `submit(page)`: click `flow-generate-icon-button button`; confirm `flow-stop-icon-button`
- [ ] `observe(page, baseline, kind, count, deadline)`: gate handling + tile uuid diff
- [ ] results → `list[GeneratedImage]` (fife_url = signed src, dims from natural size) /
      `GenerationRecord` for the existing `MigratedComposer.download`

## Task 4 — Wire readiness + routing (green Task 1)

**Files:** `migrated_composer.py`
- [ ] Anchor race in `ensure_editor`; return kind
- [ ] `run_images` / `run_video` branch; unported-form checks for the agent composer
- [ ] Model policy per Q1

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
