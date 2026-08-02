# Scenario: Add Optional `--output` / `-o` Flag to Generation Commands

## Coverage Map
- **Active Dimensions:** D6 (Data layer recording), D7 (Error handling), D8 (Cross-platform paths), D11 (Input validation & count handling), CLI-MCP Symmetry.
- **Skipped Dimensions:** D1, D2, D3, D5, D9, D10 (auth, WAF/reCAPTCHA, Playwright DOM selectors, and web transports are unaffected by local output file naming).

## Scenario Table

| # | Dimension | Scenario | Severity | Expected Behaviour | Test Category |
|---|---|---|---|---|---|
| 1 | D11 Input Validation | Single image (`t2i`/`i2i`, `count=1`) with `--output path/to/res.png` | High | File saved to `path/to/res.png` | Unit |
| 2 | D11 Input Validation | Single video (`t2v`/`i2v`) with `--output path/to/clip.mp4` | High | File saved to `path/to/clip.mp4` | Unit |
| 3 | D11 Input Validation | Multi-image (`t2i`, `count=3`) with `--output stem.png` | High | Files saved to `stem_1.png`, `stem_2.png`, `stem_3.png` | Unit |
| 4 | D8 Cross-Platform | Output path in missing nested directory `--output out/nested/dir/asset.png` | Medium | Parent directories auto-created, file written successfully | Unit |
| 5 | D8 Cross-Platform | Output path with spaces / Windows drive letters (`C:\tmp\my output.png`) | Medium | Handled cleanly via `Path` objects | Unit |
| 6 | D6 Data Layer | Explicit `--output` passed | Medium | Path recorded in catalog operation metadata | Unit |
| 7 | CLI-MCP Symmetry | MCP tool calls (`t2i`, `i2i`, `t2v`, `i2v`) with `output` parameter | High | MCP tools accept `output` argument and pass it to CLI handler | Unit / Integration |
| 8 | CLI Parity Test | `pytest tests/mcp/test_cli_parity.py` | Critical | Passes with 100% parameter symmetry | Integration |

## Must-Cover Before Merge (Critical + High)
1. Single output (`count=1`) writes exact path passed to `--output` across `t2i`, `i2i`, `t2v`, `i2v`.
2. Multi-count output (`count > 1`) uses `--output` as stem template (`stem_1.ext`, etc.).
3. MCP tools in `server.py` support `output` parameter with 100% parity verified via `test_cli_parity.py`.
4. Parent directories automatically created if they do not exist.

## Suggested BDD Scenarios (for `tests/features/`)

```gherkin
Feature: Predictable output filename flag
  Scenario: Single image generation with explicit output flag
    Given a t2i generation request with count 1 and output "custom/path/image.png"
    When the image generation completes
    Then the output file should exist at "custom/path/image.png"

  Scenario: Multi image generation with explicit output template
    Given a t2i generation request with count 2 and output "renders/hero.png"
    When the image generation completes
    Then the output files should exist at "renders/hero_1.png" and "renders/hero_2.png"

  Scenario: Video generation with explicit output flag
    Given a t2v generation request with output "videos/scene.mp4"
    When the video generation completes
    Then the output file should exist at "videos/scene.mp4"
```
