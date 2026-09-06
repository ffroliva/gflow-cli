# Live verification — v0.69.0 (pre-release evidence, 2026-09-05 / 2026-09-06)

> This ledger began as the record for #639 slice 1 and became the release ledger when
> three more user-facing changes merged into v0.69.0. The i2v record below is unchanged;
> the other features are in **§ Also shipped in v0.69.0** at the end, which states for
> each one whether this session ran it or is citing someone else's run.

**Headline feature:** image-to-video from a **local start frame** on Flow's migrated
`flow.google.com` host ([#639](https://github.com/ffroliva/gflow-cli/issues/639), slice 1) —
`gflow video i2v --initial-frame <file> --project <id>` served by the migrated composer
(`src/gflow_cli/api/transports/migrated_composer.py`). Recon:
`docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md`. The plan and scenarios were
consolidated into `docs/superpowers/memory/migrated-host-driver-wire-lessons.md` at release.

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
| 1b | flagged, en-GB (`ffroliva`, same project) | same test after the council fixes (`e2e_auth`, $0) | as run 1, plus the #125 model default | **PASS** | 17.5 s | `migrated.i2v_model_defaulted model=veo_3_1_lite` → `migrated.model_already_selected model="Veo 3.1 - Lite"`, then `frame_uploaded`/`frame_bound` on `6406df81…` |
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
- **An i2v request with no model never touched the picker** (found by the PR council, not
  by a run). Runs 1–2 built the request directly, so `request.model` was `None` and the
  composer left the editor on whatever tier it remembered — on `ffroliva` that reads
  `Veo 3.1 - Lite`, so run 2 did bill the 10-credit tier, but a queued MCP request (whose
  payload also carries no model) could have inherited a 100-credit one. The composer now
  binds `I2V_DEFAULT_MODEL` for i2v exactly as the labs path does; run 1b is the live
  proof, and `migrated.settings_applied` now reports the effective model, not the
  requested one.
- **The attach stage had an outer 90 s budget smaller than the sum of the waits it
  wrapped**, so a slow upload plus one picker miss would have replaced the stage-named
  failure with a generic timeout. Removed: every leg is bounded on its own. Worst case
  is now ~3.5 min (a 60 s upload plus three 8 s picker searches and their pauses)
  against the ~10 s measured here — a slow attach is not a hang.

## Not verified (recorded, not omitted)

> **Run-scope note (not an unverified item):** run 2 carries ledger layers 1–4 only — no
> catalog row is asserted, because that e2e drives the transport directly, below the
> recorder, and its size is checked as `> 100 KB` rather than byte-exact. Run 4 carries
> all five. Recorded here so the two runs are not read as equivalent.

Each entry names the **blocker** that stopped the run, per AGENTS.md § The Iron Law.
Where there is no blocker, it is labelled **DEBT** — a run someone still owes, not a
finding.

- The `e2e_video` test on `denon82` — **blocker: spends credits** on a second account for
  a path already proven on `ffroliva` (run 2). The billed `denon82` run was the CLI
  entrypoint (run 4), which is the stronger evidence anyway.
- A billed run *after* the council fixes — **blocker: spends credits** to re-prove a submit
  path the fixes do not touch. Covered at $0 by run 1b.
- A submit whose body carries a t2v key or a foreign media id (the `WireFormatError`
  guard) — **blocker: cannot be induced against live Flow.** The guard fires on Flow
  sending something it never sent us; it is reachable offline only.
- The "Get started" changelog modal dismissal — **blocker: cohort/account state we do not
  control.** Neither account raises the modal any more; both have acknowledged it.
- **DEBT** — `--duration` on a model whose pane renders the row (Omni 1.1 Flash) in the
  Frames submode; a start frame larger than a few hundred KB (`maseQ` inlines the file,
  60 s budget); a JPEG with EXIF (labs 400 class); portrait `9:16`; `count > 1`. No
  blocker: these are $0-to-cheap and simply were not run. Tracked in #686.
- **DEBT** — end frame, UUID and `@Name` frames exit 36 with the form named. Offline-tested
  only; the refusal never reaches Flow, so the residual risk is that the *detail string*
  drifts, not the behaviour. Tracked in #686.

---

## Also shipped in v0.69.0

### Flow credit balance — `gflow credits user` / `list`, `gflow_get_credits` ([#671](https://github.com/ffroliva/gflow-cli/pull/671))

**Run by this session, 2026-09-06, on the maintainer machine.** $0 — the endpoint is a
read-only GET. This is the feature's whole premise (a browser-free HTTP path), and the
first maintainer review found it launching Chrome on every profile, so it was re-tested
rather than accepted.

| # | Profile | Entrypoint | Exit | Output |
|---|---|---|---|---|
| 5 | `ffroliva` (flagged, en-GB) | **`gflow credits user --json`** — the user command | **0** | `credits.http_fast_path_succeeded status_code=200`, keys `credits, serviceTier, sku, subscriptionCredits, userPaygateTier`, `unknown_key_count=0`; **875** credits, `G1_TIER1`, **no browser launched** |
| 6 | all 9 saved profiles | **`gflow credits list`** — human-readable table | **0** | 3 profiles read over HTTP (875 / 641 / 50), 6 stale ones degraded per-row with their reason; `Total credits: 1566` — partial-result aggregation holds |
| 7 | `ffroliva` | `tests/e2e/test_credits_e2e.py` (`e2e_auth`, $0) | **PASS** in 1.48 s | patches the Playwright cookie fallback to fail immediately, so passing *is* the browser-free proof rather than an assertion about one |

Five-layer read: (1) one JSON object per profile; (2) n/a — no media; (3) shape asserted by
`CreditsInfo.from_response`, which fails closed rather than coercing a bad value to zero;
(4) `credits.http_fast_path_succeeded` with the status code and the redacted key set — no
credential and no value logged; (5) user-confirmable — 875 matches the balance the Flow UI
shows for that account.

### `--model veo-lite-lp` on the migrated host ([#669](https://github.com/ffroliva/gflow-cli/pull/669))

**Not run by this session — citing the contributor's run** recorded in the CHANGELOG entry:
all five tiers driven through `_select_model` with the picker read back after each switch,
including the lower-priority tier reached through the menu, plus one real 8 s generation on
it. The menu entry `Veo 3.1 - Lite [Lower Priority]` was captured on a migrated account
(`docs/superpowers/spikes/2026-09-05-migrated-model-menu-lower-priority.md`). Matching stays
on the `[Lower Priority]` tag rather than the newly-known label.

### Moved accounts exit 36 rather than `RecaptchaError` ([#673](https://github.com/ffroliva/gflow-cli/issues/673) / [#678](https://github.com/ffroliva/gflow-cli/pull/678))

**Not run by this session — citing the fixing session's run:** the `e2e_auth` regression
`test_e2e_image_on_a_moved_account_exits_36_not_recaptcha` passed live four times across four
heads, $0 each. The labs client minted the reCAPTCHA token on the pool's bootstrap page
*before* the UI transport, so no transport-level `raise_if_migrated` could fire; on a moved
account that page is the flow.google.com grid with no `enterprise.js`, so image/upscale/extend
died as exit 1 `RecaptchaError`. One guard in `_mint_recaptcha_token` makes it exit 36.

### Not a Flow surface — no live verification applicable

The `video-production` skill epoch-1 edit and the SkillOpt harness change touch no
generation path. The skill edit was validated instead by the mechanism that found the defect:
a controlled A/B rollout, one model, the document as the only variable, 0.00 → 0.90. The
harness change is covered by `tests/scripts/test_skillopt_scoring.py`.

### Challenged and then verified — 2026-09-06, post-tag

Both items below were first written into this ledger as "not verified". Neither was
blocked; both were run within the hour once that was challenged, and **both found
something**. They are the reason AGENTS.md now carries § The Iron Law.

**MCP twin of the credits path — VERIFIED.** `tests/e2e/test_credits_mcp_e2e.py`,
`e2e_auth`, $0, **3 passed in 58.15 s** against live Flow on `ffroliva`. It drives
`gflow_get_credits` itself — not the shared service — over a real profile: a live balance
for one profile with the full key contract asserted, `all_profiles=True` preserving partial
results across 9 saved profiles with every failed row carrying a reason, and an unknown
profile returning a structured refusal rather than raising across the tool boundary. The
earlier excuse — "the CLI path is verified and they share `services/credits.py`" — is
precisely the reasoning the Iron Law now forbids: the shared service was never the risk,
the adapter was.

**The 5 unscored benchmark tasks — VERIFIED, and they failed.** Run on
`gemini-3.5-flash-lite`, whose daily quota was untouched: **1/5 PASS, avg 0.280.** Because
that is a different model from the 14 scored on `gemini-2.5-flash`, a control was run
first — 5 tasks that had scored 1.00 on `flash`, re-run on `flash-lite`: **4/5, avg
0.800.** The model is therefore broadly capable on this skill, and the 0.280 is a real
skill weakness, not model weakness. Four defects the phrase "recorded, not omitted" had
made invisible:

| Task | Missed | The gap |
|---|---|---|
| `shot-001` | `i2v` | A deliberately-staged object should be animated from an approved still; the rollout reached for `t2v` |
| `manifest-001` | `all` | Scope of a re-run when an edited 24-scene manifest is re-run nightly |
| `batch-001` | `foreground` | Correctly refused the overnight batch, but never said to run it in the foreground |
| `qa-002` | `delay` | Prove a lip-sync detector against a known delay before trusting its output |

Recorded as epoch-2 targets in the skill's `optimization_notes` with this evidence
attached. The skill ships at epoch 1 as measured: **12/14 on `flash`, plus 1/5 on the tail
`flash-lite` scored** — an honest 13/19 overall rather than the 12/14 that omitting the
tail would have implied.
