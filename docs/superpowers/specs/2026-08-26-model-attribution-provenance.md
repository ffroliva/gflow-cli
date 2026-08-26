# Spec — Model attribution provenance (observed vs assumed)

**Status:** draft for council review. 2026-08-26.

**One-line problem:** the catalog's `model` column means *ground truth* on one
code path and *our own intent* on another, and nothing distinguishes them.

---

## 1. Evidence gathered today (all live, zero credits)

Every claim below was observed. Claims I could not observe are in §3.

### 1.1 The trigger — a silent wrong-model generation

| observation | evidence |
|---|---|
| Requested `imagen4` (`IMAGEN_3_5`) | CLI invocation |
| Model was not applied | log `image_model_not_set model=IMAGEN_3_5` |
| Generation ran and reported success | **exit 0**, 1 image written |
| Wire carried a different model | **HAR: `requests[0].imageModelName = 'NARWHAL'`** |
| Catalog recorded `NARWHAL` | `gflow data list images` |
| New code refuses instead | **exit 23**, 0 images written |

Three independent layers (log, catalog, raw wire) agree.

### 1.2 Why it happened — no fallback logic exists

`IMAGEN_3_5` appears in exactly four places: the enum, its CLI aliases, its
reference cap, and its picker selector. **Nothing maps it to `NARWHAL`.**

`NARWHAL` is only the CLI default for an *omitted* `--model`; an explicit model
was passed, so that default never applied.

The model came from **residual browser UI state**. Catalog timeline, same project:

```
11:55:16  model=NARWHAL  proj=2ddc3a33  'a harbour at dawn'    <- ran --model nano2
14:08:07  model=NARWHAL  proj=2ddc3a33  'old behaviour check'  <- ran --model imagen4
```

The earlier command set the picker; the later one failed to change it and
inherited the selection **two hours later**.

So this is not a fallback path — it is an *absence of enforcement*. There is no
code to review, no constant to grep, no default to point at. The effective model
is a function of what was run before in that project.

### 1.3 Flow's picker no longer matches our registry

Read live, menu open:

```
🍌 Nano Banana Pro  /  🍌 Nano Banana 2  /  🍌 Nano Banana 2 Lite
```

- `IMAGEN_3_5` → `has-text('Imagen 4')` — **MISS**, entry no longer exists
- `NARWHAL` → `has-text('Nano Banana 2')` — **AMBIGUOUS**, also matches `2 Lite`
- `Nano Banana 2 Lite` — a tier we do not model at all

`gflow models` still advertises `IMAGEN_3_5 (image4, imagen4)` to users.

### 1.4 The deeper defect — provenance

| path | `model_name_type` source | meaning |
|---|---|---|
| classic | `dto.py:183` ← response `generated["modelNameType"]` | **observed** — Flow's own attribution |
| agentic | `agentic.py:914` ← `pending_model` (the request) | **assumed** — our intent |

`recorder.py:503` writes both into the same catalog column.

The agentic driver documents why (`agentic.py:862-871`): the real wire values
"live in the Web-Worker-delegated streamChat SSE stream which Playwright's
page-level instrumentation cannot observe", so they are "scrape-synthesised
sentinels".

**Consequence:** `gflow data list images` returns truth for classic rows and
intent for agentic rows, indistinguishably. Any audit, any governance guard, and
any user query inherits that ambiguity.

It also means §1.1's catalog evidence held **only because that run was classic**.
On the agentic arm the catalog would have recorded `IMAGEN_3_5` — my own
intent — and the wrong-model generation would have been invisible in the data.

### 1.5 Cost currencies are not what I first claimed

I asserted a wrong-model generation "billed" the user. **Retracted.**

- Image → **0 Flow credits**, metered by a **per-model daily quota**
  (`tests/e2e/test_classic_count_setter_e2e.py:7`; live cost line read `0`)
- Video → Flow **credits** (live popover: omni-flash 7–15, veo-lite 10,
  veo-fast 20, veo-quality 100)

Quota exhaustion surfaces as HTTP 429 naming the model
(`"You have reached the daily limit for Nano Banana Pro."` →
`RateLimitError`, exit 4). The real harm of a wrong-model generation is
consuming **another model's daily allowance** and receiving different output.

---

## 2. Oracles available, per arm

| oracle | classic | agentic | cost |
|---|---|---|---|
| UI picker state | readable | **does not exist** — model is a natural-language directive; the driver explicitly does not drive the tune popover for model | medium |
| Request `imageModelName` | on the wire; interception already exists (`_attach_batch_request_logger`) | unverified | ~free |
| Response `modelNameType` | **already parsed** | **not observable** (SSE inside a Web Worker) | free |

**There is no single oracle across both arms.** Classic can be made
deterministic today for free. Agentic cannot, until the SSE stream is reachable.

Precedent for the pattern: `_assert_image_entities_attached` already verifies at
the wire that a UI attach actually took effect, because a click that silently
did not apply is this same bug class.

---

## 3. Explicitly NOT established — do not design on these

- Whether the agentic arm's submitted `imageModelName` matches the directive.
  **Never observed.** The natural-language directive may or may not be honoured.
- Numeric daily limits for any image model. Observable only on exhaustion.
- Whether the daily quota is per-model or per-account.
- Cost/quota of `Nano Banana 2 Lite`.
- Whether `veo_3_1_lite_lower_priority` exists in the live picker (#539) — the
  video picker uses a different trigger; two prior captures read an empty menu
  **after it closed**, which is instrument failure, not absence.

---

## 4. Proposed direction (for the council to attack)

1. **Provenance flag first.** The catalog must record whether a value was
   OBSERVED or ASSUMED. Everything downstream inherits this; a guard built on an
   ambiguous column produces confident wrong answers.
2. **Classic wire check.** Compare response `modelNameType` against
   `request.model`; refuse on mismatch. Free, uses data already parsed.
3. **Pre-submit refusal** (already implemented on this branch): a model that
   cannot be selected raises before spending.
4. **Governance guard** (already implemented and mutation-proven): offline CI
   test grading our selectors against a recorded live inventory.
5. **Agentic observability** — separate investigation; without it the agentic arm
   cannot be verified at all.

---

## 5. Questions for the council

1. Is the provenance flag the right first move, or does it over-engineer a
   two-value distinction that a boolean `verified` column would cover?
2. Is a post-hoc wire check worth it when the quota is already spent, or should
   effort go to pre-submit enforcement only?
3. Does the agentic arm's unobservability make the catalog's `model` column
   fundamentally untrustworthy — i.e. should it be nullable rather than assumed?
4. What breaks this design under: multi-account, concurrent runs, a Flow A/B that
   changes picker ordering, or a model renamed rather than removed?
5. Is there an oracle for the agentic arm we have not considered (CDP, the
   worker's own network events, tRPC polling, the project's server-side state)?
