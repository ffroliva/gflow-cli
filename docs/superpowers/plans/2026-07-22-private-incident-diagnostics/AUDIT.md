# Private Incident Diagnostics — Full Audit & Trackability Ledger

Single source of truth for what was built, verified, reviewed, and what
residual gaps are explicitly tracked. Consolidate to memory + delete at
release (per `release-spec-plan-memory-consolidation`).

Branch `feature/incident-diagnostics` · base `origin/develop` · target v0.43.0.

## 1. Verification state (all green)

| Layer | Result |
|---|---|
| Offline unit/BDD | 2684 passed, 0 failed, 91% coverage (aggregate) |
| Scenario matrix | 9 Critical + 30 High mapped to named passing tests (S43 = live gate) |
| pyright src / ruff / format | 0 errors / clean / clean |
| Doc gates | doc-links, website PII, repo-hygiene all pass |
| Packaging (aggregate) | green after the `BundleDir.__del__` lifecycle fix (`6ca8180`) |

## 2. Code-review ledger (`/code-review max`) — 14 confirmed fixed, 1 skipped

10 finder angles → 22 verifiers → gap sweep. All fixes in `e14d12d` (+ test
follow-up `d4148d0`), re-live-verified.

| # | Finding | Outcome |
|---|---|---|
| 1 | Artifact timeouts (3+4+4s) exceed 8s budget → whole bundle lost on a wedged page | FIXED — journals-first + deadline-shared budget |
| 2 | Legacy unmarked full-page screenshot written to plain out_dir every run | FIXED — `capture_ui_diagnostics` is JSON-only |
| 3 | Capture covered only the 3 generate_* methods | FIXED — shared `_raise_with_incident`; +character +upscale |
| 4 | `close_ok=cancelled is None` → truncated HAR stamped "complete" | FIXED — `close_context_bounded` reports graceful success |
| 5 | #293 stale-Chrome launch contention never captured | FIXED — metadata-only bundle at the launch raise site |
| 6 | Pending retention over-deletes when a bundle can't be deleted | FIXED — subtract size on pop |
| 7 | `sensitive/` mkdir outside the try → kills whole capture | FIXED — inside the per-artifact guard |
| 8 | Bundles for unexpected exceptions never surfaced to operator | FIXED — ref on any exc; human + `--json` paths |
| 9 | Disabling capture froze existing bundles on disk forever | FIXED — retention runs if incidents dir exists |
| 10 | `TransportSetup.recorder` dead wiring; manifest command/transport null | FIXED — field removed; command/transport bound |
| 11 | `browser.json` error_class always the constant "Error" | FIXED — `getattr(error,'name',...)` |
| 12 | Cancellation window replaces typed error during capture await | **SKIPPED** — by design (cancellation authoritative; S7497 anti-pattern to swallow) |
| 13 | `os.replace` sharing-violation strands evidence, no retry | FIXED — clear stale tmp + one bounded retry |
| 14 | Daemon tasks share one correlation id → colliding incident ids | FIXED — per-task `wk-<task_id>` rebind |
| 15 | Synchronous retention sweep blocks the event loop | FIXED — `asyncio.to_thread` |

Refuted (not filed): retryable-flip "smuggled" (documented + no worker
auto-retry), both `id()`-reuse theories (Playwright registry pins objects),
Windows sentinel-write race (pre-existing, re-exposed).

## 3. Live-verify coverage matrix

| Surface | Status | Evidence |
|---|---|---|
| Recorder bundle on real editor (UI-state) | ✅ live, grade A/1.0 | `LIVE_VERIFICATION_v0.43.0.md`; benchmark |
| Two-process profile contention (metadata-only) | ✅ live, grade A/1.0 | ditto; command populated live |
| Real credit-free t2i → no incident on success | ✅ live | ditto |
| Paid `veo-lite` T2V lifecycle (S43) | ✅ live, 1 credit (approved) | HTTP-200 → SUCCESSFUL, 2.3MB ftyp, no incident |
| Bundle diagnostic-QUALITY (2 classes) | ✅ live benchmark, both A/1.0 | `test_incident_quality_e2e.py` |
| WAF / wire-format / network bundle class | ⚠️ **offline-graded only** | `test_incident_bundle_quality.py::TestWafWireFormatClass` — rubric proven; live capture needs a real (paid/naturally-occurring) failure. TRACKED gap G1. |
| close_ok=False truncated-HAR branch | offline-pinned | not safely forceable live |
| Cancellation-mid-close teardown | offline-pinned + BDD | not safely forceable live |

## 4. Documentation audit (2 parallel auditors, `auditing-documentation` dims)

Verdict **GREEN**. Core feature lands **D4** (4 dedicated doc sections + INDEX
routing + website mirror). Operational-truth spot-checks (retention caps,
trigger list, screenshot gating, honest har_state) all MATCH the code.

| Finding | Severity | Outcome |
|---|---|---|
| MCP/worker `retryable` half-documented (docs/MCP.md silent) | High-value | FIXED — MCP.md error-envelope section |
| Bundle quality scorer had no operator doc pointer | Med | FIXED — DEBUGGING §Assessing a bundle's quality + E2E_TESTING |
| Worker per-task correlation rebind undocumented | Low | FIXED — DEBUGGING §Worker/daemon correlation |
| Incident test/benchmark topology absent from E2E_TESTING | Low | FIXED |
| KNOWN_ISSUES HTTP-400 overstated what gets captured (4a) | Med | FIXED — clarified: success writes nothing |
| Bundle-path segment named two ways (5b) | Low | FIXED — both render `<incident-id>` |
| `llms.txt` omits DEBUGGING + SECURITY (agent entrypoint) (1a) | Med | FIXED — both added |
| SECURITY mirror uses GitHub-reporting vs canonical email (5a) | Low | ACCEPTED — intended anonymization |

## 5. Skill audit (website-awareness + code-change coverage)

| Skill | Gap | Outcome |
|---|---|---|
| `/gflow:check` | — | already runs `check_website_docs_pii.py` ✅ |
| `/gflow:doc-review` | no website-mirror gate; no git-parity lens | FIXED — added §4b (mirror PII + drift) and §4c (code↔docs parity via git log, the `auditing-documentation` dim 3); §5 now scans `skills/` too |
| `/gflow:release` | no mirror-sync staging | FIXED — step 10 names the mirror gate; step 11 stages `website/docs/` + `skills/` |
| `skills/gflow-cli`, README | no incident env-var mention | ACCEPTED — diagnostic env vars delegated to CONFIGURATION by existing convention |

**Recommendation on `auditing-documentation` → `/gflow:doc-review`:** its
value over the existing council is dimension 3 (code↔docs parity via `git
log` — catches *undocumented shipped work*, which no doc-reading lens finds).
Folded that ONE lens into doc-review as §4c rather than running the whole
separate skill each release. The full `auditing-documentation` stays a
periodic health-check (before a milestone), not a per-release gate.

## 6. Residual tracked gaps (explicit, owned)

- **G1 — WAF/wire-format bundle class not floor-tested LIVE.** Rubric proven
  offline; live capture requires a real WAF-403 or wire-format failure (paid
  or naturally-occurring). Fold into the first release where such a failure is
  captured live; do not force-spend to manufacture it.
- **G2 — Website mirror is hand-synced; no generator.** doc-review §4b now
  *catches* drift, but the durable fix is a deterministic `docs/`→`website/docs/`
  generator with the anonymization map as data + a CI regen-diff gate. Tracked
  as a follow-up (see §7 decision).
- **G3 — Pre-existing mirror staleness** (unrelated to this branch):
  `website/docs/{USER_GUIDE,CHARACTER,AUTHENTICATION,DATA_LAYER}.md` lag
  canonical (USER_GUIDE missing "Journey 16"). Resolution pending the §7
  decision.
- **G4 — Cancellation-window reclassification** (review #12): accepted by
  design; if a scheduler ever needs the typed error to win over a mid-capture
  cancel, revisit with `shield`.

## 7. Open decision (for the owner)

The pre-existing stale mirror (G3) + the missing generator (G2) are one
question: hand-resync the 4 stale files now (fast, PII-gate-verified, but
unrelated to incident-diagnostics), OR build the generator + CI gate now
(deeper, prevents recurrence, more scope on a ready-to-ship branch), OR track
both as a separate follow-up issue and keep this branch focused. Recorded here
until decided.
