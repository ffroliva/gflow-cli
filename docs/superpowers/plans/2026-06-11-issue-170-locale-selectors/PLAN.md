# Locale-Free Picker Include Selectors (Issue #170) Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature issue-170-locale-selectors` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** `--reference-entity` (image t2i), movie R2V entity attach, and Vozes voice attach work on
every Flow account locale — not just pt-BR — by replacing the two hardcoded "Incluir no comando"
selectors with icon-first, text-fallback cascades.

**Architecture:** Only `ui_automation_video.py` (selector constants + the two attach helpers) and
`ui_automation.py` (new image-side `referenceEntities` submit backstop) change. The fix follows the
established `UPLOAD_MEDIA_BUTTON` tiering doctrine: locale-free anchor (Material Symbols ligature,
scoped to the open menu/dialog) as Tier 1, multi-locale text (`pt`/`ru`/`en`) as Tier 2 — the same
pattern as `_ONBOARDING_TEXT_SELECTORS` (14-locale fallback behind structural tiers). The failure
path keeps `RuntimeError` (no new exception class — minimal bugfix diff) but the message becomes
locale-neutral with a remediation hint, and the picker dialog is closed before raising so a pooled
Page is never returned dirty. Wire shape, auth, data layer: untouched.

**Predict verdict:** skipped — approach is settled selector doctrine (PR #60 / PR #127 tiering);
`/gflow:scenario` run 2026-06-11 instead (see `SCENARIO.md` in this directory).

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | pt-BR regression for existing users | pt text retained in Tier 2; credit-free route-abort live run on denon82 before merge (Task 7) |
| Critical | Icon tier matches a wrong `add` element → image t2i silently degrades to text-only (image path has no submit backstop, only a structlog summary at `ui_automation.py:1688`) | Scope icon selector to the open menu portal; add image-side `WireFormatError` backstop (Task 5) |
| High | Vozes `PICKER_INCLUDE_BUTTON` may carry no ligature icon (recon gap) | Local recon on denon82 first (Task 1) — icons are locale-invariant, so pt-BR DOM answers it; text list stays primary for that constant until icon confirmed |
| High | Page-pool contamination when attach fails mid-dialog | Escape/close cleanup in the failure path before raising (Task 4) |
| Medium | Silent tier drift (icon tier dies, text tier masks it) | `include_selector_tier` structlog event on every successful attach (Task 4) |

---

## File structure

### New files
```
tests/features/locale_picker_include.feature
  BDD scenarios from SCENARIO.md (ru-locale attach, pt regression guard, both-tiers-miss, wire backstop)
tests/api/transports/fixtures/picker_context_menu_ru.html  (or inline fixture in test module — implementer's choice)
  Russian-locale picker context-menu DOM fixture (items: add «Добавить в запрос», content_cut, content_copy, delete)
```

### Modified files
```
src/gflow_cli/api/transports/ui_automation_video.py
  PICKER_INCLUDE_BUTTON / PICKER_CONTEXT_INCLUDE → tiered cascades; _attach_character_entities
  (neutral error, dialog cleanup, tier logging); _attach_reference_audio (per Task 1 recon outcome)
src/gflow_cli/api/transports/ui_automation.py
  Image submit backstop: raise WireFormatError when captured batchGenerateImages body lacks the
  requested referenceEntities
tests/api/transports/test_ui_automation_video.py
  Lines ~1046-1080: pt-literal assertions → locale-neutral + new tier/cascade/cleanup tests
KNOWN_ISSUES.md
  §349 "selectors locale-agnostic — Phase 5 complete" amended: picker include constants were the
  exception; resolved by this fix (issue #170)
CHANGELOG.md
  [Unreleased] Fixed entry referencing #170; note loop-closure of character-scenario.md:35 prediction
```

---

## Task 1 — Local recon: Vozes include button + context-menu structure (credit-free)

**What:** Close the recon gap on the pt-BR denon82 account — ligature icons are locale-invariant,
so the pt DOM answers whether (a) the Vozes "Incluir no comando" button carries an icon, and
(b) the right-click context menu renders as `[role='menu'] > [role='menuitem']` in a portal.

**Files:**
- `scripts/dev/spike_issue170_picker_locale_recon.py` — new credit-free recon harness (spike family)

**Steps:**
- [x] Run the dump/spike harness against the resource picker on denon82 (headed real-Chrome profile, zero credits) — `scripts/dev/spike_issue170_picker_locale_recon.py`, run 2026-06-11 (`_spike_out/spike_issue170_picker_locale_recon_20260611_234140.json`)
- [x] Record: context-menu container role + `data-state`, menuitem roles, exact ligature names (`add` expected)
- [x] Record: Vozes include button markup — icon ligature present? structural anchor available (dialog-footer primary button)?
- [x] Decide Tier 1 for `PICKER_INCLUDE_BUTTON` based on findings; note decision below

**Recon findings (verified live on denon82, pt-BR, 2026-06-11):**
- Context menu: `<div role='menu' data-state='open'>` with 4 `[role='menuitem']` items, ligatures `add` («Incluir no comando»), `content_cut`, `content_copy`, `delete` — identical icon set to the ru report on #170. **Tier 1 for `PICKER_CONTEXT_INCLUDE`:** `[role='menu'][data-state='open'] [role='menuitem']:has(i.google-symbols:text-is('add'))` — `add` unique within the menu.
- Vozes include button: **NO ligature icon** (text-only `<button>Incluir no comando</button>`), but it is the **lone iconless button inside the open picker dialog** (all other dialog buttons carry ligatures: tabs, `play_arrow` preview, `arrow_drop_down` sort). Same structural situation as the documented `ADD_TO_PROMPT_DIALOG` pattern. **Decision:** multi-locale text tier primary (pt/ru/en) + structural fallback (lone iconless dialog button).
- Side observation: clicking a Vozes `[role='option']` closed the picker dialog outright — the include button shows for an already-selected resource state; `_attach_reference_audio`'s search-then-click-tile flow is unaffected.

---

## Task 2 — Unit test scaffold (red)

**What:** Red unit tests pinning the new selector cascade composition, locale-neutral failure, and
tier telemetry. Updates the two pt-literal assertions.

**Files:**
- `tests/api/transports/test_ui_automation_video.py` — new + updated tests

**Steps:**
- [x] Replace `assert "Incluir no comando" in selectors` with cascade-composition asserts: icon tier (`text-is('add')`) is what a fully-matching page uses
- [x] Replace `pytest.raises(RuntimeError, match="Incluir no comando")` with a locale-neutral match (`"include action"`); assert message carries the entity id and NOT the pt literal
- [x] ~~ru-locale DOM fixture~~ → replaced by the established `TestSelectorLocaleInvariance` string-pinning pattern (PR #127): Tier 1 locked verbatim + no-localized-text invariant; behavioral ru coverage lands in BDD (Task 3) and the reporter's live run (Task 7)
- [x] Add test: failure path presses Escape before raising (no open dialog returns to the pool)
- [x] Add test: `include_selector_tier` structlog event emitted with `tier` + `surface` keys on successful attach (both context-menu and Vozes surfaces)
- [x] Verify all new tests are red — 12 failed as expected, 2026-06-11

**Design contract pinned by the tests (Task 4 must implement):**
- `PICKER_CONTEXT_INCLUDE: tuple[str, str]` — `("[role='menu'][data-state='open'] [role='menuitem']:has(i.google-symbols:text-is('add'))", "<menu-scoped pt/ru/en captions>")`
- `PICKER_INCLUDE_BUTTON: tuple[str, str]` — `("<pt/ru/en button captions>", "[role='dialog'][data-state='open'] button:not(:has(i.google-symbols))")`
- Tiers probed sequentially (a flat comma-join resolves in DOM order and can't report which tier matched); matched tier emitted as `ui_automation_video.include_selector_tier` (tier=icon|text|structural, surface=context_menu|vozes_button)

**Tests created (red):**
- [x] `TestPickerIncludeSelectorLocaleInvariance` — 7 static-invariant tests (tier shape, Tier-1 verbatim lock, no localized text in Tier 1, pt/ru/en coverage, menu/dialog scoping)
- [x] `test_attach_right_clicks_personagens_entity_tile` — updated: icon tier drives the match
- [x] `test_attach_logs_which_selector_tier_matched` — tier=icon, surface=context_menu
- [x] `test_attach_raises_when_context_menu_absent` — locale-neutral message, carries entity id
- [x] `test_attach_failure_closes_picker_before_raising` — Escape cleanup
- [x] `TestAttachReferenceAudio::test_attach_audio_logs_selector_tier` — tier=text, surface=vozes_button
- [x] `TestAttachReferenceAudio::test_attach_audio_raises_locale_neutral_when_button_absent`

---

## Task 3 — BDD scaffold (red)

**What:** Gherkin feature file from SCENARIO.md's suggested scenarios; step stubs mirror runtime
signatures (memory: BDD stubs break with TypeError when kwargs drift — update fakes in same commit).

**Files:**
- `tests/features/locale_picker_include.feature` — 3 scenarios
- `tests/features/test_locale_picker_include_steps.py` — step bindings (seam: `cli_image._run_t2i`, per repo BDD convention)

**Steps (adapted to the repo's BDD seam — mocked at `_run_t2i`, so locale/selector
mechanics live in the Task-2 unit tests; BDD pins the CLI contract):**
- [x] Scenario: t2i passes the reference entity through to the runner (exit 0, `req.reference_entities`/`_names` recorded, image written)
- [x] Scenario: include-action timeout → typed `TransportTimeoutError`, exit 9, output contains the locale-neutral phrase and NOT the pt caption
- [x] Scenario: submit backstop `WireFormatError` → exit 7, "File a bug" (drives Task 5)
- [x] Scenarios pass by construction at this seam (stubs raise the typed errors) — they are regression contracts for the exit-code mapping discovered in Task 3: an untyped `RuntimeError` would surface as privacy-hashed "Unexpected error." exit 1, burying the remediation hint. **This upgraded the design: the attach failure must raise `TransportTimeoutError` (exit 9, instance `remediation_hint`), not RuntimeError** — Task-2 tests updated accordingly in the same commit.

**Tests created:**
- [x] `locale_picker_include.feature` — 3 scenarios above (green at seam; unit red drives Task 4)

---

## Task 4 — Selector cascade + attach-helper hardening (makes Task 2/3 green except backstop)

**What:** The core fix in `ui_automation_video.py`.

**Files:**
- `src/gflow_cli/api/transports/ui_automation_video.py`

**Steps:**
- [x] `PICKER_CONTEXT_INCLUDE` → Tier 1 `[role='menu'][data-state='open'] [role='menuitem']:has(i.google-symbols:text-is('add'))` (container per Task 1 recon); Tier 2 menu-scoped text for `'Incluir no comando'`, `'Добавить в запрос'`, `'Add to prompt'`
- [x] `PICKER_INCLUDE_BUTTON` → multi-locale text primary + lone-iconless-dialog-button structural fallback (recon: no ligature on the button; rationale in the constant's comment)
- [x] New `_resolve_include_action` helper: sequential tier probe, `include_selector_tier` event (tier + surface), screenshot + double-Escape cleanup + typed `TransportTimeoutError` with remediation hint on exhaustion — shared by both attach helpers
- [x] Docstrings reference the caption generically; spike_movie_attach_payload.py updated for the tuple constant
- [x] Task 2 unit tests green; BDD scenarios green (72 passed)

**Tests:**
- [x] All Task 2 tests green (68 unit + 3 BDD + collision guard)
- [x] `pyright src` clean (0 errors, whole tree, worktree venv)
- [x] ruff check + format clean on all touched files

---

## Task 5 — Image-side referenceEntities submit backstop

**What:** Mirror the video path's `_assert_entities_attached` defense on the image path: when
`request.reference_entities` is set, the captured `batchGenerateImages` submit must contain every
requested entity id, else raise `WireFormatError` (no silent text-only generation).

**Files:**
- `src/gflow_cli/api/transports/ui_automation.py` — backstop using the already-captured request body (`_summarize_batch_request_body` machinery at ≈ line 543/1688)
- `tests/api/transports/test_ui_automation.py` — unit tests

**Steps:**
- [ ] Extract requested-vs-captured entity-id comparison from the captured submit body summary
- [ ] Raise `WireFormatError` with discovery payload (redaction-safe — ids only, never body bytes) when any requested entity id is missing
- [ ] Unit tests: missing-entity raises; present-entity passes; no-entities-requested path untouched
- [ ] BDD scenario 4 green

**Tests:**
- [ ] `test_image_submit_backstop_raises_when_entity_missing`
- [ ] `test_image_submit_backstop_passes_when_entities_present`
- [ ] `test_image_submit_backstop_inert_without_reference_entities`

---

## Task 6 — Docs + changelog

**What:** Close the documentation loop.

**Files:**
- `KNOWN_ISSUES.md` — amend §"selectors locale-agnostic — Phase 5 complete" (≈ line 349): picker include constants were the exception, resolved via #170
- `CHANGELOG.md` — `[Unreleased]` → Fixed: issue #170, all three affected paths named
- `docs/CHARACTER.md` / `docs/MOVIE.md` — replace "Incluir no comando" prose with locale-generic wording ("the include-in-prompt context-menu action") per docs-language-agnostic rule

**Steps:**
- [ ] KNOWN_ISSUES entry updated
- [ ] CHANGELOG entry (note: predicted by `docs/superpowers/character-scenario.md:35`, fixture now added)
- [ ] Localized UI strings scrubbed from user-facing docs (raw `_RECON.md` may keep them)

---

## Task 7 — Live verification + gates + PR

**What:** Prove both locales, run gates, open the PR.

**Steps:**
- [ ] Credit-free route-abort verification on denon82 (pt-BR): attach fires, captured payload carries `referenceEntities` (reuse `spike_movie_attach_payload.py` harness)
- [ ] `/gflow:check` green (ruff lint + format, `pyright src`, scoped pytest; trust CI for full sweep)
- [ ] `uv lock --check` (no dep changes expected — cheap guard)
- [ ] PR from `bugfix/issue-170-locale-picker-selectors` → `develop`; body links #170 + SCENARIO.md; `Closes #170`
- [ ] Post candidate-build instructions on #170 for the reporter's ru-locale test (their offer); reporter confirmation OR maintainer ru-profile run before merge
- [ ] After reporter/ru confirmation: merge per branch workflow

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`KNOWN_ISSUES.md`, `docs/CHARACTER.md` / `docs/MOVIE.md` locale-generic wording)
- [ ] BDD feature file covers all Critical + High scenarios from `SCENARIO.md`
- [ ] pt-BR live route-abort verification recorded; ru-locale confirmation from reporter (or equivalent)
- [ ] No `# TODO` in diff without a tracked issue link
