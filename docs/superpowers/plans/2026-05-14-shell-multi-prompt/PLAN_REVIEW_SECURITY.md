# Security Plan Review - Shell Multi-Prompt `t2i`

Verdict: **MAJOR-REVISION**

## Findings

### 1. Positional multi-prompt input is not validated before profile/output/browser work

Severity: **High**

The spec requires all prompt-source parse/validation failures to happen before profile resolution, output directory creation, browser launch, project creation, or Flow API work (`docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md:390`). Required tests also call out `>50` prompts, long prompts, and invalid prompt-file cases before profile resolution and before output directory creation (`docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md:446`).

The plan validates count and text length in `parse_prompt_lines()` for file/stdin (`PLAN.md:861`), but `prompt_items_from_texts()` just wraps positional strings without applying the same count or length checks (`PLAN.md:612`). The planned CLI branch builds positional batch items through that unvalidated helper (`PLAN.md:1177`). As written, 51 positional prompts or a 2001-character positional prompt can reach `_resolve_profile`, `resolve_t2i_batch_output_dir`, and `run_image_batch` (`PLAN.md:1192`) instead of failing during preflight. This also undermines the accidental spend guard in spec C2 (`docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md:131`).

Suggested edit: make `prompt_items_from_texts()` call the same `_validate_prompt_count()` and per-prompt length validation, with source/index diagnostics such as `positional prompt 51` or `positional prompt 2`. Add CLI tests that patch `_resolve_profile` and `resolve_t2i_batch_output_dir` and assert they are not called for 51 positional prompts and a long positional prompt.

### 2. Prompt-source `ConfigurationError`s are planned outside the CLI error handler

Severity: **High**

The planned multi-prompt branch calls `read_prompt_file()` and `parse_prompt_lines(sys.stdin.read(), ...)` directly in the Click command body (`PLAN.md:1161`, `PLAN.md:1170`). Those helpers raise `ConfigurationError` (`PLAN.md:891`), but the existing `run_with_handlers()` boundary only catches `GFlowError` inside its coroutine wrapper (`src/gflow_cli/_cli_helpers.py:112`). The current plan only uses that wrapper in the legacy single-prompt path (`PLAN.md:1136`), not around prompt-source parsing.

This means invalid file/stdin prompt sources may surface as uncaught exceptions under the CLI instead of controlled usage/configuration errors. That risks noisy tracebacks, brittle tests, and accidental path disclosure through exception context. It also conflicts with the planned tests that expect `CliRunner.invoke(..., catch_exceptions=False)` to return a clean result for empty stdin (`PLAN.md:229`).

Suggested edit: either raise `click.UsageError` from CLI-facing prompt-source validation or catch `ConfigurationError` in `t2i` and render it through the established handler before exiting. Add one CLI test each for missing file, non-file, invalid UTF-8, oversized file, empty stdin, too many file prompts, and long file prompts with `catch_exceptions=False`, asserting clean output and no `_resolve_profile`/output-dir call.

### 3. User-facing path and detail rendering is not terminal-safe

Severity: **Medium**

The plan correctly adds `safe_prompt_preview()` for prompt text (`PLAN.md:598`) and uses `rich_escape()` on failure details (`PLAN.md:729`), but it still prints paths and saved-path details raw:

- saved output paths are joined with `str(p)` and inserted into a Rich table without escaping or control-character replacement (`PLAN.md:726`);
- `output_dir` is printed as raw Rich markup (`PLAN.md:1201`);
- existing legacy `t2i` summary also prints raw output paths (`src/gflow_cli/cli_image.py:343`), so the new shared renderer should not copy that weakness.

User-controlled `--out` paths can contain Rich markup delimiters or terminal control characters. The spec requires terminal-safe prompt previews/logs (`docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md:393`), and this same principle should apply to paths and error details because they share the terminal rendering surface.

Suggested edit: add a `safe_terminal_text()` helper for all user-influenced terminal fields, not just prompts. Use it for path details, output directory display, error details, and any future source labels. Tests should include an `--out` path or saved path containing `[red]` and `\x1b[31m`, asserting no raw escape/control characters and escaped Rich markup in rendered output.

### 4. Prompt preview leaves carriage returns and some layout-breaking whitespace intact

Severity: **Medium**

`safe_prompt_preview()` replaces most C0 controls but intentionally leaves `\t`, `\n`, and `\r` (`PLAN.md:565`, `PLAN.md:598`). File/stdin prompts are line-split, but positional prompts may contain quoted newlines or carriage returns, especially on Windows shells or generated command lines. A carriage return in a Rich table can overwrite or confuse terminal output even if it is not sent to the API.

Suggested edit: preserve raw prompt text for API calls, but render previews with carriage returns and newlines visibly encoded or replaced before Rich escaping. If tabs are intentionally allowed, document that choice. Add a test using a positional prompt containing `\r`, `\n`, Rich markup, and ESC, and assert the preview is one safe display line.

### 5. Accidental spend messaging is documented but not tested as preflight output

Severity: **Low**

The plan updates docs with the max fan-out (`PLAN.md:1239`), and prints `up to {len(batch_prompts) * count} image(s)` before execution (`PLAN.md:1197`). That is good, but the security goal here is user awareness before spend. There is no test asserting the pre-execution message appears before `run_image_batch()` is called, nor that it accurately reflects `prompt_count * count`.

Suggested edit: add a CLI wiring test that stubs `run_image_batch`, invokes two prompts with `-n 4`, and asserts output contains `up to 8 image(s)` before the summary. This is minor, but it locks the spend-warning behavior.

## Open Questions / Assumptions

- I assume the final implementation should keep `ConfigurationError` for pure helper tests if useful, but CLI-facing parse failures must still be converted into controlled Click/handler output.
- I assume full output paths are acceptable in success summaries because current CLI behavior already exposes output locations. The issue is terminal safety and avoiding absolute paths in validation errors, not hiding successful output destinations.
- I assume `--stdin` intentionally blocks when no pipe is present, per spec, and no extra TTY detection is required.

## Suggested Plan Edits

- Update Task 4 so `prompt_items_from_texts()` validates count and text length before returning batch items.
- Update Task 5 so all prompt-source validation errors are handled cleanly before profile resolution and before output directory resolution.
- Add tests for positional `>50`, positional over-2000 characters, and all file/stdin invalid cases with `_resolve_profile` and output-dir resolver patched and asserted not called.
- Add a shared display-safety helper and apply it to prompts, error details, `output_dir`, saved paths, and source labels in all new renderer paths.
- Add terminal-safety tests for paths/details and CR/newline prompt previews.
- Add a spend-awareness output test for `prompt_count * -n`.
