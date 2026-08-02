# Issue Assessment: #411 — Optional `--output` / `-o` Flag for Generation Commands

**Assessment of #411: CONFIRMED-FEATURE** (confidence 10/10)

**Restated claim:** Add optional `--output` / `-o` parameter to `gflow image t2i`, `gflow image i2i`, `gflow video t2v`, and `gflow video i2v` to allow specifying explicit output file paths for predictable scripting.

## Findings:
- [`src/gflow_cli/cli_image.py`](file:///C:/development/github/gflow-cli/src/gflow_cli/cli_image.py#L725): `t2i` and `i2i` accept `--out` (a directory path where auto-named files land), but do not allow specifying an exact single output file path.
- [`src/gflow_cli/cli_video.py`](file:///C:/development/github/gflow-cli/src/gflow_cli/cli_video.py): `t2v` and `i2v` accept `--out` (a directory path), but do not accept an explicit `--output` / `-o` target file path.
- [`src/gflow_cli/mcp/server.py`](file:///C:/development/github/gflow-cli/src/gflow_cli/mcp/server.py): MCP tools for `t2i`, `i2i`, `t2v`, and `i2v` currently mirror the CLI options and must be updated to expose `output` for full CLI-MCP schema symmetry.
- [`src/gflow_cli/cli_scene.py`](file:///C:/development/github/gflow-cli/src/gflow_cli/cli_scene.py#L66): `gflow scene create` already implements `--output` / `-o`, establishing the precedent for explicit file naming in the CLI.

## Root Cause / Hypothesis:
Generation commands (`t2i`, `i2i`, `t2v`, `i2v`) were built assuming users would save multiple or single files into timestamped/UUID filenames inside `$GFLOW_CLI_OUTPUT_DIR` or an `--out` directory. When integrating `gflow-cli` into shell scripts, Makefile targets, or automated pipelines, non-deterministic output filenames force downstream steps to glob output directories or parse JSON logs.

Adding `--output` / `-o` path support simplifies downstream asset handling:
1. Single generation output (`count == 1`): saves directly to `--output` (creating parent directories if missing).
2. Multi-generation output (`count > 1`): uses `--output` as stem/template (e.g., `stem_1.ext`, `stem_2.ext`).
3. Catalog recording: records explicit output path in `operations.metadata_json` and SQLite catalog.
4. CLI-MCP parity: updates MCP tools in `server.py` and passes `tests/mcp/test_cli_parity.py`.

## E2E Gate:
- Pure Python / CLI argument parsing & path resolution logic (browser-free): Verifiable offline via unit tests, pyright, ruff, and `test_cli_parity.py`.

## Handoff & Next Steps:
Proceed through the Standard Workflow Sequence:
- **Phase 2**: `/gflow:predict` — Adversarial audit of `--output` vs `--out` interactions, multi-count template behavior, and path overwrites.
- **Phase 3**: `/gflow:scenario` — BDD edge cases (missing parent dir, extension mismatch, count > 1 formatting).
- **Phase 4**: `/gflow:plan` — Task-by-task execution plan.
- **Phase 5**: `/gflow:pr-council-review` — Pre-implementation council review.
- **Phases 6-9**: Implementation, TDD checks, PR creation, and SonarCloud zero-issue gate.
