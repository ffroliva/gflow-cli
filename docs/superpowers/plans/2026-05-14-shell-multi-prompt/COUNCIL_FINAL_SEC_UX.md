# Security & UX Review: Shell Multi-Prompt

**Date:** 2026-05-14
**Reviewer:** Senior Code Reviewer
**Status:** Completed
**Verdict:** **PATCH** (Path leaks in terminal output)

## 1. Memory Safety
**Focus:** Bounded `sys.stdin.read` logic in `cli_image.py`.

- **Finding:** **PASS**
- **Analysis:** The implementation in `src/gflow_cli/cli_image.py` correctly uses `sys.stdin.read(MAX_PROMPT_FILE_BYTES + 1)` where `MAX_PROMPT_FILE_BYTES` is 512 KiB. This "slurp" is safely bounded, preventing OOM crashes even if a massive stream is piped to the CLI. The code properly raises `click.UsageError` if the input exceeds the limit.
- **Verification:** Verified in `src/gflow_cli/cli_image.py` lines 282-293.

## 2. Resource Integrity
**Focus:** `sqlite3` connections in `sapisidhash.py` and related tests.

- **Finding:** **PASS**
- **Analysis:** Both the implementation in `src/gflow_cli/api/transports/experimental/sapisidhash.py` and the corresponding unit tests in `tests/api/transports/test_sapisidhash.py` use robust `try...finally` blocks to ensure that `sqlite3` connections are explicitly closed (`conn.close()`). No resource leaks were identified.
- **Verification:**
    - `sapisidhash.py`: `read_sapisid_from_profile` uses `try...finally`.
    - `test_sapisidhash.py`: `_build_cookie_db` and test cases use `try...finally`.

## 3. Path Safety
**Focus:** Prevention of absolute path leaks in terminal output.

- **Finding:** **PATCH REQUIRED**
- **Analysis:** Several locations in the CLI output absolute paths, which may leak sensitive information such as the local username (e.g., `C:\Users\<username>\...`).
    - `cli_image.py`: The `output_dir` and generated image paths in tables use `str(path)`.
    - `image_batch.py`: The `render_image_batch_summary` table joins absolute paths into the "detail" column.
    - `cli_video.py`: Similar leaks in `t2v` and `i2v` success messages.
- **Recommendation:** Implement a `safe_path_text` helper that replaces the user's home directory with `~` and/or shows paths relative to the Current Working Directory (CWD) where appropriate.

## 4. CLI UX
**Focus:** Help strings for positional prompts vs flags.

- **Finding:** **PATCH SUGGESTED**
- **Analysis:**
    - The `t2i` help string says "Generate 1-4 images from a text prompt", which is slightly misleading for the new multi-prompt mode. It should be updated to "one or more text prompts".
    - The mutual exclusivity of positional `PROMPTS`, `--prompts-file`, and `--stdin` is well-enforced in code and illustrated by examples, but the `Usage` line `[PROMPTS]...` might still be slightly ambiguous to a new user. However, this is standard `click` behavior and is acceptable.
- **Recommendation:** Update the short help and main help text to explicitly mention "one or more text prompts".

## Final Verdict
The feature is functionally solid and secure regarding memory and database resources. However, it fails the "no absolute path leaks" requirement. A patch is required to sanitize path rendering in terminal output before the feature can be considered production-ready.

---
**Reviewer Signature:** Senior Code Reviewer
