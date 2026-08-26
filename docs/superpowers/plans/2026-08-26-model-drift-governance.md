# Model-Drift Governance — Implementation Plan

**Goal:** Model drift on Flow's side becomes loud. Today it is silent, and we
only found it by manual spike.

## What we are guarding against — observed, not hypothetical

Read live from Flow's image picker on 2026-08-26 (menu open, so valid):

```
🍌 Nano Banana Pro
🍌 Nano Banana 2
🍌 Nano Banana 2 Lite
```

Against `IMAGE_MODEL_OPTION_SELECTORS` (`ui_automation.py:101`):

| our model | selector | reality |
|---|---|---|
| `GEM_PIX_2` | `has-text('Nano Banana Pro')` | HIT |
| `NARWHAL` | `has-text('Nano Banana 2')` | **AMBIGUOUS** — also matches `Nano Banana 2 Lite` |
| `IMAGEN_3_5` | `has-text('Imagen 4')` | **MISS** — the entry no longer exists |
| — | — | **UNKNOWN_OFFERED**: `Nano Banana 2 Lite` is a tier we do not model |

Three distinct drift classes, none of which produced a single failing test or a
user-visible error.

## Why it is invisible today

`_select_image_model` (`ui_automation.py:1751-1782`) swallows every failure:

```python
raise RuntimeError(f"no visible option matched for model {model.value!r}")
except Exception as e:
    log.warning("ui_automation.image_model_not_set", note="Flow default model applies")
```

So a MISS means the user asks for one model, silently gets another, **and is
billed for it**. The AMBIGUOUS case is worse than a MISS: `.first` picks by DOM
order, so it works until Flow reorders, then silently selects the Lite tier.

The code comment at `:94-95` asserts `'Nano Banana 2' is not a substring of
'Nano Banana Pro', so has-text is unambiguous across the three` — true when
written, false now. A comment cannot notice that it has expired.

## Architecture

Reuse `flow_selectors`, do not build a parallel system. Its `Grade` vocabulary
(`grading.py:12-17`) already names two of the three classes — `AMBIGUOUS`
literally documents "drivers use .first, so this misclicks".

Three layers, each catching what the one below cannot:

**1. Fail loudly (immediate safety).** `_select_image_model` stops swallowing.
A requested model that cannot be selected must abort before spending anything,
not silently downgrade. This is the only change that protects a user today.

**2. Recorded inventory + offline governance test (every CI run).** The live
probe writes what Flow actually offers into a dated fixture. An offline test
grades our selectors against that fixture: every registered model resolves to
exactly one entry, and every offered entry is modelled or explicitly waived.
When the fixture updates and a selector becomes ambiguous, CI fails — the
"Nano Banana 2 Lite" case is caught by construction.

**3. Live drift probe (scheduled, $0).** Opens each picker, reads **while open**,
grades, refreshes the fixture. Exit `0` clean / `1` drift / `2` infrastructure,
matching `scripts/probe/run_probe.py`.

Layer 2 is what makes this governance rather than a one-off script: it fails in
CI, on every commit, without needing a browser.

## Risk register

| Severity | Risk | Mitigation |
|---|---|---|
| High | Probe reads an empty menu and records "no models", wiping the fixture | An empty read is INSTRUMENT FAILURE, never data. #539 made exactly this mistake; so did I, twice today. Refuse to write a fixture from an empty read. |
| High | Failing loudly breaks users mid-workflow on a transient miss | Abort BEFORE submitting, so nothing is spent; message names the offered models so the fix is obvious |
| Medium | Fixture churn on every Flow A/B | Waivable entries; the test asserts *our* selectors resolve, not that the inventory is frozen |
| Low | Probe cost | Navigation + two menu clicks, no generation |

## Tasks

### Task 1 — fail loudly (red tests first)
- [ ] Requested model not present => raises, does not warn-and-continue
- [ ] The error names the model asked for AND the options Flow offered
- [ ] Raises BEFORE any submit, so nothing is charged
- [ ] Ambiguous match (>1) is also a failure, not a `.first` guess

### Task 2 — model registry + recorded inventory
- [ ] Declare expected models per picker surface, reusing `Selector`/`Grade`
- [ ] Add an `UNKNOWN_OFFERED` concept for entries Flow offers that we do not model
- [ ] Dated, provenanced fixture of the live inventory

### Task 3 — offline governance test (the CI guard)
- [ ] Every `Model` / `VideoModel` member has a picker selector — a new model cannot be added without one
- [ ] Each selector matches EXACTLY ONE entry in the recorded inventory (catches AMBIGUOUS)
- [ ] Each selector matches AT LEAST ONE entry (catches MISS)
- [ ] Every offered entry is modelled or explicitly waived (catches UNKNOWN_OFFERED)
- [ ] Mutation-verified: the test must FAIL against the 2026-08-26 inventory before the selectors are fixed

### Task 4 — live probe
- [ ] Read menus while open; empty read => exit 2, never exit 0 and never write the fixture
- [ ] Grade via `flow_selectors.grading`
- [ ] Exit 0/1/2

### Task 5 — E2E GATE
- [ ] Probe run against real Flow reproduces the three known drift classes
- [ ] Offline test fails on the pre-fix selectors, passes after
- [ ] `/gflow:check` green

## Out of scope
- Adding `Nano Banana 2 Lite` as a usable model (inventory first, capability later)
- #539's video `[Lower Priority]` question — this makes it *answerable*, it does not answer it
