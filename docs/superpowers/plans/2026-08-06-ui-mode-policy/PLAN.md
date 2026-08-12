# Per-Command UI Mode Policy Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature ui-mode-policy` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Add `--ui-mode auto|classic|agentic` CLI option, `GFLOW_CLI_UI_MODE` setting, and strict policy validation before generation submission to fail fast with `UiModeUnavailableError` (exit 28) when requested UI mode doesn't match the mounted cohort.

**Predict verdict:** GO — confidence 10/10

---

## File structure

### Modified files
```
src/gflow_cli/config.py
  Add ui_mode setting ("auto" | "classic" | "agentic").
src/gflow_cli/api/transports/drivers/factory.py
  Enforce strict mode policy check in bind_ui_driver, raising UiModeUnavailableError.
src/gflow_cli/cli_image.py, cli_video.py, cli_run.py
  Add --ui-mode Click option.
src/gflow_cli/mcp/tools.py (and server definitions)
  Mirror ui_mode tool argument for CLI-MCP parity.
tests/api/transports/test_driver_factory.py
  Unit tests for strict UI mode policy validation.
tests/mcp/test_cli_parity.py
  CLI-MCP parity test assertions.
CHANGELOG.md
  Unreleased section update.
```

---

## Task 1 — Config & Settings Extension

**What:** Add `ui_mode: Literal["auto", "classic", "agentic"] = "auto"` setting to `Settings` in `config.py`.

**Files:**
- `src/gflow_cli/config.py`
- `tests/test_config.py`

**Steps:**
- [ ] Add `ui_mode` setting in `config.py` with validation for `auto`, `classic`, `agentic`.
- [ ] Add unit test in `tests/test_config.py`.

---

## Task 2 — Driver Factory Policy Enforcement

**What:** Update `bind_ui_driver` in `src/gflow_cli/api/transports/drivers/factory.py` to check the configured/requested `ui_mode`. If `ui_mode != "auto"` and `detected_mode != ui_mode`, raise `UiModeUnavailableError`.

**Files:**
- `src/gflow_cli/api/transports/drivers/factory.py`
- `tests/api/transports/test_driver_factory.py`

**Steps:**
- [ ] Implement `ui_mode` parameter in `bind_ui_driver`.
- [ ] Raise `UiModeUnavailableError` on mismatch.
- [ ] Add unit tests in `tests/api/transports/test_driver_factory.py`.

---

## Task 3 — CLI & MCP Option Symmetry

**What:** Add `--ui-mode` Click option to generation commands and update MCP tool definitions.

**Files:**
- `src/gflow_cli/cli_image.py`
- `src/gflow_cli/cli_video.py`
- `src/gflow_cli/cli_run.py`
- `src/gflow_cli/mcp/tools/`
- `tests/mcp/test_cli_parity.py`

**Steps:**
- [ ] Add `--ui-mode` Click option to `gflow image t2i`, `gflow image i2i`, `gflow video t2v`, `gflow video i2v`, `gflow run`.
- [ ] Update MCP tool definitions.
- [ ] Verify `tests/mcp/test_cli_parity.py` passes.

---

## Task 4 — Quality Gates & Pre-Commit Validation

**What:** Run the Impeccable Routine (`/gflow:check`).

**Steps:**
- [ ] `uv run python scripts/ci/check_repo_hygiene.py`
- [ ] `uv run python scripts/ci/check_doc_links.py`
- [ ] `uv run ruff check src tests`
- [ ] `uv run ruff format --check src tests`
- [ ] `uv run pyright src`
- [ ] `uv run python -m pytest -q`

---

## Definition of Done

- [ ] All task steps checked off
- [ ] `/gflow:check` green
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
