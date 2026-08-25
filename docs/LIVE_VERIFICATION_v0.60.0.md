# Live verification — v0.60.0

> Evidence that v0.60.0's user-facing changes work against production Flow.
> Hand-run on `develop` (release prep for `chore/release-v0.60.0`).
> Release-protocol step 4b.

## Environment

| | |
|---|---|
| Date | 2026-08-25 |
| Local version at run time | `0.59.0` (pre-bump; the code under test is the merged `develop` tree) |
| Profile | `denon82` (real-browser Chrome strategy — `real-browser-auth-mandatory`) |
| Transport | `ui_automation` |
| Model | `NARWHAL` (Nano Banana 2) |
| Out dir | `tmp/lv060/` |
| Credits spent | **0 Veo credits** — image generation and tRPC mutations are credit-free |

## Summary

| Change | Live-verified? | How |
|---|---|---|
| #528 — 400 → `ContentPolicyError` + actionable remediation | ✅ **Yes** | Fault injection through the real transport (both body shapes) |
| #528 — the *natural* policy-400 trigger | ❌ **Could not reproduce** | Two face refs + age descriptor returned 200 — see caveat |
| #528 — incident bundle `generation_requests[]` | ⚠️ **Partial** | Request-summary logger observed live; bundle write not triggered |
| #578 — `origin`/`referer` on the labs lane | ✅ **Yes** | Live A/B, plus wire confirmation via header echo |
| Selector registry + drift probe | ⚠️ **By design, CI-only** | Correct exit 2 (infrastructure) without CI credentials |
| #577 — structlog test isolation | n/a | Test-suite only, not user-facing |

---

## 1. Content-policy 400 classification (#528) — the headline change

### 1a. What could NOT be reproduced (stated first, deliberately)

The issue reported HTTP 400 on **2 face-bearing references, 5/5**, and on an
age-explicit person descriptor. Attempted verbatim:

```pwsh
uv run gflow image t2i "studio portrait of an elderly fisherman, weathered face, soft window light" --profile denon82 --out tmp/lv060 --count 1
uv run gflow image t2i "studio portrait of a middle-aged librarian with short grey hair, neutral background" --profile denon82 --out tmp/lv060 --count 1
uv run gflow image i2i "a young woman in her early 20s stands between them on a harbour wall at dusk" \
  --ref tmp/lv060/e751d739-...jpg --ref tmp/lv060/417d68bf-...jpg --profile denon82 --out tmp/lv060 --count 1
```

**Result: HTTP 200.** Two face-bearing references *plus* an age-explicit
descriptor — both documented triggers stacked — generated successfully.

Two honest caveats on this null result:

1. The issue distinguished *"2 character entities, or 1 entity + 1 portrait
   image"*. This run used two plain `--ref` images, which is a **different wire
   shape** from `--reference-entity` (see [REFERENCE_STRATEGIES.md](REFERENCE_STRATEGIES.md)).
   The entity path was not exercised.
2. Google's enforcement has demonstrably shifted before (the issue itself notes
   shapes that worked in late July failing after a 2026-08-03 Flow release). A
   trigger that fired in August may not fire today.

So: **we cannot currently produce a policy 400 on demand.** That is recorded
rather than omitted, per the release protocol.

### 1b. What WAS verified

The release does not ship "Google returns 400 in situation X". It ships *"when a
400 arrives, the operator gets the right class and the right advice."* That is
verifiable independently of Google's cooperation, and was:

`scripts/dev/live_verify_policy_400_v060.py` injects the 400 at the network
boundary and leaves everything downstream real — real browser, real Flow editor,
real prompt submit, real `ui_automation` transport, real error path.

```
--- with_reason ---                        (details[].reason = PUBLIC_ERROR_UNSAFE_GENERATION)
  class      : ContentPolicyError
  status     : 400
  PASS       : True

--- bare_400_as_seen_in_bundles ---        (no reason field — what every #528 bundle showed)
  class      : ContentPolicyError
  status     : 400
  PASS       : True
```

The **bare** case is the load-bearing one: reason-only matching would have fallen
straight back to `WireFormatError`, which is the bug.

Remediation text delivered to the operator:

> Flow refused this generation (HTTP 400 on the generation route). This is almost
> always a content-policy rejection, not a malformed request — on this path Flow's
> own web app composes the request body. Most common causes, in order: (a) more
> than ONE face-bearing reference…; (b) an age-explicit person descriptor…;
> (c) a real-person likeness or a frontal close-up face. Shortening the prompt
> does NOT help.

**Structlog invariant observed live** — the new discovery log fired:

```
[warning] ui_automation.batch_400_body
  body_prefix="{'error': {'code': 400, 'message': 'Request contains an invalid argument.',
                'status': 'INVALID_ARGUMENT'}}"
  route=.../flowMedia:batchGenerateImages
```

This is the log that will capture Flow's real 400 shape the next time one occurs
naturally — the open question from §1a.

Evidence: `scripts/dev/_spike_out/live_verify_policy_400_v060.json`

### 1c. Incident bundle `generation_requests[]` — partial

The request-summary logger was observed live on every submit:

```
[info] ui_automation.batch_request_body
  summary={'present': True, 'bytes': 5646, 'mentions_reference_entities': False,
           'request0_keys': [...]}
```

The counts-only journal record is fed from this same call site, but writing a
bundle requires a capture-triggering failure, which the injected 400 did not
produce (`ContentPolicyError` is a handled, classified error). **Unit-covered**
(`tests/test_incident_generation_requests.py`, 5 tests including the
no-free-text retention guard); bundle-on-disk not observed this cycle.

---

## 2. `origin`/`referer` on the labs tRPC lane (#578)

Verified **and its premise falsified** — see the PR and
[the falsification memory](https://github.com/ffroliva/gflow-cli/pull/578).

`scripts/dev/spike_trpc_origin_referer_401.py`, interleaved A/B against live
`project.createProject`:

```
session probe            -> HTTP 200 alive=True
A_control_no_origin      -> HTTP 200, 200      (content-type only)
B_with_origin_referer    -> HTTP 200, 200
```

Wire confirmation (header-echo endpoint, identical call shape): control
`origin present? False`, treatment `origin present? True` — so Playwright adds no
Origin of its own and the control arm was genuinely bare.

**Conclusion: the headers are harmless and consistent, not necessary.** No
regression from sending them; no 401 cured by them.

---

## 3. Selector registry + drift probe

```
uv run python scripts/probe/run_probe.py --surface editor
::error::GFLOW_CI_SESSION_TOKEN and GFLOW_CI_PROJECT_ID are required
exit 2
```

**Exit 2 is correct behaviour, not a failure.** The probe's contract is
`0 clean / 1 drift / 2 infrastructure`, and exit 2 exists specifically so an
absent or expired credential is never published as selector drift. Drift
detection itself runs on the `selector-probe` workflow (`schedule` +
`workflow_dispatch`) with CI credentials; it is not a local user-facing path.

---

## 5-layer ledger (image generations)

| Layer | Evidence |
|---|---|
| File count | 3 `.jpg` files in `tmp/lv060/` |
| Magic bytes | `ffd8ff` on all 3 → genuine JPEG, not an error page or truncated write |
| Dimensions | `768x1376` on all 3 (9:16 as requested); 761 685 / 765 400 / 784 745 bytes |
| Structlog invariants | `image_mode_entered`, `image_model_selected model=NARWHAL`, `aspect_ratio_set 9:16`, `count_setter_completed success=True`, `prompt_submitted`, `batch_response_seen status=200`, and on the injected run `batch_400_body` + `batch_response_seen status=400` |
| User-confirmable artifact | Two portraits and one two-reference i2i composite openable in `tmp/lv060/` |

## Cleanup owed

- 8 projects named `spike401-*` on the `denon82` account (from the #578 A/B spike).
- Ad-hoc projects created by the t2i/i2i runs above and by the fault-injection run.
