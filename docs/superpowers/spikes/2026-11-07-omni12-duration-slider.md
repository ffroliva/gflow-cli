# Omni 1.2 duration slider — 2026-11-07

Question: can explicit Omni 1.2 select every integer duration 3–15 without
weakening older model limits or spending video credits?

Source base: `0cc97c201d9566e26e00b48e4428160ce16cef2b`.
Google's model table confirms 3–10 without subscription and 3–15 with Google AI
Plus/Pro/Ultra: https://support.google.com/flow/answer/16352836?hl=en.

## Observed surface

A live Japanese flow.google.com menu, read while open in an operation-owned tab
through the application's existing SDK profile owner, offers Omni 1.2 Flash and
Omni 1.1 Flash together. The refreshed video inventory records the actual labels,
including the older models' retirement suffix. No account identity is included.

Omni 1.2 replaces duration radios with `flow-duration-slider`. The input is
`type=range`, min=0, max=12, step=1. Its indices 0 through 12 read back as
3 through 15 seconds, individually verified. The number is language invariant;
the adjacent `.duration-slider-value` uses a translated unit.

An early SDK-path probe immediately after a model change left the index at 5
and display at 8 seconds despite pressing Home for a requested 3s. Another
model-switch run queried before the slider mounted. Waiting for the model menu
to become hidden, then the unique slider to become visible, made the shipped
settings path pass all 13 BDD examples. These are readiness signals, not a retry
or a delay guessing the user's requested duration. Index and displayed seconds
must both match, otherwise the driver refuses before Send.

## Wire observations at zero credits

Pre-registered reading: a prepared R2V key naming the requested seconds with the
exact attached identity is positive evidence. A missing identity or T2V key
keeps the feature blocked. No capture is inconclusive, never success.

The production-submit harness was adapted locally to attach an existing named
character without uploading an asset. Its in-page fetch/XHR wrapper blocks
batchexecute before a network request is created.

| Requested | Captured RPC | Prepared key | Exact character ID |
| --- | --- | --- | --- |
| 3s | MZZa6b | macaron_cookie_r2v_3s_360p | retained |
| 15s | MZZa6b | macaron_cookie_r2v_15s_360p | retained |

Raw account/project captures stay local. The DTO regression was red before the
new model existed; the live BDD uses the real composer and makes no generation
request. CLI/MCP queued codec/catalog/manifest/chain tests cover their actual
producer boundaries with provider I/O mocked.

## Limits

No paid generation, completed clip, primary-image-plus-character combination,
720p, free-account UI or agent-only composer was live verified. Free-account
max=7 handling is an offline negative fixture based on Google's documented
limit. The agent-only path explicitly refuses this new model. Generation and
export spending/approval policies are unchanged.
