# Scenario: Issue #299 — Per-Command UI Mode Policy

## Coverage Map
- **Active Dimensions:**
  - **D11 (Input validation & boundaries):** Valid `--ui-mode` values (`auto`, `classic`, `agentic`). Invalid values raise `ConfigurationError`.
  - **D6 (CLI & MCP Parity):** Every CLI generation command with `--ui-mode` must have a corresponding MCP tool parameter.
  - **D8 (Typed Error Exit Codes):** Mismatch in strict mode (`--ui-mode classic` on agentic DOM, or `--ui-mode agentic` on classic DOM) raises `UiModeUnavailableError` with exit code 28.
- **Skipped Dimensions:** D1, D2, D4, D5, D7, D9, D10, D12.

---

## Scenario Table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D11 Input validation | `--ui-mode invalid` passed to CLI | High | Raises `ConfigurationError` | Unit |
| 2 | D8 Error exit code | `--ui-mode classic` when DOM is agentic | High | Raises `UiModeUnavailableError` (exit code 28) | Unit |
| 3 | D8 Error exit code | `--ui-mode agentic` when DOM is classic | High | Raises `UiModeUnavailableError` (exit code 28) | Unit |
| 4 | D6 Schema symmetry | `gflow image t2i --ui-mode` options | High | Mirrored in MCP `generate_image` tool schema | Parity Unit |

---

## Must-Cover Before Merge
1. Add `ui_mode` setting in `config.py` with `auto`, `classic`, `agentic` literals.
2. Implement strict UI mode enforcement in `factory.py::bind_ui_driver` raising `UiModeUnavailableError`.
3. Add `--ui-mode` Click option across generation commands (`cli_image.py`, `cli_video.py`, `cli_run.py`) and update MCP tool definitions.
4. Unit tests in `tests/api/transports/test_driver_factory.py` and `tests/mcp/test_cli_parity.py`.
