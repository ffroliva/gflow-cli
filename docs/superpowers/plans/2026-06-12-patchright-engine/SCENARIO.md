# Scenario: Patchright optional browser engine (spike-first)

> Feeds the PLAN for `feature/patchright-engine`. Upstream: `/gflow:predict` verdict
> **CAUTION 6/10** (spike-gate the 14-file refactor). The spike
> (`scripts/dev/spike_patchright.py`) is the investigation gate; the full opt-in
> engine toggle is gated behind its pass-bar. Scenarios tagged **[SPIKE-GATE]**
> must pass in the spike before any production file is touched.

## Coverage map

| Dim | Active? | Why |
|-----|---------|-----|
| D1 Auth & session lifecycle | **Yes** | Engine swap re-launches the persistent Chrome profile; `channel="chrome"` honouring is unverified for Patchright. |
| D2 WAF / reCAPTCHA | **Yes (crux)** | Mint runs via `page.evaluate`; Patchright's isolated-context default + the de-confliction both threaten the bot-score path. |
| D3 Selector cascade drift | Partial | Engine swap does not change selectors, but `discover_site_key` uses `querySelectorAll` under Patchright's isolated world — one scenario. |
| D4 Batch manifest & resume | Skip | Engine swap is below the manifest layer; no resume idempotency change. Covered indirectly by D5/D9. |
| D5 Concurrency & Page pool | **Yes** | 16 concurrent isolated-context mints; pool/`__aexit__` lifecycle under a patched driver. |
| D6 Data layer | Skip | No SQLite/migration/redaction change. (Secret-leak risk handled in D12.) |
| D7 Error propagation & exit codes | **Yes** | New `BrowserEngineUnavailableError` (exit 24), `ImportError` handling, and the `_retry.py` exception-class identity break. |
| D8 Cross-platform paths | **Yes** | Windows-primary; separate Patchright driver cache; `channel="chrome"` skip-download question. |
| D9 Transport edge cases | **Yes (crux)** | `page.on("response")`/`page.on("request")` must fire **and** bodies parse — a missed video-completion event burns a credit. |
| D10 Headless vs headed | **Yes** | Explicitly headed-only; must prove it and document the non-unlock; `channel`/exit-33 interaction. |
| D11 Input validation | **Yes** | `GFLOW_CLI_BROWSER_ENGINE` typed enum; bad-value handling. |
| D12 Observability | **Yes** | `browser.engine_selected` structlog event; engine surfaced in `auth status`; no secret leak in new events. |

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|-----------|----------|----------|--------------------|---------------|
| 1 | D2 WAF/reCAPTCHA | **[SPIKE-GATE]** `TokenMinter.mint()` runs under Patchright's `isolated_context=True` default; `_EXECUTE_JS` reads main-world `grecaptcha.enterprise` (`recaptcha.py:97`) → `undefined` in isolated world → mint raises "grecaptcha.enterprise not loaded" → every generation 403s. | **Critical** | Shim/minter forces `isolated_context=False` for Patchright on `recaptcha.py:97` and `:64`; `mint()` returns a real `0cAFc…` token against a loaded Flow editor page. If unfixable → STOP for Patchright engine. | E2E live (spike) |
| 2 | D2 WAF/reCAPTCHA | **[SPIKE-GATE]** Premise check: a **hot** profile (`denon82`, documented WAF heat) that 403s under Playwright — does Patchright flip it to 200? | **Critical** | Measurable 403→200 vs the Playwright baseline on a **credit-free** image gen. If no improvement, the whole refactor is unjustified (observed cause is WAF heat/cadence, not a CDP leak). | E2E live (spike, $0) |
| 3 | D2 WAF/reCAPTCHA | Default `playwright` path regresses to `navigator.webdriver=true` after the de-confliction strips the stealth flags/mask trusting Patchright to manage them. | **Critical** | De-confliction is runtime-gated on `engine=="patchright"` at **both** `client.py:_persistent_context_kwargs()` (287-326) **and** `ui_automation.py` (708-724, which inlines its own args + mask). `navigator.webdriver===undefined` holds on the default engine for **both** transports. | Integration + E2E |
| 4 | D9 Transport | **[SPIKE-GATE]** `page.on("response")` for `batchGenerateImages` (`ui_automation.py:1691`) fires under Patchright **and** `await response.json()` parses to a non-empty dict with a media URL. | **Critical** | Listener fires AND body parses (not just callback invoked). Network domain is un-patched by Patchright, so expected to hold — but a silent miss = no image. | E2E live (spike) |
| 5 | D9 Transport | Video status/completion listener (`ui_automation_video.py:519/542/1142`) fires under Patchright — a missed completion event burns a paid credit with no file. | **Critical** | Completion event captured + parsed end-to-end; full-credit live e2e (paid) on `video i2v` before default flip. Deferred from spike (costs a credit) to full-feature live verify. | E2E live (paid) |
| 6 | D7 Error/exit | `_retry.py:25-26` imports `playwright` `Error`/`TimeoutError` for `retry_if_exception_type`; Patchright raises **its own** class objects → retries stop matching → paid generation fails without retry. | **Critical** | Shim re-exports `Error`/`TimeoutError` from the **active** engine; `_retry.py` imports from the shim. Forced `TimeoutError` under Patchright is caught by the existing retry classification. | Unit + Integration |
| 7 | D1 / D10 | **[SPIKE-GATE]** `channel=channel_for_profile()` returns `"chrome"` (Chrome-130+ profile, avoids exit-33). Does Patchright honour `channel="chrome"` (system Chrome) or force its bundled patched Chromium (re-triggering exit-33 downgrade, or nullifying the stealth benefit)? | **Critical** | Spike determines + documents one of: (a) honours system Chrome → stealth benefit unproven on system build; (b) forces bundled → must confirm no exit-33 on the real profile. Decision recorded in PLAN. | E2E live (spike) |
| 8 | D3 / D2 | `discover_site_key()` (`recaptcha.py:64`, `querySelectorAll`) under Patchright's isolated world. | **High** | DOM is shared across worlds → site key discovered. Verify in spike; if isolated-world DOM differs, also force `isolated_context=False`. | E2E live (spike) |
| 9 | D7 Error/exit | `GFLOW_CLI_BROWSER_ENGINE=patchright` but the `patchright` package is **not pip-installed** → `ImportError` at import. | **High** | Caught at the engine-resolver seam, re-raised as `BrowserEngineUnavailableError` (exit **24**) with remediation `pip install patchright`. Never a raw `ImportError` (which gets SHA-hashed → generic exit 1). | Unit |
| 10 | D7 Error/exit | `patchright` installed but `patchright install chromium` never run → driver missing, fails at `launch_persistent_context` **mid-generation**. | **High** | Cheap preflight (or wrapped launch error) → `BrowserEngineUnavailableError` exit 24 with `patchright install chromium` hint. Never a mid-generation stack trace after a user thinks setup worked. (Verify whether `channel="chrome"` makes the driver download skippable — scenario 7.) | Integration |
| 11 | D7 Error/exit | New `BrowserEngineUnavailableError` breaks the `EXIT_CODE_MAP` most-specific-first ordering invariant. | **High** | Registered at exit 24, ordered before `ConfigurationError` if subclassed; `test_exit_code_map_ordering_invariant` passes. `AGENTS.md`/`docs/USAGE` exit tables updated (also backfill missing 23). | Unit |
| 12 | D5 Concurrency | `GFLOW_CLI_CONCURRENCY=16` fans out 16 simultaneous isolated-context mints under Patchright; site-key/world state shared safely? | **High** | No cross-page state contamination; Page pool (`_checkout_page`/`_checkin_page`), `asyncio.Queue`, `QueueFull` semantics unchanged (engine-agnostic per Performance review). | Integration + E2E smoke |
| 13 | D9 Transport | `_fingerprint.py:119` `page.on("request")` reads `request.post_data` under Patchright. | **High** | `post_data` present and unmodified; fingerprint captured. | E2E live (spike) |
| 14 | D12 Observability | Engine-selection logging / new `_pw` resolver leaks the captured Bearer (`bearer.py:243`), SAPISID, or minted reCAPTCHA token into a structlog event. | **High** | No secret in any new event; `redact_metadata` + `show_locals=False` (`observability.py:83`) preserved. New events carry only `engine=playwright|patchright`. | Unit (LogCapture) |
| 15 | D10 Headless | `GFLOW_CLI_HEADLESS=true` + `engine=patchright` — user expects headless to now evade Google. | **High** | Documented as **NOT a headless unlock** (Patchright maintainer: headless leaks `HeadlessChrome` UA on Google). Behaves like Playwright headless (degraded); optional warning when both set. | Doc + Integration |
| 16 | D11 Input val | `GFLOW_CLI_BROWSER_ENGINE=patchwright` (typo) or any non-enum value. | **Medium** | Typed `StrEnum` Settings field → pydantic `ValidationError` → `ConfigurationError` (exit 11) naming the offending key at startup, not a deep launch crash. Not raw `os.getenv`. | Unit |
| 17 | D1 Auth | Profile verified under Playwright, then engine switched to Patchright on the same profile dir. | **Medium** | Session/cookies reused (same `user_data_dir`); no re-login. `.gflow_browser_strategy` marker is engine-agnostic. | E2E smoke |
| 18 | D8 Cross-platform | Windows: Patchright downloads its **own** patched Chromium cache (separate from `%LOCALAPPDATA%\ms-playwright`); ~350 MB extra, wasted if `channel="chrome"`. | **Medium** | Documented in `.env.template`/docs; note driver download may be skippable with `channel="chrome"` (pending scenario 7). No `PYTHONUTF8` regressions (I/O already pins utf-8). | Doc |
| 19 | D12 Observability | `browser.engine_selected` structlog event emitted once at launch. | **Medium** | Stable key, documented in `docs/ARCHITECTURE.md`; carries `engine` field + `correlation_id`. | Unit (LogCapture) |
| 20 | D5 / lifecycle | `FlowApiClient.__aexit__` → `_close_browser_resources` under a patched driver leaves state. | **Medium** | `context.close()` + `pw.stop()` implemented by Patchright; no leaked process/temp profile. | Integration |
| 21 | D12 Observability | `gflow auth status` gives no way to see which engine launched when debugging a two-engine setup. | **Low** | Add a `browser_engine:` line to `auth status` output. | Unit |
| 22 | D9 Transport | Patchright's Route-based init-script injection collides with an app `page.route(..., abort)`. | **Low** | No production `page.route` exists (only a `video.py:70` docstring referencing a dev probe). No collision. Documented; no action. | N/A |

## Must-cover before merge (Critical + High)

**Spike gate (must pass before ANY production file is edited) — scenarios 1, 2, 4, 7, 8, 13:**
1. Patchright mints a real reCAPTCHA token (isolated-context fix proven) — **scenario 1**.
2. Hot-profile 403→200 vs Playwright baseline on credit-free image gen — **scenario 2** (premise validation; a null result here ends the project, cheaply).
3. `page.on("response")` fires AND `batchGenerateImages` body parses — **scenario 4**.
4. `channel="chrome"` behaviour under Patchright decided + documented (system vs bundled, exit-33) — **scenario 7**.
5. `discover_site_key` + `_fingerprint` request `post_data` work under Patchright — **scenarios 8, 13**.

**Full-feature (gated behind a green spike) — scenarios 3, 5, 6, 9, 10, 11, 12, 14, 15:**
6. Default `playwright` path stays byte-identical: `navigator.webdriver===undefined` on **both** transports; de-confliction runtime-gated at both launch sites — **scenario 3**.
7. Paid live e2e proves the video-completion listener fires under Patchright (no burned credit) — **scenario 5**.
8. `_retry.py` exception classes re-exported from the active engine; retry classification holds — **scenario 6**.
9. Missing-dependency UX: both `ImportError` and missing-driver map to `BrowserEngineUnavailableError` exit 24 with distinct remediation hints — **scenarios 9, 10**; `EXIT_CODE_MAP` ordering invariant green — **scenario 11**.
10. 16-way concurrent mint has no state contamination — **scenario 12**.
11. No secret leak in new events; headed-only documented — **scenarios 14, 15**.

## Deferred (Medium + Low — log, do not block)

- Engine reuse across a profile (17), Windows driver-cache size/docs (18), `engine_selected` event + `auth status` line (19, 21), `__aexit__` cleanup confirmation (20), Route/abort non-collision note (22). Bad-enum validation (16) is cheap — fold into the Settings-field unit test rather than deferring.

## Suggested BDD scenarios (for `tests/features/`)

```gherkin
Feature: Browser engine selection (Patchright opt-in)

  Scenario: Default engine is unchanged and stays stealthed
    Given GFLOW_CLI_BROWSER_ENGINE is unset
    When the Flow client launches its persistent context
    Then the active engine is "playwright"
    And navigator.webdriver evaluates to undefined on the ui_automation transport
    And navigator.webdriver evaluates to undefined on the client.py launch path

  Scenario: Selecting an uninstalled engine fails with a clear remediation
    Given GFLOW_CLI_BROWSER_ENGINE is "patchright"
    And the patchright package is not installed
    When any browser-backed command runs
    Then it exits with code 24
    And the error remediation contains "pip install patchright"
    And no raw ImportError traceback is shown

  Scenario: An invalid engine value is rejected at startup
    Given GFLOW_CLI_BROWSER_ENGINE is "patchwright"
    When settings are loaded
    Then a configuration error names GFLOW_CLI_BROWSER_ENGINE
    And it exits with code 11

  Scenario: Retry classification survives the engine boundary
    Given GFLOW_CLI_BROWSER_ENGINE is "patchright"
    When a navigation raises the engine's TimeoutError
    Then the retry layer classifies it as retryable
    And the configured retry policy is applied

  Scenario: Engine selection is observable and leaks no secrets
    Given a browser-backed command runs
    When the persistent context launches
    Then a "browser.engine_selected" event is emitted with the engine name
    And no event field contains a bearer token, SAPISID, or reCAPTCHA token
```

## Known-issues cross-reference

- **`batchGenerateImages` 403 / `PUBLIC_ERROR_UNUSUAL_ACTIVITY` (WAF heat)** — the proposal's motivation. KNOWN_ISSUES documents the cause as **per-profile WAF heat from burst cadence**, not a static CDP leak (clean profiles succeed). Scenario 2 is the decisive test of whether Patchright actually *mitigates* this; if it does not, this scenario **blocks** the value case and the refactor should be abandoned in favour of cadence shaping. **Status: premise unproven → spike resolves.**
- **exit-33 profile downgrade-cleanup** (`browser_manager.channel_for_profile`) — Chrome-130+ profiles need `channel="chrome"` to avoid bundled-Chromium downgrade. Scenario 7 tests whether Patchright re-introduces this. **Status: mitigated-by-design only if Patchright honours `channel`; spike confirms.**
- **`UiSelectorDriftError` exit 23** (`ui-selector-drift-error-exit-23`) — engine swap does not touch selectors, but `discover_site_key` (scenario 8) shares the DOM-query path; low interaction. **Status: not impacted.**
- **#174 Flow library-UI A/B drift (High, no workaround on affected accounts)** — unrelated surface, but flagged by Devil's Advocate as **higher-priority open work**. PLAN should note sequencing: this spike is cheap and parallelizable, but the full refactor should not preempt #174. **Status: sequencing note.**
