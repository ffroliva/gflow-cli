---
description: Auto-fix lint and formatting, then report types and tests. Run before every commit.
---

# `/gflow:check` — Quality gates

Run in order. Stop and report if a step fails after the fix pass.

## Steps

**1. Repo hygiene** (read-only — checks tmp/ output rule, secret files, etc.)

```bash
PYTHONUTF8=1 uv run python scripts/ci/check_repo_hygiene.py
```

**2. Auto-fix lint and formatting** (rewrites files in place)

```bash
uv run ruff check --fix src tests
uv run ruff format src tests
```

Report which files were modified. Do NOT stage or commit — leave the diff for review.

**3. Type check** (report only — cannot auto-fix)

```bash
uv run pyright src
```

**4. Tests + coverage** (report only)

```bash
uv run pytest -q --cov=gflow_cli --cov-fail-under=80
```

## Output

- List files changed by the fix pass (empty = nothing needed fixing)
- All pyright errors with `file:line` references
- Pytest summary line and coverage percentage
- Final verdict: all gates pass / which gates failed

## Notes

Ruff fix and format may rewrite multiple files. Always `git diff` before staging.
Pyright errors and test failures require manual intervention — do not attempt silent workarounds.
