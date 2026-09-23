# Credits on flow.google.com: the price is on screen before submit; the balance was not found

**Date:** 2026-09-14 · **Cost:** $0 (navigation, DOM reads, a typed-and-cleared prompt, opening
the settings pane; nothing submitted, created or deleted) · **Issues:** #795 (Refs #639)
**Script:** [`scripts/dev/spike_credits_migrated_surface.py`](../../../scripts/dev/spike_credits_migrated_surface.py)
**Run:** `python scripts/dev/spike_credits_migrated_surface.py --profile ci-probe --open-settings`
**Evidence:** `scripts/dev/_spike_out/spike_credits_migrated_surface_<UTC timestamp>.json`
(gitignored; the profile is a field inside the file, not part of the name)
**Design:** 2 profiles × 2 runs (v1 discovery, v2 settings pane) = 4 observations, of which
**1 is measured at pane level** (`ci-probe`, v2) and `denon82` never reached the app. Readings
pre-registered in the script docstring and committed before each run: `f0cfcd55` for v1,
`9e038489` for v2. **N = 1 profile produced every positive reading** — see "What this means".

**Question.** Can gflow read per-model credit prices and the credit balance on an account served
`flow.google.com`, without spending anything? It decides whether a spend cap
(community-feedback-uplift WS2) can be built on this host now or is blocked on #795.

## What was observed

| Run | Profile | Host served | Result |
|---|---|---|---|
| v1 | `denon82` | `flow.google.com/about` (marketing page) | **UNMEASURED** — no app, no project grid |
| v1 | `ci-probe` | `flow.google.com/project/<id>` | No credit text in the page before or after typing; the only credit-shaped wire hit was `cPZSdc`, a promotional banner |
| v2 | `ci-probe` | `flow.google.com/project/<id>` | Settings pane: **"Generating will use 0 credits"** with Image · Nano Banana 2 Lite · x1 selected |

**1. The declared price is readable from the composer's settings pane before submit.** Opening
gflow's own anchor `.settings-trigger-button` (`migrated_composer.READY_ANCHOR`) shows a pane whose
last line pairs a number with credits and follows the current mode, model and count. With an image
model at x1 it read **0** — images spend daily quota, not credits. For video the same line read
**12** for Omni 1.1 Flash · 720p · 8 s · x1 in
[2026-09-05-migrated-host-wire-protocol](2026-09-05-migrated-host-wire-protocol.md); the video
value was not re-measured today.

**2. No price was found on the wire — and for a paid model this stayed UNMEASURED.** `HTrJv`
(≈25 KB, on project load) is a model catalog mapping ids to display names
(`veo_3_1_t2v_fast_4s_relaxed` → "Veo 3.1 - Fast"); the model tokens read were not next to
price-like integers. `tRARke` (≈32 KB) is the community tools gallery. Payloads are positional
arrays, so a bare number carries no key.

Two limits make this a **non-result, not a negative**, and both are properties of how it was run:

- The script caps the evidence at the **first 12 model-token neighbourhoods per body**
  (`spike_credits_migrated_surface.py`, `[:12]`). In a ≈25 KB catalog most tokens were never read.
- The pre-registered v2 reading required "integers matching the pane's number". The pane read
  **0** (an image model at x1). At a zero reference the wire test cannot discriminate a price
  from any other zero, so its antecedent never held. The correct verdict for a *paid* model is
  UNMEASURED — re-run against a video model, where the pane reads a non-zero number, before
  reading anything into the wire's silence.

**3. No balance was found.** No page text, no aria-label and no watched response carried a
balance on either profile. `fetch_credits_http` returned `AisandboxAuthError: credits endpoint
returned 401` on both profiles — that 401 is these profiles' credential state, not a property of
the route (the same lost-Bearer state).

> **§2 and §3 are provisional.** A council review of this spike found the instrument that produced
> them swallowed body-read failures (`except Exception: body = ""`), which renders an unreadable
> response *identical in the capture* to one carrying no credit data — precisely the negative these
> two sections report. It also wrote its capture after browser teardown, so a mid-run failure lost
> everything collected. Both are fixed in the same PR as this document, and the repaired script now
> reports a count of unreadable bodies. **The numbers above came from the version before the fix**,
> so treat §2 and §3 as not-yet-settled and re-run before building on them — it costs $0. §1 (the
> pane reading) is a direct positive observation and is unaffected.

## What this means for WS2

**Read every line below as scoped to one profile.** `ci-probe` produced every positive reading
here; `denon82` never reached the app. A second profile has not confirmed any of it, and this
repo has been wrong before by promoting a one-account observation to a product fact
([[flow-capabilities-are-cohort-dependent]]). Treat these as what gflow can drive on the
profiles we can test today, not as statements about Flow.

- A cap on **declared price** is buildable on the profiles we can test: the migrated composer
  already opens the settings pane to choose model and count, so reading the cost line there adds
  no navigation. It needs a **structural anchor** for that line (it was located by its text in
  this spike, which is discovery only — never a production selector) and a locale-independent
  number parse.
- A cap that compares against the **balance** is not buildable on the profiles we can test — the
  balance surface is still unlocated (#795). Flow's own shortfall warning
  (`prompt-warning-button`) is already mapped to `InsufficientCreditsError`, so a user cannot
  silently overspend the balance.
- The labs route (`projectInitialData` `creditMapping` + `remainingCredits`) returned 401 on both
  profiles probed. That is the **credential state of these profiles**, not a property of the
  route: this account lost its `ya29` Bearer around 2026-09-11 while generation over the migrated
  composer kept working.

## Not measured

- **A paid model's wire traffic.** Every wire reading was taken with the pane at 0 credits, where
  the pre-registered test cannot discriminate (see §2). Nothing here is evidence that a video
  model's price is absent from the wire.
- **Every model token in each body** — the script reads only the first 12 neighbourhoods per body.
- A second profile. `ci-probe` is the only profile that reached the composer.
- The video cost line today, for each video model and for counts above x1.
- Whether the cost line updates without reopening the pane after changing count.
- Every integer in `HTrJv`, `Zzl0ze`, `UpteDb` and `o30O0e` bodies — a price or balance could be
  positional there; only model-token neighbourhoods were read.
- Any traffic after submit (deliberately excluded: costs credits).
- `denon82` beyond `/about` (see the 2026-09-10 `/about` redirect spikes).
- **Whether any watched body failed to read.** The instrument could not distinguish a read failure
  from an empty body (see the note under §3). The repaired script reports this; this run could not.
- **The rpcid inventory is not re-derivable from this run's stdout.** The script printed only the
  route *count*, naming a route solely when it looked credit-shaped, so the sizes quoted in §2 came
  from the JSON capture — which is gitignored and is not retained on the machine that ran it. The
  repaired script prints the route ids.

**What would settle the balance question:** the HAR harness with a human opening Flow's account or
plan surface on a `flow.google.com` profile, then searching the capture for the balance the UI shows.
