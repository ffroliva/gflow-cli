# Omni 1.2 Flash start+end: the interpolation key, and where the rpc id rides (#1178)

**Date:** 2026-11-08 · **Account:** `denon82` (flow.google.com, no Google AI plan; slider max 10s)
**Script:** [`scripts/dev/spike_omni12_interpolation_key.py`](../../../scripts/dev/spike_omni12_interpolation_key.py)
**Method:** the production `run_video` drives each case (model, duration, resolution, both
frame uploads, prompt, Send). A `page.route` handler installed first records each submit
body and `route.abort()`s it; `page.on("request")` records what gflow's observer sees.
**Cost:** intended $0. **Actual: two Omni 1.2 8s clips billed** (see "The miss").

## Results

| Case | Route handler saw | Observer saw | Model key | Both frame ids | `run_video` |
|---|---|---|---|---|---|
| omni-flash 8s 360p (positive control) | `nprQif` | `nprQif` | `omni_flash_i2v_8s_first_last_360p` | ✅ ✅ | timeout (submit aborted, as intended) |
| omni-1.2-flash 8s 360p | — | `nprQif` | `macaron_cookie_i2v_fl_8s_360p` | not read (refused on key first) | exit 7 `WireFormatError` |
| omni-1.2-flash 8s 720p | — | `nprQif` | `macaron_cookie_i2v_fl_8s` | not read | exit 7 `WireFormatError` |
| omni-1.2-flash 10s 360p / 720p | — | — | — | — | exit 37, credits exhausted by the two rows above |

## Findings

1. **Claim A reproduces.** Omni 1.2's start+end key is `macaron_cookie_i2v_fl_<n>s[_<res>]`:
   the `fl` (first/last) token, and no resolution suffix at 720p — the same suffix rule as
   Omni 1.1, whose 720p key also drops it. `_interpolation_body_problem` accepts only keys
   containing `interpolation` or `first_last`, so it refuses both (exit 7).
2. **Claim B reproduces, measured as a bill.** gflow's observer saw both Omni 1.2 submits
   and refused them, but only after they had left the browser; Flow billed both.
3. **New: Omni 1.2's `nprQif` names its rpc only in the body.** The route predicate here
   matched on the URL `rpcids` param (`url_rpcid(url) in SUBMIT_RPCS`). It caught Omni
   1.1's submit and missed both Omni 1.2 submits, while the observer, which falls back to
   `body_rpcid(body)`, saw them. A billing guard must therefore route every
   `batchexecute` request and resolve the rpc from URL **or** body, as
   `migrated_extend.py` already does in production.

## The miss

The first run's handler read `request.post_data` directly; it raises on gzip/binary
bodies (`0x8b` = gzip), and the handler failed before `continue_()`. I read that as
"routing every batchexecute is unsafe" and narrowed the predicate to URL rpcids — the
exact gap finding 3 names. The extend guard proves the wide route is safe when bodies
are read through `request_body()` (which never raises). Memory:
`omni12-submit-rpc-rides-the-body-only`.

## Not measured

- Whether Omni 1.2's start+end body carries both frame ids (refused on the key before the
  id check; the route never saw the body). The reporter reports it does.
- 10s start+end on Omni 1.2, and anything above 10s (no Google AI plan here; the
  reporter measured the end frame dropped at 12s and 15s on Ultra).
