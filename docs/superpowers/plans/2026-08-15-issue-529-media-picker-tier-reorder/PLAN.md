# Media Picker Tier Reorder Implementation Plan (#529)

> **For agentic workers:** Run `/gflow:status --feature issue-529-media-picker-tier-reorder`
> to find the next unchecked task. Implement one task at a time. Run `/gflow:check`
> before every commit.

**Goal:** A bare `--ref <uuid>` (and a UUID frame slot) stops paying ~15 s of
guaranteed-dead picker searching per reference before the scroll fallback does the
real work — with zero change to which references can be resolved.

**Architecture:** One transport-internal change. `VideoGenerationMixin._select_existing_asset`
(`src/gflow_cli/api/transports/ui_automation_video.py:2211-2321`) is the single
convergence point for both the image `--ref` path and the video frame-slot path —
they differ only in what they pass in (`display_name=""` / `search_hints=()` on the
image side). The candidate cascade splits in two: `(display_name, *search_hints)`
runs **before** the scroll fallback; `(media_id, uuid_stem)` is retried **only after**
the scroll misses, immediately before the caller's upload fallback. Nothing crosses a
module boundary; no new module, port, flag, env var, exit code, or DOM selector.

**Predict verdict:** CAUTION — confidence 6/10 (2026-08-15, 5-persona). The CAUTION is
scope-driven: proposals 1/2/4 of #529 are GO-grade, proposal 3 is gated behind an
unresolved empirical contradiction and is **out of scope here**.

**Scenario input:** [`SCENARIO.md`](SCENARIO.md) — 17 scenarios across 9 active
dimensions; 8 must-cover before merge.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| High | "Delete the tiers" would lose reachability on any cohort where Flow *does* index UUIDs — evidence is from one cohort's live rounds, and this project reverse-engineers a blackbox (AGENTS.md) | **Demote, don't delete.** The UUID tiers retry after the scroll misses, so every outcome reachable today stays reachable; the 15 s is paid only on lookups already headed for upload/failure. |
| High | Silent behaviour drift — five existing tests pin the old tier order and CHANGELOG:1049 documents it as intentional | TDD red step rewrites the five assertions (T2); T6 supersedes the #287 CHANGELOG entry by name so the tiers are not restored by a future agent. |
| High | Operator loses the only visible progress signal (three searches typed) and sees silence for the same tens of seconds | `resolved_by` telemetry (T5) ships **in the same PR**, not as a follow-up. |
| Medium | The picker is left filtered/scrolled on a pooled Page when the post-scroll retry misses | Clear the search before returning `False`, mirroring `_try_select_existing_by_filename:2113`. Covered by S7. |
| Medium | `#174` cohort (no search box) now reaches the `None` branch from a different call site | S5/S6 pin both halves; the retry stays presence-guarded. |
| Medium | The claimed benefit of reordering `search_hints` ahead of the UUID tiers is unproven if #393's caption reading is correct | T1 checks existing DOM dumps first. The CHANGELOG must not claim "hints now hit first" until observed. The **saving** is arithmetic and holds either way. |
| Low | Scroll becomes the sole pre-upload path; its cost is unmeasured on deep grids | `resolved_by` makes it visible. Out of scope to fix; file a follow-up if telemetry shows it dominating. |

---

## Scope

**In scope** — #529 proposals 1, 2, 4:
- Reorder the candidate cascade (one tuple, serving both paths).
- Retry the demoted UUID tiers after the scroll fallback.
- Emit `resolved_by` on the existing attach events.

**Out of scope** — #529 proposal 3 (port #287's hint derivation to the image path):
blocked on T1's evidence. File as a follow-up issue with T1's result attached.
Also out of scope: the 4 s viewport wait (`:2224`), scroll-step tuning, and any
`--ref` CLI surface change.

---

## File structure

### New files

```
tests/features/media_picker_tiers.feature
  BDD scenarios for the reordered cascade and the preserved binding contract
tests/features/test_media_picker_tiers_steps.py
  pytest-bdd step definitions against a fake Page
docs/superpowers/spikes/2026-08-15-picker-tile-alt-text.md
  T1 evidence note: does the picker tile's alt carry the prompt or a caption?
```

### Modified files

```
src/gflow_cli/api/transports/ui_automation_video.py
  _select_existing_asset: split cascade, post-scroll UUID retry, resolved_by
tests/api/transports/test_ui_automation_video.py
  rewrite the five tier-order assertions; add the new-order and retry tests
CHANGELOG.md
  [Unreleased] entry naming #529 and superseding the #287 tier claim
src/gflow_cli/cli_image.py            (T6, only if T1 resolves the contradiction)
src/gflow_cli/errors.py               (T6, only if T1 resolves the contradiction)
```

---

## Task 1 — Evidence: does a picker tile's `alt` carry the prompt or a caption?

**What:** Settle the #287-vs-#393 contradiction cheaply, before any wording is
committed. **Non-blocking for T2–T5** — start those in parallel if convenient.

**Files:**
- `docs/superpowers/spikes/2026-08-15-picker-tile-alt-text.md` — evidence note

**Steps:**
- [ ] Search existing `debug_picker_dom_*.json` dumps in prior out-dirs first — `_PICKER_DOM_DUMP_JS` (`ui_automation_video.py:616-633`) already captures the first three tiles' `outerHTML` truncated to 500 chars, which includes `<img alt="…">`. The answer may already be on disk, for zero live cost.
- [ ] If no dump exists: open the picker on a project with a known generated asset, dump one tile's `alt`, and compare to `queries.get_asset_prompt(db_path, media_id)` for the same id.
- [ ] Record the verdict, the raw `alt` string, the media id, and the date in the spike note.
- [ ] If `alt` is a **caption**: file the follow-up issue recording that `search_hints` is also a dead tier, and that #529 proposal 3 is refuted rather than deferred.
- [ ] If `alt` is the **prompt**: file the follow-up issue for proposal 3 (derive hints on the image path) with the evidence attached.

**Tests created:** none — this is an evidence task, no production code.

**Done when:** the spike note states one reading with a live artifact behind it, and a follow-up issue exists either way.

---

## Task 2 — Unit test scaffold (red)

**What:** Rewrite the five assertions that pin the current tier order, and add the
new-order, post-scroll-retry, and no-search-box tests. No production code.

**Files:**
- `tests/api/transports/test_ui_automation_video.py` — rewrite `:1837`, `:1841`, `:1861`, `:1865`, `:1918`

**Steps:**
- [ ] Replace `test_uuid_stem_search_is_tried_after_full_uuid` — the UUID tiers no longer run pre-scroll.
- [ ] Rewrite the `terms == [FULL_UUID, "d6f1927a"]` assertion (`:1861`) to assert **zero** `press_sequentially` awaits before the first scroll on a bare-UUID ref.
- [ ] Rewrite the `terms == [FULL_UUID, "d6f1927a", hint]` assertion (`:1918`) to `terms == [hint]` pre-scroll.
- [ ] Keep `test_no_search_box_skips_search_tiers_and_scrolls` (`:1865`) passing, and add its post-scroll-retry twin.
- [ ] Do **not** touch `test_search_tier_event_reports_term_and_rendered_count` (`:2560`) — `picker_search_tier`'s payload is unchanged.

**Tests created (red):**
- [ ] `test_bare_uuid_ref_scrolls_before_any_search` — S1: no search typed pre-scroll
- [ ] `test_uuid_tiers_retried_after_scroll_miss` — S2: `terms == [FULL_UUID, "d6f1927a"]` **after** `_scroll_picker_grid_until_rendered` returns `False`
- [ ] `test_hint_tier_runs_before_scroll_on_frame_path` — S10: `terms == [hint]` pre-scroll
- [ ] `test_post_scroll_retry_stops_on_absent_search_box` — S6: `None` breaks the retry, no `.fill()` against an absent element
- [ ] `test_picker_search_cleared_before_returning_false` — S7: pooled Page not handed back filtered
- [ ] `test_tile_match_stays_uuid_in_src` — S3: an imprecise hint surfacing extra tiles never attaches a wrong one
- [ ] `test_hyphenless_media_id_dedupes_stem` — S13: one retry pass, not two

---

## Task 3 — BDD scaffold (red)

**What:** The four Gherkin scenarios from `SCENARIO.md`, wired to step definitions.

**Files:**
- `tests/features/media_picker_tiers.feature` — the four scenarios
- `tests/features/test_media_picker_tiers_steps.py` — step definitions

**Steps:**
- [ ] Copy the four `Scenario:` blocks verbatim from `SCENARIO.md`.
- [ ] Reuse the existing fake-Page fixtures from `tests/api/transports/test_ui_automation_video.py` rather than inventing a second fake.
- [ ] Pin the exit-9 `TransportTimeoutError` message for the frame-slot scenario **byte-for-byte** against v0.57.1 (`ui_automation_video.py:1890-1897`).

**Tests created (red):**
- [ ] `A bare UUID reference off the viewport is found by scrolling` — S1
- [ ] `An unreachable reference still refuses to generate without it` — S2 (#393 contract)
- [ ] `A frame-slot UUID that cannot be located fails pre-generation` — S2/S10 (exit 9)
- [ ] `A picker cohort with no search box is never blocked by search` — S5

---

## Task 4 — Core change: split the cascade, retry after scroll

**What:** Make T2 and T3 green. The whole behavioural change lives in
`_select_existing_asset`.

**Files:**
- `src/gflow_cli/api/transports/ui_automation_video.py` — `_select_existing_asset:2211-2321`

**Steps:**
- [ ] Extract the tier loop into a small local helper so the pre-scroll and post-scroll passes share one implementation (including the `None` → break contract and the `attempted_search` bookkeeping).
- [ ] Pre-scroll candidates: `(display_name, *search_hints)`. Post-scroll candidates: `(media_id, uuid_stem)`. Keep the existing dedup so a hyphen-less id yields one term.
- [ ] Preserve the existing clear-search-before-scroll block (`:2276-2283`) — a filtered grid must never be scrolled.
- [ ] After the post-scroll retry misses, clear the search before falling through to the not-found telemetry, so the Page returns clean (S7).
- [ ] **Rewrite, do not delete,** the docstring (`:2231-2240`) and the tier commentary (`:2247-2256`): state that the UUID tiers are demoted to last-resort, cite #287's rounds and #393's 2026-07-27 capture, and say why they are retained rather than removed. This is the guard against a future agent restoring them.

**Tests:**
- [ ] All T2 unit tests green
- [ ] All T3 BDD scenarios green
- [ ] `tests/cli/test_cli_image_uuid_ref_enrichment.py` unchanged and green (#393 contract)
- [ ] `tests/cli/test_cli_video.py` hint-derivation tests unchanged and green

---

## Task 5 — Telemetry: `resolved_by`

**What:** Make the resolving tier visible at info level, replacing the progress
signal the removed searches used to provide.

**Files:**
- `src/gflow_cli/api/transports/ui_automation_video.py` — attach + not-found sites

**Steps:**
- [ ] Add `resolved_by` to the existing `image_ref_selected_existing` (`:2419`) and `frame_ref_attached` (`:1900`) events. **No new event name** — the picker surface already emits 14 from #287, and `docs/LIVE_VERIFICATION_*.md` treat these names as a contract.
- [ ] Label set, closed: `viewport` · `display_name` · `hint` · `scroll` · `uuid_retry` · `upload` · `not_found`. Exactly one per reference.
- [ ] Add `resolved_by="not_found"` to the existing `existing_asset_not_found` warning (`:2304`).
- [ ] **Label only.** Never add the winning search *term* to these events — on the video path it is a 6-word slice of a user prompt. `picker_search_tier`'s existing `term=` field is left exactly as-is, not widened.

**Tests:**
- [ ] `test_resolved_by_reports_scroll_tier` — S8
- [ ] `test_resolved_by_reports_upload_fallback` — S8
- [ ] `test_resolved_by_carries_no_search_term` — S9: assert the attach event payload contains no prompt text

---

## Task 6 — Docs, CHANGELOG, and contradiction reconciliation

**What:** Documentation is a first-class deliverable (AGENTS.md). Three source
docstrings currently assert a reading that T1 may overturn.

**Files:**
- `CHANGELOG.md` — `[Unreleased]`
- `src/gflow_cli/cli_image.py:258`, `src/gflow_cli/errors.py:649-660`, `src/gflow_cli/api/transports/ui_automation_video.py:2137` — only if T1 resolved the contradiction

**Steps:**
- [ ] CHANGELOG entry under `[Unreleased]`: name #529, state the measured saving (~14.9 s per unresolved reference; ~30 s per 2-ref generation), and state that reachability is unchanged because the tiers are demoted rather than deleted.
- [ ] The same entry must **name #287 and supersede** its claim (`CHANGELOG.md:1049`) that the UUID tiers "are kept as cheap first attempts". Without this the next agent re-adds them.
- [ ] Do **not** write "hints now hit first" unless T1 confirmed the prompt reading. If T1 confirmed the caption reading, say plainly that the hint tier's live value is unproven and link the follow-up issue.
- [ ] If T1 resolved the contradiction, align the three docstrings to the surviving reading and cite the spike note.
- [ ] No `docs/ARCHITECTURE.md` change needed — its Observability section documents boundary events (`error_raised`, `error_unhandled`) only, not transport events.
- [ ] No `CONFIGURATION.md` / `.env.template` change — no new env var or flag.

**Tests:**
- [ ] `uv run python scripts/ci/check_doc_links.py` green (merge gate)

---

## Task 7 — Gates and live verification

**What:** The Impeccable Routine, then real Flow. This touches a generation path, so
offline green is not "done" (AGENTS.md).

**Steps:**
- [ ] `/gflow:check` — hygiene, doc links, PII, ruff, format, pyright, pytest ≥ 80%
- [ ] `/gflow:branch-review` before opening the PR
- [ ] Live: `gflow image i2i` with **2 bare UUID refs** into a crowded project (the #287 repro shape). Capture the `resolved_by` events and the wall-clock delta against a v0.57.1 baseline run.
- [ ] Live: `gflow video i2v <uuid>` frame slot on the same project — confirm `frame_ref_attached` still fires and the exit-9 path is unchanged for a foreign UUID (`$0`, pre-generation).
- [ ] Record both in `docs/LIVE_VERIFICATION_*` with the measured saving. If the delta is not observed, say so — do not claim it from the arithmetic alone.
- [ ] `/gflow:sonar <PR>` green before calling the PR merge-ready.

**Acceptance signal:** `resolved_by="scroll"` on a ref that previously logged two
`picker_search_tier` misses first, with a measured wall-clock reduction of roughly
15 s per such reference.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` updated, naming and superseding #287's tier claim
- [ ] BDD feature file covers all four Critical/High scenarios from `SCENARIO.md`
- [ ] The eight must-cover items in `SCENARIO.md` each map to a passing test
- [ ] `resolved_by` emitted on every terminal outcome, carrying no prompt text
- [ ] T1's spike note committed and a follow-up issue filed for #529 proposal 3
- [ ] Live-verified per AGENTS.md; measured saving recorded, or its absence recorded
- [ ] SonarCloud gate green (zero new issues)
- [ ] No `# TODO` in the diff without a tracked issue link
