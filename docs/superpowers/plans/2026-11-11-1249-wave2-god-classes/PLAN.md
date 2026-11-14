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

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-12 | PR 1 (#1262): `DataRepository` as five sibling table groups over `RepositoryBase` | No repository method calls across groups (AST-checked), so no chain is needed; each group is under 20 public methods | a linear chain like wave 1 |
| 2026-11-12 | `OperationRecorder`: `RecorderCore` plus three parts, each inheriting the core | The parts call only core methods (`_generation_metadata`, `_resolve_prompts`); `open` stays on the final class because five CLI tests patch it | putting `open` in the core |
| 2026-11-12 | Golden master = the rows each existing data test leaves in its SQLite files, not a hand-written scenario | 100 existing tests already drive every table group; their rows are the observable outcome, at no authoring cost | a new end-to-end scenario |
| 2026-11-12 | Golden master retired once CI proved the move (run 43206267489, every test job green) | As a standing test it forces a 10k-line re-record for any new data test, costs ~12 s, and sharpit reads its 100-in-1 runs as N+1. Both reviews agreed | keeping it, or trimming it to a subset |
| 2026-11-12 | One file-level pyright directive per private module, not 19 per-line ignores | The helpers are private to the data package; renaming them is churn in a move-only wave | renaming helpers to public names |
| 2026-11-12 | Sonar S6663 (38) marked false positive, S7632 (7) accepted, each with a comment | Moved code: `sqlite3.Row` supports str keys; `# noqa: CODE - reason` is the repo convention (130 uses on develop). The 4 S108 came from the slicer and were fixed in code | rewriting moved bodies |
| 2026-11-12 | pylint duplicate-code 3 → 6 recorded, not "fixed" | pylint compares only across files: the same 3 repeated `OperationRecord(... =None)` blocks were inside the old single file. Removing them means rewriting bodies | deduplicating in this PR |
| 2026-11-12 | Paths in the golden master normalised to `/` for any drive-letter or temp path | Linux CI saw backslashes from `Path` values recorded on Windows; re-recorded on develop's code each time | per-OS fixtures |
| 2026-11-12 | PR 2 (`VideoGenerationMixin`): golden master = per-method signature + source hash over the transport's whole class chain, a global-resolution check, and an MRO check | A pure move keeps bodies byte-identical, so a hash catches any edit; the global check catches a moved method calling `VideoGenerationMixin._x` or a helper its new module never imported, which no type check sees; the existing transport suites already pin DOM calls and outcomes against fakes. Recorded at 4143e83c: 147 methods, 46 module names; mutation-checked (one edited log line, one unimported name: both fail) | a new fake-page traffic scenario per method |
| 2026-11-12 | Log events keep their names; moved methods may use their own module's `log` | Event names are literal strings (`"ui_automation_video.frame_attached"`); no `add_logger_name` processor, so the logger's `__name__` never reaches output | binding the old logger name in each new module |
| 2026-11-12 | Two test patches retarget when their method moves: `_PROBE_TIMEOUT_MS` (read only by `_probe_selector_cascade`) and `write_asset_async` (only `_save_clip`). `migrated_route` stays (only `_generate_video_locked`) | AST map of patched module globals to the methods reading them; the edits are import-path only and get listed in the commit | leaving a patch that silently stops reaching its method |
| 2026-11-12 | The preamble (912 lines of selectors and helpers) moved verbatim to `_video_common`; `ui_automation_video` re-exports every name others read | The parts cannot import from `ui_automation_video`, which imports them to build the chain: a circular import that breaks whenever a part is imported first | importing the parts after the preamble inside `ui_automation_video` (order-dependent, breaks on a direct import) |
| 2026-11-12 | Linear chain `FlowNetwork → EditorMode → VideoSettings → PickerProject → ReferenceAttach → FrameAttach → VideoGeneration`; `_tile_is_fully_visible` went to PickerProject and `_pick_option_and_include` + `_remote_option_tile` to ReferenceAttach | AST call graph: with those three placed by caller, every callee precedes its callers, so pyright sees each call with no stubs. Largest part 738 lines (ReferenceAttach) | sibling mixins (pyright rejects cross-part calls); the plan's frame/reference grouping (a two-way cycle) |
| 2026-11-12 | Bodies keep `VideoGenerationMixin._x(...)` (88 references); `ui_automation_video` binds the name into each part's module after the class exists, with a `TYPE_CHECKING` import for pyright | 26 tests patch those methods on `VideoGenerationMixin`; rewriting the calls to the defining part would make those patches stop reaching them silently. The golden master's global-resolution test fails if a binding is missing (mutation-checked) | rewriting the call sites |
| 2026-11-12 | Two file-level pyright directives: `reportPrivateUsage=false` in each part and `reportUnusedFunction=false` in `_video_common` | Private helpers are now used across the package's private modules; renaming them is churn in a move-only PR (same call as PR 1) | per-line ignores; renaming helpers |
| 2026-11-12 | ub-mode metrics, before → after: CRAP per function 86 → 86 compared, 0 worse, 0 new (11 pre-existing at CRAP ≥ 10, unchanged by a byte-identical move); pylint duplicate-code in `transports/` 12 → 12; wiring mutation 3/3 killed (drop a binding, skip a chain link, drop a re-export) | Moved bodies are byte-identical (147 source hashes unchanged), so coverage and complexity cannot move; the only new code is class shells, bindings and re-exports | mutmut over the moved bodies (they did not change) |
| 2026-11-14 | PR 4 (`MigratedComposer`): linear chain `Media → EditorGate → SettingsPane → UploadFrame → Mention → SubmitObserve → MigratedComposer`. Preamble and project RPC helpers verbatim to `_mc_common` (1,507 lines, no class). `run_video`, `run_images`, `create_project` and `rename_project` stay in `migrated_composer.py` (4,634 → 1,097 lines). Largest part: `_mc_submit_observe`, 889 lines | The AST call graph puts every callee first. `ensure_editor` and `_absent_chip_verdict` (one cycle) share `EditorGateMixin`. The functions that construct `MigratedComposer()` would cycle from a part. The composer holds no state, but mixins keep the bodies byte-identical and the class-level patches reaching | module functions instead of mixins (rewrites every `self.` call and breaks 12 class-level patches) |
| 2026-11-14 | Constants retarget with their readers: `FRAME_*` and `_unique_display_name` to `_mc_upload_frame`; `SUBMIT_*`, `RESULT_URL_GRACE_S`, `IMAGE_REPLY_BUDGET_S` and `_submit_body_problem` to `_mc_submit_observe`; `AGENT_*` to `_mc_editor_gate`; `IMAGE_RECOVERY_*` to `_mc_common` | 27 names are patched through `setattr(migrated_composer, ...)`. A patch on the aggregator no longer reaches a moved reader | keeping every reader in `migrated_composer.py` (the file would stay over 2,000 lines) |
| 2026-11-14 | The reach guard's "several allowed modules" form hid one lost patch: `test_snapshot_gives_up_at_its_deadline_and_still_closes` patched `migrated_composer.time` while `_project_snapshot` (moved to `_mc_common`) read its own `time`, so the test waited the real 15 s. The test now patches `sys.modules[mc._project_snapshot.__module__]`, so the patch follows the function | Found by the coordinator's per-test duration diff against develop. It was the only slower test, and the agent's green run could not show it | retargeting to a hard-coded `_mc_common` (breaks again on the next move) |
