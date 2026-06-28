# Task 2 Report — Tool spec models (`tools/spec.py`)

## Test command + output

```
.venv/Scripts/python.exe -m pytest tests/tools/test_spec.py -q
....
4 passed in 0.79s
```

## Files changed

- Created: `src/gflow_cli/tools/spec.py` — `DomainMode`, `ToolConfig`, `ToolSpec` pydantic BaseModels with `ConfigDict(frozen=True)`; `ToolSpec.name` constrained via `Field(pattern=r"^[a-z0-9-]+$")`; `ToolConfig.domain()` and `ToolSpec.supports()` methods.
- Created: `tests/tools/test_spec.py` — 4 tests: round-trip + supports, category validation, image-only/no-video, slug validation.

## TDD steps executed

1. Wrote failing test → FAIL `ModuleNotFoundError: No module named 'gflow_cli.tools.spec'`
2. Wrote minimal implementation
3. Re-ran test → PASS (4 passed)
4. Ruff check --fix + ruff format (1 file reformatted: test kwargs expanded)
5. Re-ran test → PASS (4 passed)
6. Committed

## Commit SHA

`5ab9527`

## Concerns

None.
