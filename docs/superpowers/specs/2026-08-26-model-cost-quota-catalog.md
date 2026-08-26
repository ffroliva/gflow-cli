# Spec — Model cost/quota catalogue and its drift governance

**Status:** proposed. Written 2026-08-26 after a live spike found three
undetected model drifts and one unsupported claim of my own.

## Correction that motivated this spec

I stated a wrong-model selection "billed you". **That was unsupported.** The
evidence says the opposite for image models:

| Source | Evidence |
|---|---|
| `tests/e2e/test_classic_count_setter_e2e.py:7` | "Spends Imagen **quota** (**0 Flow credits** — only video costs credits)" |
| live settings popover, 2026-08-26 | image cost line reads **0** |
| `errors.py:257` | "**Daily or per-minute model quota** reached; retry with a different model or wait for quota reset" |
| `tests/test_self_documenting_errors.py` | `"You have reached the daily limit for Nano Banana Pro."` → HTTP 429 |
| `tests/api/test_client_errors.py:78` | `"Daily generation quota reached"` → `RateLimitError` |

So there are **two different cost currencies**, and we conflate them:

- **Video** → Flow **credits**, metered per generation.
- **Image** → **0 credits**, metered against a **per-model daily quota**.

The real harm of selecting the wrong model is therefore *not* a charge. It is:
consuming a different model's daily allowance, receiving different output than
requested, and — once that model's quota is exhausted — a 429 attributed to a
model the user never asked for.

## The gap

There is **no structured cost/quota metadata anywhere**. `Model` holds wire
strings and CLI aliases. What we "know" lives in a test docstring and an error
remediation string — neither assertable, neither governable.

`gflow models` prints model / aliases / ref-cap / max-duration and **no cost or
quota column**. It also still advertises `IMAGEN_3_5` (`image4`, `imagen4`),
which Flow no longer offers.

Its docstring claims the catalog "can never drift from what the generation
commands accept". True, and beside the point: it is internally consistent and
externally wrong. The authority for what exists is **Flow**, not our enum.

## What we know vs what we must not assert

Governance is worthless if it records guesses as facts. Split explicitly:

**Established by evidence**
- Image generation costs 0 Flow credits.
- Image models are limited by a daily quota, enforced by Flow with a 429 whose
  message *names the model* ("daily limit for Nano Banana Pro").
- Video generation costs credits; the settings popover renders a live cost line.
- Observed video costs (#539, live popover): omni-flash 7–15 (duration-scaled),
  veo-lite 10, veo-fast 20, veo-quality 100.

**Not established — must be recorded as UNKNOWN, never inferred**
- The numeric daily limit for any image model.
- Whether that limit is per-model or shared across models on an account.
- Whether `Nano Banana 2 Lite` carries a different quota (it is a newly
  discovered tier we do not model).
- Whether `veo_3_1_lite_lower_priority` is cheaper, free, or absent (#539).

## Observability — and its hard limit

The credit line is a **credit-free oracle for video**: select a model, read the
cost. That is how #539 can be answered without spending.

**Daily quota has no such oracle.** It is observable *only on exhaustion*, via
the 429. So the catalogue must record quota as an **observation with a date**,
never as a configured constant. A number we cannot re-derive on demand is a
number that will silently rot — the exact failure this whole exercise exists to
prevent.

## Proposed catalogue

Per model, structured and assertable:

| field | source | when unknown |
|---|---|---|
| `currency` | `CREDITS` \| `DAILY_QUOTA` | must be known; no default |
| `credits_per_generation` | live cost line | `None` for quota-metered models |
| `daily_limit_observed` | 429 message | `None` — never guessed |
| `daily_limit_observed_utc` | when that 429 was seen | `None` |
| `offered_by_flow` | live picker inventory | drift signal |

`gflow models` gains a cost column, so the distinction is visible to users
rather than buried.

## Test strategy

**TDD (unit).** Every model has a `currency`. A `DAILY_QUOTA` model must not
carry `credits_per_generation`, and a `CREDITS` model must. `daily_limit_observed`
without a date is rejected — an undated observation is a guess.

**BDD (scenario).**
```gherkin
Scenario: a quota-limited model is exhausted
  Given the image model "Nano Banana Pro" has reached its daily limit
  When I run an image generation with that model
  Then it fails with RateLimitError
  And the message names the model whose quota was exhausted
  And the remediation offers a different model, not "wait and retry" alone

Scenario: a requested model is not offered by Flow
  Given Flow's picker no longer offers "Imagen 4"
  When I request that model
  Then it fails before submitting
  And the error names what Flow does offer
  And no quota is consumed
```

**E2E.** The credit-line read per video model is credit-free and repeatable —
that is the gate for the video half. The image half is asserted through the
already-passing loud-failure path; deliberately exhausting a daily quota to
observe a 429 is **not** a routine test, and the catalogue records the value
opportunistically when a real 429 occurs.

## Governance

Extend the mechanism already built and proven in
`tests/flow_selectors/test_model_governance.py`:

1. The live probe records, per model: offered-by-Flow, and the cost line.
2. The offline CI test asserts the catalogue matches the recording — a changed
   credit cost, a removed model, or a new unmodelled tier fails CI.
3. `RateLimitError` handling captures the model named in a 429 and surfaces it,
   so a real exhaustion updates the catalogue instead of being lost in a log.

The property to preserve: **a number in this catalogue must always be traceable
to an observation with a date.** Anything else drifts silently, which is how we
got here.

## Out of scope
- Enforcing quotas client-side (we cannot know the remaining allowance).
- Shipping `Nano Banana 2 Lite` (inventory first, capability later).
