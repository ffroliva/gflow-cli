# Style Variants Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature style-variants` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Allow `movie.toml` to express a global visual style system with named variants, so channel-format videos can define a style arc (e.g. monochrome → warm) once and apply it per-scene without repeating style text in every scene's action.

**Architecture:** Three modules change: `composition.py` (StyleSpec gains prefix/suffix/variants, compose_prompt gains style resolution), `movie_manifest.py` (parser gains [style.variants.*] and per-scene style_variant/style_suffix), `cli_movie.py` (template + dry-run output shows resolved style). Handoff schema gains `style_applied` per clip. State file gains `style_hash` per scene for resume detection. No new modules. No transport or auth changes.

**Predict verdict:** GO — confidence 8/10

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Prompt hash must include full composition (prefix + variant + scene suffix) | Hash the *resolved* prompt string, not just scene action |
| High | `scene.style_variant` naming collision with existing `scene.variant` (character) | Use distinct field name `style_variant`; document clearly |
| Medium | Old state files lack `style_hash` field | Default to None → always re-run (safe fallback) |

---

## File structure

### New files
```
tests/composition/test_style_variants.py
  Unit tests for style variant composition, validation, and handoff
tests/cli/test_movie_manifest_style_variants.py
  Unit tests for movie.toml parsing of [style.variants.*] and per-scene style fields
```

### Modified files
```
src/gflow_cli/composition.py
  StyleSpec: add prefix, suffix, variants fields
  Scene: add style_variant, style_suffix fields
  compose_prompt: apply prefix/suffix/variant resolution
  build_handoff: record style_applied per clip
  New: resolve_style() helper, prompt_hash() helper

src/gflow_cli/movie_manifest.py
  _parse_style: parse prefix, suffix, variants sub-tables
  _parse_scene: parse style_variant, style_suffix fields
  SceneState: add style_hash field
  MovieState: round-trip style_hash

src/gflow_cli/cli_movie.py
  Template: document [style.variants.*] usage
  Dry-run output: show resolved style per scene

docs/schemas/movie-handoff.schema.json
  Add style_applied field to clip schema

tests/composition/test_compose_prompt.py
  Add tests for prefix/suffix/variant composition
```

---

## Task 1 — Test scaffold (red tests)

**What:** Write all failing tests for StyleSpec extensions, compose_prompt style resolution, movie.toml parsing, handoff style_applied, and prompt hash.

**Files:**
- `tests/composition/test_style_variants.py` — new file
- `tests/cli/test_movie_manifest_style_variants.py` — new file
- `tests/composition/test_compose_prompt.py` — extend with style variant tests

**Steps:**
- [ ] Create `tests/composition/test_style_variants.py` with tests for:
  - StyleSpec with prefix/suffix/variants parses correctly
  - compose_prompt applies prefix before and suffix after composed text
  - compose_prompt applies variant.suffix when scene.style_variant matches
  - compose_prompt falls back to base suffix when no variant specified
  - compose_prompt applies scene.style_suffix after base/variant suffix
  - compose_prompt with style_variant="none" skips all style suffixes
  - prompt_hash changes when style suffix changes
  - prompt_hash changes when prefix changes
  - prompt_hash changes when scene.style_suffix changes
  - build_handoff records style_applied per clip
- [ ] Create `tests/cli/test_movie_manifest_style_variants.py` with tests for:
  - movie.toml with [style] prefix and suffix parses correctly
  - movie.toml with [style.variants.X] sub-tables parses correctly
  - scene with style_variant = "name" parses correctly
  - scene with style_suffix = "text" parses correctly
  - scene with style_variant referencing unknown variant raises ConfigurationError
  - scene with style_variant = "none" parses correctly
  - old movie.toml without [style.variants] still works (backward compat)

**Tests created (red):**
- [ ] test_style_spec_prefix_suffix — StyleSpec with prefix/suffix
- [ ] test_compose_prompt_prefix_before — prefix prepended
- [ ] test_compose_prompt_suffix_after — suffix appended
- [ ] test_compose_prompt_variant_suffix — variant suffix replaces base
- [ ] test_compose_prompt_fallback_to_base — no variant → base suffix
- [ ] test_compose_prompt_scene_style_suffix — scene suffix appended last
- [ ] test_compose_prompt_style_none — style_variant="none" skips suffixes
- [ ] test_prompt_hash_changes_on_suffix_edit — hash detects suffix change
- [ ] test_prompt_hash_changes_on_prefix_edit — hash detects prefix change
- [ ] test_handoff_style_applied — build_handoff records style_applied
- [ ] test_parse_style_prefix_suffix — TOML parsing
- [ ] test_parse_style_variants — TOML parsing of sub-tables
- [ ] test_parse_scene_style_variant — per-scene field
- [ ] test_parse_scene_style_suffix — per-scene field
- [ ] test_unknown_style_variant_raises — ConfigurationError
- [ ] test_style_variant_none_parses — "none" is valid

---

## Task 2 — StyleSpec model changes

**What:** Add `prefix`, `suffix`, and `variants` fields to `StyleSpec` in `composition.py`.

**Files:**
- `src/gflow_cli/composition.py`

**Steps:**
- [ ] Add `prefix: str | None = None` to StyleSpec
- [ ] Add `suffix: str | None = None` to StyleSpec
- [ ] Add `variants: Mapping[str, str] = field(default_factory=dict)` to StyleSpec
- [ ] Add `resolve_suffix(self, variant_name: str | None) -> str | None` method that returns variant.suffix if variant specified, else self.suffix
- [ ] Verify tests from Task 1 pass for StyleSpec parsing

---

## Task 3 — Scene model changes

**What:** Add `style_variant` and `style_suffix` fields to `Scene` in `composition.py`.

**Files:**
- `src/gflow_cli/composition.py`

**Steps:**
- [ ] Add `style_variant: str | None = None` to Scene
- [ ] Add `style_suffix: str | None = None` to Scene
- [ ] Verify Scene is still a frozen dataclass

---

## Task 4 — Composition rule implementation

**What:** Implement the deterministic style composition in `compose_prompt` and add `prompt_hash` helper.

**Files:**
- `src/gflow_cli/composition.py`

**Steps:**
- [ ] Add `_resolve_style_suffix(style, scene)` helper: if scene.style_variant == "none" → None; elif scene.style_variant → style.resolve_suffix(scene.style_variant); else → style.suffix
- [ ] Modify `compose_prompt` to prepend `style.prefix` (if set) before the composed parts
- [ ] Modify `compose_prompt` to append resolved suffix + scene.style_suffix after the composed parts
- [ ] Add `prompt_hash(prompt: str) -> str` function: SHA-256 hex digest of the composed prompt
- [ ] Verify all tests from Task 1 pass

---

## Task 5 — Movie manifest parsing

**What:** Extend `_parse_style` and `_parse_scene` to handle new fields.

**Files:**
- `src/gflow_cli/movie_manifest.py`

**Steps:**
- [ ] Extend `_parse_style` to read `prefix` (str) and `suffix` (str) from the [style] table
- [ ] Add `_parse_style_variants(data)` to parse [style.variants.*] sub-tables into a dict[str, str]
- [ ] Pass variants to StyleSpec constructor
- [ ] Extend `_parse_scene` to read `style_variant` (str or None) and `style_suffix` (str or None)
- [ ] Validate style_variant against defined variants (if not "none" and not None)
- [ ] Verify all parsing tests from Task 1 pass

---

## Task 6 — Handoff schema update

**What:** Add `style_applied` to the handoff manifest per clip.

**Files:**
- `src/gflow_cli/composition.py` (build_handoff)
- `docs/schemas/movie-handoff.schema.json`

**Steps:**
- [ ] In `_build_handoff_clip`, compute `style_applied` from the resolved style + variant + suffix
- [ ] Add `style_applied` field to clip dict: `{"variant": ..., "suffix": ..., "scene_suffix": ...}`
- [ ] Update `movie-handoff.schema.json` to include `style_applied` in clip properties
- [ ] Verify handoff tests pass

---

## Task 7 — State file style_hash

**What:** Store prompt hash per scene in `-state.json` for resume detection.

**Files:**
- `src/gflow_cli/movie_manifest.py` (SceneState)
- `src/gflow_cli/cli_movie.py` (runner)

**Steps:**
- [ ] Add `style_hash: str | None = None` to SceneState
- [ ] Update SceneState.to_dict/from_dict to round-trip style_hash
- [ ] In cli_movie.py runner, compute prompt_hash after compose_prompt and store in SceneState
- [ ] On resume, compare stored style_hash with newly computed hash; re-run scene if different
- [ ] Verify state round-trip tests pass

---

## Task 8 — CLI template and dry-run output

**What:** Update the movie template and dry-run display to show style information.

**Files:**
- `src/gflow_cli/cli_movie.py`

**Steps:**
- [ ] Update `_TEMPLATE` to include example [style.variants.*] usage
- [ ] In dry-run output, show resolved style variant and suffix per scene
- [ ] Verify template renders correctly

---

## Task 9 — Documentation

**What:** Update docs for the new style variant feature.

**Files:**
- `docs/MOVIE.md` (or USAGE.md movie section)
- `CHANGELOG.md`

**Steps:**
- [ ] Document [style] prefix/suffix/variants in MOVIE.md
- [ ] Document per-scene style_variant and style_suffix
- [ ] Document composition order
- [ ] Document resume behavior (prompt hash)
- [ ] Add [Unreleased] entry to CHANGELOG.md

---

## Task 10 — Full gates + commit

**What:** Run all quality gates, fix any issues, commit.

**Steps:**
- [ ] Run `/gflow:check` (ruff, format, pyright, pytest)
- [ ] Fix any failures
- [ ] Commit all changes
- [ ] Verify CI green

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`MOVIE.md` style variants section)
- [ ] BDD feature file covers all Critical + High scenarios from SCENARIO.md
- [ ] No `# TODO` in diff without a tracked issue link
