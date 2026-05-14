# Security Council Review: Shell Multi-Prompt `t2i`

**Verdict:** MINOR-EDITS

## Findings

### Medium: `--prompts-file` needs bounded ingestion before full-file read

**Spec citations:** `docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md` sections 4.2, C4, C5, and 6.3 describe reading UTF-8 prompt files, parsing into prompts, enforcing 1-50 prompts, and using `parse_prompt_lines(text: str, ...)`.

**Existing-code citations:** `src/gflow_cli/cli_run.py:101-117` reads the whole JSON config before validation and includes the path in read/parse errors. `src/gflow_cli/cli_run.py:137-180` then applies the 50-prompt and 2000-character bounds.

**Rationale:** The spec caps retained prompts but does not cap file bytes or line count before reading the entire file. For an untrusted or accidentally wrong path, this can turn `gflow image t2i --prompts-file huge.log` into avoidable memory/latency before any useful validation. This is not a credential leak or code execution issue, but it is an input-handling weakness on a new local file surface.

**Suggested edit:** Require the implementation to reject non-files and oversized prompt files before reading. A simple v0.6 rule is sufficient: `--prompts-file` must be an existing readable regular file, UTF-8 text, with a documented max byte size comfortably above `50 * 2000` plus comments, for example 256 KiB or 512 KiB. Keep the parsed prompt cap at 50.

### Medium: prompt previews should be terminal-safe while API prompt text stays raw

**Spec citations:** section 6.3 preserves inline `#` and prompt text; section 7 acceptance criterion 11 requires structured outcomes containing prompt text.

**Existing-code citations:** `src/gflow_cli/cli_run.py:355-389` renders prompt previews in a Rich table. `docs/SECURITY.md:39-42` says operational logs may contain prompts and asset IDs but no secrets.

**Rationale:** Prompt content is untrusted terminal/log content. The spec does not say how prompt text should be escaped for summaries, errors, or future structured output. Rich markup or terminal control characters in a prompt could spoof or corrupt terminal output. The raw prompt must still be sent unchanged to Flow; only user-facing display/log previews need escaping or control-character normalization.

**Suggested edit:** Add a display-safety requirement: generated prompt text is passed to the API unchanged, but prompt previews in Rich tables, errors, and logs must escape Rich markup and replace or visibly encode control characters except normal whitespace. Tests should include prompts containing `[` markup-like text and ANSI/control characters.

### Low: path disclosure guidance should be made concrete for `--prompts-file`

**Spec citations:** C4 says validation errors should identify source and index/line without leaking absolute internal paths unnecessarily. Risk table line item says to prefer source labels and basenames for user-facing errors.

**Existing-code citations:** `src/gflow_cli/cli_run.py:108-117` currently reports config paths directly in "not found", "failed to read", and "failed to parse" errors.

**Rationale:** The new prompt-file surface should not inherit full-path disclosure by accident. A user-supplied absolute path is not always secret, but terminal transcripts and CI logs often get shared. The spec's current wording is directionally correct but leaves implementation discretion high enough that existing `gflow run` style could be copied.

**Suggested edit:** Define error labels explicitly. For example: source label `--prompts-file <basename>` for user-facing validation messages, with line numbers for prompt content errors. Avoid resolved absolute paths in normal errors. Full paths can remain in debug logs only if the project has an explicit debug logging policy for them.

### Low: preflight ordering should cover all new validation, not only mutual exclusion and seed

**Spec citations:** C2 and acceptance criteria 6-7 require multi-source and seed errors before profile resolution or browser/API work. C5 and 6.5 require cap/validation errors before browser/API work.

**Existing-code citations:** `src/gflow_cli/cli_image.py:268-277` validates the existing `--seed`/`-n` conflict before profile resolution. `src/gflow_cli/cli_run.py:434-446` parses config and checks transport before profile resolution, then opens the browser later in `_run_batch` at `src/gflow_cli/cli_run.py:282`.

**Rationale:** The spec is strongest for mutual exclusion and seed, but less explicit for file read errors, empty parsed prompt lists, too many prompts, prompt length, and UTF-8 decode errors. These should all happen before `_resolve_profile`, output directory creation, `FlowApiClient`, project creation, or generation, to avoid accidental credit spend and side effects.

**Suggested edit:** Add an acceptance criterion that every prompt-source parse/validation failure occurs before profile resolution, output directory creation, browser launch, project creation, or Flow API work. Include tests for invalid file path, invalid UTF-8, zero prompts, prompt >2000 chars, and >50 prompts.

### Low: accidental spend messaging should mention total generation fan-out

**Spec citations:** C5 caps multi-prompt mode at 50 prompts. Existing `t2i` keeps global `-n` 1-4, and C6 defaults to continuing after per-prompt errors.

**Existing-doc citations:** `docs/USAGE.md:83-132` documents `t2i` `-n` and seed behavior. `docs/USAGE.md:271-322` documents `gflow run` 1-50 prompts and continue/fail-fast semantics.

**Rationale:** The design correctly prevents duplicate prompt sources and rejects `--seed` in batch mode, but users can still request up to 50 prompts times 4 variations in one shell command. That is expected behavior, not a security blocker. It should be visible in docs/help so shell users understand the credit-spend multiplier.

**Suggested edit:** In `docs/USAGE.md` and Click help, state that shell multi-prompt mode may generate up to `prompt_count * -n` images, max 200, and that `--fail-fast` can limit further attempts after a failure but not after successful expensive prompts.

## Open Questions / Assumptions

- Assumption: `--stdin` intentionally blocks when no input is piped, per spec section 4.3. No platform-specific pipe detection is required for v0.6.
- Assumption: prompt files are local convenience input only; no remote URL support should be added.
- Question: Should the prompt-file byte cap be documented as a stable user contract, or treated as an implementation safety limit with a clear error message?
- Question: Should `gflow run --config` path-disclosure behavior be aligned later, or is this review scoped strictly to new `gflow image t2i` prompt-file errors?

## Suggested Spec Edits Summary

- Add a max byte size and regular-file/readable-file requirement for `--prompts-file`.
- Require terminal-safe rendering of prompt previews and validation messages while preserving raw prompt text for Flow API calls.
- Specify basename/source-label-only prompt-file errors in normal user-facing output.
- Expand preflight acceptance tests so all prompt-source failures happen before profile resolution, output directory creation, browser launch, project creation, or API calls.
- Document the maximum shell multi-prompt fan-out and credit-spend implication.
