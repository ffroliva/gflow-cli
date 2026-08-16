# Scenario: catalog-name media picker resolution (#529)

**Change under test:** A catalog UUID is stable asset identity. Flow's browser
picker is filtered by the catalog-recorded `displayName`, and the exact UUID is
then asserted in the result tile's thumbnail URL. UUID references do not scroll
the unfiltered grid or search UUID/prompt fragments.

## Coverage map

| Dimension | Scenario | Severity | Expected behavior |
|---|---|---:|---|
| Data | UI generation response carries sibling `workflows[]` names | Critical | The returned `GeneratedImage.display_name` is populated and store-mode history persists it. |
| Privacy | Prompt history is redacted | Critical | The potentially prompt-derived Flow caption is not persisted or logged in plaintext. |
| Data | Bare image UUID is present in the local catalog | Critical | Enrichment supplies `display_name`; a matching recorded local file remains the upload fallback. |
| Picker | Named asset is outside the initial virtualized viewport | Critical | Name search surfaces candidates; exact UUID tile attaches; zero grid scroll. |
| Picker | Two assets share one display name | Critical | Search may surface both, but only the tile whose image URL contains the requested UUID attaches. |
| Picker | UUID/name/stem differ | High | Only the display name is typed. UUID and UUID stem are never search terms. |
| Picker | Exact UUID tile exists only in the unfiltered/overscan grid | Critical | It is not clicked; no Playwright implicit scroll can occur. |
| Failure | Name search misses and a local file exists | Critical | The search is cleared and the recorded file uploads; no generation can proceed without a bound ref. |
| Failure | Name search misses with no local fallback | Critical | Image/frame binding raises `TransportTimeoutError` before prompt submission. |
| Video | Start and end frames have different names | High | Each UUID carries its own display name to its own frame-slot lookup. |
| Video | Named frame picker lookup misses but a local image exists | Critical | The exact recorded bytes upload for either start or end frame. |
| Video | Catalog frame has no retained name but has a local image | High | The exact recorded bytes are uploaded; the unfiltered picker is not scanned. |
| Compatibility | Legacy catalog row lacks `display_name` | Medium | A verified local file uploads; otherwise binding fails closed without clicking the unfiltered grid. |
| Compatibility | Picker search input mounts late or is absent | Medium | Wait up to the bounded search timeout; absence uses verified local fallback or typed failure. |
| Integrity | Catalog local file was replaced after recording | Critical | Byte-count/SHA-256 mismatch disables upload fallback. |
| MCP | I2V frame is supplied as a catalog UUID | Critical | Queue payload preserves UUID, display name, and verified local fallback through worker decode. |
| Spend safety | UUID-backed I2V routes to T2V | Critical | Post-submit route guard rejects the output instead of reporting false success. |
| State | First ref leaves a picker term behind | High | Caller/search clears are presence-guarded per reference; pooled Page state does not leak. |
| Diagnostics | Exact tile is absent | Medium | Existing screenshot and bounded DOM dump remain available without adding user text to terminal events. |

## BDD scenarios

The executable feature at `tests/features/media_picker_tiers.feature` covers:

1. Catalog name → exact UUID tile → attach, with no scroll.
2. Duplicate names disambiguated by UUID.
3. Named miss → recorded local fallback, with no scroll.
4. Video frame UUID → per-frame catalog name → exact UUID tile.

## Must-cover assertions

- UI response collection uses the sibling workflow metadata instead of parsing
  media items in isolation.
- Catalog enrichment preserves an existing mention name and ignores stale local
  paths.
- `_existing_asset_tile` remains UUID-in-`src`; no name-only tile click exists.
- `_scroll_picker_grid_until_rendered` is never invoked by the UUID asset path.
- `press_sequentially` receives one value: `display_name`.
- Name collisions cannot attach a sibling UUID.
- Image fallback, named/unnamed frame local fallback, and video fail-closed
  behavior happen before submission.
- A failed/absent name search never reaches `locator.click()` on an unfiltered
  tile, preventing Playwright's implicit scroll behavior.
- Project-picker synchronization still runs before every UUID lookup.

## Empirical boundary

The spike proves the picker lookup contract without spending credits. It does
not claim that every historical catalog row already has a display name; rows
created before this parser fix, agentic results, and redacted-history rows need
a verified local-file fallback. New classic UI images retain the Flow name only
when prompt history is stored.
