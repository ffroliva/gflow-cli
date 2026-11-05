# Spike: a status reply's video URL is a thumbnail (#1153)

**Date:** 2026-11-05 · **Profile:** `ffroliva` (served flow.google.com) · **Version:** 0.92.1
· **Script:** [`spike_clip_url_redirect.py`](../../../scripts/dev/spike_clip_url_redirect.py)
· **Cost:** $0 for the script. The runs below cost five lite clips and one extension.

## Question

The paid #1141 e2e followed a clip to status 3 and then failed with `HTTP 302`. Is that specific
to a lost reply, and what does that URL lead to?

## Observed

- **It is not specific to a lost reply.** `gflow video t2v --model veo-lite` on clean code, with
  nothing injected: status 3, 3,938,084 bytes, exit 6, nothing on disk.
- **The URL at `[7][0][8]` is a thumbnail.** `lh3.googleusercontent.com/asb/<token>` answers 302,
  then `lh3.google.com/rd-asb/` answers 302, then `lh3.googleusercontent.com/rd-asb/` answers 200
  with `image/jpeg`: 23,804 and 19,550 bytes on the two walked. 29 of 58 records in one project's
  listing carry a URL in that slot.
- **Filtering it alone is not enough on t2v.** With that URL ignored, the run waited out the 20 s
  grace and ended in exit 7, so no status reply in that run named the clip's own URL.
- **The clip's own route still works.** `gflow data download` recovered a stranded clip, and the
  same read inside the run gave exit 0, 2,808,216 bytes, `ftyp`, equal to the record's size.
- **`video extend` differs.** Its `as29s` carried `flow-content.google`, 6,114,933 bytes, exit 0.

## Reading

A status reply's URL slot no longer means "the clip". The clip's route is the source that held in
every measured run, so a record with no clip URL is read there, and a failure in that read keeps
the free-recovery advice, because the clip is already billed.

## Not measured

- When Flow began sending this. The r2v e2e passed on 2026-10-27.
- Other accounts, and an account served labs.
- Whether t2v's `as29s` ever names the clip now. Each t2v run still spends the 20 s grace first.
