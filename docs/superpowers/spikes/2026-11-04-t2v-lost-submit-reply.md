# Spike: finding a text-to-video clip after its submit reply is lost (#1141)

**Date:** 2026-11-04 · **Profile:** `ffroliva` (served flow.google.com) · **Version:** 0.92.1 (`develop` 1619429f)
· **Script:** [`scripts/dev/spike_lost_submit_recovery.py`](../../../scripts/dev/spike_lost_submit_recovery.py), t2v arm
· **Cost:** one `veo_3_1_t2v_lite` clip, plus $0 reads

## Question

#1112 recovers a lost video submit reply by matching the id of a file the run uploaded.
`video t2v` uploads nothing, so `adopt_ids` is empty and the listener raises at once. Can a
t2v clip still be identified after its `YhhmEf` reply body is lost, and with which key?

## Method

`Response.text` raises for the submit reply, so gflow sees a real loss, while the probe keeps
its own copy of the body as ground truth. The prompt carried a unique marker. The project
already held 47 records. Readings were registered in the script's docstring before the run.

## Observed

- **The reporter's error reproduces.** `TransportTimeoutError`, with the exact detail
  `the YhhmEf submit reply arrived but its body was unreadable`, 4.2 s after the submit left.
  That detail, without the "no status reply named a clip" suffix, comes only from the
  `adopt_ids`-empty arm of `submit_and_observe`'s `on_response`.
- **Flow made the clip anyway.** A $0 read afterwards listed it as `done`, 2,013,670 bytes,
  `veo_3_1_t2v_lite`. No second submit was made.
- **Status polls name the clip.** 7 of 7 `jwpduf` replies after the loss, from 0.8 s on, were
  readable and each held exactly one record: ours, at status 2, new since the baseline, with
  our prompt at `[5][1]` and our model key. Its workflow and media ids matched the real reply.
- **`created` is whole seconds.** It read `1793819485`; the local clock at submit read
  `1793819485.50`, so the record is stamped *before* the click by the truncation alone.
- **No discriminating id was found in the request.** A lowercase UUID scan of the submit body
  found only the project id, which every record shares. It does not prove the earlier-noted
  session UUIDs are absent, since an uppercase or otherwise encoded id would be missed.
- **A natural read failure appeared on load.** The probe's unpatched read failed on `HTrJv`
  and `Zzl0ze` during page load, before the submit. Whether that is the same Chrome loss was
  not established.

## Reading

Second row of the pre-registered table: no echo, and polls name exactly one in-flight record
with our prompt. Prompt plus model, on a record that is not yet terminal, identifies the clip
here. A strict "created after the click" comparison would have refused this clip, so any
time check needs a margin, and a machine with a skewed clock must fall back to exit 9.

## Not measured

- **Two same-prompt runs in flight in one project.** This is the case that decides safety.
  The rule must refuse more than one match, as `_adopt` already does, but a poll naming only
  the other run was not exercised.
- **Polls reaching status 3 on t2v.** The probe stopped at 53 s: its listing read in the second
  tab timed out after 30 s and ended the watch (fixed in the script). The #1112 e2e measured
  this leg on r2v only.
- **The listing fallback on t2v.** Same cause. A later $0 read did list the clip.
- **The reporter's real loss.** This run forced it, and the rate on t2v is unknown.
- **The MCP path.** It reaches the same transport by reading the code; it was not run.

## Follow-up: two same-prompt runs in flight

`/gflow:predict` on the fix returned CAUTION from all five personas and required this first. [`spike_t2v_two_runs.py`](../../../scripts/dev/spike_t2v_two_runs.py) submitted A, then B with the identical prompt 21 s later, in one project and one page, with nothing aborted. Cost: four lite clips across two runs. The first run was void as a same-prompt test: the composer kept A's text, so B's prompt was doubled.

Observed on the corrected run:

- From B's submit until A finished, all 5 `jwpduf` polls named both records. The first arrived before B's own reply.
- A was never named alone while B was active. `as29s` named A alone once A was terminal.
- Once A left the polls, they named only B, through to its status 3.
- `created` differed by 21 s, the gap between the submits.
- None of the request's three client UUIDs appears in a record, in either case, so no exact key exists.

Reading: second row of the script's table. While both runs are active, matching on prompt and model finds two records, so refusing on more than one match is safe here. The window is the exposure: if B's reply is lost after A has gone terminal, only B matches, which is correct, and a terminal A must not be adopted. This was one run on one page.

Not measured: two separate processes on one project, or a lost reply combined with an overlap.
