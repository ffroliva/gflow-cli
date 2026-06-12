# Patchright Optional Browser Engine — Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature patchright-engine` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every
> commit. **Phase 1 is BLOCKED until Phase 0 (the spike kill-gate) passes.**

**Goal:** Offer Patchright as an opt-in, fully-reversible alternative browser engine
(`GFLOW_CLI_BROWSER_ENGINE=patchright`, default `playwright` unchanged) that hardens the
existing **headed** real-Chrome path against the `Runtime.enable`/`Console.enable` CDP
leaks reCAPTCHA Enterprise can fingerprint — **but only if a credit-free spike first
proves it actually mints tokens, preserves network listeners, and measurably reduces
403s on a hot profile.**

**Architecture:** No root-level shim. Engine selection is a call-time resolver inside the
infrastructure tier (`src/gflow_cli/api/_engine.py`) that re-exports `async_playwright`
**and** the `Error`/`TimeoutError` classes from the active engine, mirroring the existing
`make_transport()` factory + `pydantic-settings` pattern. The two real launch sites
(`client.py:_persistent_context_kwargs()` and `ui_automation.py`) consume the resolver and
apply per-engine flag/mask de-confliction runtime-gated on `engine=="patchright"`; the
default `playwright` path stays byte-identical. `patchright` is an optional dependency;
nothing in the default install or default runtime changes.

**Predict verdict:** CAUTION — 6/10 (spike-gate the refactor). Five personas unanimous.

**Scenario source:** `./SCENARIO.md` — 22 scenarios, 6 marked `[SPIKE-GATE]`.

**Governing prior decisions (must reconcile):**
- **ADR #13 + "CDP Attach Transport — BACKLOG" (PLAN.md:629-646, 681).** The repo already
  hit this exact problem (Phase 5: `batchGenerateImages` 403 from `navigator.webdriver=true`),
  designed a CDP-attach alternative, and **parked it "until the stealth-flag fix
  (`--disable-blink-features=AutomationControlled` + init script) is confirmed
  insufficient."** Patchright is a *third* option for the same problem under the same gate.
  **Phase 0's hot-profile 403→200 test (scenario 2) IS that "confirm the stealth fix is
  insufficient" measurement.** If the current stealth fix already holds on a clean profile
  (it does — clean profiles succeed; only hot profiles 403), the precondition for *any*
  alternative engine is unmet and the project should stop.
- **ADR #9 (no SaaS deps, YAGNI for a local CLI).** Patchright ships a patched Chromium
  binary from a single maintainer — it cuts against #9. Justified ONLY if Phase 0 proves a
  real, current benefit; otherwise abandon.
- **ADR #1 (hybrid Playwright+REST; mint needs a real browser).** Unchanged — Patchright is
  still a real browser; the resolver preserves the hybrid model.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | reCAPTCHA mint breaks under Patchright's `isolated_context=True` default (`grecaptcha` is main-world) | Force `isolated_context=False` for Patchright on `recaptcha.py:97,64`; **proven in Phase 0 before anything else** (scenario 1) |
| Critical | Premise may be false — observed 403s are per-profile WAF heat, not a CDP leak (KNOWN_ISSUES; PLAN.md:631) | Phase 0 scenario 2 is a hard kill-gate: no hot-profile 403→200 improvement → STOP |
| Critical | Default `playwright` path regresses to `webdriver=true` via shared/duplicated kwargs at TWO non-shared launch sites | De-confliction runtime-gated on `engine=="patchright"` at both `client.py` + `ui_automation.py`; regression test asserts `webdriver===undefined` on default engine for both transports (scenario 3) |
| Critical | `page.on("response")` video-completion miss = burned credit | Phase 0 asserts listener fires AND body parses (scenario 4); paid live e2e for video before any default discussion (scenario 5) |
| Critical | `_retry.py` exception classes differ across engines → retries misfire | Resolver re-exports `Error`/`TimeoutError`; `_retry.py` imports from resolver; forced-timeout unit test (scenario 6) |
| High | `channel="chrome"` may be ignored (bundled patched Chromium → exit-33) or honoured (stealth benefit unproven on system build) | Phase 0 decides + documents (scenario 7) |
| High | Supply chain: patched-Chromium binary touches Google session cookies/Bearer/SAPISID | Optional-only dep, exact pin + uv.lock hash, SECURITY.md threat row, version bumps = security-review (not dependabot auto-merge) |
| High | Missing-dependency UX: raw `ImportError`/mid-generation trace | `BrowserEngineUnavailableError` exit 24 with distinct hints for pkg-missing vs driver-missing (scenarios 9-11) |
| Medium | Sequencing — #174 (High, no workaround) is higher priority | Phase 0 is cheap/parallelizable; Phase 1 must not preempt #174 |

---

## File structure

### New files
```
scripts/dev/spike_patchright.py
  Phase 0 credit-free spike on the _spike_common.py harness — the kill-gate.
docs/superpowers/plans/2026-06-12-patchright-engine/SPIKE-RESULTS.md
  Phase 0 output: per-leg pass/fail + the go/no-go decision (written by Phase 0).
src/gflow_cli/api/_engine.py
  (Phase 1) Call-time engine resolver: resolve_async_playwright() + resolve_pw_errors();
  ImportError → BrowserEngineUnavailableError; optional driver preflight.
tests/api/test_engine.py
  (Phase 1) Resolver unit tests: default playwright, missing-pkg → exit 24, error re-export.
tests/features/browser_engine.feature
  (Phase 1) BDD from SCENARIO.md.
tests/features/test_browser_engine.py
  (Phase 1) BDD step defs.
```

### Modified files (Phase 1 only)
```
src/gflow_cli/config.py            + BrowserEngine StrEnum + browser_engine Settings field
src/gflow_cli/errors.py            + BrowserEngineUnavailableError, EXIT_CODE_MAP[...] = 24
src/gflow_cli/api/_retry.py        import Error/TimeoutError from _engine, not playwright
src/gflow_cli/api/client.py        _persistent_context_kwargs engine-gated; resolver; isolated_context=False for patchright
src/gflow_cli/api/transports/ui_automation.py  add _persistent_context_kwargs seam; engine-gated mask/flags; resolver
src/gflow_cli/api/recaptcha.py     isolated_context=False on mint(:97) + discover_site_key(:64) for patchright
src/gflow_cli/cli.py               `browser_engine:` line in `gflow auth status`
src/gflow_cli/observability.py     (if needed) browser.engine_selected emitter
pyproject.toml                     [project.optional-dependencies] patchright = ["patchright==X.Y.Z"]
uv.lock                            pinned hash for patchright
.env.template                      GFLOW_CLI_BROWSER_ENGINE block
docs/CONFIGURATION.md              env var reference
docs/AUTHENTICATION.md             headed-only note (NOT a headless unlock)
docs/ARCHITECTURE.md              browser.engine_selected event + resolver design
docs/USAGE.md / AGENTS.md          exit-code tables (backfill 23, add 24)
SECURITY.md                        patched-Chromium supply-chain threat row
CHANGELOG.md                       [Unreleased] entry
```

---

# PHASE 0 — Credit-free spike (KILL-GATE). Touches zero production files.

## Task 0.1 — Spike harness + throwaway Patchright install

**What:** Stand up `scripts/dev/spike_patchright.py` on the existing `_spike_common.py`
harness; install `patchright` into the worktree venv only.

**Files:**
- `scripts/dev/spike_patchright.py` — new
- (no `pyproject.toml` change — install is local/throwaway)

**Steps:**
- [ ] `.venv/Scripts/python.exe -m pip install patchright` (worktree venv only); record the resolved Patchright + effective Playwright version.
- [ ] `.venv/Scripts/python.exe -m patchright install chromium` — note whether it is *required* or skippable when `channel="chrome"` is used (feeds scenario 7).
- [ ] Scaffold `spike_patchright.py` reusing `_spike_common.build_client`/`resolve_profile_dir`/`step`; parameterize engine (`playwright` baseline vs `patchright`) and profile via env (per the e2e-parameterize rule).
- [ ] Launch the real existing Chrome profile via `patchright.async_api` with the SAME persistent-context kwargs the production path uses (`channel=channel_for_profile(...)`, `--disable-blink-features=AutomationControlled`, init-script mask), forcing `isolated_context=False` on the recaptcha evaluates.

**Tests (manual spike, not pytest):**
- [ ] Both engines import cleanly in the same venv; resolver picks the right `async_playwright`.

## Task 0.2 — Run the spike, record SPIKE-RESULTS.md, decide go/no-go

**What:** Execute the six `[SPIKE-GATE]` legs and write the kill-gate decision.

**Steps / SPIKE-GATE legs (all must be green to proceed):**
- [ ] **Leg 1 (scenario 1):** `TokenMinter.mint(action)` returns a real non-empty token under Patchright (proves `isolated_context=False` fix). *If unfixable → STOP.*
- [ ] **Leg 2 (scenario 8):** `discover_site_key()` returns the site key under Patchright.
- [ ] **Leg 3 (scenario 4):** register `page.on("response")`; fire a credit-free `batchGenerateImages`; assert the listener fires AND `await response.json()` is a non-empty dict with a media URL.
- [ ] **Leg 4 (scenario 13):** `_fingerprint` `page.on("request")` sees non-empty `request.post_data`.
- [ ] **Leg 5 (scenario 7):** record whether `channel="chrome"` is honoured (system Chrome) or forced-bundled; confirm no exit-33 on the real Chrome-130+ profile. Document the decision.
- [ ] **Leg 6 (scenario 2, DECISIVE):** on a hot profile (`denon82`, documented WAF heat) run a credit-free image gen under Playwright (baseline) and Patchright; record 403-vs-200 for each. **Pass = Patchright measurably flips 403→200.**
- [ ] (Optional, cheap) Also attach via CDP to user-launched Chrome (`connect_over_cdp`) and record its 403-vs-200 on the same hot profile — ADR #13's parked alternative; one data point to compare against Patchright before committing to the heavier dependency.
- [ ] Write `SPIKE-RESULTS.md`: per-leg result + verdict.

**Pass-bar (gate):**
- ALL six legs green → unblock Phase 1.
- ANY leg red — especially Leg 1 unfixable OR Leg 6 showing no 403→200 improvement → **STOP**. Record in SPIKE-RESULTS.md, recommend **abandon in favour of cadence-shaping** (tune the existing `--jitter`/cool-down on the batch path — the documented fix for WAF heat) and/or the parked CDP-attach transport if its data point was better. Do not enter Phase 1.

---

# PHASE 1 — Full opt-in integration (BLOCKED until Phase 0 green). TDD.

## Task 1 — Test scaffold (red): BDD + resolver/error unit tests

**What:** Author failing tests from SCENARIO.md before any production code.

**Files:** `tests/features/browser_engine.feature`, `tests/features/test_browser_engine.py`, `tests/api/test_engine.py`, additions to `tests/api/test_retry.py`, `tests/test_errors.py`.

**Steps:**
- [ ] Copy the five BDD `Scenario:` blocks from SCENARIO.md into `browser_engine.feature`; stub step defs (red).
- [ ] Unit: default engine resolves to `playwright`; bad enum value → `ConfigurationError` (exit 11) naming the key.
- [ ] Unit: `engine=patchright` with package absent → `BrowserEngineUnavailableError` (exit 24), hint contains `pip install patchright`.
- [ ] Unit: forced `TimeoutError` from each engine is matched by `_retry.py`'s classification.

**Tests created (red):** all of the above + `test_exit_code_map_ordering_invariant` extended for code 24.

## Task 2 — `GFLOW_CLI_BROWSER_ENGINE` typed Settings field

**What:** Add a `BrowserEngine` StrEnum + `browser_engine` field to `Settings` (default `playwright`), not raw `os.getenv`.

**Files:** `src/gflow_cli/config.py`.

**Steps:**
- [ ] `class BrowserEngine(StrEnum): PLAYWRIGHT="playwright"; PATCHRIGHT="patchright"`.
- [ ] `browser_engine: BrowserEngine = Field(default=BrowserEngine.PLAYWRIGHT, description=...)`.
- [ ] Respect the `reset_settings()` test seam.

**Tests:** green the bad-value + default unit tests from Task 1.

## Task 3 — `BrowserEngineUnavailableError` + exit code 24

**What:** New typed error, registered in `EXIT_CODE_MAP` (ordering invariant), distinct remediation for pkg-missing vs driver-missing.

**Files:** `src/gflow_cli/errors.py`, docs exit tables (Task 10).

**Steps:**
- [ ] Define `BrowserEngineUnavailableError` (RFC 9457 fields; `_default_remediation`).
- [ ] Register `EXIT_CODE_MAP[BrowserEngineUnavailableError] = 24`, ordered before `ConfigurationError` if subclassed.
- [ ] Two remediation variants: `pip install patchright` vs `patchright install chromium`.

**Tests:** green ordering-invariant + exit-24 unit tests.

## Task 4 — Engine resolver `api/_engine.py`

**What:** Call-time resolver re-exporting `async_playwright` + `Error`/`TimeoutError` from the active engine; ImportError → exit 24; optional driver preflight.

**Files:** `src/gflow_cli/api/_engine.py` (new), `tests/api/test_engine.py`.

**Steps:**
- [ ] `resolve_async_playwright(engine)` and `resolve_pw_errors(engine)` reading `get_settings().browser_engine` by default.
- [ ] Wrap the patchright import; on `ImportError` raise `BrowserEngineUnavailableError` (pkg hint).
- [ ] Cheap driver preflight (or wrap launch failure) → exit 24 (driver hint). Skip the preflight if Phase 0 Leg 5 proved `channel="chrome"` needs no driver.
- [ ] Emit `browser.engine_selected` (engine field only; no secrets).

**Tests:** green resolver unit tests; LogCapture asserts the event carries no token/cookie.

## Task 5 — `_retry.py` exception-class identity

**What:** Import `Error`/`TimeoutError` from the resolver, not `playwright` directly.

**Files:** `src/gflow_cli/api/_retry.py`.

**Steps:**
- [ ] Replace `from playwright.async_api import Error, TimeoutError` with resolver-sourced classes for the active engine; keep `retry_if_exception_type` matching.

**Tests:** green the forced-timeout-per-engine test.

## Task 6 — De-conflict launch kwargs at BOTH sites (engine-gated) + isolated_context fix

**What:** Runtime-gate the stealth-flag/mask de-confliction on `engine=="patchright"` at `client.py` AND `ui_automation.py`; give `ui_automation.py` the same `_persistent_context_kwargs` seam; force `isolated_context=False` for patchright on the recaptcha evaluates. Default `playwright` byte-identical.

**Files:** `src/gflow_cli/api/client.py`, `src/gflow_cli/api/transports/ui_automation.py`, `src/gflow_cli/api/recaptcha.py`.

**Steps:**
- [ ] Extract a `_persistent_context_kwargs()` override seam in `ui_automation.py` (mirrors `client.py:287`).
- [ ] When `engine=="patchright"`: drop the manual `--enable-automation` removal / `--disable-blink-features=AutomationControlled` / `add_init_script` webdriver mask (Patchright manages them); consider `no_viewport=True`. When `engine=="playwright"`: unchanged.
- [ ] Route both launch calls through `resolve_async_playwright(engine)`.
- [ ] `recaptcha.py:97` and `:64`: pass `isolated_context=False` only when engine is patchright (Patchright-only kwarg).

**Tests:** regression — `navigator.webdriver===undefined` on the **default** engine for both `client.py` and `ui_automation.py` paths; engine-gating unit tests assert flags/mask present for playwright, absent for patchright.

## Task 7 — Wire runtime launch sites through the resolver

**What:** Point the runtime `async_playwright()` callers at the resolver. Type-only imports (`BrowserContext`/`Page`/`Locator`/`Request` under `TYPE_CHECKING`) stay sourced from `playwright` (Patchright objects are API-compatible; pyright checks hold).

**Files:** `client.py`, `auth/strategies.py`, `browser_manager.py`, `transports/experimental/{bearer,evaluate_fetch,sapisidhash}.py`, `transports/ui_automation.py`.

**Steps:**
- [ ] Replace runtime `from playwright.async_api import async_playwright` with the resolver call at each launch site (8 runtime sites).
- [ ] Leave `TYPE_CHECKING` type imports as-is; confirm `pyright src` clean (whole tree, per the gate).

**Tests:** existing test-double monkeypatch paths updated to patch the resolver seam, not `playwright.async_api` directly.

## Task 8 — Optional dependency + supply-chain hardening

**Files:** `pyproject.toml`, `uv.lock`, `SECURITY.md`.

**Steps:**
- [ ] `[project.optional-dependencies] patchright = ["patchright==<pinned>"]`; NOT in core `dependencies`.
- [ ] `uv lock` to pin the hash; `uv lock --check` clean (per the release-drift rule).
- [ ] SECURITY.md threat row: patched-Chromium binary handles Google session cookies/Bearer/SAPISID; version bumps require security review (not dependabot auto-merge).

**Tests:** `uv lock --check` green; default install (`pip install gflow-cli`) does NOT pull patchright (verify).

## Task 9 — Observability + diagnostics

**Files:** `src/gflow_cli/cli.py`, `docs/ARCHITECTURE.md`.

**Steps:**
- [ ] `browser_engine:` line in `gflow auth status`.
- [ ] Document the `browser.engine_selected` event key in ARCHITECTURE.md.

**Tests:** unit asserts the status line; LogCapture asserts the event key + no-secret invariant.

## Task 10 — Docs

**Files:** `.env.template`, `docs/CONFIGURATION.md`, `docs/AUTHENTICATION.md`, `docs/USAGE.md`, `AGENTS.md`.

**Steps:**
- [ ] `.env.template` `GFLOW_CLI_BROWSER_ENGINE` block (playwright default; opt-in; `pip install patchright`; channel=chrome may skip driver download; revert by unsetting).
- [ ] CONFIGURATION.md env reference; AUTHENTICATION.md note: **headed-only, NOT a headless unlock**.
- [ ] Exit-code tables: backfill missing **23**, add **24**.

## Task 11 — Live verification ledger + CHANGELOG (default stays playwright)

**What:** Full-credit live e2e under Patchright proving the whole chain; default engine UNCHANGED (flipping default is OUT OF SCOPE).

**Steps:**
- [ ] `image t2i` under `GFLOW_CLI_BROWSER_ENGINE=patchright`: verify file count + PNG magic bytes + Pillow dims + structlog `reference_attached`/`batch_response_captured` events.
- [ ] `video i2v` under patchright (1 credit): verify mp4 `ftyp` magic + the completion listener fired (scenario 5 — the burned-credit risk).
- [ ] CHANGELOG `[Unreleased]`: "Added opt-in `GFLOW_CLI_BROWSER_ENGINE=patchright` (experimental; default unchanged)."
- [ ] Confirm `GFLOW_CLI_BROWSER_ENGINE` unset → behaviour byte-identical to today.

---

## Definition of done

- [ ] **Phase 0 SPIKE-RESULTS.md records all six legs green** (or a documented STOP).
- [ ] All Phase 1 task steps checked off.
- [ ] `/gflow:check` green (ruff + `ruff format --check` + `pyright src` whole-tree + pytest ≥ 80%). Use `.venv/Scripts/python.exe -m pytest` (uv run pytest is broken on this Windows box).
- [ ] BDD feature covers all Critical + High scenarios from SCENARIO.md.
- [ ] Default `playwright` path proven byte-identical (`webdriver===undefined` on both transports; unset env → no behaviour change).
- [ ] `uv lock --check` clean; default install does not pull patchright.
- [ ] `CHANGELOG.md` `[Unreleased]` updated; SECURITY.md threat row added; exit-code docs backfilled (23) + extended (24).
- [ ] No `# TODO` in the diff without a tracked issue link.
- [ ] **Out of scope (do not do in this PR):** flipping the default engine to patchright; headless support; removing the playwright dependency.
