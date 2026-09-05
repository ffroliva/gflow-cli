# Live verification — v0.69.0 (pre-release evidence, 2026-09-05)

**Feature:** image-to-video from a **local start frame** on Flow's migrated
`flow.google.com` host ([#639](https://github.com/ffroliva/gflow-cli/issues/639), slice 1) —
`gflow video i2v --initial-frame <file> --project <id>` served by the migrated composer
(`src/gflow_cli/api/transports/migrated_composer.py`). Recon:
`docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md`; plan and scenarios:
`docs/superpowers/plans/2026-09-05-migrated-i2v/`.

Two moved accounts, two locales. The decisive run is the **CLI entrypoint** on the
Portuguese-locale account (`[[live-verify-must-name-the-entrypoint]]`); the e2e tests in
`tests/e2e/test_migrated_i2v_e2e.py` are the contributor-facing evidence layer
([#675](https://github.com/ffroliva/gflow-cli/pull/675)) and ran on both. Credits: one
Veo 3.1 Lite clip per billed run; the attach-only tests spent nothing (they stop before
any submit — the probe upload stays in the project's library, like any upload).

## Runs

| # | Profile (account state, locale) | Entrypoint | Command | Exit | Wall-clock | Output |
|---|---|---|---|---|---|---|
| 1 | flagged, en-GB (`ffroliva`, project `300f5260…`, empty) | `tests/e2e/…::test_e2e_start_frame_uploads_and_binds_on_the_migrated_host` (`e2e_auth`, $0) | upload probe PNG → picker by file name → Start chip bound, no submit | **PASS** | 19.4 s | media id UUID from `maseQ`; `flow-prompt-box button.chip-container:has(img)` count ≥ 1 |
| 2 | flagged, en-GB (`ffroliva`, same project) | `…::test_e2e_i2v_from_a_local_start_frame_runs_on_flow_google_com` (`e2e_video`, bills one clip) | full `transport.generate_video` with a 256×256 probe PNG, no `--duration` | **PASS** | 61.0 s | `77291283-….mp4`, `ftypisom`, **777,004 B** |
| 3 | **flagged, pt** (`denon82`, project `f7ed2765…`, 32 videos) | `…::test_e2e_start_frame_uploads_and_binds_on_the_migrated_host` (`e2e_auth`, $0) | as run 1 | **PASS** (on the third attempt — see below) | 16.0 s | media id from `maseQ`, chip bound |
| 4 | **flagged, pt** (`denon82`, same project) | **`gflow video i2v --initial-frame tmp/…/orange-sphere.png "…" --project <id> --profile denon82 --aspect 16:9 --json --out-dir tmp/…`** | the user command | **0** | **64 s** | `f0b9378d-….mp4`, `ftypisom`, **632,755 B** (= bytes the status record reported) |

Run 4 is the locale-invariance proof for the new stages: on a `lang=pt` editor the
toolbar `add` ligature, the `upload` menu ligature, the `flow-prompt-box` chips, the
`flow-add-menu-popover-content` picker and its `button.asset-item[role=option]` entries
all resolved — no text label is matched anywhere in the attach path (the search input is
found by `input[type=text]` inside the picker, never by its translated `aria-label`).

## Timeline (run 4, `--json` stderr, `correlation_id`-bound)

| t | event | detail |
|---|---|---|
| 0.0 s | `migrated.dispatch` | `mode=i2v`, project named → composer chosen, direct navigation |
| 1.6 s | `migrated.editor_ready` | `.settings-trigger-button` visible |
| 2.0 s | `migrated.model_selected` | `Veo 3.1 - Lite` (the CLI's i2v default, #125) |
| 2.1 s | `migrated.settings_applied` | mode → **Frames submode (`crop_free`)** → model → aspect → count; no duration requested |
| 10.0 s | `migrated.frame_uploaded` | rpc `maseQ` **200**, `media_id=36da2cf1-…` — the app's own upload after the toolbar `add` → `upload` → file chooser |
| 11.7 s | `migrated.frame_bound` | picker searched by file name, first option clicked, chip holds the thumbnail |
| 11.8 s | `migrated.prompt_typed` | 61 chars into `[contenteditable]` |
| 12.0 s | `migrated.submit_clicked` | `arrow_forward` |
| 16.4 s | `migrated.submit_observed` | **rpc `eb1hJf`** (not `YhhmEf`), status 6, `media_id=f0b9378d-…` — the submit *request* was inspected first: body carried `36da2cf1-…` and an `_i2v_` key, so no `WireFormatError` |
| 17.3 → 47.4 s | `migrated.status` ×7 | `jwpduf` status 2 every 5 s (the app's own polling) |
| 52.4 s | `migrated.status` | `jwpduf` status **3**, `bytes=632755`, no URL yet |
| 55.2 s | `migrated.status` / `migrated.result` | `as29s` status 3 with the signed `flow-content.google` URL |
| 55.5 s | `migrated.download` | mp4 written, magic verified |

## Five-layer ledger (`[[verification-ledger-5-layer]]`, run 4)

1. **File count:** 1 mp4 in `--out-dir`.
2. **Magic bytes:** `ftypisom` at offset 4 (run 2 as well).
3. **Size:** 632,755 B, byte-exact with the size the status record reported (run 2: 777,004 B).
4. **structlog:** the timeline above; the `--json` envelope on stdout reports
   `MEDIA_GENERATION_STATUS_SUCCESSFUL`, `"mode": "i2v"`, `"model": "veo_3_1_lite"`.
5. **Catalog:** `gflow data media f0b9378d-… --profile denon82` → project `f7ed2765-…`,
   kind `video`, the local path — the `VideoStarted` callback reached the recorder through
   the unchanged transport contract.

## Exercised on the way here (and fixed before this record)

- **The picker does not always list a fresh upload on the first search.** On `denon82`'s
  32-asset project both e2e tests missed the upload within 8 s (`ReferenceNotFoundError`,
  $0, no submit) and the identical test passed minutes later. The picker search is
  server-side (`UpteDb`); the composer now reopens the popover and searches again, up to
  `FRAME_SEARCH_ATTEMPTS = 3` times, and the refusal detail lists what the picker *did*
  show. Run 4 bound on its first search.
- **Forcing `--duration` in the Frames submode is a $0 exit 11 on this cohort** (the #650
  shape: the pane rendered 4 option groups and no duration row for Veo 3.1 Lite). The e2e
  no longer forces a duration; the user command in run 4 passed none.

## Not verified (recorded, not omitted)

- The `e2e_video` test on `denon82` — the billed run there was the CLI entrypoint (run 4),
  not the pytest path; the pytest path is proven on `ffroliva` (run 2).
- A submit whose body is a t2v key or a foreign media id (the `WireFormatError` guard) —
  reproducible offline only; every live submit carried the right id and an `_i2v_` key.
- `--duration` on a model whose pane renders the row (Omni 1.1 Flash) in the Frames submode.
- A start frame larger than a few hundred KB (`maseQ` inlines the file; 60 s budget), a
  JPEG with EXIF (labs 400 class), portrait `9:16`, `count > 1`, the MCP queued path live.
- The "Get started" changelog modal dismissal — neither account raised the modal (both had
  acknowledged it); the code path is offline-tested only.
- End frame, UUID and `@Name` frames stay unported and exit 36 with the form named
  (offline-tested; not driven live).
