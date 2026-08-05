# Intra-Batch Reference & Dependency Ordering Implementation Plan (#317)

> **For agentic workers:** Run `/gflow:status --feature issue-317-intra-batch-references` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Extend `gflow image batch` to support reference entity fields (`ref` / `reference_entity`) on `BatchPromptItem` and intra-batch dependency execution ordering within a single mounted editor session.

**Architecture:** Extend `BatchPromptItem` DTO in `image_batch.py` with reference attributes. Add dependency graph validation and topological ordering before batch execution. Modify the batch loop in `image_batch.py` to forward output media IDs from completed steps as inputs to dependent steps. Update CLI and MCP tools for schema parity.

**Predict verdict:** GO (confidence 8.6/10)

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Upstream dependency failure cascades into unhandled execution crash | Abort dependent batch items cleanly with `BatchPartialError` / status `dependency_failed` |
| Medium | Circular dependency between prompt items | Run cycle detection before execution; raise `BatchIntegrityError` |

---

## File structure

### New files
```
tests/test_image_batch_references.py
  Unit tests for BatchPromptItem reference validation, DAG sorting, cycle detection, and resolution
tests/features/image_batch_references.feature
  BDD Gherkin specification for intra-batch reference workflows
```

### Modified files
```
src/gflow_cli/image_batch.py
  Add ref/reference_entity fields, DAG validator/sorter, and reference propagation in batch execution
src/gflow_cli/cli_image.py
  Expose reference options for prompt items in batch inputs
src/gflow_cli/mcp/server.py
  Update image batch MCP tool definitions for schema parity
docs/USAGE.md
  Document intra-batch reference syntax (e.g. batch:N)
```

---

## Task 1 — Unit & BDD Test Scaffold (test scaffold)

**What:** Create red unit and BDD tests covering `BatchPromptItem` reference parsing, topological sorting, cycle detection, and error handling.

**Files:**
- `tests/test_image_batch_references.py`
- `tests/features/image_batch_references.feature`

**Steps:**
- [x] Write unit test for `BatchPromptItem` with `ref` / `reference_entity`
- [x] Write unit test for topological sorting of prompt items with dependencies
- [x] Write unit test for circular dependency detection (`BatchIntegrityError`)
- [x] Write BDD scenario for dependency failure fallback

**Tests created (red):**
- [x] `test_batch_prompt_item_supports_ref_field`
- [x] `test_batch_dag_sort_orders_dependencies_correctly`
- [x] `test_batch_dag_detects_circular_dependency`

---

## Task 2 — Core DTO & Topological Ordering Implementation

**What:** Update `BatchPromptItem` and implement dependency resolution logic in `image_batch.py`.

**Files:**
- `src/gflow_cli/image_batch.py`

**Steps:**
- [x] Add `ref: str | None = None` and `reference_entity: str | None = None` to `BatchPromptItem`
- [x] Implement `resolve_batch_dependencies(prompts: list[BatchPromptItem]) -> list[BatchPromptItem]`
- [x] Implement cycle detection algorithm throwing `BatchIntegrityError`
- [x] Validate `ref` indices against prompt bounds


---

## Task 3 — Batch Execution Reference Propagation

**What:** Modify the batch execution loop to record output media IDs / local paths and inject them into dependent `BatchPromptItem` generation calls.

**Files:**
- `src/gflow_cli/image_batch.py`

**Steps:**
- [x] Track generated media outputs per batch item index during execution
- [x] Resolve `batch:N` references to actual generated media IDs prior to submitting item N+1
- [x] Handle upstream failure: mark dependent items as skipped with `status="dependency_failed"`

---

## Task 4 — CLI & MCP Schema Parity

**What:** Expose reference options in `cli_image.py` batch commands and mirror them in `src/gflow_cli/mcp/server.py`.

**Files:**
- `src/gflow_cli/cli_image.py`
- `src/gflow_cli/mcp/server.py`
- `tests/mcp/test_cli_parity.py`

**Steps:**
- [x] Update Click batch command options / prompt JSON deserializer
- [x] Update MCP `image_batch` tool schema definitions
- [x] Run `tests/mcp/test_cli_parity.py` to confirm CLI/MCP parity

---

## Task 5 — Documentation & Pre-Commit Gates

**What:** Update usage docs, changelog, and run all pre-commit quality gates.

**Files:**
- `docs/USAGE.md`
- `CHANGELOG.md`

**Steps:**
- [x] Add `gflow image batch` reference syntax examples to `docs/USAGE.md`
- [x] Update `CHANGELOG.md` under `[Unreleased]`
- [x] Run `/gflow:check` to ensure all 7 quality gates pass

---

## Definition of done

- [x] All task steps checked off
- [x] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [x] `CHANGELOG.md` `[Unreleased]` section updated
- [x] Docs updated (`docs/USAGE.md`)
- [x] BDD feature file covers all Critical + High scenarios from `/gflow:scenario`
- [x] No `# TODO` in diff without a tracked issue link

