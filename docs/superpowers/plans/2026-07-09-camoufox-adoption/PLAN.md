# Camoufox Adoption Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature camoufox-adoption` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.
> This plan supersedes PR #258 (closed) — it re-expresses that contributor work as a
> gated, decomposed series. Preserve `Co-authored-by: C1ph3r404 <C1ph3r404@users.noreply.github.com>`
> on every commit that carries their code.
>
> **STATUS (2026-07-09): Phase 1 shipped (PR #273); Phase 2 verdict = STOP — the Camoufox
> engine (Phase 3) is NOT built because the current stealth fix showed a 0.0% WAF 403 rate.
> See Phase 2 below + `docs/superpowers/spikes/2026-07-09-camoufox-waf-403.md`. Phase 4 items
> remain independent backlog.**

**Goal:** Adopt Camoufox as an optional, evidence-justified stealth browser engine for
WAF-blocked users — without regressing the default (playwright) path — landing the
engine-independent fixes from PR #258 immediately and the engine itself only after it is
proven to beat the incumbent stealth story.

**Architecture:** Camoufox slots into the existing engine abstraction (`api/_engine.py` +
`config.BrowserEngine`, the patchright precedent from v0.19.0). The current port
(`resolve_async_playwright`) is too narrow for Camoufox (its own async CM yielding a Firefox
`BrowserContext`, no Playwright driver), so Phase 3 **widens `_engine.py` into a single
context-launching adapter + an `EngineQuirks` capabilities value**. All engine conditionals
resolve once at setup and thread down as quirks — never inline `settings.browser_engine ==`
checks in transports/drivers. The default and patchright paths stay byte-for-byte unchanged.

**Predict verdict:** CAUTION — confidence 7/10 (Architect 8 · Security 8 · Performance 7 ·
CLI-UX 8 · Devil's-Advocate 8). Five-persona review 2026-07-09. Full takeover-by-rebase
was rejected unanimously in favour of cherry-pick/re-express, split by concern, gated by a
WAF-evidence spike per ADR-13.

**ADR gate (blocking for Phase 3):** PLAN.md Decision Log #13 + the CDP-Attach backlog park
all stealth-engine alternatives "until the stealth-flag fix is **confirmed insufficient** —
this must be verified before implementing anything." Phase 2 produces that verification.
Camoufox engine code (Phase 3) MUST NOT merge until Phase 2 shows a real 403-rate win.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | `mcp_warmup` automates Google Search on a logged-in account → account-level flagging, larger blast radius than the Flow WAF | **Drop it** (Phase 4 re-evaluation only; not adopted). Replace with the direct `labs.google` navigation already present. |
| High | Un-gated `create_project` REST→UI rewrite = 10–30× hot-path regression (5–12 s/call, 135 s worst case) on every engine; DOM selector leaks into the API-client layer | **Do not adopt as-is.** REST stays the default `FlowApiClient.create_project`. A UI-first variant, if ever needed, lands transport-side + camoufox-gated (Phase 4). |
| High | MCP `_SessionManager` idle-reaper can `__aexit__` a client mid-generation (>5 min) — live concurrency bug | Deferred to Phase 4; needs in-use refcounting before any MCP session-caching lands. Not required for Phases 1–3. |
| Medium | Camoufox unverified vs live reCAPTCHA-Enterprise; Firefox-on-Flow is population-anomalous; engine-switch on a Chrome-authed profile silently fails | Phase 2 spike + Phase 3 profile↔engine mismatch guard + clean fresh-profile live verification. |
| Medium | Camoufox = third-party Firefox build with full live-session-cookie access (supply-chain trust); dep unpinned vs patchright exact-pin policy | Phase 3: pin exact version, document under SECURITY.md "elevated supply-chain trust", opt-in only. |
| Medium | `ref_names` invariant reversal (PR #245 image-i2i guard) rides in un-gated on the default path | Deferred to Phase 4; evaluate on its own with tests. Phase 1's i2i fix is the *other*, safe change (remote-UUID ref resolution). |
| Low | `image upscale` still mints via enterprise.js → likely broken under Camoufox | Phase 3: document the limitation or route the mint through the quirks gate. |
| Low | reCAPTCHA token-skip changes the default-path request (empty token) | **Safe** (Security-confirmed: ui_automation discards the minted token; Flow's JS mints natively on submit). Lands in Phase 3 re-expressed as a transport capability, with A/B evidence. |

---

## Provenance — PR #258 commit map

| SHA | Commit | Disposition |
|---|---|---|
| `ad1d041` | Extract & save `display_name` for generated images | **Phase 1** — cherry-pick |
| `b94302c` | Resolve & attach remote-UUID refs in UI automation (i2i) | **Phase 1** — cherry-pick |
| `5f9d97e` | MCP video tool param parity + veo-lite i2v guard | **Phase 1** — cherry-pick (params + guard only) |
| `3a5a390` | Camoufox optional stealth engine | **Phase 3** — re-express through widened `_engine.py` |
| `85e67d3` | Camoufox WAF-retry + display_name tests | **Phase 3** (retry) / Phase 1 (display_name tests) |
| `c3254b4` | Re-attach refs during WAF retry | **Phase 3** — WAF retry hardening |
| `c8b8690` | Resource-picker auto-close detection | **Phase 3** — verify it's a real bug, not a Camoufox artifact |
| `741e44f` | `page.reload` → `goto(page.url)` (Camoufox timeout) | **Phase 4** — re-evaluate (default-path behavior change) |

---

## File structure

### New files
```
scripts/spike_waf_camoufox.py
  Phase 2: A/B 403-rate harness — N generations per engine on one profile, compares
  PUBLIC_ERROR_UNUSUAL_ACTIVITY / 403 rates. No product code.
docs/superpowers/spikes/2026-07-XX-camoufox-waf-403.md
  Phase 2: the spike's recorded evidence (the ADR-13 gate artifact).
src/gflow_cli/auth/camoufox_strategy.py
  Phase 3: AuthStrategy for `gflow auth login --browser camoufox` (re-expressed).
docs/LIVE_VERIFICATION_camoufox.md
  Phase 3: 5-layer live ledger proving the Camoufox path evades the WAF end-to-end.
```

### Modified files
```
src/gflow_cli/api/dto.py                 Phase 1: display_name field on GeneratedImage.
src/gflow_cli/api/transports/ui_automation.py   Phase 1: i2i remote-UUID ref resolution.
src/gflow_cli/mcp/tools.py               Phase 1: gflow_generate_video model/duration/count + veo-lite i2v default.
src/gflow_cli/api/_engine.py             Phase 3: widen to context-launcher adapter + EngineQuirks.
src/gflow_cli/config.py                  Phase 3: BrowserEngine.CAMOUFOX enum value.
src/gflow_cli/api/client.py              Phase 3: engine dispatch via adapter; recaptcha-skip as transport capability.
src/gflow_cli/errors.py                  Phase 3: generalize BrowserEngineUnavailableError remediation.
pyproject.toml / uv.lock                 Phase 3: pinned [camoufox] extra.
.env.template / docs/CONFIGURATION.md / README.md / docs/KNOWN_ISSUES.md / SECURITY.md
                                         Phase 3: engine docs, profile-mismatch caveat, supply-chain note.
```

---

# PHASE 1 — Quick wins (land now, no engine, contributor-credited)

> **Re-scoped 2026-07-09 after code investigation.** Two of the three planned cherry-picks
> evaporated on contact with develop — see the findings below. Phase 1's real deliverable is
> **one** genuine, safe, engine-independent improvement: MCP video param parity. Branch
> `fix/pr258-quick-wins` off `origin/develop`, `Co-authored-by: C1ph3r404`, one PR to develop.

## Task 1.1 — `display_name` — ALREADY SHIPPED (no work)

**Finding:** `GeneratedImage.display_name` + the `_workflow_display_names` parse + the recorder
`metadata_json` write are already on develop (commit `ec9377f`, shipped v0.26.0, credited to
C1ph3r404). PR #258's `ad1d041` is a duplicate of already-merged work. **Nothing to do.**

## Task 1.2 — i2i remote-UUID refs — MOVED TO PHASE 4 (not a safe quick win)

**Finding:** PR #258's only i2i commit (`b94302c`) IS the contested `ref_names` invariant
reversal, not a separable safe fix. It adds `ref_names` to `GenerateImageRequest` and **deletes
the PR #245 guard comment in `daemon.py`** ("image tasks … must not receive ref_names"),
routing image refs through the R2V `_attach_remote_references` mechanism. This is exactly the
default-image-path behavior change the Architect flagged. It needs its own predict + tests —
tracked as **Phase 4.3**, not Phase 1.

## Task 1.3 — MCP video param parity (the one genuine Phase 1 win) — ✅ DONE

**What:** Expose `model`/`duration`/`count` on `gflow_generate_video` (CLI↔MCP parity per the
AGENTS.md symmetry rule). The worker's `_build_video_request` already reads these payload keys;
the only gap was the MCP tool not accepting/forwarding them.

**Finding on the "veo-lite guard":** redundant. The transport (`ui_automation_video.py`, issue
#125) already defaults i2v-with-frames to `I2V_DEFAULT_MODEL` and raises
`ModelModeIncompatibilityError` for an explicit incompatible model — the MCP path inherits both.
So this task is parity + agent model-control, NOT a new credit guard.

**Files:** `src/gflow_cli/mcp/tools.py`, `tests/mcp/test_tools_wired.py`, `docs/MCP.md`, `CHANGELOG.md`.

**Steps:**
- [x] Add `model`/`duration`/`count` to the signature + docstring + tool description.
- [x] Validate the model alias up front → `_bad_param` 400 on unknown model (pre-spend, mirrors CLI).
- [x] Forward to payload; omit `model`/`duration` when unset so the transport's i2v default stands.
- [x] Echo the params in the result's `params` block.

**Tests created (red→green):**
- [x] `test_video_forwards_model_duration_count_to_payload`
- [x] `test_video_omits_unset_model_and_duration_from_payload` (transport default preserved)
- [x] `test_video_invalid_model_is_rejected_before_enqueue` — 400 before enqueue.

## Task 1.4 — Phase 1 gates + PR

**Steps:**
- [x] `/gflow:check` green (ruff / format / pyright / mcp+worker suites).
- [x] CHANGELOG `[Unreleased]` — Added entry crediting C1ph3r404.
- [ ] Open PR `fix/pr258-quick-wins → develop`, reference #258, wait for green CI + SonarCloud, merge.

---

# PHASE 2 — WAF evidence spike (the ADR-13 gate)

**Blocking gate for Phase 3.** No product code. The ADR-13 question is not first "does
Camoufox win?" but "*is the current stealth stack actually insufficient?*" — so the spike's
first job is the **baseline**: measure the 403 rate on the default (playwright) engine. A
~0% baseline means ADR-13's premise is unmet → **STOP, Camoufox unjustified**, and no
Camoufox arm is ever needed. Only a materially non-zero baseline escalates to a Camoufox A/B.

**Sequencing note (why the arms are staged, not parallel):** `camoufox` is **not** a valid
`BrowserEngine` on develop — it is Phase 3, gated on this spike. So the spike **cannot** drive
a Camoufox arm through product code today (chicken-and-egg). This is correct: the baseline
comes first and may end the roadmap before any Camoufox code exists. The Camoufox A/B arm is
run *during Phase 3* (once the engine is wired) by re-running the same harness with
`--engine camoufox`.

## Task 2.1 — Spike harness — ✅ DONE

**What:** `scripts/spike_waf_camoufox.py` — drives N real image generations on one authed
profile through a chosen engine and classifies each outcome
(`success`/`waf_403`/`auth_401`/`rate_limited_429`/`other_error`); emits structured JSON
(per-attempt records + summary with `waf_403_rate`/`success_rate`).

**Files:** `scripts/spike_waf_camoufox.py` (scripts/ — excluded from SonarCloud; not imported
by the package).

**Steps:**
- [x] Engine-parameterized (`--engine playwright|patchright`); selects via `GFLOW_CLI_BROWSER_ENGINE`
      + `reset_settings()`, exactly as a user would.
- [x] `--engine camoufox` fails fast (exit 3) with the ADR-13 "measure the baseline first" pointer.
- [x] Credit-safe by default: `--dry-run` (validate + estimate, spend nothing) and an explicit
      `--yes`/interactive confirmation gate; non-interactive without `--yes` refuses to spend.
- [x] Classifies `WafRejectionError`→403, `AuthExpiredError`→401 (aborts early — a dead session
      dilutes the 403 rate), `RateLimitError`→429; `--delay` between attempts avoids self-inflicted 429s.
- [x] Ruff clean; dry-run + both guard exit codes verified without spending.

## Task 2.2 — Run the baseline + record the evidence — ✅ DONE (STOP verdict)

**What:** Ran the baseline arm; wrote the ADR-13 evidence artifact.

**Result (2026-07-09):** 20-gen baseline on `ffroliva` through the default stealth stack →
**0.0% WAF 403 rate** (19/20 success; the one miss was a `TransportTimeoutError` UI-scrape
flake, not a WAF block). Evidence: [../../spikes/2026-07-09-camoufox-waf-403.md](../../spikes/2026-07-09-camoufox-waf-403.md).

**Verdict: STOP.** The current stealth fix is confirmed sufficient — ADR-13's "confirm
insufficient before implementing" gate is unmet. ADR-13 (PLAN.md Decision Log #13) updated
with the evidence. Phase 1 stays shipped; **Phase 3 is NOT built.**

- [x] Baseline run (≈20 Imagen credits; a first attempt aborted at 0 credits on an expired
      session, which validated the harness's clean expired-session handling).
- [x] Recorded raw JSON + the 403 rate.
- [x] Verdict applied: ~0% → STOP; ADR-13 updated; roadmap closed at Phase 2.

---

# PHASE 3 — Camoufox engine adoption — ❌ NOT PROCEEDING (Phase 2 = STOP)

**Gate closed 2026-07-09.** The Phase 2 baseline showed a 0.0% WAF 403 rate, so per ADR-13 the
Camoufox engine is **not built**. The tasks below are retained only as the design of record in
case a future, repeatable WAF-403 reopens the gate (re-run `scripts/spike_waf_camoufox.py`;
a materially non-zero rate would revive this phase). Until then, none of it is implemented.

Re-expressed from PR #258 through the widened abstraction so engine logic lives in one place.
Own branch `feature/camoufox-engine → develop`, contributor-credited.

## Task 3.1 — Widen `_engine.py` + `EngineQuirks` (test scaffold)

**What:** Red tests for a single `open_persistent_context(engine, *, user_data_dir, headless,
chromium_kwargs) -> (BrowserContext, aclose)` adapter and an `EngineQuirks` value
(`typing_strategy`, `supports_init_script`, `needs_webdriver_patch`, `mints_recaptcha_inline`,
`mint_isolated_context`).

**Files:** `tests/api/test_engine.py`.

**Steps:**
- [ ] Tests: each engine resolves the right quirks; playwright/patchright quirks unchanged.

**Tests created (red):**
- [ ] `test_quirks_for_playwright_default` · `test_quirks_for_camoufox` · `test_adapter_returns_context_and_aclose`.

## Task 3.2 — Implement the adapter + quirks (green)

**What:** Fold the three PR #258 camoufox launch/teardown copies (auth, client, transport)
into one adapter; the CM-vs-driver asymmetry stays inside `_engine.py`. Add
`BrowserEngine.CAMOUFOX`, the pinned `[camoufox]` extra, `retryable_engine_errors()` extension.

**Files:** `src/gflow_cli/api/_engine.py`, `config.py`, `pyproject.toml`, `uv.lock`.

**Steps:**
- [ ] One adapter call at each site; delete inline `settings.browser_engine ==` checks in
      `agentic.py` (~310) and `ui_automation.py` (~1277, ~1337) — replace with threaded quirks.
- [ ] Pin `camoufox==<exact>`; regenerate `uv.lock`.
- [ ] Partial-setup leak guard covers `AsyncCamoufox.__aenter__` failure (no orphaned firefox.exe,
      no locked profile dir) — **must have a test** (Performance finding #4).

**Tests:** 3.1 green + a leak-guard test.

## Task 3.3 — Auth strategy + profile↔engine mismatch guard

**What:** Re-express `camoufox_strategy.py`; raise `BrowserEngineUnavailableError` (exit 24,
**not** the PR's `ConfigurationError`/exit 11) for a missing package **and** a missing
`camoufox fetch` binary. Add the mismatch guard: a camoufox-authed profile opened under a
chromium engine (or vice-versa) fails fast with a typed error, not the exit-8 login loop.

**Files:** `src/gflow_cli/auth/camoufox_strategy.py`, `src/gflow_cli/errors.py`, engine-resolution site.

**Steps:**
- [ ] Unify the missing-package exit code to 24 across auth + client paths.
- [ ] Catch missing-binary at `AsyncCamoufox` launch → exit 24 with a `camoufox fetch` hint
      (verify camoufox-py's actual behavior first — CLI-UX must-test).
- [ ] Mismatch guard: read `.gflow_browser_strategy` marker; typed error on engine mismatch.

**Tests created (red):**
- [ ] `test_camoufox_missing_package_is_exit_24` · `test_camoufox_missing_binary_is_typed` ·
      `test_profile_engine_mismatch_fails_fast` — all Critical.

## Task 3.4 — reCAPTCHA-skip + WAF-retry as transport capabilities

**What:** Re-express the (safe) empty-token path as `transport.mints_recaptcha_inline` on the
transport protocol (not a stringly `name == "ui_automation"` check); port the WAF-retry
ref-re-attach (`c3254b4`) and the resource-picker auto-close (`c8b8690`, only if confirmed a
real bug). Selectors follow the v0.29.0 canonical-source rule (`drivers/factory.py`), not new
hardcoded ligatures.

**Files:** `src/gflow_cli/api/transports/base.py`, `client.py`, `ui_automation.py`, `drivers/factory.py`.

**Tests created (red):**
- [ ] `test_ui_automation_mints_recaptcha_inline_capability` · WAF-retry ref-reattach test.

## Task 3.5 — Docs + live verification

**Files:** `.env.template`, `docs/CONFIGURATION.md`, `README.md`, `docs/KNOWN_ISSUES.md`,
`SECURITY.md`, `docs/USAGE.md`, `docs/LIVE_VERIFICATION_camoufox.md`.

**Steps:**
- [ ] `.env.template` + CONFIGURATION: `GFLOW_CLI_BROWSER_ENGINE` → `playwright | patchright | camoufox`;
      install + `camoufox fetch`; "must match the engine you logged in with"; Windows binary-cache location.
- [ ] SECURITY.md: camoufox under "elevated supply-chain trust", exact-pinned.
- [ ] KNOWN_ISSUES: profile↔engine mismatch symptom; `image upscale` enterprise.js limitation under camoufox.
- [ ] README: engine mention + C1ph3r404 credit line.
- [ ] `docs/LIVE_VERIFICATION_camoufox.md`: full fresh-profile `auth login → create_project →
      t2i → batch` under camoufox (5-layer ledger), session persistence across restart, the
      Phase 2 403-rate delta, and the DevTools native-token confirmation.

## Task 3.6 — Phase 3 gates + PR

**Steps:**
- [ ] `/gflow:check` green; `/gflow:doc-review` clean.
- [ ] CHANGELOG `[Unreleased]` — Camoufox engine (opt-in), crediting C1ph3r404.
- [ ] PR `feature/camoufox-engine → develop`, reference #258, green CI + SonarCloud, merge.

---

# PHASE 4 — Deferred re-evaluations (each its own predict → plan)

Not adopted from PR #258; each needs independent design + evidence. Tracked here so nothing is
silently dropped. **Do not bundle these into Phases 1–3.**

- **4.1 — Transport-side UI-first `create_project`.** ARCHITECTURE.md already sanctions UI-first
  creation *in the transport*. If a WAF-fallback for create is ever justified: REST-first, UI
  fallback only on a WAF-classified failure, selector via `drivers/factory.py`, camoufox-gated.
  Never in the API-client layer. (Blocked on a real need — createProject returns 200 today.)
- **4.2 — `mcp_warmup` redesign or removal.** As-is it automates Google Search on a logged-in
  account (account-level risk). If any warmup is needed: direct `labs.google` navigation, own
  env flag defaulted OFF, rate-limited, hard-stop (not swallow) on a Google captcha.
- **4.3 — i2i remote-UUID refs / `ref_names` invariant reversal** (PR #258 `b94302c`). The PR's
  only i2i change reverses the PR #245 image-i2i guard on the default path (adds `ref_names` to
  `GenerateImageRequest`, routes image refs through R2V `_attach_remote_references`). This was
  originally mis-scoped into Phase 1 as a "safe fix" — it is not. Needs its own predict + tests +
  a decision on whether the PR #245 invariant should stand. Verify first whether i2i with a bare
  UUID ref is genuinely broken on develop today (the contributor's premise).
- **4.4 — MCP `_SessionManager` client caching + idle reaper.** The idle monitor can close a
  client mid-generation. Needs in-use refcounting in the worker/service layer before any
  session-caching lands.
- **4.5 — `.type()` chunked-input follow-up** for long agentic prompts under camoufox
  (10–60 s per prompt) — a perf polish once the engine is in.

---

## Definition of done (per phase)

- [ ] All in-phase task steps checked off.
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage).
- [ ] `CHANGELOG.md` `[Unreleased]` updated; contributor credit preserved.
- [ ] Docs updated (Phase 3: CONFIGURATION / SECURITY / KNOWN_ISSUES / README / live-verification).
- [ ] Phase 2 evidence recorded before any Phase 3 code merges (ADR-13 gate).
- [ ] No `# TODO` in diff without a tracked issue link.
- [ ] Each phase merges as its own PR referencing #258 for provenance.
