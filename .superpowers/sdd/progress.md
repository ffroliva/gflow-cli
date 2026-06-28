# Tools Framework PR-1 — SDD Progress Ledger

Plan: docs/superpowers/plans/2026-06-28-tools-framework-pr1.md (council-clean @ f5f2f7a)
Branch: feature/tools-framework
Base for final review (merge-base with develop): 2dc7df0

## Tasks
- [x] Task 1: banned.py (filter) — complete (f5f2f7a..403b419, review clean)
- [x] Task 2: spec.py (ToolSpec/ToolConfig) — complete (403b419..5ab9527, review clean)
- [x] Task 3: creative-director.toml + loader.py — complete (5ab9527..ef2d404, review clean; note: domain() not category-gated — final-review minor)
- [x] Task 4: registry.py — complete (ef2d404..44823a6; controller fixed pyright reportConstantRedefinition, plan had uppercase _REGISTRY)
- [x] Task 5: relocate expander + system_instruction — complete (44823a6..95d1a1f, review clean; only _cli_helpers ref remains for Task 8)
- [x] Task 6: runtime.py (apply_tool) — complete (95d1a1f..38b9dbc, review clean; D2 marker-once verified)
- [x] Task 7: cli_tools.py (gflow tools group) — complete (38b9dbc..1e8e5e0, review clean; impl fixed no-key JSON test via install_log_capture)
- [ ] Task 8: --tool on image t2i
- [ ] Task 9: --tool on video t2v
- [ ] Task 10: MCP gflow_list_tools + tools param
- [ ] Task 11: full-suite verification + CHANGELOG

## Log
(append `Task N: complete (commits <base7>..<head7>, review clean)` as tasks finish)
