# Issue #174 — Library-UI Attach Drift Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature issue-174-library-ui-attach` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Restore entity attach (`gflow image t2i --reference-entity`, movie R2V) on accounts
with Flow's new full-page media-library UI — recon-first, with implementation explicitly gated
on recon findings.

**Architecture:** No code-shape decision yet by design (predict: Devil's Advocate — the A/B may
still be mutating; the fix may be a gesture/selector change, not a branch). If a branch proves
necessary, it lands as a shared `_detect_add_media_variant(page) -> "dialog" | "navigate"` static
helper on `VideoGenerationMixin` (race pattern: dialog-appears vs URL-changes, ~100–200 ms),
called on **every** attach (A/B can flap per page-load), single checked-out Page only (size-1
pool — never a 2nd checkout). The interim UX task touches only `errors.py` remediation text +
`KNOWN_ISSUES.md`.

**Predict verdict:** CAUTION — 7/10 (Architect 7.5 · Security 8 · Performance 7 · CLI UX 7 ·
Devil's Advocate 7). Recon: unanimous GO. Implementation: gated on Task 4.

**Scenario:** deferred to Task 4 — `/gflow:scenario` on an unknown gesture would be speculation;
it runs once recon fixes the fix shape.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| High | Staging context may be scoped to the library page's floating quick-create composer — navigating back to the editor discards it | Recon explicitly tests submit **from the floating composer** AND the navigate-back-then-submit path |
| High | WAF heat on denon82 (known-hot profile) from repeated spike runs | One disciplined recon run; no iterative debug loops; 24 h+ between reruns |
| Medium | A/B may mutate or roll back mid-work — variant code becomes stale debt | Task 4 gate: re-probe rollout scope (denon82 + promo-denon82) before committing to implementation |
| Medium | Affected users today get exit 7 with no context → duplicate bug reports on #174 | Task 1 ships the #174-aware remediation hint independently of the recon |
| Medium | reCAPTCHA/WAF behaviour on full-page library navigation unknown | Recon observes; REST-side injection of `referenceEntities` is a dead end (reCAPTCHA-gated; server would reject unstaged data) |

---

## File structure

### New files
```
scripts/dev/spike_issue174_library_ui_recon.py
  Credit-free recon spike: dialog-vs-navigate detection, floating-composer submit
  test, navigate-back submit test, library-page DOM dump, route-abort capture.
tests/api/test_wire_format_remediation.py  (or extend existing errors tests)
  Asserts the #174 remediation hint rides the typed WireFormatError.
```

### Modified files
```
src/gflow_cli/errors.py
  Entity-attach WireFormatError remediation hint names the library-UI A/B + issue #174.
KNOWN_ISSUES.md
  New § Open entry for the library-UI A/B (probe: dialog opens vs page navigates).
CHANGELOG.md
  [Unreleased] entry for the remediation-hint change.
src/gflow_cli/api/transports/ui_automation_video.py   (Task 4+, shape TBD)
src/gflow_cli/api/transports/ui_automation.py          (Task 4+, shape TBD)
```

---

## Task 1 — Interim UX: point exit-7 at issue #174 (committable now, no recon needed)

**What:** Affected users hitting the PR #173 backstops get an actionable message instead of
generic "file a bug".

**Files:**
- `src/gflow_cli/errors.py` — remediation hint on the entity-attach `WireFormatError` raises
  (or per-raise `remediation_hint=` at the two raise sites)
- `src/gflow_cli/api/transports/ui_automation_video.py:1424` — `_assert_entities_attached` raise
- `src/gflow_cli/api/transports/ui_automation.py:~2062` — `_assert_image_entities_attached` raise
- `KNOWN_ISSUES.md` — new § Open entry
- `CHANGELOG.md` — `[Unreleased]`

**Steps:**
- [x] Add `remediation_hint` naming the new-library-UI A/B and linking
      https://github.com/ffroliva/gflow-cli/issues/174 to both entity-attach raise sites
      (`ENTITY_ATTACH_DRIFT_HINT` constant in `ui_automation_video.py`, imported image-side)
- [x] Add `discovery={"entity_attach_context": "video" | "image"}` to the raises (telemetry)
- [x] `KNOWN_ISSUES.md` § Open entry with the variant probe: "does Add Media open a
      `[role='dialog']` or navigate to a full-page library?"
- [x] `CHANGELOG.md` `[Unreleased]` entry

**Tests:**
- [x] Unit test: hint text rides the typed error and surfaces in `to_problem_details()`
      (TDD red→green; one per surface: `test_backstop_error_carries_issue_174_hint_and_discovery`
      / `test_assert_error_carries_issue_174_hint_and_discovery`)
- [x] Existing backstop tests still green (tests/api/transports: 364 passed, 1 skipped;
      pyright src 0 errors; ruff clean — 2026-06-12)

---

## Task 2 — Recon spike script (credit-free; no live run in this task)

**What:** A purpose-built spike answering the make-or-break questions before any attach-code
change. Reuses the `spike_movie_attach_payload.py` route-abort harness and
`spike_issue170_picker_locale_recon.py` DOM-dump utilities.

**Files:**
- `scripts/dev/spike_issue174_library_ui_recon.py`

**Steps:**
- [ ] Phase A — variant detection: click Add Media, race `[role='dialog'][data-state='open']`
      appearance vs URL change; record which won + timing
- [ ] Phase B (new UI only) — gesture matrix, each with route-abort submit capture:
      1. right-click include on Personagens tile → submit **from the floating quick-create
         composer** on the library page
      2. right-click include → navigate back to the editor → submit from the editor composer
         (does staging survive navigation?)
      3. any new "use in prompt"-style affordance discovered in the DOM dump
- [ ] Phase C — DOM dump of the library page: sidebar items, tile structure
      (`data-tile-id`?), floating composer, all ligature icons (locale-invariant anchors)
- [ ] Capture asserts: request0 `referenceEntities` presence per gesture; JSON +
      screenshots to `scripts/dev/_spike_out/` (local only — never committed; PII)
- [ ] Windows: `PYTHONUTF8=1`; parameterize profile via env (memory: e2e scripts parameterize)

**Tests:** none (dev spike script, excluded from coverage like existing `spike_*.py`).

---

## Task 3 — Run recon on denon82 + rollout re-probe (ONE run; owner-gated)

**What:** Single disciplined live run. Requires the user's machine free (no other Chrome on the
profile) — headed real-Chrome strategy mandatory.

**Steps:**
- [ ] Run `spike_issue174_library_ui_recon.py` on **denon82** (new UI) — one pass through the
      gesture matrix
- [ ] Re-probe **promo-denon82**: still old dialog UI? (rollout-scope datapoint)
- [ ] Post findings to issue #174 (filtered: `request0_keys` / gesture verdicts, no raw payloads;
      screenshots only if redacted)
- [ ] Record findings summary in this plan under Task 4

**Verification:** the spike JSON shows, per gesture, whether `referenceEntities` rode the wire.

---

## Task 4 — GATE: decide the fix shape (no code until this is recorded)

**What:** Convert recon findings into the implementation decision. Record the decision + date
here, then run `/gflow:scenario issue-174-library-ui-attach` on the chosen shape and append
implementation tasks below.

**Decision matrix:**
- **(a) Gesture fix** — a working new-UI gesture exists and the old code path can reach it with
  selector/flow changes only → small PR, no variant branch.
- **(b) Variant branch** — both UIs must coexist → `_detect_add_media_variant` helper (race
  pattern, per-attach call), new-UI attach path, `ui_automation.library_ui_variant_detected`
  structlog event, `GFLOW_CLI_LIBRARY_UI_VARIANT` env override (`auto|old|new`) for testing.
- **(c) Hold** — A/B unstable or no working gesture found → KNOWN_ISSUES stays, re-probe in
  N days; Task 1's hint carries users meanwhile.

**Steps:**
- [ ] Decision recorded: ______ (a / b / c) on ______
- [ ] `/gflow:scenario` run on the chosen shape (Critical/High scenarios → test checklist)
- [ ] Implementation tasks appended (test scaffold first, per plan-skill task rules)

---

## Out of scope

- `abra_r2v_8s` default-model drift — separate bug; file its own issue (noted in #174).
- Voice attach (`_attach_reference_audio`) new-UI path — follow-up unless recon shows the fix
  is trivially shared.
- REST-side `referenceEntities` injection — rejected by predict (reCAPTCHA-gated; dead end).

---

## Definition of done

- [ ] Tasks 1–3 checked; Task 4 decision recorded with date
- [ ] `/gflow:check` green (ruff / format / pyright `src` whole-tree / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` updated (Task 1)
- [ ] `KNOWN_ISSUES.md` entry live (Task 1)
- [ ] Issue #174 updated with recon findings (Task 3)
- [ ] Post-gate implementation tasks carry Critical + High scenario coverage from `/gflow:scenario`
- [ ] No `# TODO` in diff without a tracked issue link
