# Follow-Up Plan Review: Shell Multi-Prompt `t2i`

**Verdict:** MINOR-EDITS

## Findings

### 1. Summary terminal-safety test expects Rich escape backslashes after rendering

**Severity:** Minor

**Citations:** `PLAN.md` Step 4.6; prior security review finding 3.

The revised plan correctly adds `safe_terminal_text()` and applies it to saved paths, errors, prompt previews, and `output_dir`. However, the proposed `test_render_summary_escapes_saved_paths_and_errors()` asserts that rendered console output contains `"\\[red]"`.

That is likely too strict for Rich-rendered output. `rich.markup.escape("[red]")` returns a backslash-escaped string before rendering, but Rich consumes that escape marker when printing and emits literal `[red]` text. The important safety checks are that raw ANSI/control sequences are absent and that the text is not interpreted as styling.

**Required edit:** Change the rendered-summary assertion from expecting `"\\[red]"` to an assertion that matches rendered Rich behavior, for example `"[red]" in output` plus `"\x1b[31m" not in output`. Keep the direct `safe_terminal_text()` unit test asserting `"\\[red]"` if desired, because that tests the helper before Rich rendering.

## Addressed Prior Findings

- **Positional validation:** Addressed. `prompt_items_from_texts()` now validates prompt count and 1-2000 character bounds before constructing batch items, with CLI tests for 51 positional prompts and overlong positional prompts before profile/output resolution.
- **File-source preflight tests:** Addressed. The plan now includes CLI preflight coverage for invalid UTF-8, empty files, overlong prompts, too many prompts, missing files, and directory/non-regular files before `_resolve_profile` and output-dir resolution.
- **`ConfigurationError` to Click boundary conversion:** Addressed. `cli_image.t2i` now catches prompt-source `ConfigurationError` and raises `click.UsageError`.
- **Async mocks:** Addressed. The CLI wiring tests use async fake batch runners instead of returning plain lists from a `MagicMock`.
- **Terminal-safe prompts/paths/details:** Addressed with the minor assertion edit above. The implementation plan now sanitizes prompt previews, saved paths, error details, and `output_dir`, and adds raw prompt preservation coverage.
- **Fan-out messaging:** Addressed. The plan prints and tests `up to {prompt_count * count} image(s)` before the batch runner.
- **Docs same-commit rule:** Addressed. Task 5 updates docs in the behavior commit, and orchestration now requires amend/replace of any behavior commit that lands without docs unless the operator explicitly approves a deviation.

## New Blockers

No new blocker found. The remaining edit is test-expectation polish, not a design or implementation blocker.

## Remaining Required Edits

1. Adjust the Step 4.6 rendered-summary assertion so it matches Rich's rendered output while still proving no raw ANSI/control sequence is emitted.

