# Agent Panel Recovery Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature agent-panel-recovery` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** On flow.google.com, a project left in Flow's agent mode or with the agent panel open
runs on the classic composer again, instead of failing as selector drift (exit 23, #1384) or
being misread as the agent-only composer (exit 36 for i2v, r2v and i2i, #1374). This covers both
Flow builds: the 2026-11-19 chip and the 2026-11-20 switch.

**Architecture:** The fix stays inside the migrated editor gate. `_mc_common.py` gets constants
that match both builds. `_mc_editor_gate.py` reorders `_exit_agent_mode` so that a visible agent
panel is closed *before* the toggle is looked for. All four callers go through `ensure_editor`,
so the CLI and MCP doors are both fixed by the shared transport. The healthy classic path is
unchanged: one count. The agent-only discriminator (`tune`) is not widened, so an unobserved
new-build account with no classic arm fails closed as drift.

**Predict verdict:** CAUTION, 6.6/10. All five personas returned CAUTION, and none returned
STOP. The mitigations are folded into tasks 3–5.

**Scenario analysis:** [`SCENARIO.md`](SCENARIO.md). Must-cover: 1, 2, 3, 4, 5, 6, 7, 9, 10, 12, 16.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| High | A recoverable page is called agent-only, so account defaults are written and billed runs go through the agent chat (#1369) | `agent_only` only from today's discriminator (`tune` + settle), reached only after the panel was closed and the toggle waited for; a failed close or an unreadable probe is drift (scenarios 6–8) |
| High | A real agent-only account (#799) renders the panel open; closing it first could break that path | after the close, "no toggle" falls through to today's check unchanged, so the old-build verdict stays `agent_only` (scenario 10). Unmeasured: the driver on a just-closed panel. The 2026-09-15 spike shows its settings click opens the panel's settings view itself. No account with no classic arm is available to run it |
| High | A fixed 0.2 s sleep re-reads the toggle before Angular re-renders it | `wait_for` the pressed toggle (bounded), not a sleep |
| High | The close or settings click lands on a decoy: a `close` in the panel's prompt box, or the library `settings_2` | close anchor scoped to the panel and excluding `flow-creative-agent-prompt-box`; no `settings_2` anchor added (scenario 9) |
| Medium | Healthy path regresses | panel probe lives only in the failure branch; one count on success (scenario 5) |
| Medium | Two pages on one profile race the account-wide toggle | re-read the trigger before raising "blocked" (scenario 13) |
| Medium | The user's Flow UI changes silently | `warning` event saying Flow remembers the change for the account; KNOWN_ISSUES entry |
| Low | Flow flips back to the old build | constants are unions of both builds; one revertable commit per task |

---

## File structure

### New files
```
tests/features/agent_panel_recovery.feature
  @e2e @e2e_auth: the three live scenarios (panel open, agent mode on, MCP door)
tests/e2e/test_agent_panel_recovery_bdd.py
  binds it against a live profile ($0: layout clicks only, nothing submitted)
tests/features/agent_panel_never_agent_only.feature
  offline: failures after a close stay drift; decoys are never clicked
tests/features/test_agent_panel_never_agent_only_steps.py
  binds it against a fake page
```

### Modified files
```
src/gflow_cli/api/transports/_mc_common.py
  AGENT_MODE_CHIP / AGENT_MODE_CHIP_ANY match both builds (switch scoped to
  flow-agent-mode-switch > button.switch); AGENT_PANEL_CLOSE; a bounded wait constant;
  MIGRATED_SETTINGS_CONTRACT v2; remediation text names the switch and the panel
src/gflow_cli/api/transports/_mc_editor_gate.py
  _close_agent_panel helper; _exit_agent_mode closes a visible panel first, then waits for the
  toggle; a failed close or a missing toggle is "blocked" (drift); trigger re-read before
  "blocked"; warning event on exit
src/gflow_cli/flow_selectors/registry.py
  migrated agent-mode entries and note for the switch
tests/api/transports/test_migrated_composer.py
  fake-page cases for scenarios 1–9, 12, 13
tests/api/transports/fixtures/migrated_composer_contract.json
  re-recorded (RECORD_MIGRATED_COMPOSER_CONTRACT=1), with the reason in the commit
tests/e2e/test_migrated_host_e2e.py, tests/e2e/test_click_attribution_bdd.py
  old-chip literals replaced by the both-build constants
KNOWN_ISSUES.md, docs/MCP.md, CHANGELOG.md
  agent-only entry rewritten; new-build note; Fixed entry
```

---

## Task 1: red offline tests (fake page)

**What:** pin scenarios 1–9, 12 and 13 against fake pages modelled on the spike's DOM, before any
fix.

**Files:** `tests/api/transports/test_migrated_composer.py`, plus the offline feature and binding
listed above.

**Steps:**
- [x] Model the new build's states C1/C2/C3 from the spike's snapshots (switch, panel header
      buttons, prompt box inside the panel, library `settings_2`)
- [x] Old-build panel state (`close`, chip absent, `tune`)
- [x] Run against develop; read each failure line (`UiSelectorDriftError` / `agent_only`), not
      just the red count

**Tests created (red):**
- [x] new-build panel open returns `classic` after the close and the switch
- [x] new-build agent mode on, panel closed, returns `classic`
- [x] old-build panel open returns `classic`
- [x] healthy page: no click, one toggle count (passes on develop too; it guards the regression)
- [x] no toggle after the close → drift, not agent-only
- [x] close click fails → drift; toggle probe `None` → drift
- [x] decoys (prompt-box `close`, library `settings_2`) never clicked
- [x] at most one panel close per gate call under a slow mount
- [x] trigger visible when the click is blocked → `classic`, not "blocked"

## Task 2: red BDD e2e (live, $0)

**What:** the `@e2e @e2e_auth` scenarios, bound from `tests/e2e/`. The Given steps drive the
state the spike drove: the switch, then `right_panel_open`, then a reload. The gate scenarios
call `ensure_editor`. The command scenarios go through the doors the reporter used:
- the CLI client path, `FlowApiClient.generate_video`;
- the MCP tool, `gflow_generate_video`.

Both have the submit aborted, reusing `_abort_video_submits` from
`tests/e2e/test_project_create_e2e.py`, so nothing is billed. Teardown restores classic whatever
the outcome.

**Steps:**
- [x] Feature file and binding per `docs/E2E_TESTING.md` § BDD-bound e2e
- [x] Run on a live profile against develop: red (`UiSelectorDriftError`)
- [x] `tests/features/test_e2e_binding_guard.py` green

## Task 3: constants for both builds

**What:** make the toggle and panel-close constants match both builds, and bump the settings
contract to v2.

**Steps:**
- [x] `AGENT_MODE_CHIP` / `AGENT_MODE_CHIP_ANY`: CSS unions; the switch is scoped to
      `flow-agent-mode-switch button.switch`, because `.agent-action-button` also carries
      `aria-pressed`. Use the descendant form: the spike recorded the nearest custom ancestor,
      not a direct child
- [x] `AGENT_PANEL_CLOSE`: `close` or `right_panel_close` inside `flow-agent-panel`, excluding
      `flow-creative-agent-prompt-box`
- [x] `MIGRATED_SETTINGS_CONTRACT` to v2; registry entries and note; contract fixture
      re-recorded
- [x] `tests/flow_selectors/test_registry.py` uniqueness holds on the spike's DOM

## Task 4: the gate reorder (the fix)

**What:** `_exit_agent_mode` closes a visible panel first (once), waits a bounded time for the
pressed toggle, then runs the #749 click. The happy path is untouched.

**Steps:**
- [x] `_close_agent_panel(page)`, shared by both close sites
- [x] Bounded `wait_for` on the pressed toggle replaces the 0.2 s sleep
- [x] A close that fails, or a probe that cannot be read, maps to `"blocked"` with its
      exception, so it surfaces as drift (exit 23).
- [x] A close that lands but brings no toggle within the wait returns `"absent"`, so
      `_absent_chip_verdict` runs today's agent-only check unchanged. Reading: old build →
      `agent_only` as today; new build → no `tune` → drift.
- [x] Re-read the trigger before raising "blocked"
- [x] Rewrite the comment that claims "the pressed-state chip has already been confirmed"
- [x] Task 1's tests and task 2's e2e go green

## Task 5: messages and observability

**Steps:**
- [x] Remediation text: "close the agent panel, then switch Agent off" (`_mc_common.py`
      `_recovery_detail` and the agent-only / blocked details)
- [x] `migrated.agent_mode_exited` at `warning`, saying Flow remembers this for the account;
      `safe_page_url` only, never panel text
- [x] Offline tests for the wording and the event

## Task 6: MCP surface (no code; verification)

The CLI surface does not change, so no new parameters, codec keys or parity entries are needed.
The MCP door reaches the same gate.

**Steps:**
- [x] Re-read `mcp/tools.py` docstrings that describe agent-only (around line 1028) and
      `docs/MCP.md` around line 549; correct any claim this change makes false
- [x] The task-2 MCP scenario runs green: a twin is a separate surface (AGENTS.md, second law)

## Task 7: existing tests that name the old chip

**Steps:**
- [x] `tests/e2e/test_migrated_host_e2e.py` (`test_e2e_agent_mode_is_left_before_the_readiness_gate`,
      lines ~237 and ~273) and `tests/e2e/test_click_attribution_bdd.py:81` use the both-build
      constants; the control arm that neuters `AGENT_MODE_CHIP` keeps proving the gate is the cause
- [x] Run them live

## Task 8: docs

**Steps:**
- [x] `KNOWN_ISSUES.md`:
  - rewrite the agent-only entry: "no chip" is no longer a cohort signal, the panel is
    recoverable, and the new build fails closed as drift for an account with no classic arm;
  - add a note on the mode switch gflow performs
- [x] `CHANGELOG.md` `[Unreleased]` → Fixed (#1374, #1384)
- [x] `docs/MCP.md` as found in task 6; `generate_website_docs.py` regenerated if a mirrored doc
      changes

## Task 9: gates, review, verification, PR

**Steps:**
- [ ] `/gflow:check` green
- [ ] `/code-review xhigh` and `ponytail-review xhigh` by fresh agents, then one fix round
- [ ] e2e: task 2's feature, task 7's tests, and `tests/e2e/test_agent_only_composer_bdd.py`
      (expected to skip: no agent-only project); live profile restored to classic afterwards
- [ ] Draft PR `Closes #1384`, `Refs #1374`. On 2026-11-20 both reachable profiles were served the new switch build, so #1374's old-build symptom is covered
      offline only. File follow-up issues for:
  - #1374's doctor ask;
  - the new-build agent-only driver (blocked: no account with no classic arm).
- [ ] Ask the reporters of #1384, #1220 and #1190 to retest once released

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green
- [ ] `CHANGELOG.md` `[Unreleased]` updated
- [ ] KNOWN_ISSUES and MCP docs updated
- [ ] BDD feature files cover scenarios 1, 2, 3, 4, 5, 6, 7, 9, 12, 16
- [ ] Task 1 and task 2 were seen red on develop for the expected reason
- [ ] The e2e run pasted in the PR (redacted), live profile left on classic
- [ ] Worktree clean

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-20 | One fix for #1374 and #1384 at the gate | same root (`_exit_agent_mode` order + single-build anchors); all callers funnel through `ensure_editor` | per-caller guards |
| 2026-11-20 | Close the panel before looking for the toggle | spike: the switch is absent while the panel is open (C2/C3) and returns pressed after the close (C4) | treating "panel visible, no toggle" as agent-only (absence-only verdict; Devil's Advocate, Security) |
| 2026-11-20 | After a close with no toggle, keep today's agent-only check rather than raising drift | #799's account rendered the panel; preserving the surface's existing answer is the issue-resolve rule when it cannot be re-measured | "no toggle after close = drift" (would turn working agent-only t2i/t2v into exit 23) |
| 2026-11-20 | Keep `AGENT_ONLY_ANCHOR` on `tune` | the new build's `settings_2` gear also appears on recoverable pages (C1/C4) and on the library header; widening it would weaken the discriminator and point the agent-only driver at an unobserved button | widening to `settings_2` (Architect suggested prompt-box only; Devil's Advocate and UX: drop) |
| 2026-11-20 | Doctor composer verdict deferred | needs a browser, a project and a lease; carries an MCP twin of its own | bundling it here (Architect, UX, Devil's Advocate) |
| 2026-11-20 | No per-profile lock | re-reading the trigger before "blocked" covers the race (modelled offline, not observed live); a lock is unmeasured complexity | `asyncio.Lock` per profile (Performance) |
| 2026-11-20 | The 2026-11-20 canary's two red `test_project_create_e2e` video tests are out of scope | re-run on a live profile the same evening: 2 passed in 106 s on develop's code, so not this root | folding them into task 7 |
| 2026-11-20 | The offline "never agent-only" scenarios are plain tests, not a second feature file | the real-CSS module (`test_agent_panel_recovery.py`) and the fake-page tests cover scenarios 6–9, 12, 13 directly; a Gherkin layer over them would add a binding and no evidence | `tests/features/agent_panel_never_agent_only.feature` |
| 2026-11-20 | `test_click_attribution_bdd.py` keeps its old-chip markup | it serves synthetic markup, and the old chip still matches the both-build union; the live `test_migrated_host_e2e.py` arm was the one that would silently skip on the new build, and was updated | rewriting a synthetic page that is still valid |
| 2026-11-20 | The blocked-recovery guard asserts an unreadable page, not "first probe answers, then dark" | after the panel close the first probe answering `pressed` and the click landing is a genuine recovery, so `classic` is right there | keeping a premise the new order makes false |
| 2026-11-20 | Agent-mode handling moves to `_mc_agent_mode.py` (`AgentModeMixin`), byte-identical | `EditorGateMixin` held readiness, agent mode, dialogs and click diagnostics; agent mode is the responsibility this work grew, so it gets its own module (no god classes) | leaving it in the gate; splitting dialogs and click diagnostics too (that is #1249's scope) |
| 2026-11-20 | Leave the account in classic after recovery | #749 precedent; restoring agent mode would re-break the next run | restoring the user's agent mode after each run (decided 2026-11-20: leave it off) |
