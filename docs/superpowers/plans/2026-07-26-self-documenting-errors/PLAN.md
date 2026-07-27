# Feature Implementation Plan: Self-Documenting Errors & Remediation Guidance (#380)

> **Status:** shipped 2026-07-26 (#380) — merged to develop; boxes reconciled during the v0.45.0 release prep.

> **For agentic workers:** Run `/gflow:status --feature self-documenting-errors` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Make gflow errors self-documenting by surfacing sanitized provider error messages and structured, machine-parseable remediation hints (`remediation_hint`) across CLI, `--json` envelopes, and MCP tools at failure time.

**Architecture:** Domain-driven error enrichment. Extends `GFlowError` subclass defaults in `errors.py`, updates HTTP exception mapping in `client.py` to extract provider JSON error messages, and enriches error formatters in `json_output.py` and `mcp/tools.py`.

**Predict verdict:** GO — confidence 9.4/10

**Risk Register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Medium | Raw provider error strings leaking tokens or signed URLs | Pass provider text through `redact_sensitive_text()` before assignment |
| Low | MCP tool error formatting breaking string assertions | Retain standard exception message while appending `(Remediation: <hint>)` |

---

## File structure

### New files
```
tests/test_self_documenting_errors.py
  Unit test suite asserting remediation_hint and provider message pass-through
```

### Modified files
```
src/gflow_cli/errors.py
  Add default remediation_hint strings to WireFormatError, ContentPolicyError, RateLimitError, DataStoreError, etc.
src/gflow_cli/api/client.py
  Extract provider JSON message in _raise_for_non_retryable and assign to exc.detail
src/gflow_cli/mcp/tools.py
  Include remediation_hint in MCP tool error responses
src/gflow_cli/json_output.py
  Ensure remediation_hint is preserved in error_payload
tests/test_errors.py
  Assert _default_remediation and remediation_hint on all domain error classes
tests/mcp/test_tools.py
  Assert remediation hints in MCP tool error messages
```

---

## Task 1 — Domain Error Classes & Remediation Hints (Test + Implementation)

**What:** Add specific, actionable `_default_remediation` strings to all `GFlowError` subclasses in `errors.py`.

**Files:**
- `src/gflow_cli/errors.py` — Add `_default_remediation` to `WireFormatError`, `ContentPolicyError`, `RateLimitError`, `DataStoreError`, `SceneConcatError`, `FrameExtractionError`
- `tests/test_errors.py` — Assert `remediation_hint` across all error classes

**Steps:**
- [x] Add default remediation hints to `WireFormatError` ("Check request parameters or try a simpler prompt"), `ContentPolicyError` ("Reduce prompt text or describe <= 1 person per scene"), `RateLimitError` ("Daily model quota reached; retry with a different model or wait for reset"), `DataStoreError` ("Check database file permissions or run gflow data errors prune").
- [x] Update `tests/test_errors.py` to assert that every `GFlowError` class provides a non-empty `remediation_hint` in `to_problem_details()`.

---

## Task 2 — Provider Error Message Extraction in Client

**What:** Update `_raise_for_non_retryable` in `client.py` to extract exact server JSON error messages and pass them into `exc.detail`.

**Files:**
- `src/gflow_cli/api/client.py` — Update `_raise_for_non_retryable` to extract `error.json.message` from body text
- `tests/api/test_client_errors.py` — Unit test provider message extraction

**Steps:**
- [x] Parse `body_text` in `_raise_for_non_retryable` to extract `message` or `data.code` if JSON-formatted.
- [x] Pass the extracted provider message (sanitized via `redact_sensitive_text`) into `AuthExpiredError`, `ContentPolicyError`, `WireFormatError`, and `RateLimitError`.
- [x] Add unit tests asserting provider message pass-through in `tests/api/test_client_errors.py`.

---

## Task 3 — MCP Tool Error Payload Formatting

**What:** Update MCP tool exception handlers in `mcp/tools.py` to surface `remediation_hint` to AI agents.

**Files:**
- `src/gflow_cli/mcp/tools.py` — Format tool exception responses to include `[error_class] detail (Remediation: hint)`
- `tests/mcp/test_tools.py` — Assert remediation hints in MCP error output

**Steps:**
- [x] Update helper `_format_mcp_error(exc: GFlowError)` in `mcp/tools.py` to include `remediation_hint` if present.
- [x] Add unit tests in `tests/mcp/test_tools.py` verifying MCP error payload formatting.

---

## Task 4 — CLI Helper & JSON Output Enrichment

**What:** Ensure `_cli_helpers.py` Rich error renderer and `json_output.py` include `remediation_hint` on all error outputs.

**Files:**
- `src/gflow_cli/_cli_helpers.py` — Display `Remediation:` panel in Rich CLI error handler
- `src/gflow_cli/json_output.py` — Include `remediation_hint` in `error_payload`
- `tests/test_json_output.py` — Assert `remediation_hint` in JSON output

**Steps:**
- [x] Update `_handle_gflow_error` in `_cli_helpers.py` to print `Remediation: <hint>` when present.
- [x] Add unit tests asserting `remediation_hint` in `tests/test_json_output.py` and `tests/cli/test_helpers.py`.

---

## Task 5 — Full Verification & Quality Gates

**What:** Run full quality gates and create unit test suite `tests/test_self_documenting_errors.py`.

**Files:**
- `tests/test_self_documenting_errors.py` — End-to-end unit test suite for self-documenting errors

**Steps:**
- [x] Create `tests/test_self_documenting_errors.py` asserting remediation hints across domain errors, client raises, CLI output, and MCP tools.
- [x] Run full Impeccable Routine (`/gflow:check`).

---

## Definition of Done

- [x] All task steps checked off
- [x] All `GFlowError` classes provide non-empty `remediation_hint`
- [x] Provider JSON error messages pass through to `exc.detail` (sanitized)
- [x] MCP tool error messages include remediation guidance for AI agents
- [x] `/gflow:check` green (`ruff`, `pyright`, `pytest` coverage >= 80%)
- [x] `CHANGELOG.md` `[Unreleased]` section updated
