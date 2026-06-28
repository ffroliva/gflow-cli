# Task 5 Report — Relocate expander + domain injection

## Status: DONE

## Commit SHA
`95d1a1f`

## Steps completed

1. `git mv src/gflow_cli/api/prompt_expander.py src/gflow_cli/tools/expander.py`
2. `git mv tests/api/test_prompt_expander.py tests/tools/test_expander.py`
3. Updated import in `tests/tools/test_expander.py` to `from gflow_cli.tools.expander import ...`
4. Existing 10 tests passed after the move.
5. Added `test_custom_system_instruction_is_used` — confirmed FAIL (`unexpected keyword 'system_instruction'`).
6. Added `system_instruction: str | None = None` to `PromptExpander.__init__`; stores `self._instruction = system_instruction or _SYSTEM_INSTRUCTION`; `_build_payload` uses `self._instruction`.
7. All 11 tests pass.
8. Fixed two non-`_cli_helpers.py` stragglers:
   - `tests/tools/test_expander.py` docstring (`:mod:` reference)
   - `src/gflow_cli/data/migrations/0008_add_operation_expanded_prompt.sql` comment

## git grep verification (only _cli_helpers.py remains)

```
src/gflow_cli/_cli_helpers.py:98:    from gflow_cli.api.prompt_expander import PromptExpander
```

## pyright result

```
0 errors, 0 warnings, 0 informations
```

## Test summary

`11 passed in 0.21s` (`tests/tools/test_expander.py` only, as scoped by task brief)

## Concerns

None. All scope boundaries respected: `_cli_helpers.py`, `cli_image.py`, `cli_video.py` untouched.
