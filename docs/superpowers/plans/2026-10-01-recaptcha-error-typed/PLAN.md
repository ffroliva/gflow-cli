# PLAN — `RecaptchaError` joins the error taxonomy (#915)

Triage: CONFIRMED-BUG. Spike: [2026-10-01-recaptcha-error-shape](../../spikes/2026-10-01-recaptcha-error-shape.md).
Predict: **GO 7/10**, option (b) — a `GFlowError` with its own `type` and `retryable`, **no
new exit code** (stays 1; the community-feedback-uplift policy prefers branching on `type`
over spending the last free code on a surface the default transport never reaches, #891).

## Scenarios (written at the root: `TokenMinter`, all four failure shapes)

Offline (`tests/api/test_recaptcha.py`, `tests/test_batch_outcomes.py`, `tests/worker/test_daemon.py`):

1. A mint failure is a `GFlowError` with type `…/errors/recaptcha-mint` and a remediation.
2. Site key missing from the page → not retryable; the message names the page state, not
   "the Flow editor page / script tag layout" (false on `about:blank`, #891).
3. Site-key `evaluate` itself raises (navigation race; unguarded until now) → typed,
   retryable. *(predict amendment 1)*
4. `grecaptcha.execute` evaluate raises → typed, retryable (spike arm D: 3/3 re-mint OK).
5. Empty token → typed, class default: not retryable (unmeasured, no claim).
6. A mint failure in row 0 of a 3-row batch with `--continue-on-error` → three outcomes, row 0
   `fail`, rows 1–2 run.
7. Worker/MCP: the failure arrives as Problem Details (redacted detail, not a hash), with
   `retryable` set from the raise site.

Live (`@e2e @e2e_auth`, $0, `tests/e2e/test_recaptcha_error_shape_bdd.py`):

8. Client mint on a pool page parked at `about:blank` → typed, not retryable.
9. A mint racing a navigation on a project page → typed, retryable; the re-mint succeeds.

MCP twin: no MCP tool reaches the mint on the default transport (no upscale/extend tool on
develop); scenario 7 pins the worker envelope that an experimental-transport MCP call gets.

## Tasks

- [x] 1. RED: scenarios 1–7 as tests.
- [x] 2. `errors.py`: `RecaptchaError(GFlowError)`; `api/recaptcha.py` re-exports it and
       sets `retryable` per raise site; guard the site-key `evaluate`.
- [x] 3. GREEN offline; fix tests that assumed `RuntimeError`.
- [x] 4. e2e scenarios 8–9 (feature + binding), run live on `ci-probe`: **2 passed** (32 s).
- [x] 5. Docs: CHANGELOG Fixed; USAGE exit-1 row (a typed error can exit 1); migrated-host
       memory note that called it unmapped.
- [ ] 6. `/gflow:check`, PR, council, Sonar.
