# labs.google vs flow.google.com — what actually differs, protocol by protocol

**Date:** 2026-09-14 · **Cost:** $0 (navigation + DOM reads only; no submit, nothing created)
**Script:** [`scripts/dev/spike_two_domain_protocol_survey.py`](../../../scripts/dev/spike_two_domain_protocol_survey.py)
**Evidence:** `scripts/dev/_spike_out/two_domain_protocol_{ffroliva,denon82,ci-probe}_*.json` (gitignored)
**Design:** 3 accounts × 2 entry points × **2 runs** = 12 observations, of which **8 are
measured at app level** and 4 are unmeasured (denon82 never reached the app). Reading
pre-registered in the script docstring.

## Why this was run

Every capability statement this repo makes about the two hosts rests on `batchexecute`,
tRPC and aisandbox REST. A keyword sweep of all 31 prior spike notes found **zero**
mentions of gRPC, gRPC-Web, raw protobuf bodies, WebSockets, server-sent events, or the
negotiated HTTP version. Those were never ruled out — they were never looked for, and
absence-by-omission had been accumulating into confident prose.

The standing mental model was also wrong in a way already visible in our own data: the
repo said "migrated cohort" as if an account were on one host **or** the other, while
both maintainer accounts demonstrably hold a labs NextAuth session **and** a
flow.google.com session simultaneously.

## Observed

| account | entry | run | landed | labs.google reqs | `batchexecute`? | WS | Angular root |
|---|---|---|---|---|---|---|---|
| ffroliva | labs `/fx/tools/flow` | 1,2 | `flow.google.com/` | 1 × **308** | yes | 0 | `aisandbox-root` |
| ffroliva | `flow.google.com/` | 1,2 | `flow.google.com/` | *not visited* | yes | 0 | `aisandbox-root` |
| ci-probe | labs `/fx/tools/flow` | 1,2 | `flow.google.com/` | 1 × **308** | yes | 0 | `aisandbox-root` |
| ci-probe | `flow.google.com/` | 1,2 | `flow.google.com/` | *not visited* | yes | 0 | `aisandbox-root` |
| denon82 | labs `/fx/tools/flow` | 1,2 | `flow.google.com/about` | 1 × **308** | **no** | 0 | `aisandbox-root` |
| denon82 | `flow.google.com/` | 1,2 | `flow.google.com/about` | *not visited* | **no** | 0 | `aisandbox-root` |

**Every categorical cell agrees between run 1 and run 2.** Request *volume* does not:
denon82's labs entry drew 171 then 169 requests. So the cells are stable; the traffic is
not identical, and a claim of run-to-run identity would be wrong.

**denon82's four cells are UNMEASURED for anything app-level**, per this spike's own
pre-registered reading ("a host that redirects to `/about` → unmeasured for that cell").
It never reached the app: `batchexecute` count **zero**, against `['batchexecute']` on the
other two. Its 308 still counts — that happens before the redirect — but its WebSocket and
content-type zeros are *absence of a loaded app*, not absence of a WebSocket. **Measured
app-level: 8/8. Unmeasured: 4.**

### 1. `labs.google/fx/tools/flow` is an HTTP **308 Permanent Redirect**

Not a client-side handoff. A server-side, permanent, HTTP-level redirect: **6/6
observations that actually visited labs** — 3/3 accounts × 2/2 runs — each making exactly
one request to `labs.google` and getting 308. The `flow.google.com/` rows never touch labs
at all, so they neither support nor contradict it.

This is a different mechanism from the `/about` redirect, which
[is decided client-side](2026-09-11-about-redirect-is-decided-client-side.md). Do not
conflate them — they have different causes and different remedies.

### 2. The graduated app IS the aisandbox app

The Angular root custom element on `flow.google.com` is **`aisandbox-root`** — the same
name as the REST host `aisandbox-pa.googleapis.com` we have been calling "the labs API".
Present on all three accounts. Supporting custom elements: `flow-project-card`,
`flow-app-header`, `flow-banner`, `router-outlet`, `mat-icon` (Angular Material).

Together with two facts already in the codebase — aisandbox returns protobuf **Duration
strings** inside JSON (`api/scene.py:3`), and `application/json+protobuf` is *rejected
400* by agentInfo (`api/client.py:1852`) — the picture is one product lineage with a
JSON transcoding over a protobuf service, not two products.

### 3. No WebSocket. No streaming. No gRPC. No raw protobuf on Flow's own hosts

**0 WebSocket events in the 8/8 observations where the app actually loaded** (denon82's
4 are unmeasured — see above). No `text/event-stream`, no `application/grpc*`, no
`application/x-protobuf`, no NDJSON, no multipart streaming.

**The instrument was validated, not assumed.** A negative from an unarmed detector is
worthless, so the same instrumentation was pointed at a page that *does* open a WebSocket:
it fired (`Network.webSocketCreated` + Playwright's `websocket` event, detector = 2), on
both hosts, across the cross-origin handoff. A four-arm isolation found exactly one
blind-spot class — pages sending `COOP: same-origin` — and **neither host is in it**:
flow.google.com sends `same-origin-allow-popups`, labs.google sends none.

The single `application/json+protobuf` hit per run is **not Flow**: it is
`ogads-pa.clients6.google.com/$rpc/google.internal.onegoogle.asyncdata.v1` — the OneGoogle
account bar, a shared Google surface. Worth knowing as the shape of Google's JSON-over-
protobuf convention (`$rpc/<fully.qualified.Service>`), but it is not a Flow wire.

**So: STOMP is not applicable** (it rides on WebSocket, and there is none), and any future
design that assumes a push channel has to establish one first.

### 4. HTTP/3 (QUIC) is the dominant transport — previously unrecorded

34–171 of each run's requests negotiate **h3**; 1–4 negotiate h2; 1–3 per run report no
protocol at all and are not counted. Never measured before,
and not visible through Playwright's API — it takes CDP `Network.responseReceived`.

### 5. `denon82` is persistently on `/about`

Both entry points, both runs, landing on `flow.google.com/about`. Its inability to open a
project is **not transient**, corroborating
[about-redirect-is-stable-for-an-account](2026-09-11-about-redirect-is-stable-for-an-account.md).

## What this changes

**"labs-only" is not a testable capability axis on the profiles surveyed.** labs 308s to
flow.google.com for all three of them. Scope this precisely: nine profiles exist here, but
only these three still hold a live Flow session, so the other six were not surveyed and say
nothing either way. A "labs-only" claim cannot be tested on any of the three, which makes it
unfalsifiable *here* rather than false.

It is also **not** a claim that labs is gone. v0.67.0 drove a pt-locale profile through the
labs route end to end (`docs/PROJECT_STATUS.md`), and `migrated_route()`'s labs arm is
still reached routinely for reasons unrelated to host — an unreadable URL, `about:blank`,
or any request the caller did not prefer the migrated host for.

**The right vocabulary is per-surface uplift, not per-account cohort.** An account is not
"on labs" or "migrated". Surfaces graduate independently, which is why `auth` and
`credits` fail independently on the same profile.

## NOT measured — leads, not conclusions

- **Project-page traffic.** Only root/idle pages were surveyed. The generation rpcids
  (`YhhmEf`, `jwpduf`, `as29s`, `maseQ`, …) fire on a project page and were not re-observed
  here. A WebSocket opened only during an active generation would not have been seen.
- **Any submit path.** Costs credits; deliberately excluded.
- **Whether `labs.google` still serves a real app to a NON-uplifted account.** We hold no
  such account, so this is **unmeasured, not absent**. What would settle it: one account
  that gets 200 rather than 308 on `/fx/tools/flow`.
- **QUIC/TCP frame internals.** CDP reports the negotiated protocol, not the wire below it.
- **`ci-probe`'s host lineage** was previously mislabelled "labs" in a v0.71.0 note; this
  run shows it landing on flow.google.com like the others.

## Corrected after audit — what the first draft got wrong

This note was audited before it shipped, and three of its claims did not survive. They are
recorded rather than quietly edited, because the failure modes are reusable.

1. **A listener leak corrupted the saved evidence.** `page.on("response", …)` was never
   removed from a **pooled** page, so each observation kept appending into the previous
   one's array. Derived aggregates were computed before the leak and were always clean, but
   the raw list was not — and reading it naively showed a `labs.google` 308 in rows whose
   navigation never touched labs. The first draft's table reported exactly that. Fixed
   (`page.remove_listener` before the snapshot) and **all evidence regenerated**; the table
   above is from the clean run.
2. **"Run 1 and run 2 agree on every cell. Nothing here flaps."** Overstated, and
   contradicted by the draft's own numbers. Categorical cells agree; request volume varies
   by up to ~22%.
3. **The pre-registered "unmeasured" verdict was not applied to its own data.** The script
   declared that a host redirecting to `/about` is unmeasured for that cell — then the
   findings counted denon82's four `/about` cells in a "12/12" WebSocket claim. That is the
   precise failure pre-registration exists to prevent, committed by the person who wrote
   the pre-registration.

One honesty note the audit is right about and this cannot retroactively fix: the script and
these findings landed in **one commit, after the data**. The reading was written first, but
git cannot prove it. Future spikes should commit the script — pre-registration and all —
*before* the run.
