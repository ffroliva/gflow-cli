# labs.google vs flow.google.com — what actually differs, protocol by protocol

**Date:** 2026-09-14 · **Cost:** $0 (navigation + DOM reads only; no submit, nothing created)
**Script:** [`scripts/dev/spike_two_domain_protocol_survey.py`](../../../scripts/dev/spike_two_domain_protocol_survey.py)
**Evidence:** `scripts/dev/_spike_out/two_domain_protocol_{ffroliva,denon82,ci-probe}_*.json` (gitignored)
**Design:** 3 accounts × 2 entry points × **2 runs** = 12 observations. Reading
pre-registered in the script docstring *before* execution.

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

| account | entry | run | landed | labs.google status | WS | h3 | h2 | Angular root |
|---|---|---|---|---|---|---|---|---|
| ci-probe | labs/fx/tools/flow | 1,2 | `flow.google.com/` | **308** | 0 | 34 | 2 | `aisandbox-root` |
| ci-probe | flow.google.com/ | 1,2 | `flow.google.com/` | **308** | 0 | 34 | 2 | `aisandbox-root` |
| denon82 | labs/fx/tools/flow | 1,2 | `flow.google.com/about` | **308** | 0 | 131,160 | 2 | `aisandbox-root` |
| denon82 | flow.google.com/ | 1,2 | `flow.google.com/about` | **308** | 0 | 46,48 | 2 | `aisandbox-root` |
| ffroliva | labs/fx/tools/flow | 1,2 | `flow.google.com/` | **308** | 0 | 43 | 1 | `aisandbox-root` |
| ffroliva | flow.google.com/ | 1,2 | `flow.google.com/` | **308** | 0 | 43 | 1 | `aisandbox-root` |

Run 1 and run 2 agree on every cell for every account. Nothing here flaps.

### 1. `labs.google/fx/tools/flow` is an HTTP **308 Permanent Redirect**

Not a client-side handoff. A server-side, permanent, HTTP-level redirect, on **3/3
accounts, 2/2 runs**. Exactly one request reaches `labs.google` per navigation and its
status is 308.

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

**0 WebSocket events in 12/12 observations.** No `text/event-stream`, no
`application/grpc*`, no `application/x-protobuf`, no NDJSON, no multipart streaming.

The single `application/json+protobuf` hit per run is **not Flow**: it is
`ogads-pa.clients6.google.com/$rpc/google.internal.onegoogle.asyncdata.v1` — the OneGoogle
account bar, a shared Google surface. Worth knowing as the shape of Google's JSON-over-
protobuf convention (`$rpc/<fully.qualified.Service>`), but it is not a Flow wire.

**So: STOMP is not applicable** (it rides on WebSocket, and there is none), and any future
design that assumes a push channel has to establish one first.

### 4. HTTP/3 (QUIC) is the dominant transport — previously unrecorded

34–160 of each run's requests negotiate **h3**; 1–2 negotiate h2. Never measured before,
and not visible through Playwright's API — it takes CDP `Network.responseReceived`.

### 5. `denon82` is persistently on `/about`

Both entry points, both runs, landing on `flow.google.com/about`. Its inability to open a
project is **not transient**, corroborating
[about-redirect-is-stable-for-an-account](2026-09-11-about-redirect-is-stable-for-an-account.md).

## What this changes

**"labs-only" is not a live capability axis for any account we hold.** labs 308s to
flow.google.com for all three. Code, docs or issue replies that branch on "labs vs
migrated" are branching on something that no longer varies here — and a claim that a
feature is "labs-only" cannot be tested on our accounts at all, which makes it
unfalsifiable rather than true.

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
