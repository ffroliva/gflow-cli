# Scenario: Issue #315 — Driver Delay Jittering & Interaction Humanization

## Coverage Map
- **Active Dimensions:**
  - **D2 (WAF / reCAPTCHA scoring):** Randomizing wait delays breaks deterministic timing signatures that static `wait_for_timeout(N)` calls emit.
  - **D5 (Concurrency & Page pool):** Ensure randomized delays do not cause worker thread or page checkout deadlocks.
  - **D11 (Input validation & boundaries):** Bounds checking — `_jitter_ms(0)` must return `0` (never negative or fractional ms).
- **Skipped Dimensions:** D1, D3, D4, D6, D7, D8, D9, D10, D12 (unaffected by internal delay helper).

---

## Scenario Table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D11 Boundaries | `_jitter_ms(0)` is called | High | Returns `0` strictly (no negative delays) | Unit |
| 2 | D2 WAF entropy | `_jitter_ms(1000, variance=0.25)` is called 100 times | High | Returns values bounded between 750ms and 1250ms with non-zero variance | Unit |
| 3 | D5 Concurrency | `_wait_jitter(page, base_ms)` is awaited | High | Calls `page.wait_for_timeout(jittered_ms)` with integer milliseconds | Unit |

---

## Must-Cover Before Merge
1. Add `_jitter_ms(base_ms: int, variance: float = 0.25) -> int` to `ui_automation.py`.
2. Add `_wait_jitter(page: Page, base_ms: int, variance: float = 0.25)` helper method in `UiAutomationTransport`.
3. Update key interaction delay calls (`wait_for_timeout`) in `ui_automation.py` to use `_wait_jitter`.
4. Unit tests in `tests/api/transports/test_ui_automation.py` validating `_jitter_ms` boundary conditions and variance.
