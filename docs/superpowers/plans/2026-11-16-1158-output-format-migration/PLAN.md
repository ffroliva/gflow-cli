# Plan: Issue #1158 Output Flag Breaking Migration (-o/--output json|table|id & --out-file)

Status: **COMPLETE**  
Tracking Issue: **#1158** (Release N+1 breaking switch)  
Reference: [N1_PLAN.md](../2026-11-14-1158-1202-release-n/N1_PLAN.md), [SCENARIO.md](SCENARIO.md)  
Methodology: **Uncle Bob's reduced principles (/ub-mode)** — First Make it Work, Then Make it Right, Follow Practice.

---

## Objective & Problem Statement

`gflow-cli` historically used `-o/--output PATH` for generated media/export file paths on 9 commands, while `--json` toggled machine-readable JSON output across 29 commands. In Release N (v0.99.0+), `--out-file PATH` was introduced and `-o/--output PATH` was deprecated with a stderr warning.

In this breaking release (Path A of Issue #1158):
1. **`-o/--output [json|table|id]`** becomes the universal format selector across commands emitting structured data, aligning with `kubectl` and `gh`.
2. **`--json`** remains as a backwards-compatible alias for `-o json`.
3. **`--out-file PATH`** is strictly enforced for file paths. Any legacy attempt to pass a file or directory path to `-o/--output` fails immediately with exit code 2 (`click.UsageError`) and actionable remediation naming `--out-file` or `--out`.
4. **`-o id`** emits newline-separated bare IDs to stdout (kubectl `-o name` convention) with no headers or decoration.
5. **MCP parity**: the deprecated `output` parameter is completely removed from `gflow_generate_image` and `gflow_generate_video`; `out_file` is the sole destination parameter.

---

## Architectural Design (First Principles & Domain Modeling)

### 1. Domain Types & Formats
```python
from enum import StrEnum

class OutputFormat(StrEnum):
    JSON = "json"
    TABLE = "table"
    ID = "id"

VALID_OUTPUT_FORMATS: frozenset[str] = frozenset({"json", "table", "id"})
```

### 2. Universal Click Decorator: `output_format_option`
Located in `src/gflow_cli/_cli_helpers.py`.
- Registers `-o, --output` with custom Click `OutputFormatOption` / parameter type.
- Registers `--json` as a boolean flag alias for `-o json`.
- Case-insensitive parsing (`json`, `JSON`, `Table` -> lowercase).
- Conflict guard: combining `--json` with `-o table` or `-o id` raises `click.UsageError("Cannot combine --json with -o/--output {format}.")`.
- Non-format detection & remediation:
  - When a command supports `--out-file` and a non-format string is given to `-o/--output`:
    `click.UsageError("--output now selects the format (json|table|id); use --out-file <val>")`
  - When `video upscale` receives a non-format string for `-o`:
    `click.UsageError("-o now selects the format (json|table|id); use --out <val>")`
  - When another command receives an invalid format:
    `click.UsageError("Invalid value for '-o' / '--output': '{val}' is not one of 'json', 'table', 'id'.")`
- Injects `output_format: str = "table"` (and preserves `as_json: bool` where helpful).

### 3. Removal of Path Aliases
- `out_file_option` in `src/gflow_cli/_cli_helpers.py`: drops `deprecated_alias_option`; registers only `--out-file`.
- `upscale_out_dir_option`: drops `-o` alias; registers only `--out`.

### 4. MCP Parity (Task 6 Non-Negotiable)
- `src/gflow_cli/mcp/tools.py`: remove `output` parameter from `gflow_generate_image` and `gflow_generate_video`.
- `src/gflow_cli/mcp/out_file.py`: retire `DeprecatedOutputParam`.
- `tests/mcp/test_cli_parity.py`: add `output_format` to `_CLI_ONLY_PARAMS` (MCP always returns structured data); remove stale `output` exemptions.

---

## Tasks Breakdown

### Task 1: TDD Red Test Scaffold for Output Format & Remediation
- **Files**:
  - `tests/cli/test_output_format.py` (new)
- **Checklist**:
  - [x] Test `output_format_option` directly with runner:
    - `-o json`, `--output json`, `--json` emit JSON format
    - `-o table`, `--output table`, default (no flag) emit table format
    - `-o id`, `--output id` emit id format
    - `-o JSON` (case-insensitivity)
    - `--json -o json` succeeds
    - `--json -o table` and `--json -o id` raise `click.UsageError` (exit 2)
  - [x] Test remediation for legacy path values passed to `-o/--output`:
    - Value with extension (e.g. `-o clip.mp4`, `-o image.png`) on an `--out-file` command exits 2 with `--output now selects the format (json|table|id); use --out-file <path>`
    - Value on `video upscale -o ./out` exits 2 with `-o now selects the format (json|table|id); use --out ./out`
    - Value on command with no file output (e.g. `gflow project list -o clip.mp4`) exits 2 naming invalid choice
  - [x] Run pytest to verify all new tests fail with expected errors (RED).

### Task 2: Core Decorator Implementation & Helper Inversion
- **Files**:
  - `src/gflow_cli/_cli_helpers.py`
- **Checklist**:
  - [x] Implement `OutputFormatOption` and `output_format_option(default="table", allow_id=True, has_out_file=False)`.
  - [x] Invert `out_file_option` to bind only `--out-file`.
  - [x] Invert `upscale_out_dir_option` to bind only `--out` (with `-o` remediation).
  - [x] Run `tests/cli/test_output_format.py` to confirm GREEN.

### Task 3: MCP Surface Cleanup (Parity Gate Compliance)
- **Files**:
  - `src/gflow_cli/mcp/tools.py`
  - `src/gflow_cli/mcp/out_file.py`
  - `tests/mcp/test_cli_parity.py`
  - `tests/mcp/test_server.py`
- **Checklist**:
  - [x] Remove `output: DeprecatedOutputParam` from `gflow_generate_image` and `gflow_generate_video` signatures.
  - [x] Simplify `_resolve_out_file` to validate and resolve `out_file` only.
  - [x] Clean up docstrings in `mcp/tools.py`.
  - [x] Add `output_format` to `_CLI_ONLY_PARAMS` in `tests/mcp/test_cli_parity.py`.
  - [x] Run `uv run pytest tests/mcp/test_cli_parity.py tests/mcp/test_server.py` to ensure zero drift.

### Task 4: Roll out `output_format_option` to Entity & Catalog Commands
- **Files**:
  - `src/gflow_cli/cli_project.py`
  - `src/gflow_cli/cli_character.py`
  - `src/gflow_cli/cli_models.py`
  - `src/gflow_cli/cli_credits.py`
  - `src/gflow_cli/cli.py` (`auth list`, `auth renew`)
  - `src/gflow_cli/cli_data.py` (`data list *`, `data stats`, `data download`, `data sync`, `data errors export`)
  - `src/gflow_cli/cli_instructions.py` (`instructions list`)
  - `src/gflow_cli/cli_tools.py` (`tools list`, `tools run`)
  - `src/gflow_cli/cli_cancel.py`
  - `src/gflow_cli/cli_capabilities.py`
  - `src/gflow_cli/cli_doctor.py`
  - `src/gflow_cli/cli_docs.py`
  - `src/gflow_cli/cli_telemetry.py`
  - `src/gflow_cli/cli_update.py`
- **Checklist**:
  - [x] Replace `@click.option("--json", ...)` with `@output_format_option(...)`.
  - [x] For commands producing IDs:
    - Lists (`project list`, `character list`, `models list`, `data list *`, `instructions list`, `tools list`): emit newline-separated IDs under `-o id`.
    - Creates (`project create`, `character create`): emit created ID under `-o id`.
    - Shows / Status (`project show`, `character show`, `models show`, `cancel`): emit ID under `-o id`.
  - [x] For commands without an ID entity (`doctor`, `capabilities`, `stats`): reject or ignore `id` format cleanly with exit 2 or fallback.
  - [x] Update unit tests in `tests/cli/` to test `-o id`, `-o json`, `-o table`.

### Task 5: Roll out `output_format_option` to Generation Commands
- **Files**:
  - `src/gflow_cli/cli_image.py` (`t2i`, `i2i`, `generate`, `gen`)
  - `src/gflow_cli/cli_video.py` (`t2v`, `i2v`, `r2v`, `chain`, `extend`, `generate`, `gen`, `upscale`)
  - `src/gflow_cli/cli_scene.py` (`scene create`)
- **Checklist**:
  - [x] Apply `@output_format_option(has_out_file=True)` to generation commands.
  - [x] For `image t2i`/`i2i`/`generate`:
    - `-o id`: emit generated media IDs (one per line, e.g. for `-n 4`).
    - `-o <path>`: loud remediation exit 2.
    - `--out-file <path>`: saves file as requested.
  - [x] For `video t2v`/`i2v`/`r2v`/`chain`/`extend`/`generate`:
    - `-o id`: emit video operation/media ID.
    - `-o <path>`: loud remediation exit 2.
    - `--out-file <path>`: saves file as requested.
  - [x] For `video upscale`:
    - `-o <path>`: loud remediation exit 2 (`use --out <dir>`).
  - [x] For `scene create`:
    - `@output_format_option(has_out_file=True)`: `-o id` emits scene/media ID.
  - [x] Update integration tests in `tests/cli/test_cli_image.py`, `tests/cli/test_cli_video.py`.

### Task 6: Invert Click-Tree & Deprecation Tests
- **Files**:
  - `tests/cli/test_out_file_deprecation.py` -> update or rename to `tests/cli/test_output_format_tree.py`
- **Checklist**:
  - [x] Click-tree guard: walk every leaf of `main`.
  - [x] Assert that EVERY leaf command's `-o` or `--output` option is an `OutputFormatOption`.
  - [x] Assert that NO command in the Click tree accepts a file or directory path via `-o` or `--output`.
  - [x] Assert that `--out-file` is the only file destination flag across the 9 media/export commands.

### Task 7: Documentation, Skills, and CHANGELOG
- **Files**:
  - `CHANGELOG.md`
  - `docs/USAGE.md`
  - `docs/USER_GUIDE.md`
  - `docs/MCP.md`
  - `skills/gflow-cli/SKILL.md`
  - `plugins/gflow/skills/gflow-cli/SKILL.md`
- **Checklist**:
  - [x] Add **Breaking Changes** section to `CHANGELOG.md` documenting:
    - `-o/--output` now selects format (`json|table|id`).
    - `--out-file PATH` is strictly required for file paths; legacy `-o PATH` exits 2.
    - MCP `output` removed from `gflow_generate_image` / `gflow_generate_video`.
  - [x] Update all command examples in `docs/USAGE.md` and `docs/USER_GUIDE.md` to reflect `-o json|table|id`.
  - [x] Update `skills/` and synchronize plugin byte copy (`scripts/ci/generate_plugin_skills.py`).

### Task 8: CI Quality Gates & SonarCloud Verification
- **Checklist**:
  - [x] Run full mechanical test suite:
    - `python scripts/ci/check_repo_hygiene.py`
    - `python scripts/ci/check_size.py`
    - `python scripts/ci/check_doc_links.py`
    - `python scripts/ci/check_website_docs_pii.py`
    - `python scripts/ci/generate_website_docs.py --check`
    - `python scripts/ci/check_council_memory.py`
    - `python scripts/ci/generate_plugin_skills.py --check`
    - `ruff check src tests`
    - `ruff format --check src tests`
    - `pyright src`
    - `pytest -q --cov=gflow_cli`
  - [x] Verify zero regressions and 80%+ test coverage.
