# Security Implementation Review

**Target:** `2026-05-14-shell-multi-prompt`
**Reviewer:** Senior Code Reviewer
**Date:** 2026-05-14

## Findings

### 1. Prompt-File Validation
- **Strengths:** `read_prompt_file` correctly validates the file via `path.stat()` before reading. It checks `is_file()` to prevent reading from block devices or directories, and strictly enforces the `MAX_PROMPT_FILE_BYTES` (512 KiB) limit before attempting to load data into memory, preventing trivial memory exhaustion.
- **Strengths:** Invalid UTF-8 handling is robust.

### 2. Path Disclosure
- **Strengths:** `_prompt_file_label` relies on `path.name` rather than `str(path)`. This ensures that absolute paths from the local filesystem do not leak in standard usage errors or rendering outputs.

### 3. Terminal-Safe Previews
- **Strengths:** The `safe_terminal_text` and `safe_prompt_preview` functions securely strip ANSI control sequences and escape Rich markup `[ ]`. This successfully mitigates terminal injection attacks and prevents log mangling.
- **Strengths:** The raw, unescaped prompt text is properly preserved when sent to the Flow API (`run_one_image_prompt` uses `item.text` directly).

### 4. Accidental Credit-Spend Messaging & Preflight Validation
- **Strengths:** `cli_image.py` effectively calculates and displays the fan-out summary (`up to X image(s)`) prior to running the batch.
- **Strengths:** Mutual exclusivity checks between prompt sources are evaluated correctly at the start of the command execution, avoiding unintended overlapping runs.

### 5. Stdin Memory Safety (Vulnerability Identified)
- **Issue:** In `cli_image.py`, standard input is read using `sys.stdin.read()`.
- **Risk:** Unlike the `--prompts-file` path, which caps files at 512 KiB, `sys.stdin.read()` blocks and pulls the entirety of the stream into memory. If an exceptionally large pipe or infinite stream is passed (`cat /dev/zero | gflow image t2i --stdin`), the process will encounter an Out-Of-Memory (OOM) crash.
- **Recommendation:** Implement a bounded read for standard input. For example, use `sys.stdin.read(MAX_PROMPT_FILE_BYTES + 1)` and throw a usage error if the content exceeds the cap, identical to the file behavior.

## Recommendations
- Fix the unbounded `sys.stdin.read()` to protect against memory exhaustion.

## Verdict
**MINOR-EDITS**