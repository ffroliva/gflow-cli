# Predict: #1249 wave 2 PRs 3 and 4 - split `UiAutomationTransport` and `MigratedComposer`

## Verdict: CAUTION

**Confidence:** 7/10. The persona average is 7.4. No persona called STOP. Every persona's
concern has a mitigation inside the proposal, and those mitigations are listed below.

## Summary

Both PRs are pure moves, and no persona found a user-visible change: exit codes, error classes,
the MCP and CLI surfaces, log event names and Flow traffic all stay the same. The risk sits in
the test and CI seams. Some tests patch module globals that a moved method would stop
reading. Some drift guards are keyed by file path. And the two agents run in parallel while
the two modules import each other one way.

## Persona findings

### Architect - CAUTION (7/10)

**PR 3 (`UiAutomationTransport`).** The call graph over 62 methods has no cycles, so a linear,
callee-first chain works. Module code at lines 1-925 is a leaf (`_ui_common`), and no part may
import `ui_automation`. The parts also call `_attach_*` and `_record_generation_request` from
`VideoGenerationMixin`, so each part must inherit the chain below it.

**PR 4 (`MigratedComposer`).** It has 48 methods and no instance state. There is one cycle,
`ensure_editor` and `_absent_chip_verdict`, so those two share a part. `run_video`,
`run_images`, `create_project` and `rename_project` construct `MigratedComposer()`, so they
stay in `migrated_composer.py`; moving them into a part would create a cycle.

Mixins are right for both classes. Composition would rewrite every `self.` call and break 12
class-level patches.

Wanted: a reach guard that checks patched globals mechanically instead of by hand.

### Security / reCAPTCHA - CAUTION (8/10)

Neither class contains token or cookie code. The stealth `add_init_script` call
(`ui_automation.py` ~1111) lives in `setup`. The `redact_sensitive_text` calls in
`migrated_composer.py` (~1760/~1807) need their import in whichever module they move to; a
missing import fails only on an error path. `show_locals=False` is set globally. Profile
isolation is not in these files.

### Performance / Playwright - GO (8/10)

Neither file has a `global` statement, a module-level lock, a cache or a pool. Every
module-level dict is read-only, apart from the one class-level mutable dict
`_CHARACTER_MODEL_MATCHERS: ClassVar`, which must stay on exactly one class. Importing
`ui_automation` takes about 1.17 s, almost all of it third-party, and new modules add
roughly 1-5 ms. MRO cost is negligible.

### CLI and MCP UX / Cross-platform - CAUTION (7/10)

No `__module__`, `__qualname__` or `inspect.getsource` dependency exists in `src` or `tests`. No
`add_logger_name`. Path-keyed guards are `tests/api/transports/test_navigation_settled.py`
(scans `ui_automation.py` only) and `tests/test_browser_proxy.py` `SITES`. Live docs citing
`file:line` (`docs/FLOW_WIRE_RPCS.md`, the selector-registry spec) go stale; spikes are history
and stay. Baseline C901/PLR0915 keys re-key with their functions.

### Devil's Advocate - CAUTION (7/10)

Moving the module code alone gets neither file under 2,000 lines (about 3,830 and 3,050), so
the class split is needed. The gate's value is reviewability, not decoupling, and the PR must
not claim a design gain. A self-written golden master proves only what its author thought to
pin, so it needs an independent check and the $0 e2e tiers. #987 conflicts with PR 4.
Rollback: one commit per part, with a merge commit (not a squash), so each part can be
reverted alone.

## High-confidence risks (flagged by 2+ personas)

1. **Silent patch loss.** A module-global patch stops reaching a method after the method moves.
   Flagged by all five personas.
2. **Path-keyed drift guards.** The guards stop covering a moved `goto` or launch site
   (CLI UX, Architect).
3. **Import cycles** from a part importing its aggregator (Architect, Devil's Advocate).
4. **Trusting the agent.** A self-certified golden master and a self-reported green run
   (Devil's Advocate, Security).

## Conflicts resolved

**Devil's Advocate's "functions, not mixins" for the stateless composer vs Architect's
"mixins".** Mixins win. Functions would rewrite every body and break the class-level patches,
which voids the byte-identical proof. The PR states plainly that this split is about
size, not decoupling.

**Devil's Advocate's "module code first, as its own PR" vs the plan.** This is kept as each
agent's first commit (the preamble move), not as a separate PR. A separate PR would add a
review cycle and prove nothing that the per-commit test gate does not.

**Devil's Advocate's "PR 4 waits for #987".** The owner delegated the decision on
2026-11-14. The refactor goes first. #987 has been idle since 2026-10-27, and it rebases its
hunks onto the part modules.

## Required mitigations before EXECUTE

1. **Reach guard in each contract test.** For every module global a test patches, every
   function in the chain that reads it must satisfy `fn.__globals__ is module.__dict__`.
   Mutation-check it.
2. **Path guards updated.** Add the new part files to the `test_navigation_settled` parametrize
   list wherever they hold a `goto`, and move each exemption key with its `goto`. `setup`, the
   launch site, stays in `ui_automation.py`.
3. **No part imports its aggregator.** `run_video`, `run_images`, `create_project` and
   `rename_project` stay in `migrated_composer.py`. `ensure_editor` and `_absent_chip_verdict`
   share a part.
4. **Every name stays importable.** Every name importable from either module today stays
   importable, and the contract pins the set. PR 3 does not change its import from
   `migrated_composer`.
5. **Coordinator verification.** Before merge, the coordinator runs the contract test at its
   fixture commit against develop's code, checks that the fixture has exactly one commit, and
   runs the e2e tiers `e2e_auth` and `e2e_image` on denon82.

## Recommended next step

`SCENARIO-PR3-PR4.md`. The briefs carry mitigations 1-4, and the coordinator carries 5.
