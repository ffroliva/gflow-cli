# Live verification — v0.43.0 (private incident diagnostics)

> Evidence for the design's §10.3 live matrix
> (`docs/superpowers/specs/2026-07-22-private-incident-diagnostics-design.md`).
> Offline coverage (2,600+ tests incl. 43-scenario traceability) verifies
> gflow-cli's own behavior; this document records what was proven against
> **real Google Flow**. Harness:
> [`scripts/dev/spike_incident_live.py`](../scripts/dev/spike_incident_live.py).

**Date:** 2026-07-23 · **Profile:** `denon82` (real Pro session) · **Credits
spent: 1 Veo credit** (explicit operator approval; steps 1–3 were $0)

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

## Step 4 — paid `veo-lite` T2V lifecycle (✅ S43, explicit approval, 1 credit)

`gflow --verbose video t2v "a paper boat drifting across a calm pond at
sunrise, gentle ripples" --profile denon82 --model veo-lite` with incident
capture enabled end-to-end:

- **Submission:** `ui_automation_video.generate_captured` — HTTP **200** on
  the real `aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText`
  endpoint (correlation `6525222c`).
- **Polling:** `poll_terminal` → `MEDIA_GENERATION_STATUS_SUCCESSFUL` for
  media `9e1750e7-df7a-4f4b-ad07-8e3672d55aa9` (~43 s of polling with the
  journal listeners attached throughout).
- **Valid MP4:** 2,337,517 bytes, `ftyp isom` magic bytes, saved to the
  requested `--out-dir`.
- **Provenance:** `gflow data list videos` shows the row (media id, profile,
  project, prompt, `veo_3_1_lite`, local path, `copy_count: 1`).
- **No incident on success:** incidents directory count unchanged (4 → 4)
  and **zero** `incident.capture_started` events in the full verbose log.
- **Listener cleanup / no leaked lease:** clean exit 0, and the `denon82`
  `ProfileLease` was re-acquirable immediately after the run.

**Scope honesty:** this proves ONE Veo lifecycle under the recorder. It is
not a soak; any unattended-availability claim still requires a separately
approved, budgeted soak with a declared run count and duration.

## Aggregate-gate note

The first full-suite aggregate run after implementation reproduced the
§13.2 failure class (`test_built_distributions_contain_sql_migrations`
failing only in aggregate). Treated as a lifecycle regression per the
design — bisected to staged-but-unfinalized incident bundles holding their
kernel-locked `.pending` fd for the session (Hatchling's sdist traversal
then failed on the locked byte, Windows). Root-cause fixed in
`BundleDir.__del__` (crash-left semantics: marker stays, lock frees) with a
regression test; the exact repro group and the full aggregate are green.

## Pre-tag gates

**`/gflow:check` (2026-07-23):** repo hygiene ✓, doc-links ✓ (27 files),
`website/docs/` PII gate ✓ + mirror in-sync ✓, `ruff check` + `ruff format
--check src tests` clean, `pyright src` 0 errors. Full suite is green on the
release tree (CI **success** on the merge commit `abfa62f`, the exact tree cut
into this release); version-sensitive tests re-run locally after the bump
(`test_smoke` / `test_handoff` / `test_diagnostics_events` — 19 passed).

**`/gflow:doc-review` council (2026-07-23):** verdict **GREEN / YELLOW /
YELLOW** across the drift / completeness / cross-reference auditors — no RED,
no release-blocking (Tier-1) findings. Two Tier-2 findings, both fixed in the
release-prep commit: (1) exit code `31` (`FlowAppError`) was missing from the
canonical exit-code table — added to `docs/USAGE.md`; (2) `docs/PROJECT_STATUS.md`
"Current release" was stale at v0.41.0 (a prior release skipped its update) —
promoted to v0.43.0 and backfilled the v0.42.0 + v0.41.0 history rows. The
drift auditor verified every CHANGELOG `[0.43.0]` technical claim against real
source (retention 50/250 MiB, offset-1 lock layout, honest `har_state`, single
`errors.is_retryable()` across CLI-JSON / MCP / worker, exit 31/15 maps,
version parity). Council reports at `tmp/council/0{1,2,3}-*.md` (local-only).
