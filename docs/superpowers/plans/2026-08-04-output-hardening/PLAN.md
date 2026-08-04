# Custom Output Path (-o/--output) Hardening Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature output-hardening` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Harden the `-o`/`--output` option across cloud-storage routing, video multi-count suffixes, `video r2v`/`chain` commands, de-mocked unit tests, and discovery documentation (Issue #415).

**Architecture:**
- **`src/gflow_cli/api/client.py`**: Fix key calculation when `GFLOW_CLI_STORAGE_URI` is set so custom subpaths outside `output_dir` preserve relative paths instead of flattening to `out_path.name`.
- **`src/gflow_cli/cli_video.py`**: Update `_relocate_video_output` to support `_1`/`_2` stem suffixes for `--count > 1` runs; expose `-o`/`--output` on `video r2v` and `video chain`.
- **`docs/` & `README.md`**: Update discovery docs and Journey 11 around `-o`.

**Predict verdict:** GO — confidence 9.2/10

---

## File structure

### New files
```
tests/features/output_hardening.feature
  BDD scenarios for custom output path hardening
tests/features/test_output_hardening_steps.py
  BDD step implementations
```

### Modified files
```
src/gflow_cli/api/client.py
  Fix cloud storage key resolution for custom output paths
src/gflow_cli/cli_video.py
  Update _relocate_video_output for multi-count suffixes and add -o to r2v/chain
tests/cli/test_cli_image.py
  De-mock explicit output test
tests/cli/test_cli_video.py
  De-mock explicit output test and verify multi-count suffixes
README.md, docs/USER_GUIDE.md, docs/AGENT_GUIDE.md, llms.txt
  Document -o/--output usage across CLI and scripting journeys
```

---

## Task 1 — BDD & Unit Test Scaffold (Red)

**What:** Add BDD step definitions and initial unit test assertions for custom output paths.

**Files:**
- `tests/features/test_output_hardening_steps.py`
- `tests/cli/test_cli_video.py`

**Steps:**
- [ ] Create `tests/features/test_output_hardening_steps.py` mapping steps from `output_hardening.feature`.
- [ ] Run pytest to confirm new tests fail or run as expected.

---

## Task 2 — Cloud-Storage Relative Subpath Routing

**What:** Fix `client.py` key calculation when `GFLOW_CLI_STORAGE_URI` is set so relative subpaths outside `output_dir` do not flatten to `out_path.name`.

**Files:**
- `src/gflow_cli/api/client.py`
- `tests/api/test_client.py`

**Steps:**
- [ ] Update `download_image` and `download_video` in `client.py` to sanitize `out_path.as_posix()` when `out_path.relative_to(output_dir)` raises `ValueError`.
- [ ] Add unit test in `test_client.py` asserting `s3://bucket/out/custom_sub/hero.png` key structure.

---

## Task 3 — Video Multi-Count Suffixes & Command Surface Expansion

**What:** Support `_1`/`_2` stem suffixes in `_relocate_video_output` for multi-count videos and add `-o`/`--output` to `video r2v` and `video chain`.

**Files:**
- `src/gflow_cli/cli_video.py`
- `tests/cli/test_cli_video.py`

**Steps:**
- [ ] Refactor `_relocate_video_output` to accept lists or multi-count results and apply stem suffixes.
- [ ] Add `-o`/`--output` option to `video r2v` and `video chain` Click commands.
- [ ] Add CLI tests in `test_cli_video.py`.

---

## Task 4 — De-mock Unit Tests

**What:** Rewrite tautological output tests so product code performs directory creation and relocation without pre-mocked `mkdir`.

**Files:**
- `tests/cli/test_cli_image.py`
- `tests/cli/test_cli_video.py`

**Steps:**
- [ ] Remove `mkdir` pre-population from mocks in `test_t2i_explicit_output_nested_dir` and `test_video_t2v_explicit_output_file`.
- [ ] Verify test suite passes with code under test performing filesystem operations.

---

## Task 5 — Discovery Documentation Updates

**What:** Update README.md, docs/USER_GUIDE.md, docs/AGENT_GUIDE.md, and llms.txt to feature `-o`/`--output`.

**Files:**
- `README.md`
- `docs/USER_GUIDE.md`
- `docs/AGENT_GUIDE.md`
- `llms.txt`

**Steps:**
- [ ] Update Journey 11 in `docs/USER_GUIDE.md` around `-o`.
- [ ] Add top-of-funnel `-o` examples in `README.md` and `docs/AGENT_GUIDE.md`.
- [ ] Regenerate website docs mirror via `scripts/ci/generate_website_docs.py`.

---

## Task 6 — Quality Gates & Verification

**What:** Run `/gflow:check` and ensure all quality gates pass.

**Steps:**
- [ ] Run `uv run python scripts/ci/check_repo_hygiene.py`
- [ ] Run `uv run python scripts/ci/check_doc_links.py`
- [ ] Run `uv run ruff check src tests`
- [ ] Run `uv run ruff format --check src tests`
- [ ] Run `uv run pyright src`
- [ ] Run `uv run python -m pytest -q --cov=gflow_cli`
