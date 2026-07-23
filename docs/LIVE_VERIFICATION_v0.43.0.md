# Live verification — v0.43.0 (private incident diagnostics)

> Evidence for the design's §10.3 live matrix
> (`docs/superpowers/specs/2026-07-22-private-incident-diagnostics-design.md`).
> Offline coverage (2,600+ tests incl. 43-scenario traceability) verifies
> gflow-cli's own behavior; this document records what was proven against
> **real Google Flow**. Harness:
> [`scripts/dev/spike_incident_live.py`](../scripts/dev/spike_incident_live.py).

**Date:** 2026-07-23 · **Profile:** `denon82` (real Pro session) · **Credits
spent: 0** (t2i is credit-free; no video was generated)

## Step 1 — recorder on a real authenticated Flow page (✅ 19/19 checks)

A real headed-Chrome session opened the live Flow editor (no generation
submitted); after ~5 s of real traffic a deliberate `FlowAppError` was staged
through `FlowApiClient._capture_incident`, then the session closed normally.

- **Journals saw real traffic:** the network ring was at its 100-record cap
  from genuine editor requests before capture.
- **Bundle finalized** (`gflow-incident-v1`), artifacts
  `browser.json`/`network.json`/`ui.json`/`sensitive/screenshot.png`,
  `incident.capture_started`/`capture_completed status=complete` events.
- **Structural DOM engine works on the real page:** 3 unique
  Material-Symbol ligatures captured; the live URL reduced to
  `{host_category: flow_app, route: /fx/pt/tools/flow}` — note the real
  session served the localized `/fx/pt/...` route and the canonicalizer
  handled it (query-free, no identifiers).
- **Host reduction on real traffic:** every journaled host fell into
  `{flow_app, aisandbox, google_cdn, google_static, google_web, other}` —
  no raw third-party host survived.
- **Leak scan clean:** the account email, profile name, `ya29`/`SAPISID`/
  session-token markers, and any URL query string are absent from every JSON
  artifact in the bundle.
- **HAR honesty:** `har_state: disabled` (no `GFLOW_CLI_HAR_PATH` set); no
  HAR was created or copied.
- **Screenshot:** present under `sensitive/`, classified `sensitive` in the
  manifest; reviewed locally, not shared.

## Step 2 — real T2I with capture enabled, no incident on success (✅)

`gflow image t2i "minimalist geometric test pattern, flat colors"
--profile denon82` → exit 0, real 768×1376 image with JPEG magic bytes
(`FF D8 FF E0 … JFIF`), catalog row recorded — and the incidents directory
count was **unchanged (4 → 4)**: a successful command writes no bundle,
with listeners attached the whole time. $0 (t2i consumes no credits —
long-standing verified credit model).

## Step 3 — real two-process profile contention (✅, part of the 19/19)

While the Phase-1 client held the `denon82` lease, a second **real CLI
process** ran `image t2i`:

- exit **11** (`ProfileLockedError`) in **5.6 s** — fails fast, no Chrome
  launched for the contender;
- human output printed the `Incident bundle:` path **and** the
  review-before-sharing warning;
- a **metadata-only** bundle was written (no `ui.json`, no `sensitive/`),
  `error.class=ProfileLockedError`, `exit_code=11`, leak-scan clean;
- the holder's lock file and process were untouched (no reclaim).

## Step 4 — paid `veo-lite` T2V lifecycle (⏸ NOT RUN — awaiting approval)

**Explicitly recorded as an unverified release risk, not silently omitted**
(design §10.3): one real Veo generation with capture enabled (bounded
journals over a long poll, listener cleanup, no incident on success — S43)
requires a paid credit and therefore **separate explicit operator
approval**. Until it runs, release notes must state that the long-running
video lifecycle under the incident recorder is unverified. One successful
run proves that lifecycle only — any availability/stability claim would
additionally require a separately approved, budgeted soak.

## Aggregate-gate note

The first full-suite aggregate run after implementation reproduced the
§13.2 failure class (`test_built_distributions_contain_sql_migrations`
failing only in aggregate). Treated as a lifecycle regression per the
design — bisected to staged-but-unfinalized incident bundles holding their
kernel-locked `.pending` fd for the session (Hatchling's sdist traversal
then failed on the locked byte, Windows). Root-cause fixed in
`BundleDir.__del__` (crash-left semantics: marker stays, lock frees) with a
regression test; the exact repro group and the full aggregate are green.
