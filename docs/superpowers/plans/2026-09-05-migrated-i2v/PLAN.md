# Migrated-host i2v (slice 1: local start frame) Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature migrated-i2v` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** `gflow video i2v --initial-frame <local file> PROMPT --project <id>` generates on
Flow's migrated `flow.google.com` host — the same command an account Google has moved gets
exit 36 for today — with the frame uploaded, bound and asserted before a credit is spent.

**Architecture:** Adapter growth only. `MigratedComposer` gains one stage between the
settings and the prompt: `attach_start_frame` = upload the file through the editor's own
toolbar Upload entry (file chooser; the app posts `maseQ` and answers with the media id),
then bind it through the Start-chip picker by file name and verify the chip holds an
image. `submit_and_observe` learns the i2v submit rpc (`eb1hJf`) and asserts the submit
request body carries that media id and an i2v model key — the migrated twin of the labs
`_assert_i2v_route`. `migrated_can_serve` admits exactly this request shape; every other
i2v form (end frame, in-project UUID, `@Name`) keeps today's behaviour: labs driver on an
unmoved account, exit 36 with a naming detail on a moved one. CLI, MCP tool signature,
worker codec, chain and the `VideoResult` contract do not change. No request is ever
built by gflow itself — the driver observes the page's traffic (Security STOP condition).

**Predict verdict:** CAUTION — confidence 7/10 (`PREDICT.md`); every mitigation the verdict
required is answered by the $0 recon (`docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md`).

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Unbound frame submits as t2v and bills (`YhhmEf` + `veo_3_1_t2v_lite`, measured) | Pre-click gate on `button.chip-container:has(img)`; post-click body assertion → `WireFormatError` |
| Critical | Duplicate file names in the picker bind a sibling asset | Body must carry the media id `maseQ` returned; mismatch → `WireFormatError`, ids in `detail` |
| High | Upload never completes / rejected | Hard 60 s wait on the `maseQ` reply; miss or non-200 → `MediaUploadRejectedError` (27), no submit |
| High | Overlay confusion (settings pane vs picker) — the #665/#669 class | Picker resolved as `OVERLAY:has(flow-add-menu-popover-content)`; hidden ≤15 s before typing |
| High | Localized picker placeholder / chip labels | Zero text anchors; the only typed text is the file name; live check on `denon82` (pt) |
| High | Status rpcs after an i2v submit never observed | `eb1hJf` accepted as submit rpc; `TransportTimeoutError` names the rpcs seen; first billed live run confirms |
| High | ~12 doc sentences say "t2v only" on the migrated host | Task 7 sweep; check § 1b with `text-to-video` as the grep symbol |
| Medium | Post-handoff changelog modal covers the editor (#673 shares this) | Structural dismissal in `ensure_editor`; whoever takes #673 rebases onto it |

---

## File structure

### New files
```
tests/features/migrated_i2v.feature
  BDD: local start frame on the migrated host — happy path + the six failure shapes
tests/features/test_migrated_i2v_steps.py
  step definitions, reusing the migrated_driver harness (fake page + frames)
tests/e2e/test_migrated_i2v_e2e.py
  e2e_auth ($0): upload + bind + chip assertion, no submit · e2e_video (bills one clip): full run
```

### Modified files
```
src/gflow_cli/api/transports/migrated_composer.py
  migrated_can_serve admits I2V with a local start_image only; run_video drops the i2v refusal and
  wires attach → prompt → submit; MigratedComposer: Frames submode in apply_video_settings, new
  attach_start_frame / _upload_via_toolbar / _pick_frame_by_name, dialog dismissal in ensure_editor,
  SUBMIT_RPCS = (YhhmEf, eb1hJf) + submit-body assertion in submit_and_observe; new constants
tests/api/transports/test_migrated_composer.py
  fake DOM grows: toolbar menu, file chooser, picker overlay + options, chip states, maseQ frames,
  request listener with a fake POST body
tests/api/transports/test_migrated_dispatch.py
  can_serve matrix for i2v forms; run_video exit-36 detail for end frame / UUID on the forced host
tests/mcp/test_server.py (or a new tests/mcp/test_migrated_i2v_parity.py)
  the request the MCP tool builds for i2v + local initial_frame is admitted by migrated_can_serve
src/gflow_cli/errors.py            FlowHostMigratedError docstring + remediation: t2v AND i2v
src/gflow_cli/api/transports/_common.py   raise_if_migrated detail wording
src/gflow_cli/config.py + .env.template   flow_host description
src/gflow_cli/mcp/tools.py         gflow_generate_video docstring (host paragraph)
docs/USAGE.md · docs/MCP.md · docs/CONFIGURATION.md · README.md · llms.txt · KNOWN_ISSUES.md ·
docs/PROJECT_STATUS.md · skills/gflow-cli/SKILL.md · docs/ARCHITECTURE.md (events) · CHANGELOG.md
website/docs/ (regenerated, never hand-edited)
```

---

## Task 1 — Composer unit-test scaffold (red)

**What:** Extend the fake page in `tests/api/transports/test_migrated_composer.py` so the new
stages can be driven offline, then write the red tests that pin every Critical/High scenario.

**Files:**
- `tests/api/transports/test_migrated_composer.py` — fake DOM + new tests

**Steps:**
- [x] Fake DOM: toolbar `add` button (outside `flow-prompt-box`), an add-menu overlay with an
      `upload`-ligature `[role='menuitem']`, `page.expect_file_chooser()` yielding a fake chooser
      whose `set_files` records the path and (configurably) fires a `maseQ` response frame with
      `[media_id, project_id, …]`, or a non-200 / no reply
- [x] Fake DOM: Start chip (`button.empty-chip`) → picker overlay (`flow-add-menu-popover-content`
      with `input[type=text]` and `button.asset-item[role=option]` items with configurable texts);
      option click flips the chip to `button.chip-container` with an `img` (configurable no-op for
      the "did not bind" case); picker hidden after the click
- [x] Fake page: `on("request", …)` support with a fake request carrying `url` (batchexecute rpcid)
      and `post_data`, fired by `_fire_submit(rpcid, body)`
- [x] Fake DOM: an optional `[role=dialog]` with a `close`-ligature button on load

**Tests created (red):**
- [x] `test_i2v_settings_select_the_frames_submode` — `apply_video_settings` with `Mode.I2V` checks `crop_free`
- [x] `test_attach_uploads_then_binds_the_frame_by_file_name` — returns the `maseQ` media id; chip bound; events `migrated.frame_uploaded`, `migrated.frame_bound`
- [x] `test_attach_refuses_when_the_add_menu_has_no_upload_entry` — `UiSelectorDriftError`, no chooser
- [x] `test_attach_refuses_when_no_file_chooser_opens` — `UiSelectorDriftError`
- [x] `test_attach_is_upload_rejected_when_maseq_does_not_answer` — `MediaUploadRejectedError` exit 27, route `batchexecute:maseQ`
- [x] `test_attach_is_upload_rejected_on_a_non_200_maseq` — same class, status in detail
- [x] `test_attach_is_reference_not_found_when_the_picker_lists_no_such_name` — `ReferenceNotFoundError` (32)
- [x] `test_attach_is_selector_drift_when_the_chip_stays_empty_after_the_pick` — `UiSelectorDriftError`, no submit possible
- [x] `test_attach_picks_the_first_option_when_names_repeat` — two identical names, first clicked
- [x] `test_submit_accepts_eb1hjf_as_the_i2v_submit_rpc` — record parsed, `on_started` fired
- [x] `test_submit_body_without_the_bound_media_id_is_wire_format_error` — exit 7, both ids in detail
- [x] `test_submit_body_with_a_t2v_key_is_wire_format_error` — exit 7 naming the key
- [x] `test_submit_body_assertion_is_off_for_t2v` — `expect_media_id=None` keeps today's behaviour
- [x] `test_ensure_editor_dismisses_a_dialog_before_waiting_for_the_trigger`
- [x] `test_ensure_editor_without_a_dialog_is_unchanged`

---

## Task 2 — Dispatch + BDD scaffold (red)

**What:** Pin the routing matrix and the user-visible contract before any production change.

**Files:**
- `tests/api/transports/test_migrated_dispatch.py` — can_serve / run_video cases
- `tests/features/migrated_i2v.feature`, `tests/features/test_migrated_i2v_steps.py` — Gherkin from SCENARIO.md

**Steps:**
- [x] Dispatch tests: `migrated_can_serve` True for I2V + local `start_image` + project; False for
      end frame, `start_image_ref_id`, `start_image_ref_name`, reference entities, labs-only model
- [x] Dispatch tests: `run_video` on the forced host with an end frame → exit 36, detail contains
      "end frame"; with a UUID → exit 36, detail contains "UUID"; never 2 or 11
- [x] Rewrite `test_run_video_rejects_modes_not_yet_ported_with_exit_36` to use R2V (I2V is now served)
- [x] BDD feature: the six scenarios from SCENARIO.md § Suggested BDD scenarios, verbatim titles
- [x] Steps file: reuse `test_migrated_driver_steps.py`'s harness (fake page, frames, exit-code capture)

**Tests created (red):**
- [x] `test_i2v_with_a_local_start_frame_is_served_by_the_migrated_host`
- [x] `test_i2v_with_an_end_frame_keeps_the_labs_driver_on_an_unmoved_account`
- [x] `test_i2v_by_uuid_keeps_the_labs_driver_on_an_unmoved_account`
- [x] `test_run_video_names_the_end_frame_in_the_exit_36_detail`
- [x] `test_run_video_names_the_uuid_form_in_the_exit_36_detail`
- [x] BDD: 6 scenarios (moved account generates from a local start frame · frame did not bind ·
      t2v body for an i2v request · upload rejected · end frame not ported on a moved account ·
      unmoved account with an end frame keeps labs)

---

## Task 3 — Composer: admit i2v, Frames submode, dialog dismissal (green for Task 1 part A + Task 2)

**What:** The dispatch predicate and the two small composer changes that need no new stage.

**Files:**
- `src/gflow_cli/api/transports/migrated_composer.py`

**Steps:**
- [x] `migrated_can_serve`: `T2V` as today; `I2V` only when `start_image` is a `Path` and every
      end-frame / ref-id / ref-name field is `None`; keep the model and entity checks
- [x] `run_video`: replace the `mode is not T2V` refusal with a helper `_unported_form(request)`
      returning `None` or a noun ("an end frame", "a frame given by Flow media UUID", "a frame
      given by @Name", "reference-to-video") → `FlowHostMigratedError` whose detail names it and
      points at `--initial-frame <local file>`
- [x] `apply_video_settings`: after the mode radio, `_select(axis="submode", lig="crop_free")`
      when `request.mode is Mode.I2V` (`chrome_extension` stays the default for t2v — do not touch)
- [x] `ensure_editor`: before waiting on `READY_ANCHOR`, if a `[role='dialog']` is visible click its
      `button:has(mat-icon:text-is('close'))` (fallback `Escape`), log `migrated.dialog_dismissed`;
      bounded to one attempt, 3 s
- [x] Constants block (after #669's): `FRAMES_LIGATURE = "crop_free"`, `DIALOG = "[role='dialog']"`

**Tests (green):** Task 1 settings/dialog tests · Task 2 dispatch tests · existing suite unchanged

---

## Task 4 — Composer: `attach_start_frame` (green for Task 1 part B)

**What:** Upload the local file through the editor and bind it on the Start chip; return the media id.

**Files:**
- `src/gflow_cli/api/transports/migrated_composer.py`

**Steps:**
- [x] Pre-flight in `attach_start_frame(page, project_id, image_path) -> str`: file exists, size ≤
      `MAX_IMAGE_BYTES` (import from `client`), header is a supported image (reuse the client's
      header check) — refuse with the same errors the labs upload raises, before any click
- [x] `_upload_via_toolbar(page, image_path) -> str`: register a URL-filtered `response` listener
      for `batchexecute` rpcid `maseQ` (parse with `parse_frames`, take the first UUID-shaped
      string as the media id); click the toolbar `add` (`button:has(mat-icon:text-is('add'))` not
      inside `flow-prompt-box`); wait for `OVERLAY [role='menuitem']:has(mat-icon:text-is('upload'))`
      (`FRAME_PICKER_OPEN_S = 8`) else `UiSelectorDriftError`; click it inside
      `page.expect_file_chooser(timeout=8 s)` else `UiSelectorDriftError`; `set_files(str(path))`;
      wait ≤ `FRAME_UPLOAD_S = 60` for the reply: none / non-200 / no id → `MediaUploadRejectedError`
      with `route="batchexecute:maseQ"`; log `migrated.frame_uploaded {media_id, status}` (never the
      file name); remove the listener in `finally`
- [x] `_pick_frame_by_name(page, name, media_id) -> None`: click `flow-prompt-box button.empty-chip`
      (first); picker = `OVERLAY.filter(has=flow-add-menu-popover-content)`, wait visible ≤ 8 s;
      click its `input[type='text']`, `insert_text(name)`; wait ≤ 8 s for
      `button.asset-item[role='option']` filtered by `has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")`
      else `ReferenceNotFoundError`; click `.first` (newest under the picker's default sort);
      wait the picker hidden ≤ `FRAME_COMMIT_HIDDEN_S = 15`; assert
      `flow-prompt-box button.chip-container:has(img)` count ≥ 1 within `FRAME_THUMB_VISIBLE_S = 5`
      else `UiSelectorDriftError`; log `migrated.frame_bound {media_id}`
- [x] Wrap upload+pick in `asyncio.wait_for(…, ATTACH_STAGE_S = 90)` → `TransportTimeoutError`
      naming the stage
- [x] Do NOT touch `_select_model` / `_close_pane` (#669's territory)

**Tests (green):** the eight `test_attach_*` tests from Task 1

---

## Task 5 — Composer: i2v submit rpc + body assertion; `run_video` wiring (green for Task 1 part C)

**What:** Make the submit stage i2v-aware and connect the stages in `run_video`.

**Files:**
- `src/gflow_cli/api/transports/migrated_composer.py`

**Steps:**
- [x] `SUBMIT_RPC` → `SUBMIT_RPCS = ("YhhmEf", "eb1hJf")`; the response listener treats either as
      the submit; `I2V_KEY = re.compile(r"_i2v_")`, `T2V_KEY = re.compile(r"_t2v_")`
- [x] `submit_and_observe(..., expect_media_id: str | None = None)`: when set, register a
      `request` listener before the click that inspects the first batchexecute POST whose rpcid is in
      `SUBMIT_RPCS`: body must contain `expect_media_id` and match `I2V_KEY`; a `T2V_KEY` or a
      missing id resolves a `route_error` future → raise `WireFormatError` (7) with both ids / the key
      in `detail`, discovery head redacted through the existing `_redact`-style helper; listener
      removed in `finally`
- [x] `run_video`: for I2V, `media_id = await composer.attach_start_frame(page, pid, request.start_image)`
      between `apply_video_settings` and `send_prompt`; pass `expect_media_id=media_id` to
      `submit_and_observe`; log `migrated.dispatch mode=i2v`
- [x] Docstrings: module header lists `eb1hJf`; `run_video` docstring drops "t2v only"

**Tests (green):** the four `test_submit_*` tests from Task 1 · BDD scenarios from Task 2

---

## Task 6 — MCP surface mirror (truth, not code)

**What:** No tool signature or payload key changes (the port is transport-only — verified in
`PREDICT.md` § CLI UX / MCP); make the agent-facing claims true and pin the queued path.

**Files:**
- `src/gflow_cli/mcp/tools.py` — `gflow_generate_video` docstring host paragraph
- `docs/MCP.md:86`
- `tests/mcp/test_server.py` (or new `tests/mcp/test_migrated_i2v_parity.py`)

**Steps:**
- [x] Docstring + `docs/MCP.md`: "on flow.google.com … text-to-video is the only ported mode" →
      "text-to-video and image-to-video with a local `initial_frame`; a Flow media UUID as
      `initial_frame`, `end_frame` and `r2v` return the exit-36-equivalent envelope there"
- [x] Test: build the payload the tool writes for `mode="i2v", initial_frame="<tmp png>"`, decode it
      through `worker/codec.py`, assert `migrated_can_serve(request, "p") is True`; and the UUID form
      decodes to a request `migrated_can_serve` rejects (routing stays deterministic on the queued path)
- [x] Run check § 1b mirror sweep with `text-to-video` and `i2v` as the grep symbols; tick each axis

**Tests created:** the two parity assertions above

---

## Task 7 — Docs, CHANGELOG, remediation strings

**What:** Every sentence that says the migrated host serves t2v only becomes true again.

**Files:** `docs/USAGE.md` (`:562` blockquote + the `i2v` section gets the same note, `:1708` exit-36
row), `docs/CONFIGURATION.md:368`, `README.md:127`, `llms.txt:3`, `KNOWN_ISSUES.md:17,19,56`,
`docs/PROJECT_STATUS.md` (current release / milestone row when released), `skills/gflow-cli/SKILL.md:274`,
`.env.template` + `src/gflow_cli/config.py` flow_host description, `src/gflow_cli/errors.py:681-703`,
`src/gflow_cli/api/transports/_common.py:134`, `docs/ARCHITECTURE.md` (new events), `CHANGELOG.md`
`[Unreleased] ### Added`, `website/docs/` via `generate_website_docs.py`

**Steps:**
- [x] Sweep with `grep -rn "text-to-video only\|only t2v\|only \`t2v\`\|not ported" …` and fix each hit
- [x] `docs/USAGE.md` i2v section: the migrated-host paragraph — local file only, the upload is
      permanent in the Flow project (scenario #24), end frame / UUID exit 36 there
- [x] `CHANGELOG.md`: Added entry with the mechanism (toolbar upload → `maseQ`, picker by file
      name, `eb1hJf` body assertion) and the exit-36 forms that remain
- [x] `docs/ARCHITECTURE.md`: `migrated.frame_uploaded`, `migrated.frame_bound`, `migrated.dialog_dismissed`
- [x] Regenerate the mirror; `check_doc_links`, `check_website_docs_pii`, `generate_website_docs --check` green

---

## Task 8 — E2E evidence, gates, PR

**What:** The generation path is live-verified on both moved accounts before the PR is called done
(#675 makes e2e evidence a required deliverable; a BDD feature is not e2e).

**Files:**
- `tests/e2e/test_migrated_i2v_e2e.py`
- `docs/LIVE_VERIFICATION_v<next>.md` (or an `[Unreleased]` section the release folds in)

**Steps:**
- [x] `e2e_auth` ($0): `attach_start_frame` on the real editor — upload a probe PNG, bind it, assert
      the chip holds an image and the returned id is a UUID; no submit
- [x] `e2e_video` (bills one Veo Lite clip): full `run_video` with a local start frame; five-layer
      ledger (file count, `ftyp` magic, dimensions, structlog invariants incl.
      `migrated.submit_observed rpc=eb1hJf`, user-openable mp4); assert `result.status.media_id`
- [x] Run on `ffroliva` (en-GB, moved) and `denon82` (pt, moved) — record both in the verification doc
- [x] `/gflow:check` green; `pytest tests/api/transports tests/features tests/mcp` green; coverage ≥ 80 %
- [x] Push `feature/639-migrated-i2v`, open the PR to `develop` with `Closes` nothing (#639 stays
      open; body says "i2v local-file slice"), then `/gflow:pr-council-review`

---

## Definition of done

- [x] All task steps checked off
- [x] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [x] `CHANGELOG.md` `[Unreleased]` section updated
- [x] Docs updated (`USAGE.md`, `CONFIGURATION.md`, `MCP.md`, `KNOWN_ISSUES.md`, mirror regenerated)
- [x] BDD feature file covers all Critical + High scenarios from `SCENARIO.md`
- [x] E2E test run live on both moved accounts and recorded (#675 bar)
- [x] No `# TODO` in diff without a tracked issue link
- [x] Deferred to slice 2 (tracked in #639): `--initial-frame <uuid>` / `@Name` by name mapping,
      `--end-frame`, cataloguing the upload as a media row (scenario #21)
