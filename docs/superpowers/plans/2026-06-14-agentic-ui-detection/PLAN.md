# Pluggable Strategy Pattern for Agentic UI Support Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature agentic-ui-strategy` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Drive Google Flow generations successfully when the browser session is placed in the new Agentic UI cohort. We do this by implementing a pluggable `FlowUiDriver` Strategy Pattern, expanding the Agentic UI settings panel, and using DOM scraping to retrieve the generated media URLs.

**Architecture:**
- Define the `FlowUiDriver` strategy protocol.
- Implement `ClassicFlowUiDriver` to keep existing classic selectors and logic fully isolated.
- Implement `AgenticFlowUiDriver` to handle Agent settings gear (`tune`), aspect/model menus, and DOM scraping for generated images.
- Dynamically detect and bind the correct driver strategy during browser setup.

---

## File structure

### New files
```
src/gflow_cli/api/transports/drivers/
  base.py
    Defines the FlowUiDriver protocol.
  factory.py
    Detects the UI mode and returns the correct FlowUiDriver instance.
  classic.py
    Concrete driver for the classic Flow UI.
  agentic.py
    Concrete driver for the new Agentic Flow UI.
tests/features/video_agent_ui.feature
  BDD scenarios covering Agentic UI settings configuration, scraping, and policy blocks.
tests/features/test_video_agent_ui_steps.py
  Step bindings and mock page fixtures for Agentic UI BDD scenarios.
```

### Modified files
```
src/gflow_cli/api/transports/ui_automation.py
  Delegate image generation actions to the bound FlowUiDriver.
src/gflow_cli/api/transports/ui_automation_video.py
  Delegate video generation actions to the bound FlowUiDriver.
tests/api/transports/test_ui_automation.py
  Adapt unit tests to verify the driver strategy selection and delegation.
CHANGELOG.md
  Add release notes for pluggable Agentic UI strategy under Unreleased.
```

---

## Task 1 — Define FlowUiDriver Strategy Protocol & Factory

**What:** Create the driver interfaces and factory logic to route UI actions.

**Files:**
- Create: `src/gflow_cli/api/transports/drivers/base.py`
- Create: `src/gflow_cli/api/transports/drivers/factory.py`

**Steps:**
- [x] Define the `FlowUiDriver` protocol in `base.py` containing common automation actions (`switch_to_image_mode`, `switch_to_video_mode`, `configure_image_settings`, `configure_video_settings`, `send_prompt`, `await_images`).
- [x] Implement the dynamic mode detector `get_ui_driver(page: Page) -> FlowUiDriver` in `factory.py` (probes the DOM for `crop_*` vs. `tune`/agent elements). Detection SOT lives in `drivers/` (the transport depends on it, not the reverse); skeleton `classic.py`/`agentic.py` conform to the protocol and are filled in by Tasks 2/3.
- [x] Write unit tests verifying that `get_ui_driver` returns the correct driver type depending on mock DOM states (`tests/api/transports/drivers/test_factory.py`, 8 tests; ruff + pyright clean).

---

## Task 2 — Implement ClassicFlowUiDriver

**What:** Extract classic UI selectors and actions into `ClassicFlowUiDriver` and delegate from the transports.

**Files:**
- Create: `src/gflow_cli/api/transports/drivers/classic.py`
- Modify: `src/gflow_cli/api/transports/ui_automation.py`
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`

**Steps:**
- [x] `ClassicFlowUiDriver` implements 5 of 6 protocol methods by delegating to the existing classic helpers (`_switch_to_image_mode`, `_switch_to_video_mode`, `_configure_generation_settings`, video settings block, `_send_prompt`) via **function-level late imports** — keeps `drivers/` a leaf (no circular import). `await_images` is deferred: the transport keeps its inline `page.on("response")` capture path (extracting it cleanly is entangled with the listener lifecycle; the driver method raises `NotImplementedError` and is not on the classic path). Selectors were left in place and delegated to rather than physically moved.
- [x] Driver bound **per generation, not in `setup()`** — `ui_driver = ClassicFlowUiDriver(transport=self)` at the top of `_generate_images_locked` / batch / `_generate_video_locked`. Bound **unconditionally to classic** (NOT via `get_ui_driver`) so the agentic cohort keeps its existing `FlowAgentUiError` path until Task 3; the call site carries a comment marking where `get_ui_driver` goes.
- [x] Generation call sites delegate `switch_to_image_mode` / `switch_to_video_mode` / `configure_image_settings` / `configure_video_settings` / `send_prompt` to `self.ui_driver`.
- [x] Full transports + BDD suite green: **411 passed, 1 skipped**; drivers 8 passed; ruff + pyright clean. Call-order regression tests adapted to patch `ClassicFlowUiDriver` at the class level (assertions preserved).

> **Review notes (carry into Task 3 / cleanup):** (a) `configure_video_settings` re-derives `is_i2v_with_frames`/`effective_model` — behaviour-identical to `_generate_video_locked` but a DRY duplication to consolidate later; (b) `send_prompt` needs the transport injected (`transport=self`) because the underlying `_send_prompt` is an instance method — this coupling dissolves once the logic fully moves into the driver; (c) classic `await_images` still owes a real implementation (or an explicit "classic captures via listener" contract) when the protocol's image-return path is unified.

---

## Task 3 — Implement AgenticFlowUiDriver (DOM Scraping)

**What:** Implement the Agentic UI driver using prompt-encoded settings and DOM scraping for generated media.

> **Grounded by live capture 2026-06-14** (`docs/AGENT_UI_RECON.md` § "DOM scraping
> validation"). Assets render as remote `https` `<img>`; page-level HAR = 0 entries
> (worker-delegated), so scraping is the only path. The four corrections below are
> mandatory — the naive node-count approach over-counts ~3×.

**Files:**
- Create: `src/gflow_cli/api/transports/drivers/agentic.py`

**Steps:**
- [x] **Bind the driver per generation, not in `setup()`** — `ui_driver = await get_ui_driver(page)` after `_enter_editor` in `_generate_images_locked` and the batch helper; classic gets `_transport=self` injected post-probe (factory returns a bare instance). Image path only; **video stays unconditional classic** (agentic video raises `FlowAgentUiError`).
- [x] Slate composer entry via `keyboard.insert_text` (NOT `fill()`, and NOT `keyboard.press_sequentially` — that method doesn't exist on Playwright's `Keyboard`; the review caught a masked `AttributeError` here and switched to the proven classic `insert_text` path).
- [x] **Settings encoded in the prompt, not the `tune` popover** — `configure_image_settings` stores count/aspect/model; `_compose_directive` builds `Generate {n} image(s)[ in {aspect} aspect ratio]: {prompt}`.
- [x] DOM scraping in `await_images`:
  - [x] Snapshot existing media UUIDs (regex `[?&]name=([0-9a-fA-F-]+)` over every `img` src via `eval_on_selector_all`).
  - [x] Poll until **`expected_count` distinct new `name=<uuid>` ids** appear (dedupe by UUID — verified 9 nodes → 3 UUIDs in `test_extract_uuids_deduplicates`).
  - [x] Build full-res download URL `…media.getMediaUrlRedirect?name=<uuid>`; **`labs.google` added to `_ALLOWED_DOWNLOAD_HOST_SUFFIXES`** so the redirect downloads. `GeneratedImage` wire-only fields (`seed`/`workflow_id`/`dimensions`) are scrape-synthesised sentinels (documented in `_build_generated_images`).
  - [x] Partial/zero produced → `TransportTimeoutError` with produced-vs-requested detail (typed, not silent).
  - [x] Conservative content-policy fail-fast: explicit text + `warning`/`error`/`block` symbols → `ContentPolicyError`; **`flag` excluded**. Selector provisional pending a live block sample (recon "Open follow-ups").
- [x] Gates: 435 passed / 1 skipped (transports+features); 32 driver tests; ruff + pyright clean (after review fixes: `press_sequentially`→`insert_text`, 2 dead `is not None` guards removed, `cast` on the scrape comprehension, test imports).

> **Unverified live (no agentic session available — browser closed):** the redirect-URL download (host now allow-listed but not exercised end-to-end), the synthetic `GeneratedImage` fields downstream (filenames/metadata), and the content-policy selector (no positive block sample). The DOM-scraping *logic* is unit-tested against mocked DOM; live validation is owed before relying on the agentic path in production.
>
> **Known limitations:** batch binds one driver per batch (not per item — cohort flap mid-batch, scenario #11, unaddressed); agentic + reference-image/entity attaches run classic selectors before the agentic branch (t2i is the validated path); `await_images` snapshots its baseline just after submit (real generation ~15 s ≫ 0.5 s poll, so low-risk, but ideally captured pre-submit).

---

## Task 4 — BDD and Integration Test Verification

**What:** Add feature files and mock tests asserting Agentic UI generation success, settings configuration, and content policy blocks.

**Files:**
- Create: `tests/features/video_agent_ui.feature`
- Create: `tests/features/test_video_agent_ui_steps.py`
- Modify: `tests/api/transports/test_ui_automation.py`

**Steps:**
- [x] Added 5 agentic scenarios to `video_agent_ui.feature` (UUID dedup 9→3, settings-in-prompt, content-policy block, **flag-only NOT a block**, count-mismatch timeout) alongside the existing forced-agentic-video → `FlowAgentUiError` (exit 25) scenario.
- [x] Step bindings in `test_video_agent_ui_steps.py` drive `AgenticFlowUiDriver` at the driver boundary with a mocked `Page` (eval_on_selector_all / evaluate / locator.count / keyboard). Timeout scenario monkeypatches `_AWAIT_TIMEOUT_S`/`_POLL_INTERVAL_S` to run in ~1s. pytest-bdd is sync, so async driver calls are wrapped in `asyncio.run`.
- [x] Green: `tests/features/` 40 passed; combined transports+features **440 passed, 1 skipped**; ruff clean; new step file pyright-clean (4 pre-existing pyright errors remain in unrelated `test_auth*_steps.py`, not introduced here). No `src/` changes.

---

## Task 5 — Changelog & Repository Hygiene

**What:** Document the strategy and run formatting and linting checks.

**Files:**
- Modify: `CHANGELOG.md`

**Steps:**
- [x] `CHANGELOG.md` `[Unreleased]` documents the pluggable `FlowUiDriver` strategy + initial agentic-cohort image generation (DOM scraping, UUID dedup), with agentic video still `FlowAgentUiError`.
- [x] Hygiene green: `check_repo_hygiene.py` (418 files, no violations), `check_doc_links.py` (all links resolved), `ruff check` + `ruff format --check` clean, `pyright` 0 errors on the changed surface (4 pre-existing errors remain in unrelated `tests/features/test_auth*_steps.py`).

---

## Definition of done

- [x] All task steps checked off (Tasks 1-5)
- [x] ruff / format / pyright clean on the feature surface; pytest **440 passed, 1 skipped**
- [x] `CHANGELOG.md` `[Unreleased]` section updated
- [x] BDD features cover classic (existing transports suite) and agentic driver generation (`video_agent_ui.feature`)
- [x] **E2E live validation (classic):** real `gflow image t2i` (647 KB JPG, signed CDN URL) and `gflow video t2v` (6.3 MB MP4, `MEDIA_GENERATION_STATUS_SUCCESSFUL`) both succeeded on profile `denon82` — classic cohort, exit 0. Validates the full classic image + video paths through the new driver delegation (no regression).
- [x] **E2E-discovered fix:** the first image run hit the *agentic* cohort and wrongly bound classic → `FlowAgentUiError` (exit 25). Root cause: `get_ui_driver` did an **instant** DOM probe that raced the composer render (agentic `tune` indicator appeared ~1.25 s later). Fixed `detect_ui_mode` to **poll** until a signal appears (8 s window, then default classic); regression test `test_detect_agentic_after_delayed_render`; transport-test conftest collapses the poll window so unit tests stay fast.
- [x] **Agentic path live validation (DONE):** since the cohort can't be forced server-side, added a deterministic trigger — `GFLOW_CLI_FORCE_AGENT_UI=1` clicks the in-input "Agent" toggle (confirmed live to flip `crop_*`→`tune`) so the agentic driver binds on any load. Validated on denon82: `--count 1` → one 420 KB JPG; `--count 3` → 3 distinct-UUID JPGs (dedup live). Proves scrape → redirect-URL download (`labs.google` allowlist) → synthetic-field save. Repeatable via `scripts/e2e/agentic_image_e2e.ps1`; runbook `docs/AGENT_UI_E2E.md`.
- [x] **E2E-discovered fixes (3):** force-toggle render race (`wait_for` the toggle); content-policy false positive on static chrome (scoped detection to alert/dialog regions); + the earlier detection race. All with regression tests.
- [ ] **Still owed:** a captured positive content-policy refusal sample (to widen detection beyond alert/dialog regions); agentic video.
