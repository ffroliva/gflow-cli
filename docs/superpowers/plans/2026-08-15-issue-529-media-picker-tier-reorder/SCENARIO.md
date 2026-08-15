# Scenario: #529 — media-picker UUID search tiers demoted below the scroll fallback

> Predict verdict: **CAUTION** (2026-08-15, 5-persona; confidence 6/10). Mitigations
> folded into PLAN.md tasks. Scope fixed at issue proposals **1 + 2 + 4**; proposal 3
> (porting #287's hint derivation to the image path) is gated behind the alt-text
> experiment (T0) and is explicitly **out of scope** for this plan.

**Change under test.** `VideoGenerationMixin._select_existing_asset`
(`src/gflow_cli/api/transports/ui_automation_video.py:2211-2321`) currently walks
`(display_name, media_id, uuid_stem, *search_hints)` before falling back to
scrolling the virtualised grid. Two of those tiers — the full media UUID and its
first hyphen segment — are live-proven never to match (#287 rounds, #393
2026-07-27) and cost ~14.9 s per reference. The change reorders the cascade to
`(display_name, *search_hints)` pre-scroll and **retries `(media_id, uuid_stem)`
only after the scroll fallback misses**, plus emits a `resolved_by` tier label on
the attach events.

---

## Coverage map

| Dim | Active? | Why |
|---|---|---|
| **D2** WAF / reCAPTCHA | ✅ light | Removes 2 `press_sequentially` bursts per ref from the pre-scroll path — changes the session's interaction fingerprint (net fewer synthetic keystrokes, shorter dialog-open window). No token minting touched. |
| **D3** Selector drift & locale | ✅ | `PICKER_SEARCH_INPUT` presence-probing moves; the #174 no-search-box cohort now hits a *different* code path. No new selectors, so locale-invariance is not engaged. |
| **D4** Batch & resume | ✅ | `image batch` / `movie run` attach the same refs across many rows; the saving compounds and any reachability regression multiplies. |
| **D5** Concurrency & Page pool | ✅ | The post-scroll retry types into a grid that is already scrolled — pooled-Page state on checkin. Reduced hold time raises effective throughput. |
| **D6** Data layer | ✅ light | `_media_search_hints` → `get_asset_prompt` and `_enrich_uuid_refs` → `resolve_seed_image` are the only catalog reads; both already best-effort. No schema change. |
| **D7** Error propagation & exit codes | ✅ | The exit-9 `TransportTimeoutError` frame-slot contract and the #393 "never generate without the ref" contract must survive byte-for-byte. |
| **D9** Transport edge cases | ✅ | `_search_picker_for_tile` returning `None` (no search box) is a three-state result; the reorder changes when that state is first observed. |
| **D11** Input validation | ✅ edge-only | `uuid_stem = media_id.split("-", 1)[0]` on a hyphen-less id yields the full id — a duplicate the `terms` dedup already absorbs. Must stay absorbed after the split. |
| **D12** Observability | ✅ | New `resolved_by` field; `picker_search_tier` event ordering changes; `docs/ARCHITECTURE.md` event contract. |
| D1 Auth & session | ⛔ skipped | No cookie, SAPISID, token, or profile-lease surface. |
| D8 Cross-platform paths | ⛔ skipped | No path construction, no `platformdirs`, no filename handling. `local_path` is passed through unchanged. |
| D10 Headless vs headed | ⛔ skipped | Wall-clock only; no environment-conditional branch. The change behaves identically headed and headless. |

---

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| S1 | D7 | Bare `--ref <uuid>` whose tile is **off-viewport**; scroll finds it | **Critical** | Attached from the scroll tier. Zero `press_sequentially` calls before the scroll. Same tile, same `_attach_selected_tile` call as today. | Unit (fake page) |
| S2 | D7 | Ref is unreachable by **every** tier (viewport, scroll, demoted UUID tiers) | **Critical** | Image path uploads `local_path` (#393 rescue); frame-slot path raises `TransportTimeoutError` **exit 9**. Generation **never** proceeds without the reference. | Unit + BDD |
| S3 | D9 | Tile matching after the reorder | **Critical** | `_existing_asset_tile` still matches `[role='option']:has(img[src*='<uuid>'])` — never a name/caption match. A hint that surfaces 30 tiles still selects only the UUID one. | Unit |
| S4 | D4 | `image batch` / `movie run` attaching the same 2 UUID refs across 11 rows | **High** | ~30 s saved per row (~5.5 min per pass). No cross-row search-term leakage — the per-ref `search.fill("")` at `:2404-2409` still runs first. | Integration |
| S5 | D3 | #174 cohort: picker variant with **no search box at all** | **High** | Pre-scroll `terms` is empty on the image path, so `_search_picker_for_tile` is never called and `picker_search_unavailable` is not emitted before the scroll. Scroll runs; post-scroll retry probes once, gets `None`, and gives up without a hard dependency on the box. | Unit |
| S6 | D9 | Post-scroll UUID retry returns `None` (no search box) mid-loop | **High** | `break` out of the retry, proceed to not-found telemetry + upload fallback. No unguarded `.fill("")` against an absent element (would burn a full actionability timeout). | Unit |
| S7 | D5 | Post-scroll retry types a term into an **already-scrolled** grid | **High** | Search must be cleared before the caller returns `False`, so the pooled Page is never handed back holding a filtered + scrolled grid. Mirrors `_try_select_existing_by_filename:2113`. | Unit |
| S8 | D12 | `resolved_by` emitted on every terminal outcome | **High** | Exactly one of `viewport` / `display_name` / `hint` / `scroll` / `uuid_retry` / `upload` / `not_found` per ref, on the existing `image_ref_selected_existing` and `frame_ref_attached` events. Never a new event name. | Unit (capture logs) |
| S9 | D12 | `resolved_by` payload contains no user content | **High** | The field is a fixed tier **label**. The winning search *term* (a 6-word prompt slice on the video path) is never added to the attach events. `picker_search_tier`'s existing `term=` is unchanged — not widened. | Unit |
| S10 | D7 | Video frame-slot path (`_attach_frame_by_media_id:1850-1900`) | **High** | `display_name` is `""`, hints may be present. Pre-scroll cascade is `(*search_hints,)` only. Error message, screenshot name, and exit code identical to v0.57.1. | Unit + BDD |
| S11 | D3 | Flow renames the thumbnail URL param so `img[src*=<uuid>]` stops matching | **Medium** | Unchanged failure mode: every tier misses, `existing_asset_not_found` warns with screenshot + `debug_picker_dom_<uuid8>.json`. The reorder must not swallow this diagnostic path. | Unit |
| S12 | D2 | Profile with elevated WAF heat runs a 2-ref i2i | **Medium** | Fewer synthetic keystrokes and a shorter dialog-open window than v0.57.1 — strictly less automation surface. No new interaction primitive introduced (scroll already ran on every one of these lookups). | Live (observational) |
| S13 | D11 | `media_id` with no hyphen (malformed or non-standard id) | **Medium** | `uuid_stem == media_id`; the dedup keeps `terms` at one entry in the post-scroll retry. No double 7.9 s pass. | Unit |
| S14 | D6 | Catalog unavailable / asset unknown → `search_hints=()` and `local_path=""` | **Medium** | Video path: pre-scroll cascade is empty, straight to scroll. Image path: unchanged from today. Both stay best-effort; no `DataStoreError` escapes. | Unit |
| S15 | D12 | `picker_search_tier` events now appear **after** `picker_scroll_done` | **Medium** | Log-order change is intentional and documented. Anything parsing these events (live-verification ledger) must be told the order flipped. | Review gate |
| S16 | D5 | `GFLOW_CLI_CONCURRENCY` at its current ceiling with UUID refs | **Low** | ~30 s less Page checkout per generation → more headroom at the same ceiling. The safe ceiling itself is **not** raised by this change. | Live (observational) |
| S17 | D4 | A ref that resolves from the **viewport** (freshly generated asset) | **Low** | Untouched path — still the 4 s `tile.wait_for(state="visible", timeout=4000)` at `:2224`, no searches, no scroll. `resolved_by="viewport"`. | Unit |

Severity: **Critical** (data loss / billed twice / unrecoverable) · **High** (feature broken, workaround exists) · **Medium** (degraded UX, explicit error) · **Low** (cosmetic or edge-only)

---

## Must-cover before merge (Critical + High)

1. **S1** — the saving actually lands: assert zero `press_sequentially` awaits before the first scroll on a bare-UUID image ref.
2. **S2** — the #393 contract is untouched: unreachable ref uploads (image) or exits 9 (frame slot); **never** generates without the reference.
3. **S3** — tile identity stays UUID-in-`src`; an imprecise hint can surface extra tiles but can never attach a wrong one.
4. **S5 / S6** — the #174 no-search-box cohort survives both the new empty-`terms` path and the post-scroll retry probe.
5. **S7** — the picker is left clean (search cleared) whenever `_select_existing_asset` returns `False`.
6. **S8 / S9** — `resolved_by` covers every terminal outcome and leaks no prompt text.
7. **S10** — the video frame-slot error contract (message, screenshot name, exit 9) is pinned byte-for-byte.
8. **S4** — batch/manifest paths keep the per-ref `search.fill("")` reset, so terms never leak between rows.

**Rewrite targets (existing tests pin the old order — TDD red step):**
`tests/api/transports/test_ui_automation_video.py:1837` (`test_...full_uuid`), `:1841`
(`test_uuid_stem_search_is_tried_after_full_uuid`), `:1861` (`terms == [FULL_UUID, "d6f1927a"]`),
`:1918` (`terms == [FULL_UUID, "d6f1927a", hint]`), `:1865`
(`test_no_search_box_skips_search_tiers_and_scrolls`).

---

## Deferred (Medium + Low — log as issues, not blockers)

1. **S11** — thumbnail-URL drift is a pre-existing exposure, unchanged by this plan.
2. **S12 / S16** — WAF and concurrency effects are observational; record in live verification, do not gate the merge.
3. **Proposal 3** (port hint derivation to the image path) — blocked on the T0 alt-text experiment. File as a follow-up issue with the experiment's result attached.
4. **The 4 s viewport wait** (`:2224`) is paid per ref on every lookup and was not raised by #529. If T0's telemetry shows it dominating on fast cohorts, file separately.
5. **Scroll cost** (350 ms/step, stall-bounded at 3, ceiling 200) becomes the sole pre-upload path. If `resolved_by` telemetry shows scroll depth dominating, that is the next bottleneck — a new issue, not this plan.

---

## Suggested BDD scenarios (`tests/features/media_picker_tiers.feature`)

```gherkin
Feature: Media picker resolves UUID references without dead search tiers
  The picker's full-UUID and UUID-stem search tiers are live-proven never to
  match (#287, #393). They must not run before the scroll fallback, and the
  reference-binding contract must be unchanged.

  Scenario: A bare UUID reference off the viewport is found by scrolling
    Given an image i2i generation with one bare "--ref <uuid>"
    And the reference tile is not in the picker's initial viewport
    When the transport binds the reference
    Then no search term is typed before the grid is scrolled
    And the tile is attached from the scroll tier
    And the attach event reports resolved_by "scroll"

  Scenario: An unreachable reference still refuses to generate without it
    Given an image i2i generation with one bare "--ref <uuid>"
    And the reference tile is absent from the picker entirely
    When the transport binds the reference
    Then the demoted UUID search tiers are attempted after the scroll
    And the recorded local file is uploaded as the fallback
    And the generation never proceeds without the reference

  Scenario: A frame-slot UUID that cannot be located fails pre-generation
    Given a video i2v generation with an initial frame given as a media UUID
    And the asset is absent from the picker entirely
    When the transport binds the frame
    Then a TransportTimeoutError naming the slot and the UUID is raised
    And the process exits with code 9
    And no generation is submitted

  Scenario: A picker cohort with no search box is never blocked by search
    Given an image i2i generation with one bare "--ref <uuid>"
    And the picker variant renders no search input
    When the transport binds the reference
    Then no search input is filled at any point
    And the grid is scrolled to locate the tile
```

---

## Known-issues cross-reference

| Entry | Relationship |
|---|---|
| `KNOWN_ISSUES.md:118` — *i2v frame-slot picker selection by UUID — RESOLVED live 2026-07-11* | **Must not regress.** S10 pins its contract. The reorder keeps `_select_existing_asset` as the single resolution point, so the resolution stands. |
| `#174` — full-page media-library drift (picker variant with no search box) | **Mitigated, differently.** Today the `None` return is observed on the first search tier; after the reorder the image path never probes pre-scroll. S5/S6 cover both halves. |
| `#282` / `#287` — virtualised grid scrolling, progress-bounded | **Load-bearing.** Scroll becomes the *only* pre-upload resolution path for bare UUID refs. Its correctness is now more critical, not less. |
| `#393` — `image i2i --ref <UUID>` bound nothing and hard-failed | **Contract preserved.** The `local_path` upload rescue is the terminal fallback and is untouched. |
| `#287` CHANGELOG entry (`CHANGELOG.md:1049`) | **Superseded in part.** It states the UUID tiers "are kept as cheap first attempts". The new entry must name #287 and record the demotion, or the next agent restores them. |

## Open empirical contradiction (gates proposal 3 only)

`#287` asserts picker tile `alt` carries the **generation prompt** (basis for the
`search_hints` tier). `#393`'s later live DOM capture (2026-07-27) recorded
`alt="Box tied with crimson ribbon"` — a **short Flow-authored caption**, and
`cli_image.py:258`, `errors.py:649-660`, `ui_automation_video.py:2137` all encode
that reading. Both cannot hold of the same picker.

This does **not** block proposals 1/2/4 — the ~14.9 s saving is arithmetic from
`_search_picker_for_tile` and is real either way. It blocks only the *claim* that
hints now hit first, and all of proposal 3. `_PICKER_DOM_DUMP_JS`
(`ui_automation_video.py:616-633`) already captures the first three tiles'
`outerHTML` truncated to 500 chars, which includes the `<img alt="…">` — so any
existing `debug_picker_dom_*.json` from a prior miss may already answer it
without spending a live round.
