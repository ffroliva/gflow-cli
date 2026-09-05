# Scenario: i2v on the migrated flow.google.com host — slice 1, `--initial-frame <local file>`

**Inputs:** predict verdict `PREDICT.md` (CAUTION 7/10) · recon
`docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md` ($0, 2026-09-05) · issue #639
(reporter pipeline `image i2i → video i2v`, comment 2026-09-05T16:16) · #673 (RecaptchaError
on image commands, moved accounts) · PR #669 (model picker, same file, open).

**Slice 1 scope.** `gflow video i2v --initial-frame <local file> PROMPT --project <id>` on the
migrated host, via `MigratedComposer`: Frames submode → upload the file through the toolbar `+`
→ Upload (file chooser; the app posts `maseQ` and answers with the media id) → open the Start
chip picker → search by file name → click the option → assert the chip is bound → prompt →
submit → observe. The submit rpc is `eb1hJf` (t2v stays `YhhmEf`) and its body carries the bound
media id — that body is the credit-safety backstop. **Out of slice 1:** `--end-frame`,
`--initial-frame <uuid>` / ref-by-name (#287), r2v, image commands. Those keep today's
behaviour: labs driver on an unmoved account, exit 36 with a naming detail on a moved one.

## Coverage map

| Dim | Active? | Why |
|---|---|---|
| D1 auth/session | yes | the upload is the first *write* the migrated driver makes; reCAPTCHA reload rides in-page; #644 harvest is irrelevant (no httpx) but #673's landing-page strand is the same account class |
| D2 WAF/reCAPTCHA | yes | `maseQ` is preceded by an in-page `recaptcha/enterprise/reload`; a failed mint = upload refused |
| D3 selectors/locale | yes | five new anchors (chip, picker, option, toolbar `+`, upload menuitem); the picker's search placeholder and aria-label are localized |
| D4 batch/resume | yes | `gflow video chain` link ≥1 is a local-path i2v and routes through the same dispatch; no `video batch` exists |
| D5 concurrency | yes (small) | video is serialized under `_generate_lock`; the only new listener is the upload observer |
| D6 data layer | yes | the uploaded asset is a new media row candidate; recorder contract unchanged for the clip |
| D7 errors/exit codes | yes | zero new codes; every new failure must land on 2 / 7 / 11 / 23 / 27 / 36 |
| D8 cross-platform | yes | local path → `set_files`; file name → picker search text (unicode, spaces) |
| D9 transport | yes | `maseQ` reply shape, `eb1hJf` vs `YhhmEf`, status rpcs after an i2v submit never observed |
| D10 headed/headless | yes (inherited) | same real-Chrome constraint as t2v; nothing new |
| D11 input validation | yes | file missing / not an image / oversize / duplicate names |
| D12 observability | yes | three new events, host-tagged |
| D13 MCP parity | yes | `gflow_generate_video(mode="i2v", initial_frame=<path>)` direct and queued |

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D9/D2 | **Unbound frame at submit** (picker pick silently failed, chip still `empty-chip`) — the app submits as t2v (`YhhmEf`, `veo_3_1_t2v_lite`), measured | **Critical** | Pre-click gate: the Start slot must be `button.chip-container:has(img)`; otherwise `UiSelectorDriftError` (23) and no click. If the click still produces a `YhhmEf`/t2v body: `WireFormatError` (7) naming "t2v key on an i2v request" — loud, never a success record | Unit (fake DOM) + BDD |
| 2 | D9 | **Submit body carries the wrong media id** (duplicate file names; picker picked a sibling upload) | **Critical** | `page.on("request")` backstop on the `eb1hJf` body: must contain the media id `maseQ` returned; mismatch → `WireFormatError` (7) after the click, run fails with the ids in `detail` | Unit + BDD |
| 3 | D9/D2 | `maseQ` never answers, or answers non-200 / a body with no media id (reCAPTCHA reload failed, size rejected) | High | Hard wait `FRAME_UPLOAD_S=60`; on miss `MediaUploadRejectedError` (27) with `route="batchexecute:maseQ"` and the status; **no submit** | Unit + BDD |
| 4 | D3 | Toolbar `+` present but the menu has no `upload`-ligature item (Flow renames/moves it) | High | `UiSelectorDriftError` (23) "migrated host: upload entry (upload) not in the add menu"; nothing uploaded | Unit |
| 5 | D3 | Upload menuitem clicked but no `filechooser` fires within `FRAME_PICKER_OPEN_S=8` | High | `UiSelectorDriftError` (23); no `set_files` on a hidden input as a fallback (labs lesson: it adds to the library but does not bind) | Unit |
| 6 | D3 | Picker opens but its search input is absent (layout change) or the option list is a virtual scroll that never renders the match | High | Search input anchored as `<picker> input[type='text']` (never placeholder/aria); if the option is not visible after typing the full file name within 8 s → `ReferenceNotFoundError` (32) "uploaded file not listed in the frame picker" | Unit |
| 7 | D3 | Two overlays open (settings pane still up when the picker opens, or the picker still up when the composer is clicked) — the #665/#669 class | High | Every overlay resolved by anchor: pane = `OVERLAY:has([role='radiogroup'])`, picker = `OVERLAY:has(flow-add-menu-popover-content)`; wait for the picker `hidden` (≤15 s) before typing the prompt | Unit |
| 8 | D3 | Non-English account (pt-BR `denon82`): chip text is "Início"/"Fim", placeholder "Pesquisar recursos" | High | No text anchors on chips, placeholder, aria-labels or menu items; the only text typed is the file name; run passes on `denon82` unchanged | E2E live |
| 9 | D3 | Same file uploaded twice (re-run): picker lists two identical names | High | Pick the first option under the picker's default "Recent" sort; #2's body assertion is the guarantee, not the pick | Unit + E2E live |
| 10 | D9 | Status rpcs after `eb1hJf` differ from t2v's (`jwpduf`/`as29s` never observed for i2v) | High | `submit_and_observe` accepts `eb1hJf` as a submit rpc; if no status record for the workflow arrives within `poll_timeout_s` → `TransportTimeoutError` with the observed rpc list in `detail`. Confirmed on the first billed live run | Unit + E2E live |
| 11 | D4 | `gflow video chain` link ≥1 on a moved account (link 0 = t2v, link 1 = i2v from the extracted last frame) | High | Routes through the same dispatch and now succeeds; `project_id` must be bound for links (chain passes none today, `chain.py:255`); a failure is `ChainPartialError` (21), not a bare 36 | Integration + BDD |
| 12 | D13 | MCP direct: `gflow_generate_video(mode="i2v", initial_frame="C:\\…\\a.png", project=…)` on a moved account | High | Same transport; envelope on failure is the Problem Details twin of #1–#7; `docs/MCP.md:86` and the tool docstring no longer say "text-to-video is the only ported mode" | Unit (tools) + doc grep |
| 13 | D13 | MCP queued: the worker process reads `initial_frame` from the payload (`codec.py:248`) and must resolve the path in *its* cwd | High | Payload key unchanged; the worker validates the file exists before opening a browser → 400-equivalent, not a mid-run 23 | Unit (codec) |
| 14 | D7 | `--end-frame` given on a moved account (slice 2) | Medium | `migrated_can_serve` returns False → unmoved account keeps labs (silent, correct); moved/forced → `FlowHostMigratedError` (36) whose `detail` names "end frame not ported yet" — never 2 or 11 | Unit (dispatch) + BDD |
| 15 | D7 | `--initial-frame <uuid>` or `--initial-frame @Name` on a moved account (slice 2) | Medium | As #14, `detail` names the UUID/ref form and points at a local file | Unit (dispatch) |
| 16 | D11 | Local file missing / unreadable / not an image (magic bytes) / over the upload size cap | Medium | Refused before any browser work, exit 2 (CLI) / 400 (MCP), reusing `upload_image`'s size + header checks (`client.py:173`, `:1713`) | Unit |
| 17 | D11 | JPEG with EXIF that labs' `uploadImage` 400'd on (KNOWN_ISSUES) | Medium | If `maseQ` rejects it: #3 path (27) with the status; the file is not re-encoded silently | E2E live (opt-in) |
| 18 | D8 | Windows path with spaces / unicode file name / drive letter | Medium | `set_files(str(path))` verbatim; picker search types the exact `path.name`; `PYTHONUTF8=1` not required for the name to round-trip | Unit + E2E live |
| 19 | D3 | Post-handoff changelog modal (`[role=dialog]`, "Get started") covers the editor on first load | Medium | `ensure_editor` dismisses a `mat-dialog` with a structural close before waiting on the trigger; if #673 lands first, reuse its dismissal | Unit |
| 20 | D5 | The upload observer listener leaks or catches the composer's own `on_response` traffic | Medium | Scoped URL-filtered listener registered before `set_files`, removed in `finally`; composer's rpcid filter untouched | Unit |
| 21 | D6 | The uploaded asset is never catalogued: a later `--initial-frame <uuid>` (slice 2) cannot map it | Medium | Record the upload as a media row (kind image, source `upload`, media id from `maseQ`, display name = file name) through the existing recorder path; redaction gate respected | Unit (recorder) |
| 22 | D12 | New events `migrated.frame_uploaded {media_id, status}`, `migrated.frame_bound {media_id}`, `migrated.submit_observed rpc=eb1hJf` | Low | Key names documented in `docs/ARCHITECTURE.md`; never the file name or a signed URL; `correlation_id` bound | Unit (caplog) |
| 23 | D1 | Moved account whose session strands on the labs landing page (#673 shape 1) before the migrated route is chosen | Low (owned by #673) | Not this slice's bug; the i2v path only starts after `migrated.dispatch` | — |
| 24 | D6/D1 | Side effect: the upload adds the file to the Flow project permanently (visible in the web UI, counts toward storage) | Low | Documented in `docs/USAGE.md` i2v section and the CHANGELOG entry; no auto-delete | Docs |

Severity: **Critical** (billed on the wrong route / wrong asset) · **High** (feature broken, loud
failure) · **Medium** (explicit refusal or degraded) · **Low** (cosmetic / documented).

## Must-cover before merge (Critical + High)

1. **Route assertion** (#1, #2): pre-click chip-bound gate + post-click `eb1hJf` body check
   (i2v key AND the `maseQ` media id). Fake-DOM tests for: unbound chip → 23 and no click;
   t2v key in the body → 7; foreign media id → 7.
2. **Upload lifecycle** (#3, #4, #5): menu entry missing → 23; no chooser → 23; `maseQ`
   miss/non-200 → 27 and no submit; success path yields the media id.
3. **Picker** (#6, #7, #9): structural search input; option not listed → 32; picker overlay
   resolved by anchor and hidden before the prompt; first "Recent" match on duplicates.
4. **Locale** (#8): zero text anchors — verified by a grep test over the new selectors and by
   the live run on `denon82`.
5. **Status after submit** (#10): `eb1hJf` accepted as submit rpc; timeout names the rpcs seen.
6. **Chain** (#11): link ≥1 routes and binds `project_id`.
7. **MCP** (#12, #13): both hops exercised in `tests/mcp/`; docstring + `docs/MCP.md` truthful.

## Deferred (Medium + Low — log as issues, not blockers)

- #14/#15 exit-36 detail wording for end-frame / UUID (cheap, do in-slice but not gating).
- #17 EXIF JPEG behaviour on `maseQ` — record in KNOWN_ISSUES after the first live hit.
- #19 changelog modal — take #673's dismissal if it lands first; otherwise a 6-line structural close.
- #21 catalogue the upload — needed by slice 2; ship in-slice if the recorder path is one call.
- #24 doc the permanent-upload side effect.

## Suggested BDD scenarios (for `tests/features/migrated_i2v.feature`)

```gherkin
Feature: image-to-video on the migrated flow.google.com host
  On the new host a start frame is an in-project asset. gflow uploads the local file
  through the editor's own Upload entry, observes the app's maseQ reply for the media id,
  binds it through the Start-frame picker by file name, and asserts the eb1hJf submit
  body carries that id before treating the run as an i2v generation.

  Scenario: a moved account generates from a local start frame
    Given the editor hands the session to flow.google.com after entering the project
    And a local start frame "hero.png"
    When gflow video i2v runs with an 8 s request
    Then the composer uploads the file and the maseQ reply names a media id
    And the Start chip binds the asset listed under "hero.png"
    And the eb1hJf submit body carries that media id and an i2v model key
    And the result reports success with the workflow id

  Scenario: the frame did not bind, so nothing is submitted
    Given the picker lists no asset named "hero.png"
    When gflow video i2v runs with an 8 s request
    Then the run fails with exit 32 before any submit
    And the detail names the file and the picker

  Scenario: the app submitted a text-to-video body for an i2v request
    Given the Start chip is bound
    And the submit reply arrives on YhhmEf with a t2v model key
    When gflow video i2v runs with an 8 s request
    Then the run fails with exit 7 naming the t2v key on an i2v request

  Scenario: the upload is rejected
    Given maseQ answers 400
    When gflow video i2v runs with an 8 s request
    Then the run fails with exit 27 naming route batchexecute:maseQ
    And no submit was clicked

  Scenario: an end frame is not ported yet on a moved account
    Given the editor hands the session to flow.google.com after entering the project
    When gflow video i2v runs with a local start frame and a local end frame
    Then the run fails with exit 36 and the remediation names the end frame

  Scenario: an unmoved account with an end frame keeps the labs driver
    Given the account has not been moved and a project is given
    When gflow video i2v runs with a local start frame and a local end frame
    Then the labs driver serves the request
```

## Known-issues cross-reference

- **#639** — this slice resolves the "i2v not ported" row; issue stays open for image/r2v/etc.
- **#125 / #626 (labs: unbound frames submit as t2v; omni-flash end-frame guard)** — scenario #1
  is the migrated twin; the labs `_assert_i2v_route` intent is reproduced by the `eb1hJf` body check.
- **#665 / #669 (stacked overlays, `.last` pane)** — scenario #7; #669's `_close_pane` change lands in
  the same file — rebase on it.
- **#672 (submit-enable race)** — merged 2026-09-05; the enable-poll is reused unchanged.
- **#673 (RecaptchaError on image commands, moved accounts)** — scenario #23, not this slice; the
  changelog-modal dismissal (#19) is shared work.
- **KNOWN_ISSUES "uploadImage JPEG metadata 400"** — scenario #17; unknown whether `maseQ` shares it.
- **#644 (cookie harvest keyed on labs.google)** — not touched: no httpx call is added.
