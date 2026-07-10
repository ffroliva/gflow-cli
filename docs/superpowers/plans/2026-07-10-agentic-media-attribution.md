# Fix: agentic wrong-media attribution (#281) + UUID-ref picker scroll (#282)

**Context.** 2026-07-10 production run (agentic cohort, v0.30.0): `image t2i` silently
downloaded pre-existing project logos as "generated portraits" (#281 — live evidence in the
issue), and multi-`--ref` UUID selection failed on every ref after the first (#282). Design
principle for every task: **fail fast, never silent-wrong** — an error the user sees beats a
wrong artifact reported as success (precedent: the `--model` silent no-op, PR #48).

**Branch:** `fix/281-agentic-media-attribution` off `develop`. PR into `develop`.

## Global constraints (binding)

- TDD mandatory: write the failing test first (RED), then implement (GREEN). Coverage must not drop.
- Unit tests only (no e2e); mirror the fake/mock patterns already used in the target test files.
- Conventional Commits; reference the issue number (`#281` / `#282`) in each commit message.
- CHANGELOG.md `[Unreleased]` gets a `### Fixed` entry per user-visible change (Keep-a-Changelog).
- Do not change public CLI flags or DTO wire shapes.
- `ruff check` and the full unit+integration test suite must pass before every commit.
- Run tests via `uv run pytest tests -m "not e2e" -q`.

## Task 1 — `MediaAttributionError` + agentic ambiguity fail-fast (#281)

Files: `src/gflow_cli/errors.py`, `src/gflow_cli/api/transports/drivers/agentic.py`,
tests: `tests/api/transports/drivers/test_agentic.py`, error-registry tests if the repo has them
(check how existing error classes are covered, e.g. tests for exit codes).

1. `errors.py`: add `class MediaAttributionError(GFlowError)` — "generated media could not be
   attributed / attributed media is not new". Follow the structure of neighbouring classes
   (detail + remediation-style message, exit-code registration if the codebase maps error →
   exit code; read how `TransportTimeoutError` and `DataIntegrityError` are declared and mapped,
   and mirror that).
2. `agentic.py await_images` (~line 444):
   a. **Baseline settle**: take the baseline as the UNION of two `_scrape_img_srcs` passes
      separated by one `_POLL_INTERVAL_S` sleep, so lazy tiles that render late do not count as
      "new". Keep the total timeout unchanged.
   b. **Ambiguity fail-fast**: after the poll loop, if `len(new_uuids) > expected_count`, raise
      `MediaAttributionError` naming the candidate UUIDs and `expected_count`, with remediation
      text ("cannot attribute the generation among N candidates; re-run; a dedicated project
      with fewer assets avoids lazy-render ambiguity"). NEVER truncate/slice.
3. `_build_generated_images` (~line 522): now only reachable with exactly `expected_count`
   UUIDs; require that (defensive check) and remove the "set is unordered" arbitrary-slice
   behaviour/comment.
4. Tests (RED first): (a) exactly-expected passes through; (b) MORE new UUIDs than expected →
   `MediaAttributionError` (not a truncated success); (c) baseline-union: a UUID present only in
   the second baseline pass is not "new"; (d) timeout path unchanged.

## Task 2 — pre-download attribution guard + collision escalation (#281)

Files: `src/gflow_cli/data/recorder.py`, `src/gflow_cli/cli_image.py`,
maybe `src/gflow_cli/image_batch.py` (it duplicates the warn helper — apply the same
escalation there if it has the same record path),
tests: `tests/data/test_recorder.py`, the cli_image unit tests (find where
`_record_generated_images_safe` / download flow is covered; add a test module if none).

1. `recorder.py`: add public `is_media_recorded(self, *, profile_name: str, flow_media_id: str) -> bool`
   delegating to `repository.get_asset_by_flow_media_id` (repository.py:167). No behaviour change
   to existing methods.
2. `cli_image.py`: new `_verify_media_attribution(recorder, *, profile_name, images)` — for each
   `img.media_name` already recorded for this profile, collect it; if any → raise
   `MediaAttributionError` listing the UUIDs ("the driver returned media that already exists in
   local history — wrong-media attribution (#281); nothing was downloaded"). Call it in every
   image-generation flow (t2i, i2i, and the batch flow if it shares the path) AFTER
   `generate_images` returns and BEFORE `_download_images`.
3. `_record_generated_images_safe` (~line 925): catch `DataIntegrityError` FIRST and re-raise as
   `MediaAttributionError` naming the flow_media_id and the saved local path ("downloaded file is
   suspect — it may be a pre-existing asset, #281"). Keep the existing warn-only behaviour for
   other `DataStoreError`s. Update `image_batch.py`'s equivalent if it exists.
4. Tests (RED first): (a) guard passes when no media recorded; (b) guard raises listing only the
   already-recorded UUIDs; (c) `DataIntegrityError` → `MediaAttributionError` escalation;
   (d) generic `DataStoreError` still warns and does not raise.

## Task 3 — picker: scroll virtualised grid + fresh search state (#282)

Files: `src/gflow_cli/api/transports/ui_automation_video.py`,
tests: wherever `_select_existing_asset` / `_attach_image_uuid_refs` are covered today (search
`tests/` for those names; mirror the fake-page pattern of neighbouring picker tests, e.g. the
entity-picker `_find_picker_entity_tile` tests).

1. `_select_existing_asset` (~1493): when the tile is not visible in the initial viewport (and
   after the display-name search attempt), loop `_scroll_picker_grid` (~1599) the same way
   `_find_picker_entity_tile` (~1612) does, re-checking tile visibility between scrolls, before
   returning `False`.
2. `_attach_image_uuid_refs` (~1560): ensure per-iteration fresh state — clear the picker search
   input before use (and between refs), and re-resolve the dialog locator per iteration rather
   than reusing a possibly-stale handle.
3. Tests (RED first): (a) tile visible only after N scrolls → selected (no upload fallback);
   (b) tile absent after exhausting scrolls → existing behaviour (fallback to local path if
   given, else the TransportTimeoutError with the same message); (c) search input cleared
   between two UUID refs.

## Task 4 — docs: CHANGELOG + KNOWN_ISSUES (#281, #282)

Files: `CHANGELOG.md`, `KNOWN_ISSUES.md`.

1. `[Unreleased]` `### Fixed`: three entries (agentic wrong-media attribution fail-fast #281;
   pre-download attribution guard + DataIntegrityError escalation #281; UUID-ref picker
   virtualised-grid scroll #282). Follow the file's existing entry style.
2. `KNOWN_ISSUES.md`: add a resolved-style entry for the wrong-media class (like the
   "`--model` silent no-op" precedent): symptom ("local history was not updated" +
   wrong file), root cause, fixed-in version, defense layers. Cross-link #281/#282 and #174.
