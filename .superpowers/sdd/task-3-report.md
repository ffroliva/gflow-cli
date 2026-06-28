# Task 3 Report: creative-director.toml + builtin loader

## Status: DONE

## Commit
SHA: ef2d404

## Files Created
- `src/gflow_cli/tools/builtin/__init__.py` — empty resource package marker
- `src/gflow_cli/tools/builtin/creative-director.toml` — verbatim 5-component formula + 15 domains + 13 banned keywords
- `src/gflow_cli/tools/loader.py` — _parse / _validate / load_builtin_tools / _load_dir / load_user_tools (exact code from brief)
- `tests/tools/test_loader.py` — 5 tests (exact code from brief)

## Test Results
All 5 tests pass:
```
5 passed in 0.18s
```

Tests exercised:
1. `test_loads_creative_director_builtin` — all assertions green (title, category, requires_env, 8k in banned_keywords, domain set superset checks, "Subject" in system_template)
2. `test_user_tools_empty_when_dir_absent`
3. `test_user_tools_empty_when_dir_empty`
4. `test_invalid_schema_raises_configuration_error`
5. `test_malformed_toml_raises_configuration_error`

## Load-check Output
```
['creative-director']
```
TOML loadable via importlib.resources without any pyproject.toml changes — hatchling auto-includes non-.py files under `src/gflow_cli`.

## pyproject.toml
**Not touched.** Hatchling's `packages = ["src/gflow_cli"]` already includes all files in the package tree (non-.py included by default). The `importlib.resources.files()` call works correctly in both editable-install (dev) and wheel mode.

## TOML Content Notes
- `system_template`: Contains the verbatim 5-component formula (Subject, Action, Location/Context, Composition, Style) transcribed from `banana-claude/skills/banana/references/prompt-engineering.md` lines 8–65 plus CRITICAL RULES from `SKILL.md`. Does NOT end with "User prompt:" marker (council D2 compliance — `build_instruction` in Task 6 appends it once).
- Image domains (9): cinema, product, portrait, editorial, ui, logo, landscape, infographic, abstract — vocabulary transcribed verbatim from prompt-engineering.md lines 67–123.
- Video domains (6): cinematic, documentary, animation, social, video-product, video-abstract — named with `video-` prefix on conflicting image-domain names to avoid shadowing in `domain()` first-match lookup. All test assertions pass: `{"cinematic", "documentary", "social"} <= names` is satisfied.
- `banned_keywords`: all 13 items from the plan (4k, 8k, ultra HD, high resolution, masterpiece, highly detailed, ultra detailed, trending on artstation, hyperrealistic, ultra realistic, photorealistic, best quality, award winning).

## Concerns
None. Tests pass, lint clean, load-check outputs `['creative-director']`.
