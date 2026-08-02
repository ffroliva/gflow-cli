# Predict: Add Optional `--output` / `-o` Flag to Generation Commands (#411)

## Verdict: GO
**Confidence:** 9.4/10

## Summary
The proposal adds an optional `--output` / `-o` Click option to `gflow image t2i`, `gflow image i2i`, `gflow video t2v`, and `gflow video i2v`, along with matching `output` parameters in MCP tools (`src/gflow_cli/mcp/server.py`). The five personas evaluated the change across architectural, security, performance, UX, and YAGNI dimensions. All personas approved with no STOP conditions.

## Persona Findings

### Architect — Clean boundary extension (9/10)
- Fits cleanly into CLI layer (`cli_image.py`, `cli_video.py`) and MCP layer (`server.py`).
- No domain core changes needed; output path resolution remains at the orchestration / delivery boundary.
- Preserves CLI-MCP schema symmetry tested by `tests/mcp/test_cli_parity.py`.

### Security / reCAPTCHA — Zero auth impact, safe path handling (10/10)
- Does not touch auth headers, Playwright sessions, or reCAPTCHA tokens.
- Standard path handling via `click.Path(path_type=Path)`. Parent directory creation using `mkdir(parents=True, exist_ok=True)`.

### Performance / Playwright — No performance overhead (10/10)
- No added latency or extra DOM calls. Simple file saving logic.

### CLI UX / Cross-platform — Intuitive scripting & multi-count template support (9/10)
- Harmonizes with `gflow scene create --output <path>`.
- Single-asset outputs (`count == 1`): saves directly to specified `--output` path.
- Multi-asset outputs (`count > 1`): treats `--output` as a template stem (e.g., `output.png` -> `output_1.png`, `output_2.png`).
- Cross-platform: Uses `pathlib.Path` for cross-platform Windows / Posix path handling.

### Devil's Advocate — High-value, lightweight addition (9/10)
- Directly addresses reported community feedback (`u/_suren` on r/SideProject) regarding scripting predictability.
- Implementation is minimal (~30-50 lines total changes), low risk, and easily verified via unit tests.

## High-Confidence Risks & Mitigations
1. **Multi-count collision handling (`count > 1`)**:
   - *Mitigation*: When `count > 1` and `--output path/to/target.ext` is passed, format as `path/to/target_1.ext`, `path/to/target_2.ext`.
2. **Interaction with `--out` (dir option)**:
   - *Mitigation*: `--output` specifies exact file output path and takes precedence over `--out` directory.

## Recommended Next Step
Proceed to **Phase 3 (`/gflow:scenario`)** and **Phase 4 (`/gflow:plan`)** to detail BDD edge cases and build the step-by-step implementation plan.
