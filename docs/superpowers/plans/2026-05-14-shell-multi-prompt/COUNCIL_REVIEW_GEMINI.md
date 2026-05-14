# Spec Council Review: Shell Multi-Prompt `t2i`

**Reviewer:** Gemini (Cross-Model Reviewer)
**Workspace:** `C:/development/github/gflow-cli`
**Spec Version:** v1 (2026-05-14)
**Verdict:** **PROCEED-AS-IS** (with minor implementation notes)

---

## Findings

### 1. Architecture: Shared Logic Refactoring

- **Severity:** Minor
- **Citation:** Section 6.2 Shared batch machinery
- **Analysis:** `src/gflow_cli/cli_run.py` currently contains `BatchConfig`
  and `_run_batch` logic that is tightly coupled to JSON file parsing, for
  example `_from_dict` and `from_json_path`. To support the shell shortcut
  effectively, the shared batch machinery should be extracted to a layer that
  operates on a sequence of `BatchPromptItem` domain objects rather than raw
  JSON structures.
- **Recommendation:** Ensure the extracted `run_image_batch` helper is
  decoupled from the `BatchConfig` JSON-specific fields, like `profile` and
  `transport`, which are handled at the CLI layer for `t2i`.

### 2. Output Directory Consistency

- **Severity:** Minor
- **Citation:** Section 6.4 Output directory behavior
- **Analysis:** There is a slight mismatch in default behaviors. Existing
  `gflow image t2i` defaults to date-partitioned paths
  (`images/YYYY-MM-DD/`), whereas `gflow run` currently defaults to timestamped
  run folders (`out/YYYYMMDDTHHMMSSZ/`). The spec correctly opts to keep the
  `t2i` date-partitioned default for the new shell shortcut, but the
  implementation should be careful when reusing `cli_run` logic to ensure it
  does not accidentally force the `run` timestamped folder structure on `t2i`
  users.

### 3. Variation Indexing (0-based vs 1-based)

- **Severity:** Minor (Clarification)
- **Citation:** Section C3 Output naming
- **Analysis:** The spec explicitly chooses zero-based indexing
  (`prompt_0_0.png`) for the multi-prompt path to match `gflow run`, while
  legacy `t2i` remains one-based (`<media>_1.png`). While this is a divergence
  within the same command group, the justification, correlation with `gflow run`
  batch logic, is sound. The implementation plan should explicitly note this
  to the TDD engineers.

### 4. Project Title Parameterization

- **Severity:** Minor
- **Citation:** Section 6.2 Shared batch machinery
- **Analysis:** `cli_run._run_batch` hardcodes the project title as
  `"gflow-cli run"`. The shared machinery must support a `project_title`
  parameter so that `t2i` can use `"gflow-cli t2i"` or
  `"gflow-cli t2i batch"` as established by current conventions in
  `cli_image.py`.

---

## Open Questions & Assumptions

1. **i2i Scope:** The spec focused exclusively on `t2i`. It is assumed that
   `i2i` remains single-prompt for v0.6.0a1, though the shared machinery should
   ideally be generic enough to support refs in the future.
2. **50-Prompt Limit:** Is the 50-prompt limit a hard technical constraint of
   the Flow API or a design choice for CLI responsiveness? Assumption: it is a
   design choice to maintain parity with `gflow run` for now.
3. **Output Filename Overrides:** In shell mode, it is assumed users cannot
   provide custom `output_filename` stems as they can in JSON; they always get
   `prompt_<idx>`. This matches the shortcut philosophy of Section 2.

---

## Suggested Edits

- **Section 6.2:** Explicitly mention that the shared `run_image_batch` should
  accept an optional `project_title` string.
- **Section 6.3:** Note that `parse_prompt_lines` should also handle potential
  UTF-8 BOM gracefully if encountered in `--prompts-file`.
- **Section 10:** Add a specific test case for output directory resolution to
  ensure it respects the `images/<date>/` partition when `--out` is omitted in
  multi-prompt mode, specifically verifying it does not use the `gflow run`
  timestamped default.

---

End of review.
