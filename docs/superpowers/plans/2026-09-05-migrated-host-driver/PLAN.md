# Migrated-Host Driver Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature migrated-host-driver` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** an account whose Flow lives on `flow.google.com` can run `gflow video t2v`
(and `gflow image t2i`) end to end — settings, submit, observe, download — instead of
exiting 36; any account can opt in to the new host, and the new host becomes the routed
default for flagged accounts.

**Architecture:** three pieces, no change to the domain model. (1) `batchexecute.py` — a
pure parser for Google's `)]}'` envelope and the generation record it carries
(workflow id, media id, status cell, signed CDN URLs). (2) `migrated_composer.py` — the
Angular-editor driver: `cdk-overlay` settings pane with `[role=radio]` groups, a
`[role=menu]` model picker, a `contenteditable` composer, the `arrow_forward` submit; it
submits and then *observes* the app's own `jwpduf` polling and the `as29s` result
through `page.on("response")`, never adding traffic. (3) one dispatch point in
`_generate_video_locked` / `_generate_images_locked` after the editor mounts: when the
page is on the migrated host (or `GFLOW_CLI_FLOW_HOST=flow.google.com` forces it) the
request goes to the composer and returns the same `VideoResult` / `GeneratedImage` the
labs path returns, so recorder, CLI, MCP and worker are untouched. Recon:
`docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md`.

**Predict verdict:** CAUTION 5/10 as opt-in (2026-09-04); the "default now" STOP was
conditioned on wire evidence, which the 2026-09-05 spike produced (two real clips).
Routing rule that satisfies both: **flagged accounts → migrated composer automatically**
(they have nothing today); **unflagged accounts → labs driver unless forced**; global
default flips only after the e2e matrix is green on both profiles.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Envelope/record drift on `YhhmEf`/`jwpduf`/`as29s` | Shape-searched parser (finds the record by its `[uuid, uuid, uuid, "CAE", …]` signature), `WireFormatError` with redacted discovery payload; fixtures from the captures |
| Critical | Failure status enum never observed | Status ≠ 2/3/6 → `VideoStatus` FAILED carrying the raw value; bounded by `poll_timeout_s` |
| High | `aria-label` anchors are translated | Anchors: ligature (`videocam`, `crop_16_9`, `arrow_forward`, `arrow_drop_down`), role (`radiogroup`/`radio`/`menu`/`menuitem`), class (`.settings-trigger-button`), numeric tokens (`8s`, `x1`), product names — never aria-label |
| High | Playwright `:text-matches` CSS escaping eats `\s` | Python-side `filter(has_text=re.compile(...))` (spike trap) |
| High | Download host is a new CDN (`flow-content.google`, signed URL) | Try the existing `media.getMediaUrlRedirect` path first (same media id, REST works on migrated accounts); fall back to the signed URL through the allowlist (+ `flow-content.google`) |
| High | Composer `textarea` not clickable | Type into `[contenteditable='true']`; assert prompt echoed in the submit body |
| Medium | Dispatch reads a pre-hop URL | Decide after `_wait_video_editor_ready` (measured: hop already landed), and re-check host on the readiness-timeout path |
| Medium | MCP/docs still claim the host cannot be driven | Task 7 updates `mcp/tools.py` docstrings, `docs/MCP.md`, `USAGE.md` exit-36 row, `KNOWN_ISSUES.md` #639, `CONFIGURATION.md`, `.env.template` |

---

## File structure

### New files
```
src/gflow_cli/api/transports/batchexecute.py
  parse_frames(text) -> [(rpcid, payload)]; find_generation_record(payload) -> GenerationRecord;
  status semantics (2 running, 3 done, 6 submitted); signed-URL extraction
src/gflow_cli/api/transports/migrated_composer.py
  MigratedComposer: ensure_editor, apply_video_settings / apply_image_settings, select_model,
  send_prompt, submit_and_observe (YhhmEf -> jwpduf/as29s), download; structlog events migrated.*
tests/api/transports/test_batchexecute.py
  parser unit tests on sanitized captures (submit, poll running, poll done, result, drift, unknown status)
tests/api/transports/test_migrated_composer.py
  fake page/locator tests: radios, missing axis, stale radio, model not offered, composer fallback,
  submit observation, status timeout, allowlist, expired URL
tests/api/transports/test_migrated_dispatch.py
  transport dispatch: flagged host after ready, forced host on labs, kill switch keeps exit 36,
  recorder VideoStarted, queued worker twin
tests/features/migrated_driver.feature + tests/features/test_migrated_driver_steps.py
  the three BDD scenarios from SCENARIO.md
```

### Modified files
```
src/gflow_cli/config.py                       flow_host: "auto"|"flow.google.com"|"labs.google" (GFLOW_CLI_FLOW_HOST)
src/gflow_cli/api/transports/ui_automation_video.py   dispatch after _wait_video_editor_ready; force-host goto
src/gflow_cli/api/transports/ui_automation.py         image dispatch; allowlist += flow-content.google
src/gflow_cli/errors.py                       exit-36 remediation names GFLOW_CLI_FLOW_HOST; UiSelectorDriftError host detail
src/gflow_cli/mcp/tools.py                    docstrings: migrated host supported for t2v/t2i
docs/USAGE.md, docs/CONFIGURATION.md, docs/MCP.md, KNOWN_ISSUES.md, .env.template, CHANGELOG.md
website/docs mirror (generated)
```

---

## Task 1 — batchexecute parser (test scaffold + implementation)

**What:** a pure module that turns the `)]}'` envelope into `(rpcid, payload)` frames and
locates the generation record by shape.

**Files:**
- `tests/api/transports/test_batchexecute.py` — fixtures are the captured bodies with ids
  replaced by synthetic uuids and URLs by `https://flow-content.google/x?Expires=1&KeyName=k&Signature=s`
- `src/gflow_cli/api/transports/batchexecute.py`

**Steps:**
- [x] Red tests: submit wrapper → record (workflow/media/project, status 6); poll running (2); poll done (3, bytes, no URL); result (3 + two signed URLs); drift (no `CAE` record) → `WireFormatError` with `rpcid` and a ≤200-char head, no token; unknown status 7 → `is_failed`; non-envelope text → empty frames
- [x] Implement `parse_frames`, `GenerationRecord` (frozen dataclass), `find_generation_record`, `record_status`

**Tests created (red → green):** 8 tests in `test_batchexecute.py`

---

## Task 2 — config + routing decision

**What:** one setting and one pure decision function.

**Files:** `src/gflow_cli/config.py`, `src/gflow_cli/api/transports/_common.py`, `.env.template`, tests

**Steps:**
- [x] `Settings.flow_host: Literal["auto","flow.google.com","labs.google"] = "auto"` (`GFLOW_CLI_FLOW_HOST`)
- [x] `_common.migrated_route(page_url, flow_host) -> "labs" | "migrated" | "blocked"`: `auto` → by host kind; `flow.google.com` → migrated; `labs.google` → `blocked` when the host is migrated (exit 36 path), else labs
- [x] Exit-36 remediation names the setting; tests for the three values × two hosts

---

## Task 3 — MigratedComposer: settings + model + prompt (fake-page tests first)

**What:** the DOM half, against a fake page that models `cdk-overlay-pane`, six radiogroups
with `aria-checked`, a `menu` of `menuitem`s, a non-clickable `textarea` and a
`contenteditable`.

**Steps:**
- [x] Red tests: scenarios 2, 3, 4, 5, 19, 20, 24 from SCENARIO.md
- [x] `ensure_editor(page, project_id)`: direct goto when not already on `flow.google.com/project/<id>`; readiness = `.settings-trigger-button` visible (bounded); login-form detection → `AuthExpiredError`
- [x] `apply_video_settings(page, request)`: open pane → mode `videocam`, aspect `crop_16_9|crop_9_16`, duration `<n>s`, count `x<n>`; each axis: missing group → `ConfigurationError` naming it; click → read back `aria-checked`, one re-query retry, then `UiSelectorDriftError(host=migrated)`
- [x] `select_model(page, model)`: `VideoModel` → menu label map (`Omni 1.1 Flash`, `Veo 3.1 - Lite/Fast/Quality`); not offered → `ConfigurationError` listing offered names
- [x] `send_prompt(page, prompt)`: `[contenteditable='true']` click + type; `arrow_forward` enabled
- [x] close pane: Escape → trigger → backdrop

---

## Task 4 — MigratedComposer: submit, observe, download

**Steps:**
- [x] Red tests: scenarios 7, 8, 9, 10, 11, 14, 16, 21
- [x] `submit_and_observe(page, request, *, poll_timeout_s, on_started)`: attach `page.on("response")` for `batchexecute` before the click; first `YhhmEf` frame → record → `VideoStarted(media_id, project_id, flow_operation_id=workflow_id)`; then every `jwpduf`/`as29s` frame with the same workflow id updates status; status 3 → done; ≠2/3/6 → FAILED; `poll_timeout_s` → `TimeoutError`; error frame → `RecaptchaError`-class or `WireFormatError`
- [x] `download(page, record, media_id, out_dir)`: existing `_download_video(media_id, …)` first; on ≥400 fall back to the signed video URL via `_is_allowed_download_host` (+`flow-content.google`), naming via the existing helper
- [x] events: `migrated.dispatch`, `migrated.settings_applied`, `migrated.submit_observed`, `migrated.status`, `migrated.result_url`, `migrated.download`

---

## Task 5 — dispatch in the transport (video, then image)

**Steps:**
- [x] Red tests (`test_migrated_dispatch.py`): scenarios 1, 12, 15, 17, 18, 22
- [x] `_generate_video_locked`: after `_wait_video_editor_ready` + overlay dismissal, `route = migrated_route(page.url, settings.flow_host)`; `blocked` → `raise_if_migrated` (exit 36, unchanged); `migrated` → `MigratedComposer(self).generate_video(...)` returning `VideoResult`; forced host on a labs page → `ensure_editor` navigates first
- [x] `_generate_images_locked`: same dispatch for t2i (image mode radio, image model menu), returning `GeneratedImage` list
- [x] `UiMode.AGENTIC` explicit on the migrated route → `UiModeUnavailableError` (exit 28) pre-submit

---

## Task 6 — BDD + MCP/worker twin

**Steps:**
- [x] `tests/features/migrated_driver.feature` + steps (three scenarios)
- [x] Worker queued-path test: flagged host dispatch reaches the composer through `FlowWorker.process_task` with a stubbed transport; envelope on failure derives `retryable` from `is_retryable`
- [x] No new CLI leaf/param → `tests/mcp/test_cli_parity.py` unaffected (state so); `mcp/tools.py` docstrings no longer say the migrated host is undrivable

---

## Task 7 — docs

**Steps:**
- [x] `docs/CONFIGURATION.md` + `.env.template`: `GFLOW_CLI_FLOW_HOST`
- [x] `docs/USAGE.md` exit-36 row; `KNOWN_ISSUES.md` #639 entry (t2v/t2i drivable; rest of matrix pending); `docs/MCP.md`; `README` architecture note; `CHANGELOG.md [Unreleased]`
- [x] regenerate `website/docs`

---

## Task 8 — gates + live matrix

**Steps:**
- [x] `/gflow:check` green (scoped suite locally; CI runs the full sweep)
- [x] Live, credits approved: `gflow video t2v` on the flagged profile (auto route) → exit 0 in 49.9 s, `ftyp`, byte-exact size, recorder row; opt-in `GFLOW_CLI_FLOW_HOST=flow.google.com` on the unflagged `pt` profile → exit 0 in 50.5 s. (`image t2i` is not ported on this host — exits 36 by design, see KNOWN_ISSUES.)
- [x] `docs/LIVE_VERIFICATION_v0.67.0.md` naming the entrypoints and wall-clocks

---

## Definition of done

- [x] All task steps checked off
- [x] `/gflow:check` green (ruff / format / pyright at baseline / scoped pytest; CI full sweep)
- [x] `CHANGELOG.md` `[Unreleased]` section updated
- [x] Docs updated (`USAGE.md` / `CONFIGURATION.md` / `KNOWN_ISSUES.md` / README / `.env.template`; `MCP.md` carried no host claim to change)
- [x] BDD feature file covers the three headline scenarios; Critical + High cases are unit-covered in `test_migrated_composer.py` / `test_migrated_dispatch.py` / `test_batchexecute.py`
- [x] No `# TODO` in diff without a tracked issue link
