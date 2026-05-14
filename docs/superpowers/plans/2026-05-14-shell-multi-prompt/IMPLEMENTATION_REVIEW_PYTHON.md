# Python Implementation Review

**Target:** `2026-05-14-shell-multi-prompt`
**Reviewer:** Senior Code Reviewer
**Date:** 2026-05-14

## Findings

### 1. Python Idioms, Type Hints, and Async Correctness
The code is exceptionally well-written.
- Modern Python features like `from __future__ import annotations` and `dataclasses` are used consistently.
- Type hints are comprehensive and accurate.
- `asyncio` patterns are correctly implemented, with `asyncio.run` properly gating the async execution from synchronous Click commands. The shared context approach inside `run_image_batch` (single `async with client:`) correctly implements the "one session, one project" requirement.

### 2. Shared Image Batch Extraction (`image_batch.py`)
The separation of concerns is handled gracefully.
- Core execution (`run_image_batch`, `run_one_image_prompt`) and rendering (`render_image_batch_summary`) are modularized in `image_batch.py` without leaking JSON-specific configuration logic.
- `cli_run.py` was correctly refactored to consume these shared helpers while maintaining its own `BatchConfig` parser.

### 3. `gflow run --config` Regression Risk
- The risk is extremely low. The `cli_run` logic retains its previous parsing semantics. 
- The inclusion of both `image4` and `imagen4` in `ALLOWED_MODELS` ensures aliases work correctly for the new shell surface without breaking existing JSON configurations.

### 4. Single-Prompt Behavior Preservation
- The legacy `t2i` single-prompt flow is strictly isolated behind an `if not is_multi_prompt:` branch in `cli_image.py`.
- This ensures that output naming rules, seed parameters, and project initialization for single prompts remain completely untouched.

### 5. Deviation from Spec: Output Directory Partitioning
- **Issue:** Section 6.4 of the spec dictates that multi-prompt shell mode without `--out` should write to `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/` (using the same date partition as legacy `t2i`).
- **Implementation:** `resolve_t2i_batch_output_dir` in `image_batch.py` writes to a timestamped subdirectory (`images/%Y%m%dT%H%M%SZ`). 
- **Impact:** While isolating 50-image batches into their own timestamped folders is arguably *better* UX than flooding the daily partition, it technically deviates from the spec.

## Recommendations
- **Output Path:** Decide whether to accept the timestamped directory as a UX improvement (and update the spec) or revert `resolve_t2i_batch_output_dir` to use `date.today().isoformat()` to strictly honor the original design. 

## Verdict
**MINOR-EDITS**