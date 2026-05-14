# Plan Review: Shell Multi-Prompt `t2i`

**Verdict:** MAJOR-REVISION

## Findings

### 1. Positional prompt validation is specified by tests but not implemented in the plan

**Severity:** Major

**Citations:** Spec §5 C4-C5, §7 AC10 and AC17, §10 required tests; Plan Task 1 Step 1.1, Task 2 Step 2.1, Task 3 Step 3.1, Task 5 Step 5.7.

The plan adds tests for over-50 parsed prompt lines and a BDD scenario for 51 positional prompts, but the implementation steps only validate prompt count inside `parse_prompt_lines()`. Positional multi-prompt input goes through `prompt_items_from_texts()` in Task 5, and the Task 3 implementation snippet for `prompt_items_from_texts()` does not validate:

- 1-50 prompt count
- prompt length 1-2000
- source/index-aware diagnostics

A fresh engineer following the plan would likely leave `gflow image t2i p0 ... p50` accepted, causing the Task 2 BDD scenario and spec AC10/AC17 to fail.

**Suggested edit:** Add a shared validation path for positional prompts, for example `parse_positional_prompts(prompts, source_label="positional")` or validation inside `prompt_items_from_texts()`. Include tests for 51 positional prompts and a positional prompt over 2000 characters, both proving `_resolve_profile` and output directory creation are not reached.

### 2. Preflight-order coverage is incomplete for file-source validation failures

**Severity:** Major

**Citations:** Spec §6.5, §7 AC16-AC17, §10 required tests; Plan Task 1 Steps 1.2-1.3, Task 4 Steps 4.1-4.3, Task 5 Steps 5.5-5.8.

The spec requires invalid path, non-regular file, unreadable file, invalid UTF-8, oversized file, zero prompts, prompt >2000 chars, and >50 prompts to fail before profile resolution and before output directory creation. The plan includes pure `read_prompt_file()` tests and some CLI preflight tests for multi-source, seed, and empty stdin, but it does not add CLI-level tests proving all file validation failures occur before `_resolve_profile` or output directory creation.

The implementation order in Task 5 appears intended to parse file/stdin before profile resolution, but the automated acceptance proof is missing for several required cases.

**Suggested edit:** Add a parametrized CLI preflight test that patches `_resolve_profile`, `resolve_t2i_batch_output_dir`, and `run_image_batch`, then invokes `--prompts-file` with missing, directory/non-regular, invalid UTF-8, oversized, empty-after-filtering, overlong prompt, and 51-prompt files. Assert exit code 2 and all patched post-validation functions are not called.

### 3. Raw prompt preservation is not directly tested

**Severity:** Minor

**Citations:** Spec §6.5 and §7 AC18; Plan Task 4 Step 4.5, Task 3 Step 3.2.

Task 4 says the display-safe preview test should cover raw API preservation, but the proposed test only asserts that `safe_prompt_preview()` escapes Rich markup and removes a control character. It does not prove that `run_one_image_prompt()` passes `item.text` unchanged into `GenerateImageRequest` / Flow while only terminal output is sanitized.

**Suggested edit:** Add a unit test around `run_one_image_prompt()` with a fake client and prompt text containing Rich markup plus terminal controls. Assert the fake client receives the exact original prompt string, and separately assert summary rendering uses `safe_prompt_preview()`.

### 4. Docs-in-same-commit handling is mostly correct, but the orchestration fallback weakens the locked constraint

**Severity:** Minor

**Citations:** Spec §2 locked constraints, §7 AC19; Plan Task 5 Steps 5.9-5.13; Orchestration §6 "Docs/code commit split".

Task 5 correctly updates `docs/USAGE.md`, `README.md`, and `CHANGELOG.md` in the same commit as the CLI behavior. However, the orchestration doc says that if behavior lands without docs, a later immediate fixup commit plus a note is acceptable. That conflicts with the locked constraint wording: every commit affecting user-facing behavior must update docs.

**Suggested edit:** Strengthen Orchestration §6 to require amending or replacing the offending behavior commit before proceeding, unless the operator explicitly approves the deviation.

## Open Questions / Assumptions

- I assume red test commits are intentional and acceptable for this branch because the spec locks TDD as RED -> GREEN -> REFACTOR.
- I assume examples and release prep are in scope for the implementation plan even though they go beyond the minimum feature acceptance criteria.
- I did not run tests or inspect network-dependent behavior; this was a plan-only review.

## Suggested Edits Summary

1. Add positional prompt count and length validation to the implementation steps.
2. Add CLI preflight tests for every file-source validation failure required by the spec.
3. Add a raw-prompt-preservation test for the batch runner/API request path.
4. Tighten the orchestration docs/code split rule so it preserves the locked same-commit constraint.
