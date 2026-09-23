# The video path polls on a fixed 5.00 s client timer — nothing signals it

**Date:** 2026-09-14 · **Cost:** **one real Veo generation, ~10 credits** (`veo_3_1_lite`,
t2v, x1). The only credit-spending measurement in this line of work.
**Script:** [`spike_generation_wire_survey.py`](../../../scripts/dev/spike_generation_wire_survey.py)
`--mode video` — the video arm was pre-registered and committed in `8d4502bb`, **before
any video data existed**, so the ordering is checkable this time.
**Evidence:** `scripts/dev/_spike_out/generation_wire_video_ffroliva_20260914_145524.json`,
plus three `$0` image runs (`generation_wire_image_ffroliva_20260914_16*.json`) that verify
the instrument and measure its blind spot in §6 — all gitignored.
**Design:** profile `ffroliva`, project `c5550ed7-…`, **1 run**, full submit→completion
lifecycle instrumented. One run because each further one costs credits — `--runs` now
defaults to 1 in video mode for that reason.

The generated clip is **left in the project**. `skills/spike/SKILL.md` says to delete what a
spike creates, and that rule is aimed at throwaway entities; this one is a paid artefact the
account owns, indistinguishable from any other generation, and deleting it would destroy the
only durable receipt that the credits bought a real completed job.

## The gap this closes

[Survey #2](2026-09-14-generation-wire-no-push-channel.md) settled the **image** path —
`ogiZ0b` dispatched once, answered 19.7 s later with the finished images, a held response
with neither push nor poll — and then **corrected its own premise**:

> "The image path does not poll at all, so it says much less about video than assumed. …
> Images use a **held response**; video uses a **poll loop** (`jwpduf`, `as29s`). Those are
> different mechanisms, so an absence on one is not an absence on the other."

It named the question worth paying for: *does anything arrive between `jwpduf` polls, and
are the polls a client timer or a reaction?* This answers exactly that.

## Observed

All timing figures below are recomputed from the capture. Standard deviations are
**population** (`ddof=0`) — the eight polls are the complete set for this run, not a
sample of it.

| | video run 1 |
|---|---|
| generation completed | ✅ `done=True`, status 3, 1,273,409 bytes, `flow-content.google` — see the sourcing note below |
| **WebSocket events** | **0** |
| streaming content-types | 0 |
| `jwpduf` polls | 8 |
| **`jwpduf` DISPATCH gaps** | 5.002, 5.009, 5.011, 5.005, 5.008, 5.001, 5.004 s |
| — mean / stdev / spread | **5.006 / 0.0035 / 0.010 s** |
| `jwpduf` ARRIVAL gaps | mean 5.007, stdev **0.178**, spread **0.641** s |
| `jwpduf` in flight | 0.441–0.809 s, mean 0.538 |
| `YhhmEf` (submit) in flight | 4.301 s |
| `as29s` (result) in flight | 2.541 s |
| `as29s` dispatched | **0.034 s** after the last `jwpduf` reply |
| total wall clock | 56.61 s |

### 1. It is a fixed 5.00 s client timer, and only dispatch timing can show that

Seven consecutive inter-dispatch gaps span **10 milliseconds** end to end (stdev 3.5 ms).
That is a `setInterval`, not a reaction to anything.

The **arrival** gaps of the same eight polls have a stdev of 178 ms and a spread of 641 ms —
**fifty times wider**, and purely network jitter. An arrival-only instrument would have
reported "roughly every 5 s, a bit ragged" and could not have separated a timer from a
reactive cadence. Survey #2 rebuilt its instrument mid-spike to record request dispatch for
exactly this reason; this is the second finding that turns on it, and the first where the
two readings disagree in character rather than only in precision.

**That 50× ratio is what excludes the competing explanation.** If each poll were sent in
*response* to something — the previous reply, or a server ticking its own 5 s boundary —
the sending times would inherit that thing's jitter, so dispatch spacing would be at least
as ragged as arrival spacing (σ ≈ 178 ms). It is 3.5 ms. A clock the network cannot
perturb is a local clock.

What this does *not* distinguish: whether the 5 s value is hardcoded in the bundle or
handed down in server config. Either way the page holds the timer; only its origin is
unmeasured.

### 2. The cadence does not collapse near completion

The pre-registered alternative was that gaps shorten at the end, which would mean something
told the client. They do not: the **last** inter-dispatch gap (5.004 s) is indistinguishable
from the **first** (5.002 s).

What happens instead is fully accounted for by the poll itself. The generation finished
during the poll dispatched at t=53.554; that poll's own reply (t=54.021) carried status 3
and the byte count, and the app dispatched `as29s` **34 ms later**. The client reacted to
the answer it had asked for. Nothing arrived out of band to prompt it.

### 3. Nothing arrives between polls that could be the signal

Across the whole submit→completion window the only non-`batchexecute` responses are:

| t | host | what |
|---|---|---|
| 15.549–15.562 | `region1.google-analytics.com/g/collect` ×4 | GA4 beacons fired at the submit click. **204 No Content** — no body to carry anything |
| 31.907 | `play.google.com/log?hasfast=true&auth=SAPISIDHASH…` | Google's shared client-logging sink. Outbound telemetry |

Both are **outbound telemetry the page sent**, not channels it was pushed on. That alone is
not the argument, though — a reply to an outbound request can certainly carry a signal, and
in §2 exactly that happens: the client is told the job is finished by the reply to a request
it made. So the argument has to be about the cadence, and it is: the `play.google.com` hit
sits inside the **longest** arrival gap (5.379 s) and between two dispatches **5.011 s**
apart — within the 10 ms spread of every other pair. Whatever its reply carried, the timer
did not react to it. The GA4 four are `204 No Content`, so their replies carry nothing at
all.

One extra Flow rpcid, **`WuwhI`**, fires once mid-poll (t=30.32 → 30.572, 4,925-byte
request). It is a client-sent `batchexecute` call like the rest, on the same host, and is
not a push.

> **Scope of this section.** "The only non-`batchexecute` responses" means *the only ones the
> page listener saw* — the video run predates the context-level listener added in §6. A
> page-scoped listener misses three responses per run; in the two runs where they were
> measured, all three land during page load at t≈1.4–4.0 s, well before submit and far
> outside this run's poll window of t=18.97–56.60. That makes it unlikely they fall between
> polls, not proven. **What would settle it:** the next video run, with the §6 listener
> bound — it costs credits, so it waits for a reason to spend them.

### 4. Nothing on this path is held open — `jwpduf`, `as29s` or the submit

Every one of the eight polls is answered in under 0.81 s (mean 0.538). The pre-registration
named `as29s` alongside `jwpduf`, so it gets the same test, and so does the submit:

| rpcid | held in flight | what it is |
|---|---|---|
| `jwpduf` ×8 | 0.441–0.809 s | the status poll |
| `as29s` | **2.541 s** | the result fetch, issued once the poll said done |
| `YhhmEf` | **4.301 s** | the submit, accepting the job |

`as29s` and `YhhmEf` are the two longest, and neither is a progress channel: both are
one-shot request/response for a specific thing (fetch the finished record; accept this
job), each dispatched at a moment the client chose, each answered once. A long-poll is a
request held open *instead of* polling, spanning the work — `ogiZ0b` on the image path was
**held 19.7 s**, covering the entire generation. Nothing here spans anything: the 45 s of
generation is covered by eight short polls, not by one held call.

So the two paths really do use different mechanisms — the distinction survey #2 drew is
confirmed rather than assumed — and neither of them is push.

### 5. No WebSocket, no streaming, no Flow protobuf — now measured *during* a video run

Zero WebSocket events, zero `text/event-stream` / `grpc` / `x-protobuf` / NDJSON /
multipart. The single `application/json+protobuf` is **`ogads-pa.clients6.google.com`** at
t=0.53 — the OneGoogle account bar, before submit, not Flow. That is the third time it has
shown up and the third time it is not Flow; it is named here so the next reader does not
have to re-derive it.

Every `batchexecute` 200 again arrives as `application/json` with **no `content-length`**.
Ordinary chunked transfer of the batchexecute envelope — the same non-finding as the image
arm, recorded so it is not mistaken for streaming a second time.

### 6. The detector's own blind spot — measured, and it is real

Every survey in this series bound `page.on(...)`. A service worker's fetches are reported on
the **BrowserContext** instead, so a page-scoped zero could not tell "no such traffic" from
"traffic on a surface I never bound". Rather than argue it, both listeners were bound at once
and diffed — whatever the context saw and the page did not **is** the blind spot. Two `$0`
image runs, identical results:

| | run A | run B |
|---|---|---|
| responses seen by `page.on` | 80 | 80 |
| responses seen by `context.on` | 80 | 80 |
| **reported to the context, never to the page** | **3** | **3** |

The same three, both times:

| t | response | |
|---|---|---|
| ≈1.4 | `play.google.com/log?format=json&hasfast=true&authuser=0` | Google's shared client-logging sink |
| ≈3.9 | `www.google.com/recaptcha/enterprise/reload?k=…` | reCAPTCHA Enterprise |
| ≈4.0 | `www.google.com/recaptcha/enterprise/clr?k=…` — **`application/binary`** | reCAPTCHA Enterprise |

And the carrier that motivated the check is confirmed to exist: one **dedicated worker**,
`www.google.com/recaptcha/enterprise/webworker.js`, in both runs.

**What this corrects.** Surveys #1 and #2 — and this note's own video run — counted
page-scoped responses, so each of them missed three. Their "zero WebSocket" numbers were
narrower than they read.

**What it does not change.** None of the three is a channel: two are reCAPTCHA's own
endpoints and one is a logging sink, all short request/response. And with the wider
listener plus `Network.webTransport*` handlers registered for the first time in this series,
the count is **still zero** WebSocket, SSE and WebTransport. So the no-push conclusion
stands, now measured on a wider surface than it was argued on.

**Still unmeasured, deliberately.** `Target.setAutoAttach` was *not* used: flattened child
sessions arrive with a `sessionId` Playwright's connection does not route to a `CDPSession`,
so those events would be dropped silently — a detector that looks armed and is not, which is
the single failure mode this series keeps guarding against. A socket opened strictly inside a
worker's own scope and never surfaced to the context therefore remains unobserved. The one
worker present is reCAPTCHA's, not Flow's.

### 7. The third-party hosts on Flow's pages, named once

`ogads-pa.clients6.google.com` has now been re-derived as "not Flow" in three separate notes.
Recording the census so the next reader does not pay for it a fourth time — measured on the
image runs above, and consistent with the video run:

| host | what it is | Flow? |
|---|---|---|
| `www.gstatic.com`, `fonts.gstatic.com`, `fonts.googleapis.com`, `ssl.gstatic.com` | static assets and fonts | no |
| `region1.google-analytics.com`, `www.googletagmanager.com` | GA4 — `/g/collect` beacons are **204 No Content** | no |
| `play.google.com/log` | Google's shared client-logging sink (`hasfast=true`). Page-scoped on the video run, **context-only** on both image runs | no |
| `www.google.com/recaptcha/enterprise/*` | reCAPTCHA Enterprise, incl. a dedicated worker and an `application/binary` reply | no |
| `ogads-pa.clients6.google.com` | the OneGoogle account bar. Its `application/json+protobuf` is **not a Flow wire** | no |
| `accounts.google.com`, `lh3.google*` | sign-in surface; avatar/media thumbnails | no |

**None of these should be blocked or route-aborted in production**, and gflow has no
machinery that would: there is no third-party host registry, block list or filter anywhere in
`src/`, and the only `context.route()` is a narrow generation-payload interception. That
absence is correct rather than an oversight — this project's whole premise is that Google's
stack rejects browsers advertising automation, and suppressing a beacon every real Chrome
session sends is itself the anomaly.

## What this settles

**There is no push channel on the video path either — and that is now measured, not
generalised.** The three surveys together cover Flow's hosts at idle (#1), during an image
generation (#2), and during a video generation (this one):

| path | mechanism | measured in |
|---|---|---|
| image | one **held response** (`ogiZ0b`, ~19.7 s), no poll | survey #2 |
| video | **fixed 5.00 s client-timer poll** (`jwpduf`) → `as29s` | this spike |

The polling in `migrated_composer.py` is the real mechanism, not a fallback, and the driver
is right to observe rather than drive it: **the 5 s cadence is Flow's page's, and gflow adds
no traffic to it** (`submit_and_observe` waits on futures resolved by the page's own
responses; `_await_terminal` issues nothing). Any future "we could subscribe instead of
polling" proposal for video is refuted by measurement, as it already was for images.

**The timer is not ours to tune.** It is a floor we read: a finished video becomes visible
up to one poll interval late, and no gflow-side change moves that while we observe the
page's own traffic.

## NOT measured — leads, not conclusions

- **One run, one account, one shape.** `ffroliva`, one project, t2v, `veo_3_1_lite`, x1, a
  generation that finished in ~35 s of polling. Each further run costs credits.
- **Whether the 5 s cadence holds for a long or queued generation.** This one never sat in a
  queue (`status=2` throughout, then 3). A multi-minute generation could back the timer off
  and this run could not see it. *What would settle it:* the same script on a longer
  generation — another ~10 credits.
- **Whether the cadence varies by model, duration, count or cohort.** Unmeasured.
- **i2v / r2v**, which add upload and attach traffic before submit.
- **`labs.google`'s generation path** — nothing here is served it ([survey #1](2026-09-14-two-domain-protocol-survey.md)).
- **Per-request HTTP version in this run** — see the instrument note below. No h2/h3 claim is
  made from this capture.
- **Carriers outside the page's own scope** — **now measured, see §6.** The video run above
  was page-scoped like every earlier survey, so it missed three responses; the widened `$0`
  image runs found them and found no channel among them. What remains unmeasured after §6 is
  narrow: traffic strictly inside a worker's scope that never surfaces to the context, and
  anything below HTTP semantics (an h3 datagram, or server-initiated frames on a reused
  connection that CDP does not report as a response).
- None of that bears on the **cadence** finding, which rests on the client's own dispatch
  times and would be unchanged by any inbound traffic whatsoever.

## Instrument notes

**Sourcing correction — the terminal outcome is NOT in the cited capture.** "`done=True`,
status 3, 1,273,409 bytes" and "`status=2` throughout, then 3" come from the driver's own
`structlog` lines (`migrated.status` / `migrated.result`) on **stdout**, not from
`generation_wire_video_ffroliva_20260914_145524.json`. The capture stored the result as
`str(record)[:120]`, which truncates a `GenerationRecord` before `status`, `is_done` and
`size_bytes` — `grep -c 1273409` against the evidence file returns **0**. Everything else in
this note is recomputed from the capture; those three figures are not, and citing them under
an "Evidence:" header that does not hold them was wrong. The script now records the terminal
fields by name, so the next run persists what this one only printed.

**The url-keyed dispatch dict would have destroyed this result, and the fix was made before
the run.** Request dispatch times were keyed by `url[:200]`. Measured here: **all 8 `jwpduf`
URLs are byte-identical at that truncation** — "1 distinct of 8". Every poll would have
overwritten the previous one's dispatch time, and `in_flight_s` — the discriminator for
long-polling — would have been wrong for seven of eight. The key is now the Playwright
`Request` object. Survey #2's image arm was unaffected only because `ogiZ0b` fired once.

**Per-request HTTP version is unattributed in this capture, and that is a bug this run
found.** `protocols` was keyed on `url[:300]` while events carry `url[:200]`, so the lookup
missed every long URL — 62 of this run's responses, including all eight polls. This run's
h2/h3 split is therefore **not reportable** and nothing above rests on it. **Survey #1 is
not affected** — it truncates both sides at 300, so its "34–171 requests per run negotiate
h3" stands.

Fixed afterwards (both sides now `[:200]`), **and the fix was then run** on the free image
arm rather than shipped unexercised: `generation_wire_image_ffroliva_20260914_160723.json`
attributes **73 of 76 responses** (all `h3`), against 62 unattributed before. The residual 3
are responses whose CDP `Network.responseReceived` did not pair — a known, small remainder,
not the systematic miss. That run also exercised the `finally`-wrapped page check-in, the
dispatch-keyed gap table, the named terminal fields, and the new mode-aware `--runs`
default, at zero Veo credits.

## Applying the pre-registered reading to this data

The script's video arm listed five outcomes. Stating which one fired, because the previous
spike in this series wrote a reading and then did not apply it to its own numbers:

| pre-registered outcome | fired? |
|---|---|
| gaps UNIFORM → a fixed client timer | ✅ **this one** — with one correction to the wording it was written in, below |
| gaps COLLAPSE near completion → something signalled the client | ❌ last inter-dispatch gap (5.004 s) == first (5.002 s) |
| non-`batchexecute` traffic between polls is the candidate signal | ⚠️ **partly** — 5 hits found. All outbound telemetry, and the cadence is untouched across them, so none is the signal. Reported in §3 rather than dismissed |
| `jwpduf` **or `as29s`** HELD IN FLIGHT for many seconds → long-poll | ❌ — `jwpduf` max 0.809 s, and `as29s` (2.541 s) and `YhhmEf` (4.301 s) are one-shot fetches that span no work. §4 |
| submit refused / timed out → **UNMEASURED** | ❌ n/a, the run completed |

**The correction.** The pre-registration wrote that arm as *"a client-side timer **we already
own**; latency is **ours to tune**"*. The data fired the arm and refuted its consequent: the
timer is **Flow's page's**, and gflow observes it. The script's docstring said the same thing
and is corrected in this PR. Recording it rather than quietly rewording, because a
pre-registration you edit after the fact is not one — and because a ✅ on a row whose
consequent the body contradicts is the exact failure this table exists to catch.
