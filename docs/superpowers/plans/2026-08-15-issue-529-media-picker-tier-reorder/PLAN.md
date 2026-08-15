# Catalog-name media picker resolution plan (#529)

> Run `/gflow:status --feature issue-529-media-picker-tier-reorder` for the next
> unchecked task and `/gflow:check` before committing.

## Goal

Resolve catalog image UUID references through Flow's browser-searchable
`displayName`, then verify the exact UUID in the surfaced tile. The UUID path
must not scroll the unfiltered media grid or type UUID/prompt fragments into a
name search.

## Evidence-driven design

The 2026-08-15 headed-Chrome spike used one populated private test project and
three captured assets. Live account and asset identifiers are
intentionally omitted from this committed artifact:

- One shared display name surfaced two distinct UUID tiles.
- One unique display name surfaced its exact UUID tile.
- Exact UUID-in-thumbnail matching disambiguated the same-name results.
- The harness made zero scroll calls, asset clicks, or generation requests.

That proves the contract:

```text
catalog UUID -> Flow displayName -> picker name search -> exact UUID tile -> attach
```

It also exposed the catalog defect behind the older workaround:
`GeneratedImage.from_response_dict()` already joins `workflows[].metadata.displayName`,
but the UI response collector parsed `media[]` items alone and discarded the
sibling workflow names. Consequently UI-generated catalog rows could not provide
the browser search key.

## Architecture

1. Preserve Flow `displayName` while parsing captured UI responses so the
   existing recorder persists it in `assets.metadata_json` when prompt history
   is stored; redacted history omits the potentially prompt-derived caption.
2. Enrich image `--ref <uuid>` values from `AssetLookup`: display name for
   picker search and an integrity-verified local file for #393's upload fallback.
3. Resolve I2V start/end UUIDs to separate catalog display names and extant
   local fallbacks in the CLI, and carry both beside the stable UUIDs on
   `GenerateVideoRequest`.
4. In `_select_existing_asset`, search only the display name and match only the
   exact UUID tile. Never scroll, click an unfiltered tile, or search
   UUID/UUID-stem/prompt text. Wait briefly for a delayed search input.
5. Preserve fail-closed behavior: CLI/MCP image and I2V refs use a recorded
   local fallback only when byte count/SHA-256 still matches; otherwise frame
   refs raise `TransportTimeoutError` before submission. Every I2V frame form
   activates the post-submit T2V-route backstop.

## Tasks

### Task 1 — Live spike

- [x] Select a populated private test project with multiple named assets.
- [x] Search two Flow display names in the real picker.
- [x] Verify exact UUID tiles, including a duplicate-name case.
- [x] Record zero scroll/click/generation requests.

### Task 2 — TDD and BDD

- [x] Add a red regression proving UI response parsing retains workflow names.
- [x] Add a red regression proving bare image UUIDs gain catalog names.
- [x] Add red picker tests forbidding scroll and UUID-term searches.
- [x] Add per-frame video name-resolution tests.
- [x] Replace the old scroll-tier Gherkin with name-search/exact-UUID scenarios.

### Task 3 — Implementation

- [x] Fix UI response parsing and catalog enrichment.
- [x] Replace prompt hints with per-frame display names.
- [x] Remove scroll and UUID-term retries from UUID asset selection.
- [x] Preserve exact UUID matching, local fallback, project sync, and typed failure.
- [x] Address both xhigh review rounds; preserve MCP UUID identity and verified
  local fallback symmetry.
- [x] Run the scoped implementation suite (350 passed).

### Task 4 — Documentation

- [x] Rewrite this plan and `SCENARIO.md` around the live-proven contract.
- [x] Expand the spike note with sanitized live evidence.
- [x] Update reference-strategy documentation and `[Unreleased]` changelog.
- [x] State that historic #287 prompt-hint/scroll guidance is superseded.

### Task 5 — Quality and review

- [x] Run `/gflow:check` (all seven local gates).
- [x] Run `/gflow:pr-council-review` and reach consensus green.
- [x] Re-run the picker verification against both the implemented image-picker
  selector and real I2V Start-frame slot paths.
- [ ] Update PR #540, then verify CI and `/gflow:sonar 540` are green.

## Definition of done

- [x] All local quality gates green.
- [x] Council consensus green.
- [x] Image-picker and I2V frame-slot paths live-verified with no generation
  submission required.
- [ ] PR #540 updated; CI and SonarCloud show zero new issues.
- [x] Current guidance supersedes the historical changelog entries that record
  UUID/prompt search and unfiltered grid scrolling experiments.
