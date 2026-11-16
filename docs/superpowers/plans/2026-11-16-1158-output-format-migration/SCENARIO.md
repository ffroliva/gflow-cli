# Scenario: Issue #1158 Output Flag Breaking Migration (-o/--output json|table|id & --out-file)

Systematic pre-implementation scenario analysis for the breaking output flag migration (#1158, Path A).
Enforces `--out-file` for output file paths, transitions `-o/--output` to select output formats (`json|table|id`), retains `--json` as a backwards-compatible alias, and removes deprecated `output` from MCP generation tools.

---

## Coverage map

- **D4 — Batch manifest & resume**: Active. Multi-item output (e.g. `image t2i -n 4 -o id`) must print each ID on a newline.
- **D7 — Error propagation & exit codes**: Active. Non-format arguments to `-o/--output` must exit 2 with actionable remediation (`use --out-file <path>` or `use --out <dir>`). Conflicting flags (`--json -o table`) must exit 2. Error payloads under `-o json` remain unchanged RFC 9457 Problem Details. Error under `-o id` prints nothing to stdout and outputs error to stderr.
- **D8 — Cross-platform paths**: Active. Shell piping (`gflow project list -o id | xargs ...`) must emit newline-delimited IDs cleanly on Windows (`\n` without encoding corruption or trailing carriage returns).
- **D11 — Input validation & boundary values**: Active. Validates format choices (`json`, `table`, `id`), case-insensitivity, empty list behavior (`0` items emits empty output with exit 0), and multi-item lists.
- **D12 — Observability & structured log contract**: Active. Strict single-channel rule: stdout carries solely the formatted data (JSON, table, or IDs); logs, diagnostics, and errors route exclusively to stderr.
- **D13 — MCP surface parity**: Active. Removal of deprecated `output` parameter from `gflow_generate_image` and `gflow_generate_video` in MCP; `out_file` is the sole destination parameter. Format selector `-o/--output` is documented as CLI-only in parity gates (`_CLI_ONLY_PARAMS`).
- *Skipped dimensions*: D1 (Auth), D2 (WAF/reCAPTCHA), D3 (Selectors), D5 (Page pool), D6 (Data layer migrations), D9 (Transport), D10 (Headless display) are unaffected because output formatting and CLI flag routing are pure presentation/CLI-layer boundaries.

---

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| S1 | D7 Exit codes | User passes a file path to `-o` on a command with `--out-file` (e.g. `gflow video t2v "prompt" -o clip.mp4`) | Critical | Exits 2 (`UsageError`); stderr contains: `Error: --output now selects the format (json|table|id); use --out-file clip.mp4`. No file written, no Flow submission | Unit / CLI test |
| S2 | D7 Exit codes | User passes a directory path to `-o` on `video upscale` (e.g. `gflow video upscale <id> -o ./out`) | High | Exits 2 (`UsageError`); stderr contains: `Error: -o now selects the format (json|table|id); use --out ./out`. No upscale initiated | Unit / CLI test |
| S3 | D7 Exit codes | User passes an invalid format name (e.g. `gflow project list -o yaml` or `-o invalid`) | Medium | Exits 2 (`UsageError` or `BadParameter`); stderr clearly lists valid choices (`json`, `table`, `id`) | Unit / CLI test |
| S4 | D7 Exit codes | Conflicting flags: `--json` with `-o table` or `-o id` | High | Exits 2 (`UsageError`) with explicit conflict message | Unit / CLI test |
| S5 | D11 Input | Redundant flags: `--json` with `-o json` | Low | Accepted without error; format is `json`, exit 0 | Unit / CLI test |
| S6 | D11 Input | Case insensitivity: `-o JSON` or `--output Table` | Low | Normalized to lowercase, accepted, executes correctly | Unit / CLI test |
| S7 | D11 Input | Format `id` on list commands (e.g. `gflow project list -o id`) | High | Emits one ID per line to stdout, no header, no decoration, exit 0 | Integration / CLI test |
| S8 | D11 Input | Format `id` on empty list (e.g. 0 projects in catalog) | Medium | Emits nothing to stdout, exit 0 | Integration / CLI test |
| S9 | D11 Input | Format `id` on entity create (e.g. `gflow project create "name" -o id`) | High | Emits single created ID followed by newline, exit 0 | Integration / CLI test |
| S10 | D4 Batch | Multi-item generation with `-o id` (e.g. `gflow image t2i "prompt" -n 4 -o id`) | High | Emits all 4 generated media IDs (one per line) to stdout | Integration / CLI test |
| S11 | D12 Observability | Error occurring during `-o id` execution | High | Stdout remains empty; error details emitted to stderr; non-zero exit code matching error class | Unit / CLI test |
| S12 | D12 Observability | `--json` backwards compatibility (existing scripts) | Critical | `gflow ... --json` behaves identically to `-o json`, emitting single valid JSON doc | Unit / CLI test |
| S13 | D13 MCP | MCP `gflow_generate_image` or `gflow_generate_video` called with `output` | High | Rejected as unexpected keyword argument; schema no longer advertises `output` | Unit / MCP schema test |
| S14 | D13 MCP | MCP `gflow_generate_image` or `gflow_generate_video` called with `out_file` | Critical | Validated, confined to output directory, successfully writes artifact to requested path | Integration / MCP test |
| S15 | D13 MCP | Option parity gate `test_every_cli_option_reaches_its_mcp_tool` | Critical | Passes with `output_format` registered in `_CLI_ONLY_PARAMS` and stale `output` references removed | Parity gate test |

---

## Must-cover before merge (Critical + High)

1. **S1 & S2 (Loud remediation for legacy `-o PATH`)**: Zero-tolerance for guessing. Any path passed to `-o/--output` must exit 2 with explicit remediation naming `--out-file` or `--out`.
2. **S12 (`--json` alias preservation)**: Existing scripts relying on `--json` must continue to function with 100% byte-for-byte fidelity with `-o json`.
3. **S4 & S5 (Conflict resolution)**: `--json -o json` succeeds; `--json -o table` and `--json -o id` exit 2 with usage error.
4. **S7, S9, S10 (`-o id` formatting)**: Newline-delimited bare IDs on stdout for list, create, and generation commands.
5. **S13 & S14 (MCP breaking cleanup)**: `output` parameter completely removed from MCP tools; `out_file` verified as the sole file path parameter.
6. **S15 (Parity gate green)**: `tests/mcp/test_cli_parity.py` passes with zero unmirrored options and zero stale exemptions.

---

## Deferred (Medium + Low)

- Format `yaml`: explicitly deferred in issue #1158 decision log to avoid adding PyYAML dependency until requested.

---

## Suggested BDD scenarios

```gherkin
@cli @cli_output_format
Feature: Kubectl-aligned output flag formatting (-o/--output json|table|id)

  Scenario: Legacy file path passed to -o fails with remediation
    When I run "gflow video t2v 'a flying eagle' -o clip.mp4"
    Then the exit code should be 2
    And stderr should contain "--output now selects the format (json|table|id); use --out-file clip.mp4"

  Scenario: Legacy directory path passed to video upscale -o fails with remediation
    When I run "gflow video upscale 3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14 -o ./out"
    Then the exit code should be 2
    And stderr should contain "use --out ./out"

  Scenario: List projects with -o id emits bare IDs
    Given multiple projects exist in the catalog
    When I run "gflow project list -o id"
    Then the exit code should be 0
    And stdout should contain only newline-separated project IDs

  Scenario: Backwards compatibility of --json
    When I run "gflow project list --json"
    Then the exit code should be 0
    And stdout should be valid JSON identical to "gflow project list -o json"

  Scenario: Conflicting output format flags are rejected
    When I run "gflow project list --json -o table"
    Then the exit code should be 2
    And stderr should contain "Cannot combine --json with -o/--output"
```

---

## Known-issues cross-reference

- Complements **#1158** (Release N+1 switch).
- Aligns with **N1_PLAN.md** (`docs/superpowers/plans/2026-11-14-1158-1202-release-n/N1_PLAN.md`).
- Respects **AGENTS.md** § MCP & CLI Schema Symmetry.
