# Issue Ticket: Add Optional `--output` / `-o` Flag to Generation Commands

- **Repo**: `ffroliva/gflow-cli`
- **Type**: Feature Request / UX Improvement
- **Target Release**: `v0.48.0`
- **Origin / Reddit Reference**: Feedback from user `u/_suren` on [`r/SideProject thread 1uiatq9`](https://www.reddit.com/r/SideProject/comments/1uiatq9/):
  > *"The areas I’d test first are failed auth, missing/expired model access, long prompt files, partial downloads, and whether generated file names are predictable enough to script around."*

---

## 1. Problem Statement & Rationale

Currently, `gflow scene create --output <path>` supports specifying an explicit destination file path. However, generation commands (`gflow image t2i`, `gflow image i2i`, `gflow video t2v`, `gflow video i2v`) construct auto-generated timestamped filenames inside `$GFLOW_CLI_OUTPUT_DIR`.

For developers building shell scripts, Makefile targets, or AI workflows around `gflow-cli`, auto-generated filenames force extra steps (globbing `./out/` or parsing JSON output to locate the generated file). Adding an optional `--output` / `-o` parameter makes CLI execution predictable and simple to chain in automated pipelines.

---

## 2. Technical Specification

### Target Commands
Update Click options in:
- `src/gflow_cli/cli_image.py`: `t2i`, `i2i`
- `src/gflow_cli/cli_video.py`: `t2v`, `i2v`

```python
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Explicit output file path for the generated asset.",
)
```

### Requirements & Behavior
1. **Single Asset Output**: If `--output` is provided and a single asset is generated, save the file to `--output` (creating parent directories if missing).
2. **Batch Generation**: If multiple images are requested (`--count > 1`) and `--output` is supplied, use `--output` as a template or stem (e.g. `output_1.png`, `output_2.png`).
3. **SQLite Metadata**: Record the explicit output path in `operations.metadata_json` and SQLite catalog recorder (`OperationRecorder`).
4. **CLI-MCP Symmetry**: Update corresponding MCP tool definitions in `src/gflow_cli/mcp/server.py` to expose the `output` parameter to AI assistant clients.

---

## 3. Verification & Test Plan

- **CLI Unit Test**: Add tests in `tests/cli/test_image.py` and `tests/cli/test_video.py` verifying `--output custom_filename.png` writes to the target path.
- **MCP Parity Test**: `uv run pytest tests/mcp/test_cli_parity.py` must pass.
- **The Impeccable Routine**: `uv run ruff check src tests && uv run pyright src && uv run pytest -q --cov=gflow_cli`.

---

## 4. Agent Handoff Checklist

- [ ] Add `-o` / `--output` Click option to `cli_image.py` (`t2i`, `i2i`)
- [ ] Add `-o` / `--output` Click option to `cli_video.py` (`t2v`, `i2v`)
- [ ] Update MCP tool definitions in `src/gflow_cli/mcp/server.py`
- [ ] Add CLI & MCP unit tests in `tests/cli/` and `tests/mcp/`
- [ ] Verify `tests/mcp/test_cli_parity.py` passes
