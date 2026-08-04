---
name: predict
version: "1.0"
description: >
  Pre-implementation multi-persona adversarial analysis for gflow-cli proposals.
  Five expert personas independently evaluate a proposed change before a single
  line of code is written, then converge on a GO / CAUTION / STOP verdict.
  Invoke before any high-stakes decision: new transport, auth change, selector
  redesign, schema migration, API surface change, or backlog item requiring an
  investigation gate.
---

# `predict` — Pre-Implementation Multi-Persona Analysis

Structured pre-implementation review. Five expert personas assess the proposal
**independently**, then debate, then converge on a verdict with a confidence
score. Surfacing architectural, security, performance, and UX flaws before the
first commit is the cheapest place to catch them.

---

## When to invoke

Use before implementing any of:
- A new transport strategy (`sapisidhash`, `cdp_attach`, `official_veo`)
- Auth flow changes (new strategy, G12 bypass technique, cookie extraction)
- Selector cascade redesigns affecting `ONBOARDING_SELECTORS`, `NEW_PROJECT_SELECTORS`, `FRAME_SLOTS_STRUCT`
- Schema migrations in `gflow_cli/data/`
- New CLI surface or exit-code changes
- Any backlog item with an "investigation gate" in PLAN.md before coding

Skip for: trivial bug fixes (< 10 lines, isolated, no boundary cross), already-approved PLAN.md tasks entering EXECUTE, pure doc changes.

---

## Invocation

```
/gflow:predict <proposal>
```

`<proposal>` is a short description of what you intend to build or change —
one paragraph is enough. Examples:
- "Wire SAPISIDHASH auth header into `_post_json` for all `aisandbox-pa` routes (Issue #15)"
- "Add CDP-attach transport as opt-in `--transport cdp_attach` alongside `ui_automation`"
- "Redesign `gflow video batch` to use a local manifest ledger for skip-already-done"
- "Add `AuthBrowserBlockedError` to `internal_chromium.py` when Google rejects bundled Chromium"

---

## Protocol

### Phase 1 — Persona briefings (parallel)

Dispatch five personas simultaneously. Each reads `AGENTS.md`, `PLAN.md`, `KNOWN_ISSUES.md`, and the relevant source files for the proposal. Each assesses **independently** — no persona sees another's output during Phase 1.

#### Persona 1 — Architect
*Scope: hexagonal target, modular-monolith current shape, dependency direction, module boundary rules.*

Asks:
- Does this proposal respect the dependency rule (`interfaces → application → domain ← infrastructure`)?
- Which module does this live in? Does it fit cleanly or does it need a new module, and if so, is that justified?
- Will this make the eventual DDD graduation harder or easier?
- Are there hidden coupling risks (e.g., a transport leaking into `cli.py`, a domain model importing from `infrastructure`)?
- Does the proposed shape match the existing pattern (Protocol-based ports, frozen dataclasses for value objects, `structlog` for all logging)?

Output: structured analysis, confidence `0–10`, architectural risks.

#### Persona 2 — Security / reCAPTCHA
*Scope: Google's anti-bot stack, SAPISIDHASH, G12 block, WAF scoring, profile isolation, secret storage.*

Asks:
- Does this touch auth headers, cookie extraction, or token minting? If yes, what's the trust boundary?
- Could this trigger WAF score inflation on a per-profile basis?
- Does the Chrome profile isolation guarantee (`SecurityError` if profile_dir outside `GFLOW_CLI_HOME`) remain intact?
- Are any auth secrets (SAPISID, bearer tokens, reCAPTCHA tokens) at risk of leaking to logs? (`show_locals=False` is mandatory on exception renderers.)
- Could this bypass the G12 stealth flag mechanism or reintroduce `navigator.webdriver=true`?
- What's the attack surface against a third party who controls the Flow UI (XSS / selector injection)?

Output: structured analysis, confidence `0–10`, security risks with severity.

#### Persona 3 — Performance / Playwright
*Scope: Page pool, `asyncio.gather`, reCAPTCHA mint latency, headless detection, BrowserContext lifecycle.*

Asks:
- Does this add latency to the hot path (per-generation or per-poll)?
- Does it interact with the Page pool (`_checkout_page` / `_checkin_page`)? Is there a `QueueFull` risk?
- Does it require additional `page.evaluate` calls? What's the latency budget vs the 200 ms/page threshold?
- Does it affect `GFLOW_CLI_CONCURRENCY`? Could it reduce or increase the safe ceiling?
- Could running this in a headless context trigger reCAPTCHA detection or WAF scoring?
- Does it add any persistent state that's not cleaned up when `FlowApiClient.__aexit__` runs?

Output: structured analysis, confidence `0–10`, performance bottlenecks.

#### Persona 4 — CLI UX / Cross-platform
*Scope: exit codes (RFC 9457), `structlog` events, Windows/macOS/Linux path handling, `--help` text, error recovery UX.*

Asks:
- What exit code does failure produce? Is it in `EXIT_CODE_MAP`? Is it distinct from existing codes?
- What `structlog` events does this introduce? Are `error_raised` / `error_unhandled` paths handled?
- Are new env vars or flags introduced? Do they follow `GFLOW_CLI_*` convention and have a `.env.template` entry?
- Does the UX degrade gracefully if the new feature fails (remediation hint in error message)?
- On Windows: are path separators, `platformdirs` paths, and `PYTHONUTF8=1` requirements respected?
- If this is a new subcommand or flag: is the `--help` text self-contained and accurate?

Output: structured analysis, confidence `0–10`, UX friction points.

#### Persona 5 — Devil's Advocate
*Scope: YAGNI, simpler paths, interaction with KNOWN_ISSUES, backlog sequencing.*

Asks:
- Is there a simpler way to achieve the same user outcome (fewer files, less Playwright surface, existing code reuse)?
- Does PLAN.md already have an ADR that contradicts or defers this work?
- Is there a known issue in `KNOWN_ISSUES.md` that makes this approach risky or likely to fail?
- If this fails in production (WAF score spike, reCAPTCHA regression, selector drift), what's the rollback story?
- Is the timing right? Does something else need to land first (e.g., Issue #14 before Issue #15)?
- What's the simplest experiment (smoke test, `scripts/` script, isolated spike) that could prove/disprove the core assumption before committing to a full implementation?

Output: structured analysis, confidence `0–10`, alternative paths, blocking concerns.

---

### Phase 2 — Conflict resolution

After all five personas return:

1. **Tally signals.** Identify any dimension where 2+ personas flag the same concern — those surface as high-confidence risks.
2. **Resolve conflicts.** If Architect says "fits cleanly" but Devil's Advocate says "ADR #13 defers this" — surface the conflict explicitly. Do NOT silently suppress one view.
3. **Score overall confidence** as the average of the five persona confidence scores, then apply modifiers:
   - Any STOP condition from any persona → overall **STOP** regardless of average.
   - Devil's Advocate identifies a simpler approach the others missed → downgrade confidence by 2.
   - All five personas agree on the approach → upgrade confidence by 1.

---

### Phase 3 — Verdict

**GO** (confidence ≥ 7, no STOP conditions): all personas aligned or concerns are mitigated within the proposal. Safe to proceed to PLAN mode.

**CAUTION** (confidence 4–6, or one unresolved STOP candidate): proceed but explicitly address the flagged concerns in the PLAN before EXECUTE. Surface the specific mitigations needed.

**STOP** (confidence < 4, or any hard STOP): one or more of:
- A security bypass that cannot be fixed within the proposal scope
- A fundamental architectural incompatibility with the hexagonal target
- A performance regression that violates the 200 ms/page threshold at N=16
- A PLAN.md ADR that explicitly defers this work
- An existing KNOWN_ISSUES entry that makes the approach likely to fail
- Devil's Advocate found a simpler approach that makes this one wasteful

On STOP, output the specific blocking concern and the minimum change required to convert to CAUTION.

---

## Output format

```
# Predict: <proposal short title>

## Verdict: <GO | CAUTION | STOP>
**Confidence:** <N>/10

## Summary
<2-3 sentences. What the five personas collectively found.>

## Persona findings

### Architect — <signal> (<confidence>/10)
<findings>

### Security / reCAPTCHA — <signal> (<confidence>/10)
<findings>

### Performance / Playwright — <signal> (<confidence>/10)
<findings>

### CLI UX / Cross-platform — <signal> (<confidence>/10)
<findings>

### Devil's Advocate — <signal> (<confidence>/10)
<findings>

## High-confidence risks (flagged by 2+ personas)
1. …

## Conflicts resolved
- <Persona A vs Persona B — resolution>

## Required mitigations before EXECUTE (CAUTION only)
1. …

## Recommended next step
<One sentence. E.g.: "Open a PLAN.md task for Issue #15 gated on SAPISIDHASH investigation steps 1–3." or "Run the smoke script in scripts/smoke_video_editor.py against the live API before committing to the full design.">
```

---

## Integration & Pipeline Continuation (Next Step Handoff)

- **After GO:** Proactively announce: **"Predict GO. Next step: Phase 3 BDD Scaffolding (`/gflow:scenario <feature>`) or Phase 4 Implementation Plan (`/gflow:plan <feature>`)."**
- **After CAUTION:** Address required mitigations in the PLAN spec, then announce: **"Predict CAUTION. Next step: Address mitigations in Phase 4 Implementation Plan (`/gflow:plan <feature>`)."**
- **After STOP:** Address blocking concern or file an investigation gate task before moving to Phase 4.

---

## Provenance

Adapted from `vc-predict` in [vibecode-pro-max-kit](https://github.com/withkynam/vibecode-pro-max-kit) (assessment 2026-05-28).
Personas re-scoped to gflow-cli surfaces: Google anti-bot stack, Playwright Page pool, RFC 9457 exit codes, hexagonal architecture target.
