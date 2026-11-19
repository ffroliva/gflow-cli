# Spike: naming an agent-only video clip whose page shows only `/asb/` media (#1354)

**Date:** 2026-11-19 · **Host served:** flow.google.com · **Locale:** en-GB · **Cost:** $0
for the probe (navigation and DOM reads); the verification runs below spent two Veo 3.1 Lite clips

## Question

#1354 reports an agent-only `video t2v` that Flow billed and finished, while gflow waited out
600 s and exited 9. The report's DOM showed the finished tile, its hover `<video>` and the chat
option's poster all as opaque `flow.google.com/asb/...` URLs, with no `data-media-id`. gflow
named a clip only from a `flow-content.google/video/<uuid>` URL. Is there anything on the page
or the wire that names the clip?

## Probe

[`scripts/dev/spike_agent_only_asb_join.py`](../../../scripts/dev/spike_agent_only_asb_join.py)
opens a project and records the composer verdict. It reads the same video tiles and chat options
the driver reads, and captures the `Zzl0ze` project listing that the page load fires. For each
tile and option, it reports which listing record carries the same `/asb/` token in its
`poster_url` (`lh3.googleusercontent.com/asb/<token>`).

## Observation

| Composer served | Project | Finished tiles | `flow-content.google` srcs | Tiles joined to a record | Chat options joined |
|---|---|---|---|---|---|
| classic | A | 3 | 0 | 3 of 3 | (none rendered) |
| agent-only | A | 4 | 0 | 4 of 4 | 1 of 1 |
| agent-only | B | 1 | 0 | 1 of 1 | (none rendered) |

- On the agent-only page every finished tile and the chat option are `/asb/`, with no
  `flow-content.google` URL. That is the reported DOM.
- Each tile's `/asb/` token, without its `=…` size spec, equals the token in exactly one listing
  record's poster. That record names the clip's media id, status (3, done), size and creation
  time.
- A clip made during the session joined to its own new record. The file downloaded through the
  tile's `/asb/` hover player matched that record's `size_bytes` exactly (3,337,551 B), so the
  `/asb/` download is the stored asset, not a transcode.

## Decision

When the new finished tile names no uuid, the agent-only driver reads the project listing in a
second tab and takes the record whose poster carries the tile's token. That record must be
finished and created no earlier than the submit, less a 15 s clock margin. The time check is
needed because an agent that only re-renders an older tile to `/asb/` also changes the newest
tile, and that tile's record must not be returned as the new clip.

## Verification

- A direct `run_agent_video` call on the classic composer driven through its agent arm, with the
  driver's `flow-content.google` pattern disabled so that only the listing could name the clip.
  The clip was named and downloaded (H.264 1280×720, 7.92 s).
- The same account was then served the agent-only composer, and `gflow video t2v` ran unmodified
  on that surface (see the release's live-verification ledger).
