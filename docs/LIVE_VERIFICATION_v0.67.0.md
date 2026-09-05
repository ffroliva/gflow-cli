# Live verification — v0.67.0 (pre-release evidence, 2026-09-05)

**Feature:** text-to-video on Flow's migrated `flow.google.com` host (#639) — the
migrated composer (`src/gflow_cli/api/transports/migrated_composer.py`), routed by
`GFLOW_CLI_FLOW_HOST`. Recon: `docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md` (the implementation plan was folded into project memory at release time, per the doc-review skill).

Every number below was measured from the **user entrypoint** — the `gflow` CLI — not
from a component in isolation (`[[live-verify-must-name-the-entrypoint]]`). Wall-clock
includes Chrome launch. Credits: one 8 s Omni 1.1 Flash clip per run at the account's
cohort rate; nothing else in this verification spent credits.

## Runs

| # | Profile (account state, locale) | Route | Command | Exit | Wall-clock | Output |
|---|---|---|---|---|---|---|
| 1 | flagged, en-GB | `auto` (default) → migrated composer | `gflow video t2v "…" --project <id> --duration 8 --aspect 16:9 --json --out-dir tmp/…` | **0** | **49.9 s** | `684649e9-….mp4`, `ftyp`, **1,792,457 B** (= size the record reported) |
| 2 | **unflagged, pt** | `GFLOW_CLI_FLOW_HOST=flow.google.com` (forced; under `auto` this request now takes the identical `route == "migrated"` path, since t2v-with-project is served by the new host by default) | same shape, own project | **0** | **50.5 s** | `f080c0c5-….mp4`, `ftyp`, **2,143,562 B** (= record size) |

Run 2 is the locale-invariance proof: a Portuguese-locale account (its migrated editor
served `lang=pt`, captured by the 2026-09-04 landing probe), every anchor resolved
(ligatures, roles, class, numeric tokens) — no text label was matched.

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

## #650 — `--duration` on the Veo 3.1 models (verified on the migrated host, $0)

Both maintainer accounts are on `flow.google.com` as of 2026-09-05 (the second one
moved overnight: 3/3 loads), so the labs-side duration guard is **cohort-external**.
Per model on the new host, measured with `scripts/dev/spike_migrated_duration_by_model.py`
(read-only) on **one profile, one cohort (the flagged en-GB maintainer account, N=1)** —
other cohorts render other rows (`flow-capabilities-are-cohort-dependent`):

| Model | Duration row | Resolution row | Credits (8 s, x1) |
|---|---|---|---|
| Omni 1.1 Flash | `4s 6s 8s 10s` | `360p 720p` | 12 |
| Veo 3.1 – Lite | — | — | 10 |
| Veo 3.1 – Fast | — | — | 20 |
| Veo 3.1 – Quality | — | — | 100 |

Entrypoint run: `gflow video t2v "…" --model veo-fast --duration 6 --aspect 16:9 --project <id> --json`
on the flagged profile → **exit 11** in 9.7 s, `ConfigurationError`:
*"the migrated Flow host renders no 'duration' control offering '6s' on this account
and model (4 option groups shown) — drop the option or pick a model that offers it"*,
`retryable: false`, `migrated.dispatch → editor_ready` and nothing after — no submit, no
credits, no file. That is the #650 contract ("control rendered?" decides, pre-submit,
at zero cost). The **positive** Veo 4/6/8 path remains NOT verified here: no maintainer
cohort renders it on either host.

The first attempt of this run exposed a driver bug on the `--model` path (the settings
pane resolved to a detached menu overlay after the model switch, reporting "no 'aspect'
control … 0 option groups"); fixed in the same change and pinned by
`test_axes_still_resolve_after_a_model_switch`.

## Pre-tag gates

| Gate | Result |
|---|---|
| `/gflow:check` (hygiene · doc-links · PII · mirror · council-memory · ruff · format · pyright · pytest) | ✅ 2026-09-05 on the release tree: 940 tracked files clean, 29 doc files' links resolve, 25 mirror files PII-clean, mirror in sync (20 files), 46 council memories resolve, ruff + format clean (426 files), pyright 0/0/0, pytest **3764 passed, 7 skipped** (coverage run, 10 min 21 s). 53 setup errors in that run were a `tmp/pytest` collision with a concurrent scoped run — the same files re-ran green in isolation (172 passed); one deadline-based agentic test flaked under load and passed 3/3 alone, including on the unmodified tree. CI on the `develop` head this branch was cut from (`6680344`) is green. |
| `/gflow:live-verify` | this document (runs 1–2 + the #650 negative path) |
| `/gflow:pr-council-review` (branch mode on `develop`) | ✅ pre-release council on `develop`, fixes merged as PR #666 (`6680344`) — exit-code truth on the migrated host, #659, dead code, doc drift |
| `/gflow:doc-review` (mechanical + 3-auditor council) | ✅ mechanical §1–7 PASS. _Council verdict: YELLOW across all 3 auditors (no RED). 12 findings; 0 Tier 1; 9 Tier 2 fixed in the release-prep commit (`AGENTS.md` exit range 3–36; `llms.txt`, `docs/USAGE.md` t2v, `docs/INDEX.md`, `docs/ARCHITECTURE.md`, `skills/gflow-cli/SKILL.md` now name the migrated host + `--project`; `docs/MCP.md` exit-11 equivalent; `docs/PROJECT_STATUS.md` v0.66.1 "0 ms" row re-credited to v0.66.2; stale "flaps / callers retry" comments in `drivers/factory.py`); 3 Tier 3 deferred (MCP.md naming `FlowHostMigratedError` in its terminal list, exit codes in the USAGE duration note, dangling `[[wikilinks]]` into the private memory store). Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._ |
| `scripts/ci/check_release_artifacts.py` | ✅ `release artifacts OK — /gflow:release protocol satisfied.` |

## Post-tag evidence

Filled after the tag push: release workflow run, PyPI version, GitHub Release URL,
`pip install gflow-cli==0.67.0` smoke.

## NOT verified (recorded, not omitted)

- `gflow image t2i` and every other command on the migrated host — not ported;
  they still exit 36 there (documented in KNOWN_ISSUES #639).
- Project creation on the migrated host — `--project` is required.
- The `GFLOW_CLI_FLOW_HOST=labs.google` kill switch on an UNMOVED account (no such account
  remains). On a moved account it was exercised live: `labs.google` + `--project` → exit 36 at
  8.9 s with `at=flow_host_kill_switch`, no submit.
- The MCP queued path live (shares the transport; unit-covered by the dispatch
  tests, worker envelope semantics unchanged).
- A failure status value from the migrated backend (never observed; surfaced raw).
