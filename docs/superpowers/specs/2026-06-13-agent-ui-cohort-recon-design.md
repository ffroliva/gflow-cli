# Spike & Recon Design — Flow Agentic UI Cohort

- **Date:** 2026-06-13
- **Status:** DESIGN — awaiting user review
- **Type:** Recon spike (evidence capture) → assessment → feature plan (deferred)
- **Driving issues:** #183 (image `t2i` exits 23 on `mode_switch_trigger` miss), #174 (full-page library UI, entity drops off the wire — currently on HOLD)
- **Prior art:** PR #124 (`_exit_agent_mode`), `scripts/dev/spike_issue174_library_ui_recon.py`

> **Principle for this work: we do not guess, we test and verify.** This document
> designs the capture; it deliberately does **not** propose the fix. The feature
> plan is written *after* the spike produces evidence, grounded in what S0–S8 prove.

---

## 1. Problem statement

Google is rolling out a **fully-agentic Flow UI** to a cohort of accounts. It is
not random selector drift — it is one coherent surface, captured live on the
`ffroliva` account (screenshots 2026-06-13):

- The composer shows an **"Agent" pill** and the placeholder *"What do you want to create?"*.
- The classic **inline `crop_*` aspect-ratio / mode control is gone**.
- Aspect ratio, model, and upscale have moved into a separate **"Agent settings"** panel (Always/Never confirm; 16:9…9:16; Nano Banana 2; Omni Flash).
- Clicking the Agent pill opens a docked **chat session panel** ("Flow A/B Testing Support") with **session history** and a welcome state ("Hi Flavio…").
- Media attach goes through a **library overlay** with an "Add to Prompt" button (the #174 surface).

### Why the existing handling does not cover it

gflow already has `_exit_agent_mode()` (`ui_automation_video.py:719`), wired into
**both** `_switch_to_video_mode` and `_switch_to_image_mode`
(`ui_automation.py:1006`). It handles an *older, lighter* agent surface — an
in-composer pill or a chat side-panel sitting **on top of a still-present classic
composer** — and is keyed on the outcome `_media_panel_present()` (= "did the
`crop_*` control come back?").

That logic assumes classic media mode is reachable. In the new agentic UI the
`crop_*` control **does not exist at all** — so `_media_panel_present()` returns
`False` permanently, the loop clicks the pill (which likely *opens* the chat
panel rather than restoring a classic composer), `crop_*` never returns, and the
caller raises `UiSelectorDriftError` (exit 23). **The existing recovery has
nothing to recover to.** This is the #183 failure.

### The reframing: three states, not two

| State | Signal | Recoverable? | Status |
|---|---|---|---|
| **Classic media** | inline `crop_*` present | n/a | works today |
| **Agent toggle over classic** ("user-activated") | pill/panel present **and** toggling restores `crop_*` | yes | `_exit_agent_mode` handles it |
| **Forced agentic cohort** ("cookie-activated") | agentic chrome present, `crop_*` never returns | **no** | **unhandled — #183/#174** |

From automation's seat there is no "user clicked it mid-run": gflow opens a fresh
context against the persisted account state. So **"user-activated" ≈ a recoverable
persisted toggle** and **"cookie-activated" ≈ a forced cohort assignment with no
classic mode to return to.** The spike's job is to prove which mechanism is in
play and how to detect it.

---

## 2. Objective

Capture the forced agentic Flow UI end-to-end — **DOM, network (HAR), cookies and
storage** — on two accounts and two locales, and answer with evidence:

1. **Gating** — how is the cohort assigned (cookie / `localStorage` / server flag)?
2. **The Agent button** — its DOM, and what clicking it actually does.
3. **The agent expanded window** — chat panel, session history, Agent settings DOM.
4. **The wire** — what the agentic surface POSTs, and whether reference entities ride it (the #174 link).

Deliverables are a capture harness, raw artifacts, and a recon document. The
feature design (detect → disambiguate → fail-cleanly-or-drive) is **out of scope
here** and follows in a separate plan.

---

## 3. Accounts & locales

| Profile | Locale | Expected cohort | Reusable project |
|---|---|---|---|
| `ffroliva` | English (en) | **agentic** in the *primary* profile (screenshots); gflow profile **unconfirmed** | `58c24049-c3bc-44fb-8615-852f84e5fd0f` |
| `denon82` | Portuguese (pt-BR) | per #174: flaps; re-probe live | `580a6bbf-…` (see `flow-reusable-project-ids`) |

Two locales let us **separate locale effects from cohort effects**: if the gating
signal is byte-identical across en and pt-BR, it is cohort-driven, not locale
-driven. All selectors stay Material-Symbols-ligature-based (locale-leak rule).

> Cohort state flaps (denon82 reverted within 12h per #174), so **S0 re-probes
> each profile live** rather than trusting any prior label — including the table above.

### 3.1 Session/profile model (a third diff axis)

gflow never uses the user's primary browser session. Every auth strategy —
`chrome` (real Chrome **binary**), `internal` (bundled Chromium), and the
`patchright` **engine** (v0.19.0) — drives a **dedicated profile under
`GFLOW_CLI_HOME`** (enforced by `real_chrome._validate_profile_dir`). The recent
work swapped the *engine/binary*, not *whose session it is*.

This matters because A/B cohorts are often bucketed **client-side** (a cohort
cookie / `localStorage` seed persisted per profile). If so, the agentic UI seen
in the user's *primary* profile (the screenshots — note the everyday-Chrome
extension icons) may differ from what **gflow's dedicated profile** sees for the
**same account**. So the diff matrix is not just *account × locale* but
*account × locale × **(profile identity + engine)***:

- **primary profile vs gflow profile** (same account) → isolates per-profile client bucketing.
- **`chrome` vs `internal` vs `patchright`** → isolates fingerprint/engine-driven bucketing.

Evidence is mixed and must be resolved, not assumed: the #183 reporter hit the
agentic UI *through gflow* (gflow profiles **can** land in it), yet #174's
denon82 reverted within 12h (looks like re-bucketing or a server toggle).

---

## 4. The unknowns the spike must resolve

### 4.1 Gating (highest-value — the cookie hypothesis)
- Is the agentic UI gated by a **cookie**, a `localStorage`/`sessionStorage` key, or a **server-delivered experiment-flags payload** (`__NEXT_DATA__` / a `/fx/api/...` config response)?
- Is assignment **account-level (server-side)** or **per-profile/fingerprint (client-side)**? If client-side, it may be **steerable** — a fresh gflow profile could re-bucket to classic, which would be a *workaround*, not just a detection target.
- Does **gflow's dedicated profile** present the *same* cohort as the user's **primary profile** for the same account? Does it differ by **engine** (`chrome` / `internal` / `patchright`)?
- Can classic media mode be reached **at all** on a forced account (the definitive recoverable-vs-forced answer)?

### 4.2 The Agent button & expanded window (DOM)
- Exact DOM of the Agent pill in the new composer — is it the same `span.content` anchor `COMPOSER_AGENT_TOGGLE_SELECTOR` targets, or new?
- What clicking the pill does: restore a classic composer, or open the chat session panel?
- DOM of the chat panel, session history, and **Agent settings** — where aspect / model / upscale now live (selector-design input).

### 4.3 The wire (HAR — the #174 link)
- When generating through the agent chat, what request fires — same `batchGenerateImages` / video endpoint, or a new agentic endpoint? Different payload **and response** shape?
- Does "Add to Prompt" stage an entity that **rides the wire**, or reproduce the #174 silent drop?
- What does "Confirm before generating: Always/Never" change in the request flow?

---

## 5. Methodology

**Two tools, split by responsibility** (confirmed 2026-06-13):

- **Capture → `C:\development\github\gflow-agent-browser-spike`** — a separate,
  isolated, non-git sandbox driving `agent-browser@0.27.0` over **CDP** (no Node
  deps in gflow-cli). It attaches **real Chrome to a gflow profile**
  (`launch-flow-chrome.ps1 -ProfileName <name>` resolves `profile_<name>` under
  `GFLOW_CLI_HOME`), then **manually drives** the agentic UI while capturing.
  Manual drive is deliberate: we don't have the agentic selectors yet, so passive
  capture beats scripted clicking on unknown elements.
- **Analyze → `gflow-cli`** — offline Python. The tested pure helpers (classifier,
  redaction, diff) **consume the sandbox artifacts**. No browser driving in
  gflow-cli for this spike.

**Capture stack (sandbox, per run):**
- **HAR** via `agent-browser network har start|stop` → full request/response
  archive. Capture the **whole** HAR and identify the agentic gen endpoint *from*
  it — do **not** assume `aisandbox-pa`; the agentic surface may use a new route.
- **DOM / signals** via `agent-browser --json eval <js>` and `snapshot -i`:
  composer-state signals (crop_* / agent-pill / chat-panel presence), composer
  `outerHTML` slice + ligature inventory, the Agent-settings panel.
- **Gating** via `eval`: `localStorage`, `sessionStorage`, `__NEXT_DATA__`
  pageProp keys, and JS-visible `document.cookie`. **httpOnly cohort cookies are
  recovered from HAR request `Cookie` headers** (JS cannot read them).
- **`navigator.webdriver`** (engine/fingerprint axis) via the existing
  `run-cdp-smoke.ps1`.
- **Screenshots** of the Chrome window at each step.

**Credit posture — credit-free by default:**
- Image generation is free → manually trigger **one image** generation through the
  agentic UI and let HAR capture the real request **and** response at $0.
- **Do not manually trigger video generation** — manual CDP capture has no clean
  `route.abort()` $0 path. The video wire is inferred from #174 / deferred unless
  the user explicitly opts to spend one credit.

**Three-axis diff:** run identical capture across *account* (`ffroliva` /
`denon82`), *locale* (en / pt-BR), and *profile+engine* (the user's primary
profile vs gflow's dedicated profile; `chrome` / `internal` / `patchright`). The
cookie/storage/flag **delta** that tracks the agentic-vs-classic UI — and is
stable across locales — is the gating signal. (Primary-profile capture is
read-only / manual; gflow-profile capture runs the harness.)

**Privacy:** the sandbox's raw HAR carries auth cookies, tokens, and prompts (its
own README flags this). Artifacts stay in the sandbox's `artifacts/` —
**local only, never committed**. The gflow-cli analyzer **redacts before writing
any finding**: cookie/flag/storage **names** with values reduced to length + a
short hash; account name / email / avatar scrubbed from anything it emits.

**Discipline:** headed, supervised, **one disciplined run per (profile, scenario)**
(WAF heat — same rule the #174 plan sets). The capture script is parameterized
(`-ProfileName`, `-Port`, project URL, locale) and the analyzer takes the capture
artifact paths — per the parameterize rule.

---

## 6. Scenarios

**Pre-flight (P0) — run this first; it reorders everything.** Before the full
capture, probe the one question that gates priority: *does gflow's own dedicated
profile for `ffroliva` even render the agentic UI?* Open the project in the gflow
profile (headed) and classify. If gflow profiles rarely/never land agentic, the
problem is far less urgent and the plan shifts toward "detect + warn" over "drive
the agent UI"; if they do, the full spike proceeds.

Each scenario is **non-fatal**: errors are recorded in the run JSON and the run
continues (same pattern as the #174 harness).

| ID | Focus | What it captures |
|---|---|---|
| **S0** | Cohort census | For each profile: open project, classify composer (classic / agent-toggle / forced-agent), record the deciding signals. Live ground truth before full capture. |
| **S1** | Initial state | Composer DOM + screenshot on first open — is the account already in agent mode? |
| **S2** | The Agent button | Locate the pill, dump its DOM, **click it, record the transition** (panel opens? mode changes? `crop_*` appears?). |
| **S3** | Agent settings | Open the gear → Agent settings; dump DOM of aspect / model / upscale / confirm controls. |
| **S4** | Expanded window | Chat panel + session history DOM and screenshots. |
| **S5** | Wire — image | Generate an image via the agent chat (free); capture submit payload **+ response** + HAR entry. Compare endpoint/shape to classic `batchGenerateImages`. |
| **S6** | Wire — entity (#174) | "Add to Prompt" a reference entity, submit; does `referenceEntities` ride the wire? (route-abort for video; live for free image). |
| **S7** | Gating diff | Cookies + `localStorage`/`sessionStorage` + `__NEXT_DATA__`/flags, captured on both accounts; emit the diff. |
| **S8** | Recoverable? | Exhaust every avenue to force classic mode (existing `_exit_agent_mode`, Agent settings, URL params, pill toggle). Settle **recoverable vs. forced** definitively. |
| **S9** | Profile/engine axis | Capture the gating signal (S7) in gflow's dedicated profile vs the primary profile (same account), and across `chrome` / `internal` / `patchright`. Resolves per-account (server) vs per-profile/fingerprint (client) bucketing — and whether the cohort is steerable. |

P0 answers *"does gflow even hit this?"*; S2/S3/S4 answer *"the button + the
expanded window"*; S7/S8/S9 answer *"how Google manages agent mode"* (gating +
recoverable + the profile/engine axis); S5/S6 answer *"the wire"* and close the
#174 loop.

---

## 7. Deliverables

1. **Capture scripts in `gflow-agent-browser-spike`** — reuse `launch-flow-chrome.ps1` + the HAR scripts; add `capture-agent-ui.ps1` (composer signals + gating `eval` + DOM dump + HAR around one manual image generation), tagged per profile / locale / engine.
2. **`gflow-cli/scripts/dev/analyze_agent_ui_capture.py`** — offline analyzer: reads the sandbox capture JSONs + HAR summaries, classifies composer state, diffs gating signals across the three axes, emits a consolidated, redacted findings JSON. Pure logic unit-tested in `tests/scripts/`.
3. **Raw artifacts** in the sandbox's `artifacts/` — HAR, `eval` JSONs, snapshots, screenshots. Local only, never committed.
4. **`docs/AGENT_UI_RECON.md`** — the assessment written **from the captured evidence**: the gating mechanism, the three-state detection signals, the agentic wire protocol, and the #174 resolution. Indexed in `docs/INDEX.md` (docs-first-class rule).
5. **Feature plan (separate, deferred)** — detect → disambiguate (recoverable toggle vs forced cohort) → fail-cleanly (new typed error + cohort fingerprint) or drive-the-agent-UI. Written only after the recon doc lands, decided on evidence.

---

## 8. Verification protocol

The capture is only trustworthy if it is verified, not assumed (verification
-ledger discipline):

- **S0 classification** is cross-checked against the captured DOM (the label must match the dumped `outerHTML`, not a guess).
- **Wire claims (S5/S6)** assert on the **real response/HAR**, never the request shape alone (request shape ≠ response shape — established trap).
- **Gating claim (S7)** must reproduce: the identified signal must differ between agentic and classic and be **stable across both locales**; a signal that only differs by locale is rejected as the cohort key.
- **Recoverable verdict (S8)** requires that *every* attempted avenue is logged with its outcome — "forced" is only concluded after exhausting them.

---

## 9. Out of scope (deferred to the feature plan)

- Building the detection classifier / new typed error / cohort-fingerprint capture in product code.
- Driving generation **through** the agent UI (a new transport flow).
- Any change to `_exit_agent_mode` or the transports.

This spike captures and documents only. No `src/` changes.

---

## 10. Risks & open questions

- **Cohort flapping** — the agentic UI may revert mid-spike; S0 re-probes live and the run records the cohort state with a timestamp.
- **WAF heat** — headed, supervised, one disciplined run per account; no retries-in-a-loop.
- **Gating may be server-side with no client-visible signal** — if S7 finds no cookie/storage/flag delta, the assessment records that detection must remain **DOM-based** (post-navigation), and pre-navigation detection is impossible. That is itself a valid, documented finding.
- **Free image gen still creates artifacts** on the account — acceptable (image gen is free and reversible); video stays route-aborted.
- **Per-profile bucketing may be non-deterministic** — if assignment is client-side, two fresh gflow profiles could land in different cohorts. P0/S9 record each profile's identity + creation context alongside its captured cohort and avoid over-generalizing from a single profile.

---

## 11. Next step

On approval of this design → invoke **writing-plans** to produce the task-by-task
implementation plan for `spike_agent_ui_cohort.py` and the recon doc. **Sequencing:
run the P0 pre-flight probe first** — its result (does gflow's own profile hit the
agentic UI?) reorders the remaining scenarios and the eventual feature priority —
then execute the full spike under supervision.
