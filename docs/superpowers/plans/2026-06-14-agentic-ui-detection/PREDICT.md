# Predict: Pluggable UI Driver Strategy for Agentic UI Support

## Verdict: GO (evidence-backed)
**Confidence:** 8/10

> **Evidence update — 2026-06-14 (live capture).** The original GO (8.8) scored the
> architecture but never tested the core scraping assumption. A focused re-run flagged
> that gap as a CAUTION, then a live capture
> (`gflow-agent-browser-spike/scripts/capture-media-scrape.ps1`, artifact
> `media-scrape-agentic-20260614-202739.json`) **resolved it in favour of GO** — with
> three concrete corrections the original plan got wrong (see Required corrections).
> Full findings: `docs/AGENT_UI_RECON.md` § "DOM scraping validation".

## Summary
A modular Strategy Pattern (`FlowUiDriver` → `ClassicFlowUiDriver` / `AgenticFlowUiDriver`),
bound by a runtime DOM probe, drives both cohorts. The architecture is sound and endorsed
across all personas. Live capture confirms the agentic cohort's generated assets are
scrapeable as remote `https` `<img>` nodes (no blob/data/canvas/background-image), and that
page-level network capture is dead (HAR = 0 entries; worker-delegated). DOM scraping is
both **viable and the only option** — but it must dedupe by media id, not count nodes.

## Persona findings

### Architect — GO (9/10)
- Drivers isolated under `src/gflow_cli/api/transports/drivers/`; DOM interaction stays in
  the transport layer, client/CLI untouched. Clean Protocol-port shape.
- **Binding must live at the generation boundary, not `setup()`** — the cohort is
  server-assigned per page load and flaps mid-session (recon). A driver cached at setup
  goes stale on the next batch item / re-navigation.

### Security / reCAPTCHA — GO (9/10)
- Typing into the Slate box + clicking send lets the page mint its own reCAPTCHA token
  (the `streamChat` payload's `recaptchaContext.token` is client-produced) — we never
  touch auth headers. Strictly safer than HTTP-forging.
- Scraped src is same-origin (`labs.google/fx/api/trpc/media.getMediaUrlRedirect`);
  session cookies authorize download. Do not log full media URLs at INFO.

### Performance / Playwright — GO (7/10)
- **Validated:** assets are remote `<img>`, count-delta scraping works.
- **Correction:** one asset = multiple `<img>` nodes (full-res + thumbnail variants);
  9 nodes for 3 assets in the sample. Dedupe by the `name=<uuid>` query param.
- Slate.js input needs `pressSequentially` / `insertText`, not `fill()`.

### CLI UX / Cross-platform — GO (8/10)
- `FlowAgentUiError` already exists → exit 25; `ContentPolicyError` → 5; reuse
  `TransportTimeoutError` → 9 for a scrape that never reaches the expected UUID count.
- Reconcile the stale "exit 23" in the recon prose against the mapped 25.

### Devil's Advocate — GO with discipline (7/10)
- The cohort is a volatile, active A/B (observed reverting within 12h). Prefer the
  lowest-selector-surface path: **prompt-encode settings** rather than driving the
  `tune` popover (the agentic UI resolves count/duration/aspect from natural language,
  MCP-style). Keep popover automation as a fallback only.
- Keep Phase-1 fail-clean (`FlowAgentUiError`) as the floor when scraping can't confirm
  the expected distinct-UUID count.

## High-confidence risks (flagged by 2+ personas)
1. ~~Scraping mechanism unverified~~ — **RESOLVED** by live capture: remote `<img>`,
   scrapeable, HAR-confirmed worker bypass.
2. **Driver staleness vs. flapping cohort** (Architect, Devil's Advocate) — bind per
   generation, never cache across navigations.
3. **Node-count over-counting** (Performance) — dedupe by `name=<uuid>`, not `<img>` count.

## Required corrections before EXECUTE
1. **`await_images` counts distinct `name=<uuid>` media ids**, not raw `<img>` nodes
   (proven ~3× inflation). Extract the URL pattern
   `media.getMediaUrlRedirect?name=<uuid>[&mediaUrlType=…THUMBNAIL]`.
2. **`configure_settings` prefers prompt-encoding** the count/duration/aspect; scrape +
   dedup verifies the produced set and raises a typed mismatch if it differs from request.
3. **Fail-fast must not key on the `flag` ligature** (a normal chat affordance — matched
   11× on a successful generation). Detection keys on `warning`/`error`/`block` or dialog/
   stream text — pending a content-policy block capture (still outstanding; see recon
   "Open follow-ups").
4. **Re-detect/bind the driver per generation**, not once in `setup()`.

## Recommended next step
Update `PLAN.md` Task 3 to encode the four corrections above, then proceed to EXECUTE.
Capture a deliberate content-policy-refusal sample in the next live agentic session to
finalise fail-fast detection before relying on it.
