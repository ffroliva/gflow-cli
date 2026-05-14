# Architectural Review: Shell Multi-Prompt

**Target:** `2026-05-14-shell-multi-prompt`
**Reviewer:** Senior Architect
**Date:** 2026-05-14

## Summary of Findings
The architectural review of the Shell Multi-Prompt feature reveals a successful extraction of shared logic for the image domain, but also highlights minor technical debt and architectural divergence from the video domain.

### Key Insights:

1. **DRYness and Coupling**:
   - `image_batch.py` effectively DRYs up the execution and rendering logic for image batches.
   - **Leakage**: The module imports `rich` for summary rendering. While acceptable for a CLI-targeted helper, it strictly couples the batch logic to terminal-based reporting.
   - **Duplication**: `cli_run.py` duplicates validation of model/aspect ratios instead of using helpers.
   - **Inconsistency**: `resolve_t2i_batch_output_dir` (image) uses `images/<YYYY-MM-DD>`, while `_resolve_output_dir` (run) uses `out/<UTC-timestamp>`.

2. **Extension Safety**:
   - `BatchPromptItem` is tightly coupled to image-specific fields (`count`, `aspect_ratio`). Adding video-batch support will require either a more generic `BatchItem` or a parallel `VideoBatchItem`.
   - The image batch runner is sequential (supporting fail-fast), whereas the existing video batcher is concurrent (using `asyncio.gather` and the Page pool). Unifying these will require reconciling these different execution models.

3. **Constant Alignment**:
   - `image_batch.py` acts as the CLI source of truth.
   - `ALLOWED_MODELS` is a restrictive subset of `api/image.py`'s aliases. This is consistent across the CLI but limits users to "friendly" aliases (e.g., `nano2` vs `narwhal`).

## Verdict
**PROCEED**

The implementation is robust, well-tested, and a significant improvement for UX. The identified technical debt is manageable and can be addressed in future phases (e.g., during video-batch unification).

## Recommended Refactors
- Unify output directory resolution logic.
- Move duplicated validation in `cli_run.py` into `image_batch.py`.
- Parameterize `run_image_batch` to accept a worker function, facilitating future video support.
