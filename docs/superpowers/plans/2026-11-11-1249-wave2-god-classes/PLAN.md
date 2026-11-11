# #1249 wave 2 - split the remaining god classes - Plan (not implemented)

> Written at the end of wave 1 (2026-11-11). Nothing here is implemented. Run `/gflow:predict`
> and `/gflow:scenario` per class before starting; re-measure first, because line numbers drift.

**Goal:** bring every class under the size gate (1,000 lines, 20 public methods, files under
2,000 lines) without changing behaviour: the same CLI, MCP tools, exit codes, JSON, log events and
Flow traffic.

**Method (proved in wave 1, [PLAN](../2026-11-11-1249-split-god-classes/PLAN.md)):**
1. Pin first: a golden-master contract per class (method set + signatures, names read off the old
   module, and traffic/DOM calls/outcomes against a fake), recorded on develop.
2. Move with a lever: AST spans, byte-identical bodies, helpers to the lowest module every user
   can import. One surface per commit, tests green between.
3. Mixins over composition wherever tests patch private helpers on the instance or the class.
4. Retarget module-path patches as import-path-only edits, each listed in the commit.
5. Ratchet the baseline; a pure move carries its entry (`check_size.py` handles it).
6. e2e on denon82 for every PR that touches `api/transports/`.

**A trap wave 1 found, which matters more here.** Tests that patch a **module global**
(`patch("...ui_automation.log")`, `setattr(migrated_composer, "FRAME_...", ...)`) stop reaching a
method once it moves to another module. Wave 1 had 6 such sites; wave 2 has about 40. If the
patch is not retargeted, the test can fail, or pass without testing anything. Before each move,
grep the module's patched globals and list the methods that read them.

---

## One seam per class

Sizes from an AST pass on 2026-11-11 (wave 1 tree).

### 1. `UiAutomationTransport` - `api/transports/ui_automation.py:942-4473` (3,532 lines, 63 methods, 11 public)

**Seam: page primitives at the bottom, workflows on top.**

| Class (bottom -> top) | Holds | ~Lines |
|---|---|---|
| `UiSessionMixin` | `__init__`, `apply_setup`, `setup`, login check, onboarding bypass, park, `refresh_auth`, `teardown`, jitter | 360 |
| `PageNavigationMixin` | overlay dismissal, `_settle_if_redirecting`, `_enter_editor` | 420 |
| `PromptInputMixin` | image mode, prompt box, `format_character_prompt`, body-prompt checks | 525 |
| `GenerationSettingsMixin` | model/aspect/count settings incl. `_set_count` | 430 |
| `BatchCaptureMixin` | response listeners, `_await_captured`, `_download` | 315 |
| `ImageGenerationMixin` | the image flow | 660 |
| `CharacterGenerationMixin` | character constants + the character flow | 760 |

- **Order constraint:** the image flow calls reference attachment that lives in
  `VideoGenerationMixin` (`_attach_references`, `_intercept_reference_entities`,
  `_attach_character_entities`), and the video mixin calls up into `_enter_editor`. So
  reference attachment moves below both (see class 2) before this split.
- **Test seams:** 132 instance `setattr`s; class-level `setattr(UiAutomationTransport, ...)` for
  `_await_captured` (8), `_switch_to_image_mode` (3), `_select_image_model` (3); module-path
  patches `ui_automation.log` (3), `async_playwright` (3), `IMAGE_MODEL_OPTION_SELECTORS` (1).
- **Keep in `ui_automation.py`:** `setup` (reads `async_playwright`) and the settings methods that
  read `log` / the selector tuple, or retarget those 7 patches.
- **Risks:** module-global patches; base order (`VideoGenerationMixin` must stay ahead of any base
  that receives methods moved out of it); the 941-line module prelude also has to move to get the
  file under 2,000.

### 2. `VideoGenerationMixin` - `api/transports/ui_automation_video.py:913-4190` (3,278 lines, 71 methods, 1 public)

**Seam: shared asset/reference plumbing out, video orchestration stays.** Image generation uses
the reference code too, so it must not live in a "video" class.

| Class | Holds | ~Lines |
|---|---|---|
| `FlowNetworkMixin` | entity interception, status polling, download, `_save_clip` | 400 |
| `EditorModeMixin` | selector cascade, agent mode, crash detection | 345 |
| `VideoSettingsMixin` | video settings + `_select_video_aspect` | 240 |
| `FrameAttachMixin` | start/end frames | 275 |
| `ReferenceAttachMixin` | open-dialog upload, existing-asset tiles, image UUIDs, character entities, audio | 700 |
| `PickerProjectMixin` | picker project sync/ensure | 540 |
| `VideoGenerationMixin` | orchestration only (`_generate_video_locked`, `_submit_and_poll`) | 680 |

- **Test seams:** 26 class-level `setattr(VideoGenerationMixin, ...)` (`_upload_via_open_dialog`
  10, `_sync_picker_project` 7, `_select_existing_asset` 5, `_ensure_picker_project` 4). These keep
  working as long as the new bases come after `VideoGenerationMixin` in the MRO. No module-path
  patches.
- **Risks:** MRO order; `ReferenceAttachMixin` near the limit (split audio/entities if it grows);
  the 912-line prelude.

### 3. `MigratedComposer` - `api/transports/migrated_composer.py:1432-4054` (2,623 lines, 49 methods, 20 public)

**Seam: by composer stage.** It holds no `self._` state.

| Class (bottom -> top) | Holds | ~Lines |
|---|---|---|
| `EditorGateMixin` | `ensure_editor*`, agent chip, `_click` + miss diagnosis, cookie bar | 380 |
| `SettingsPaneMixin` | the settings pane | 510 |
| `UploadFrameMixin` | uploads and frame pickers | 415 |
| `MentionMixin` | `@` mentions / existing references | 400 |
| `SubmitObserveMixin` | submit + observe; move `fetch_mp4`/`clip_bytes`/`download` out for headroom | 910 -> ~805 |

- **Test seams:** `setattr(migrated_composer, "FRAME_*")` (19), `AGENT_ONLY_RELOAD_WAIT_S` (4),
  `_project_snapshot` (2), `_unique_display_name` (2): these pin the upload/frame and editor-gate
  code to the module unless 27 patch strings are retargeted. `patch.object(MigratedComposer, ...)`
  2. Constructed in src at 4 sites (`MigratedComposer()`).
- **Risks:** the constant patches; the file is 4,641 lines (module code at 1-1431 and 4055-4641,
  `run_video` and friends, must move too); `SubmitObserveMixin` close to the limit.

### 4. `OperationRecorder` - `data/recorder.py:247-1436` (1,190 lines) and `DataRepository` - `data/repository.py:80-1257` (1,178 lines, 38 public)

**`OperationRecorder` seam: by asset kind.** Core (lifecycle, attribution, prompt/tool/generation
metadata, ~175) at the bottom; `ImageRecordingMixin` (~245), `VideoRecordingMixin` (scene/extend +
video/failure, ~485), `CharacterRecordingMixin` (~240) on top. Keep `open` as a classmethod on the
final class: 5 tests patch `OperationRecorder.open` through CLI import sites.

**`DataRepository` seam: by table group.** `ProjectsRepo` (~100), `AssetsRepo` (~320),
`OperationsRepo` (~210), `ScenesChainsRepo` (~225), `LocalFilesRepo` (~300), all mixins over the
one `_store`. Each lands under 20 public methods, which retires the PLR0904 baseline entry. No
tests patch the module or the class. `repo.assets.*` namespaces would read better but rewrite
every caller (22 construction sites + the whole recorder): not in a pure refactor.

**Risk:** low for both.

---

## Suggested PR order

1. `DataRepository` + `OperationRecorder` (low risk, no Flow surface: e2e not required, say so).
2. `VideoGenerationMixin` (pull `ReferenceAttachMixin`/`FlowNetworkMixin` down first), e2e.
3. `UiAutomationTransport`, e2e.
4. `MigratedComposer`, e2e on an account served flow.google.com.

Each PR: golden master first (its own commit or PR), then one surface per commit, the baseline
ratchet, the ub-mode metrics (CRAP per function before/after, duplicate-code, wiring mutation),
`/gflow:check`, two fresh xhigh reviews, Sonar to zero, CodeRabbit answered.
