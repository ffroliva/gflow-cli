# Task 6 Report — Runtime (`tools/runtime.py`)

## Status: DONE

## Commit SHA
`38b9dbc` — feat(tools): runtime apply_tool (instruction build + banned strip)

## Files Created
- `src/gflow_cli/tools/runtime.py`
- `tests/tools/test_runtime.py`

## Pyright Result
`0 errors, 0 warnings, 0 informations` on `runtime.py` (ruff also auto-fixed `Mapping` import to `collections.abc.Mapping` for strict compliance).

## Tests
`2 passed in 0.17s` — `test_build_instruction_appends_domain`, `test_apply_tool_strips_banned_from_output`

## Concerns
None. The ruff auto-fix moved `Mapping` from `typing` to `collections.abc.Mapping` (correct for Python 3.11+). One E501 line-too-long in the test was split cleanly. Implementation matches the plan's exact code exactly.
