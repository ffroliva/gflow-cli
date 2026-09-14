# Is there a push channel during an active generation? No — the reply is just held open

**Date:** 2026-09-14 · **Cost:** 4 image generations. **Zero Veo credits**, daily image
quota only. No video.
**Script:** [`spike_generation_wire_survey.py`](../../../scripts/dev/spike_generation_wire_survey.py)
— committed in `ac8bd2b2`, **before any data existed**, so the pre-registered reading is
checkable by anyone.
**Evidence:** `scripts/dev/_spike_out/generation_wire_ffroliva_*.json` (gitignored)
**Design:** profile `ffroliva`, one real project, **2 runs**, full submit→completion
lifecycle instrumented.

## The gap this closes

[Survey #1](2026-09-14-two-domain-protocol-survey.md) found no WebSocket, SSE, gRPC or raw
protobuf — but only ever loaded **root and idle** pages, and named the hole itself:

> "A WebSocket opened only during an active generation would not have been seen."

## Observed

| | run 1 | run 2 |
|---|---|---|
| generation completed | ✅ | ✅ |
| **WebSocket events** | **0** | **0** |
| streaming content-types | 0 | 0 |
| `batchexecute` calls | 17 | 17 |
| **`ogiZ0b` held in flight** | **19.687 s** | **19.841 s** |
| next-longest request | 1.023 s | 1.286 s |
| total wall clock | 24.46 s | 24.74 s |

### The mechanism is a long-held response, not a poll and not a push

`ogiZ0b` — the image submit — is **dispatched once and answered ~20 seconds later**, with
the finished images in the reply. Everything else on the wire finishes in under 1.3 s, and
between submit and completion **nothing else is sent**. There is no progress channel to
subscribe to because there is no progress traffic at all.

This only became visible after the instrument was fixed mid-spike. The first version
recorded **response arrival** only, which cannot distinguish "sent late" from "held open
for 20 s" — and those are completely different architectures. Request-dispatch timing is
what settles it.

### Every `batchexecute` reply is chunked, and that is not streaming

All 17 arrive as `application/json` with **no `content-length`**. That is ordinary chunked
transfer for Google's batchexecute envelope, not SSE and not a stream to read incrementally.
Counting "200 without content-length" as evidence of streaming would have been wrong, which
is why the content-type was recorded next to it.

The only non-JSON body in the whole run is again **OneGoogle's account bar**
(`ogads-pa.clients6.google.com`, `application/json+protobuf`) — not Flow.

## What this settles

**Stop looking for a push channel on the image path.** gflow already does the right thing
(`submit_images_and_observe` decodes the completed `ogiZ0b` reply); there is no WebSocket to
subscribe to, no SSE to read, and no poll loop to optimise. Any future "we could subscribe
instead of polling" proposal for images is refuted by measurement rather than by opinion.

## What it does NOT settle — and this correction matters

**The image path does not poll at all, so it says much less about video than assumed.** The
spike's own pre-registration justified using images with: *"if a push channel exists for
progress it is overwhelmingly likely to be shared by both."* That reasoning is now weaker,
not stronger. Images use a **held response**; video uses a **poll loop** (`jwpduf`, `as29s`
in `migrated_composer.py`). Those are different mechanisms, so an absence on one is not an
absence on the other.

**The video path remains unmeasured for push.** Settling it costs Veo credits. Given images
resolve in a single held call, the question worth paying for is narrow and specific: *does
anything arrive between `jwpduf` polls, and are the polls a client timer or a reaction?* The
same script answers it with `--runs 1` pointed at a video request.

Also not measured: any account other than `ffroliva`; the `labs.google` generation path
(nothing here is served it — see survey #1); and whether a long-held `ogiZ0b` behaves the
same when the server is slow enough to time it out.

## A harness bug worth recording

The first attempt failed both runs — *"image submit stayed disabled"*, then *"the settings
pane opened but rendered no option groups"* — and both read exactly like live selector
drift. Neither was.

1. The harness **skipped `send_prompt`**. `run_images` types the prompt before submitting;
   without it the submit control is correctly disabled. A selector that does not match is
   evidence about the selector, and here it was evidence about the harness
   ([[format-button-disabled-on-empty-prompt]] is the same shape).
2. Run 2 then failed *earlier* than run 1 because run 1 had died with the settings pane
   open. A dirty starting state is not a second independent observation; each run now
   reloads first.

Had those been written up as findings, this note would have reported a broken Flow surface
that was working correctly the whole time.
