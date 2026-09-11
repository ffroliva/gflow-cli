# Nano Banana 2 Lite: the tier is real, the quality is fine, and the binding is unverifiable

- **Date:** 2026-09-11
- **Script:** [`scripts/dev/spike_nano2_lite_capability.py`](../../../scripts/dev/spike_nano2_lite_capability.py)
- **Profile / project:** `ci-probe` · `1e4efe0d-…` (migrated host)
- **Cost:** four image generations — **0 Flow credits**, daily quota only. No video.
- **Raw:** `scripts/dev/_spike_out/spike_nano2_lite_capability_20260911_2007*.json` (gitignored)
- **Refs:** [#787](https://github.com/ffroliva/gflow-cli/pull/787)

## Why it was run

`tests/flow_selectors/test_model_governance.py` carried a waiver:

> `"🍌 Nano Banana 2 Lite"` — Discovered 2026-08-26. A lower-tier image model we do not
> expose. **Waived pending a capability spike (cost/quality) before we ship it.**

PR #787 removes the waiver and ships the tier. This is the spike it was waiting for.

## Rung 1 changed the question

Two existing specs already list this as open, and one of them makes half the waiver
unanswerable:

- **Image generation costs 0 Flow credits** — so "cost" for an image tier means *daily
  quota*, not credits.
- **"Daily quota has no such oracle. It is observable *only on exhaustion*, via the
  429."** ([`2026-08-26-model-cost-quota-catalog.md`](../specs/2026-08-26-model-cost-quota-catalog.md))

So the **cost half cannot be measured without deliberately burning an account's day**, and
this spike does not pretend otherwise.

Rung 1 also exposed a sharper question. `migrated_composer.py` builds its result with
`model_name_type=request.model.value` — it **echoes the model we asked for**. The labs path
reads `generated["modelNameType"]` from the response; this one does not. And
`batchexecute.image_records` decodes media id, workflow id, url, seed, prompt and
dimensions — **no model field at all**.

```
A REPLY THAT REPEATS OUR REQUEST IS NOT AN OBSERVATION.
```

So "it generated an image with `--model nano2-lite`" proves the UI did not error. It does
**not** prove the tier bound.

## Pre-registered readings

| Outcome | Reading |
|---|---|
| lite carries HARBOR_SEAL, control NARWHAL | the picker binds; #787's mapping is real |
| both replies carry the SAME token | the lite selection silently falls back |
| neither reply carries any token | the wire does not attribute images at all here |
| a reply is unparseable, or a run fails | unmeasured for that arm; report it, never average |

## What was observed

**Both tiers generate.** Two arms, same prompt, same aspect, no errors:

| Arm | Elapsed | Dimensions | Media |
|---|---|---|---|
| `HARBOR_SEAL` (nano2-lite) | 20.3 s | 1024×1024 | `6ae9628b-…` |
| `NARWHAL` (nano2) | 25.4 s | 1024×1024 | `a5c0bf04-…` |

**The submit reply attributes nothing.** Both `ogiZ0b` replies were real payloads
(~1141 bytes) and **neither contained any model token** — not the wire name, not the menu
label. That is the third pre-registered reading.

**Nor does the project load.** Re-opening the project and searching every `batchexecute`
reply:

| rpcid | Size | Model tokens | Media ids |
|---|---|---|---|
| `HTrJv` | 24,762 B | **all six** — HARBOR_SEAL, NARWHAL, GEM_PIX_2, and the three menu labels | **none** |
| `Zzl0ze` | 38,880 B | **none** | **both** |

The catalogue lists models. The media listing lists media. **Neither joins them.**

## Verdict

**The tier is real.** `HARBOR_SEAL` and `Nano Banana 2 Lite` both appear in `HTrJv`,
Flow's own model catalogue for this account — independent corroboration of #787's
wire-string discovery, from a surface that is not our request echoed back.

**The quality is fine, and is not ranked.** Both arms returned 1024×1024 RGB JPEGs
(428,799 B for lite, 384,036 B for nano2) with no visible degradation on either; lite
showed slightly more surface texture, nano2 slightly smoother. **One prompt cannot rank
two tiers** and this does not try to. What it supports is the narrower claim the waiver
needed: lite is a working tier that returns a usable image at full resolution.

**The binding is unverifiable.** No surface gflow reads associates a generated image with
the model that produced it. So neither #787's live check nor this one can establish that
`--model nano2-lite` actually bound rather than silently falling back. Reported as
**unmeasured**, which is the honest answer, not a failed run.

**The cost half is unmeasurable by design** — see rung 1. Recording *why* is the answer
here; inferring a number would be worse than the gap.

## On the waiver

Its stated condition was "cost/quality". Quality is answered. Cost is not answerable
without exhausting a day, and the repo already wrote that down in 2026-08. Un-waiving on
the quality half plus the catalogue corroboration is defensible — **provided the cost gap
is carried forward rather than deleted with the waiver**, which is what the spec files
already do.

## What this surfaced that is not about Lite

`model_name_type` on the migrated path is an **unbacked claim**: gflow reports a model
attribution that was never observed, on every migrated image, for every tier. A caller
reading that field — or a future recorder persisting it — believes something no surface
confirmed. Filed separately; it is pre-existing on `develop` and not #787's doing.

## Not measured

- **Daily quota**, per the oracle problem above. Whether Lite carries a *different* quota
  is therefore still open, exactly as the 2026-08-26 catalogue recorded it.
- **Whether the picker silently falls back** when a tier is unavailable — unverifiable by
  the routes tested, and the reason the point above matters.
- **Quality at scale.** Two images. Any ranking would need a prompt set and a blind
  comparison, which is a different exercise from this gate.
