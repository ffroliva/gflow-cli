# Scenario: #1249 wave 2 PRs 3 and 4 - split `UiAutomationTransport` and `MigratedComposer`

What could change behaviour when this code moves? Each row was measured on develop at 96339be1
(2026-11-14), not assumed. The method is the same as PR 2's (see PLAN.md, Decision log).

## Coverage map

| Dimension | Relevant? | Why |
|---|---|---|
| D1 Auth & session | yes | `UiAutomationTransport.setup` launches the persistent context and installs the stealth init script (`add_init_script`, ~`ui_automation.py:1111`). It moves verbatim or stays put |
| D2 WAF / reCAPTCHA | no | No token or header code in either class |
| D3 Selectors & locale | yes | Selector constants move into the preamble modules, and the selector registry (`flow_selectors/registry.py`) reads `migrated_composer.COMPOSER` and others by module attribute |
| D4 Batch resume | no | |
| D5 Concurrency | no | No module-level locks, caches or `global` statements in either file (measured) |
| D6 Data layer | no | |
| D7 Errors / exit codes | yes | Error raising moves verbatim. Redaction (`redact_sensitive_text`, `migrated_composer.py` ~1760/~1807) must keep its import in its new module |
| D8 Paths | no | |
| D9 Transport | yes | `ui_automation.py` imports `MENU_ITEM`, `ModelMenuMatcher` and `migrated_images_prefer` from `migrated_composer`, one way. No composer part may import `ui_automation` |
| D10 Headless | no | |
| D11 Input validation | no | |
| D12 Log contract | yes | Event names are literal strings, and the logger name is never rendered (no `add_logger_name`). But 3 tests patch `ui_automation.log` |
| D13 MCP parity | no | No tool, option or payload key is touched |

## Scenario table

| # | Sev | Scenario | Evidence | Handling |
|---|---|---|---|---|
| S1 | Critical | A test patches a module global on the old module, and a moved method reads its own module's copy. The test fails, or passes while testing nothing | ui: `async_playwright` 11, `_count_tabs_locator` 7, `log` 3, `IMAGE_MODEL_OPTION_SELECTORS` 2. mc: 27 names / ~73 `setattr(mc, ...)` sites, incl. `FRAME_*`, `AGENT_ONLY_RELOAD_WAIT_S`, `_project_snapshot`, `_unique_display_name`, and patch strings `SUBMIT_REPLY_BUDGET_S` 3, `run_video` 5, `run_images` 4, `create_project`, `rename_project` | **Reach guard** in the contract test: for each patched (module, name), every function in the chain that reads `name` must have `fn.__globals__ is module.__dict__`. Then either keep the readers in the old module or retarget the patch (import-path only, listed) |
| S2 | Critical | A name importable from `ui_automation` / `migrated_composer` today stops being importable | 9 src importers of ui_automation, 11 of migrated_composer, incl. the selector registry | Contract test pins the full importable set; every moved name is re-exported |
| S3 | Critical | A class-level patch on the final class (`setattr(UiAutomationTransport, "_await_captured")` 8, `_switch_to_image_mode` 3, `_select_image_model` 3; `setattr`/`patch.object(MigratedComposer, ...)` 12 names) stops reaching the code | test greps | Moved bodies keep `UiAutomationTransport._x(...)` / `MigratedComposer._x(...)` and `self._x`; bind the final name into each part's module (PR 2's recipe) |
| S4 | High | A source-scan drift guard keyed by file path silently stops covering moved code | `tests/api/transports/test_navigation_settled.py:94-103` scans only `transports/ui_automation.py` for `goto` without a settle (exemptions keyed `("ui_automation.py", ...)`); `tests/test_browser_proxy.py:170` `SITES["api/transports/ui_automation.py"]` + an rglob drift test | Add each new part file that holds a `goto` to the parametrize list, and move an exemption key with its `goto`. Keep `setup` (the launch site) in `ui_automation.py` |
| S5 | High | Stealth init script or launch args skipped after `setup` moves | `add_init_script` in setup | `setup` stays in `ui_automation.py` (it also reads the patched `async_playwright`) |
| S6 | High | Import cycle: a part imports its aggregator | the aggregators build the chain | Preamble to a leaf module (`_ui_common` / `_mc_common`); `run_video`, `run_images`, `create_project`, `rename_project` (they construct `MigratedComposer()`) stay in `migrated_composer.py` |
| S7 | High | `ensure_editor` and `_absent_chip_verdict` call each other (one SCC) | AST SCC pass | Same part |
| S8 | High | Class-level mutable `_CHARACTER_MODEL_MATCHERS: ClassVar[dict]` (~`ui_automation.py:3733`) splits or duplicates | measured | It moves with its readers and stays on exactly one class |
| S9 | High | The moved redaction call loses its import and raises NameError only on an error path | `redact_sensitive_text` in mc | Contract global-resolution check covers every name a moved function reads |
| S10 | High | The two agents' outputs conflict: PR 4 moves a name PR 3 imports | ui_automation:44 | Every name importable from `migrated_composer` stays importable (S2), and PR 3 does not change that import |
| S11 | High | #987 (+550 lines in migrated_composer.py, idle since 10-27) and #1214 (3 lines) conflict with PR 4 | `gh pr diff` | The refactor goes first (owner delegated). #987 rebases its hunks onto the part modules |
| S12 | Medium | A headless agent's self-written golden master proves nothing | design | The coordinator runs the contract test at its fixture commit against unmodified develop code, checks the fixture has exactly one commit, and then runs e2e (`e2e_auth`, `e2e_image`) on denon82 |
| S13 | Medium | pyright strict `reportPrivateUsage` across private modules | PR 2 precedent | One file-level directive per private part module |
| S14 | Medium | A new part over 1,000 lines, or a file over 2,000 | `check_size.py` | Split by call graph; the ratchet re-keys moved C901/PLR0915 entries |
| S15 | Low | `__qualname__` / `__module__` of a moved method changes | no src or test reads them (CLI UX persona) | Accept |

## Must-cover before merge (Critical + High)

S1-S11. S1 and S4 are the two that no type check or ordinary test sees.
