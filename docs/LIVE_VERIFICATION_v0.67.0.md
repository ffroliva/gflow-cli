# Live verification — v0.67.0 (pre-release evidence, 2026-09-05)

**Feature:** text-to-video on Flow's migrated `flow.google.com` host (#639) — the
migrated composer (`src/gflow_cli/api/transports/migrated_composer.py`), routed by
`GFLOW_CLI_FLOW_HOST`. Plan: `docs/superpowers/plans/2026-09-05-migrated-host-driver/`.

Every number below was measured from the **user entrypoint** — the `gflow` CLI — not
from a component in isolation (`[[live-verify-must-name-the-entrypoint]]`). Wall-clock
includes Chrome launch. Credits: one 8 s Omni 1.1 Flash clip per run at the account's
cohort rate; nothing else in this verification spent credits.

## Runs

| # | Profile (account state, locale) | Route | Command | Exit | Wall-clock | Output |
|---|---|---|---|---|---|---|
| 1 | flagged, en-GB | `auto` (default) → migrated composer | `gflow video t2v "…" --project <id> --duration 8 --aspect 16:9 --json --out-dir tmp/…` | **0** | **49.9 s** | `684649e9-….mp4`, `ftyp`, **1,792,457 B** (= size the record reported) |
| 2 | **unflagged, pt** | `GFLOW_CLI_FLOW_HOST=flow.google.com` (forced; under `auto` this request now takes the identical `route == "migrated"` path, since t2v-with-project is served by the new host by default) | same shape, own project | **0** | **50.5 s** | `f080c0c5-….mp4`, `ftyp`, **2,143,562 B** (= record size) |

Run 2 is the locale-invariance proof: a Portuguese-locale account, every anchor
resolved (ligatures, roles, class, numeric tokens) — no text label was matched.

## Timeline (run 1; run 2 within ±1 s at every step)

| t | event | detail |
|---|---|---|
| 6.8 s | `migrated.dispatch` | bootstrap page already on the migrated host → composer chosen before any labs project entry |
| 6.8 s | `migrated.navigate` | direct `https://flow.google.com/project/<id>` |
| 8.6 s | `migrated.editor_ready` | `.settings-trigger-button` visible |
| 8.8 s | `migrated.settings_applied` | mode/aspect/duration/count radios, `aria-checked` read back |
| 10.0 s | `migrated.prompt_typed` | `[contenteditable]` composer |
| 10.2 s | `migrated.submit_clicked` | `arrow_forward` |
| 14.5 s | `migrated.submit_observed` | `YhhmEf` 200, status 6 — `VideoStarted` fired here (recorder row) |
| 15.6 → 40.6 s | `migrated.status` ×6 | `jwpduf` status 2 every 5 s (the app's own polling; the driver adds no traffic) |
| 45.6 s | `migrated.status` | `jwpduf` status **3**, bytes reported, no URL yet |
| 47.9 s | `migrated.status` | `as29s` status 3 with the signed `flow-content.google` URLs |
| 47.9 s | `migrated.result` | `url_host=flow-content.google` |
| 48.6 s | `migrated.download` | mp4 written, magic verified |

## Five-layer ledger (`[[verification-ledger-5-layer]]`)

1. **File count:** 1 mp4 per run in the requested `--out-dir`.
2. **Magic bytes:** `ftyp` at offset 4 on both files (the first driver build had
   downloaded a 37 KB JPEG here — the poster URL — which is why `download()` now
   verifies the container and falls back to the other URL).
3. **Size:** byte-exact match with the size the migrated backend reported in the
   status record (1,792,457 and 2,143,562).
4. **structlog:** the `migrated.*` timeline above, `correlation_id`-bound, in the
   `--json` run's stderr; the `--json` envelope on stdout reports
   `MEDIA_GENERATION_STATUS_SUCCESSFUL`.
5. **Catalog:** `gflow data media 684649e9-… --profile <flagged>` → project id, kind
   `video`, local path — the `VideoStarted` callback reached the recorder through the
   unchanged transport contract.

## Exercised on the way here (and fixed before this record)

- `jwpduf` reports status 3 **before** the record that carries the URLs; the first
  build treated it as terminal and had no URL → grace period for the URL record.
- The labs `media.getMediaUrlRedirect` route answers **404** for a migrated media
  id → the signed CDN URL is the primary download path.
- `DETAILS[10]` is the poster JPEG, `MEDIA_INFO[0][8]` the mp4 → mapping swapped,
  magic verified.

## NOT verified (recorded, not omitted)

- `gflow image t2i` and every other command on the migrated host — not ported;
  they still exit 36 there (documented in KNOWN_ISSUES #639).
- Project creation on the migrated host — `--project` is required.
- The `GFLOW_CLI_FLOW_HOST=labs.google` kill switch live (unit + BDD only).
- The MCP queued path live (shares the transport; unit-covered by the dispatch
  tests, worker envelope semantics unchanged).
- A failure status value from the migrated backend (never observed; surfaced raw).
