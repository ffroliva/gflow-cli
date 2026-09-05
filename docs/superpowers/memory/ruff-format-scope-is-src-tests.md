---
name: ruff-format-scope-is-src-tests
description: Never run `ruff format .` in gflow-cli — it reformats python blocks inside markdown and 40+ unrelated files; CI only checks `src tests`.
---

CI runs exactly `ruff check src tests`, `ruff format --check src tests`, `pyright src`
(.github/workflows/ci.yml). Running `ruff format .` locally instead reformatted **43
files** — including python code blocks *inside* markdown (CONTRIBUTING.md, docs/*.md,
website/docs/*.md) — plus `scripts/`, which CI never checks. `ruff check --fix .` then
reported 52 errors from files outside the CI scope, none of them actionable.

**Why:** the repo is only kept ruff-clean at the CI scope. Everything else is
pre-existing drift, and sweeping it into a feature PR is unwanted scope the reviewer
did not ask for.

**How to apply:** always `uv run ruff check src tests` and `uv run ruff format --check
src tests`. If you already ran the unscoped form, do NOT commit — revert every path
outside your intended change set (`git status --porcelain` → `git checkout --` the
rest) and re-verify the kept files carry only your edits. See also
[[worktree-subagent-build-gotchas]] for the separate local↔CI ruff *version* skew.
