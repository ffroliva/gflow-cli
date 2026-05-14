# Security Plan Review Follow-Up - Shell Multi-Prompt `t2i`

Verdict: **MINOR-EDITS**

## Summary

The MAJOR-REVISION blockers from `PLAN_REVIEW_SECURITY.md` are addressed well enough to proceed after one small plan hardening edit. I found no remaining security blocker that should force another major revision before implementation.

## Blocker Verification

- **Positional preflight validation:** Addressed. `prompt_items_from_texts()` now validates prompt count and prompt length before returning batch items, and Task 1 adds CLI tests asserting `_resolve_profile` and output-dir resolution are not called for 51 positional prompts or a 2001-character positional prompt (`PLAN.md:262`, `PLAN.md:281`, `PLAN.md:786`).
- **Prompt-source `ConfigurationError` handling at Click boundary:** Addressed. Task 5 catches `ConfigurationError` from file/stdin/positional parsing and converts it to `click.UsageError` before profile resolution or output-dir resolution (`PLAN.md:1360`, `PLAN.md:1429`, `PLAN.md:1452`).
- **Terminal-safe paths/details/prompts:** Mostly addressed. The shared renderer applies `safe_terminal_text()` to saved paths and error details, `safe_prompt_preview()` to prompts, and the `t2i` fan-out preamble escapes the displayed output directory (`PLAN.md:754`, `PLAN.md:907`, `PLAN.md:917`, `PLAN.md:1470`).
- **CR/newline prompt previews:** Addressed. `_CONTROL_RE` covers C0 controls including `\r` and `\n`, and the planned test asserts prompt previews contain no raw ESC, CR, or newline (`PLAN.md:754`, `PLAN.md:1155`).
- **File validation before full read:** Addressed. `read_prompt_file()` checks `stat()`, regular-file status, and the 512 KiB cap before `read_text()` (`PLAN.md:1104`).
- **Spend fan-out messaging:** Addressed. Task 5 prints `up to {prompt_count * count} image(s)` before `run_image_batch()`, and the CLI wiring test asserts `up to 8 image(s)` for two prompts with `-n 4` (`PLAN.md:412`, `PLAN.md:1468`).

## Findings

### 1. Validation error source labels are not explicitly terminal-safe

Severity: **Low**

`_prompt_file_label()` uses `path.name` directly, and parse/count validation errors interpolate `source_label` directly into `ConfigurationError` messages (`PLAN.md:778`, `PLAN.md:1049`, `PLAN.md:1100`). The plan sanitizes Rich-rendered summaries and output paths, but a malicious or accidental filename containing ESC, `\r`, or `\n` could still affect Click usage-error output.

Suggested edit: add a tiny source-label sanitizer, or build `_prompt_file_label()` with `safe_terminal_text(path.name)`, and add a CLI test with a weird filename containing Rich markup/control characters. Keep raw path handling unchanged for filesystem access; sanitize only the user-facing label.

## Verdict Rationale

This is **MINOR-EDITS**, not **MAJOR-REVISION**, because the previous high-risk blockers around preflight ordering, file-size validation before read, and uncaught prompt-source errors are fixed in the revised plan. The remaining issue is terminal-output hygiene for validation labels, not a path to browser/API work or accidental spend.
