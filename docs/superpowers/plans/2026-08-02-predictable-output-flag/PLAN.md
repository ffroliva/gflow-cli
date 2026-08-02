# Predictable Output Flag (`--output` / `-o`) Implementation Plan

> **For agentic workers:** Run `/gflow:status` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Add an optional `--output` / `-o` parameter to `gflow image t2i`, `gflow image i2i`, `gflow video t2v`, and `gflow video i2v` for predictable output file naming during automated scripting, maintaining 100% MCP tool parity.

**Architecture:**
- Extends Click CLI options in `cli_image.py` and `cli_video.py`.
- Updates MCP tool definitions in `src/gflow_cli/mcp/server.py`.
- Keeps domain models unchanged; maps explicit file paths in local image/video download & record handlers.
- Preserves 100% parameter symmetry enforced by `tests/mcp/test_cli_parity.py`.

**Predict verdict:** GO — confidence 9.4/10

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Medium | Multi-count generation (`count > 1`) overwriting same file | Format filename with index suffix (e.g. `stem_1.ext`, `stem_2.ext`) |
| Medium | Missing output parent directories | Automatically create parent directories via `output.parent.mkdir(parents=True, exist_ok=True)` |
| High | MCP parameter drift | Update `src/gflow_cli/mcp/server.py` and enforce via `tests/mcp/test_cli_parity.py` |

---

## File structure

### New files
```
docs/superpowers/plans/2026-08-02-predictable-output-flag/SCENARIO.md
  BDD scenario matrix for explicit output flag
docs/superpowers/plans/2026-08-02-predictable-output-flag/PLAN.md
  Task-by-task execution plan
tests/cli/test_predictable_output.py
  Unit & integration tests for --output flag on t2i, i2i, t2v, i2v
```

### Modified files
```
src/gflow_cli/cli_image.py
  Add -o/--output option to t2i and i2i commands and handlers
src/gflow_cli/cli_video.py
  Add -o/--output option to t2v and i2v commands and handlers
src/gflow_cli/mcp/server.py
  Add output parameter to t2i, i2i, t2v, i2v MCP tools
docs/USAGE.md
  Document --output / -o usage examples
```

---

## Task 1 — Unit & MCP Test Scaffold (Red Tests)

**What:** Add unit and integration tests asserting `--output` / `-o` functionality on image and video CLI commands and verifying MCP schema parity.

**Files:**
- `tests/cli/test_predictable_output.py` — Test single and multi-count file naming, nested directories, and catalog metadata recording.

**Steps:**
- [ ] Create `tests/cli/test_predictable_output.py`.
- [ ] Add red unit tests for `gflow image t2i --output ...` and `i2i --output ...`.
- [ ] Add red unit tests for `gflow video t2v --output ...` and `i2v --output ...`.
- [ ] Verify test suite fails before implementation.

**Tests created (red):**
- [ ] `test_t2i_explicit_output_single_file` — Assert `count=1` writes directly to exact `--output` path.
- [ ] `test_t2i_explicit_output_multi_count` — Assert `count=2` writes to `stem_1.ext` and `stem_2.ext`.
- [ ] `test_video_explicit_output_file` — Assert `t2v` and `i2v` write output to exact `--output` path.

---

## Task 2 — CLI Implementation (`cli_image.py` & `cli_video.py`)

**What:** Add `-o` / `--output` Click options and wire custom target paths through `_download_images` / `_run_t2i` / `_run_i2i` and `_generate_and_report`.

**Files:**
- `src/gflow_cli/cli_image.py`
- `src/gflow_cli/cli_video.py`

**Steps:**
- [ ] Add `@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Explicit output file path for the generated asset.")` to `t2i` and `i2i` in `cli_image.py`.
- [ ] Update `_download_images` and `_run_t2i` / `_run_i2i` to accept `output_path: Path | None`.
- [ ] Implement single output path saving and multi-count stem suffixing (`stem_1.ext`, `stem_2.ext`).
- [ ] Add `-o` / `--output` option to `t2v` and `i2v` in `cli_video.py`.
- [ ] Update `_generate_and_report` in `cli_video.py` to route explicit output paths.
- [ ] Verify Task 1 unit tests pass (green).

---

## Task 3 — MCP Tool Symmetry (`src/gflow_cli/mcp/server.py`)

**What:** Expose `output` parameter in MCP tool definitions for `t2i`, `i2i`, `t2v`, `i2v` to ensure CLI-MCP parameter parity.

**Files:**
- `src/gflow_cli/mcp/server.py`
- `tests/mcp/test_cli_parity.py`

**Steps:**
- [ ] Add `output: str | None = None` parameter to `t2i`, `i2i`, `t2v`, and `i2v` MCP tool signatures.
- [ ] Forward `output` to underlying CLI invocation handlers.
- [ ] Run `uv run pytest tests/mcp/test_cli_parity.py` and verify parity checks pass cleanly.

---

## Task 4 — Documentation Updates

**What:** Document the `--output` / `-o` flag in `docs/USAGE.md`.

**Files:**
- `docs/USAGE.md`

**Steps:**
- [ ] Update image generation examples in `docs/USAGE.md` with `--output` / `-o` usage.
- [ ] Update video generation examples in `docs/USAGE.md` with `--output` / `-o` usage.

---

## Task 5 — Quality Gates & Verification (`/gflow:check`)

**What:** Run the full local quality gate suite before declaring completion.

**Steps:**
- [ ] Run `uv run python scripts/ci/check_repo_hygiene.py`
- [ ] Run `uv run python scripts/ci/check_doc_links.py`
- [ ] Run `uv run python scripts/ci/check_website_docs_pii.py`
- [ ] Run `uv run ruff check src tests`
- [ ] Run `uv run ruff format --check src tests`
- [ ] Run `uv run pyright src`
- [ ] Run `uv run python -m pytest -q --cov=gflow_cli`

---

## Definition of Done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (hygiene, doc links, pii, ruff, pyright, pytest >= 80% coverage)
- [ ] `tests/mcp/test_cli_parity.py` passes 100%
- [ ] Docs updated in `docs/USAGE.md`
