# Extend on flow.google.com sends `fZytfe` alone, with an Omni key (#1180)

**Date:** 2026-11-08 · **Cost:** $0 (every request carrying `fZytfe` aborted at a context route)
**Script:** `scripts/dev/spike_extend_1180_capture.py` · **Account:** the maintainer profile, served flow.google.com

## Question

`test_e2e_extend_submit_contract_at_zero_cost` failed on unchanged develop (v0.96.0):
no `rqZuUc` reached the release hook, and `submit_facts` read no model key from `fZytfe`.
Was `rqZuUc` batched behind another rpc, or not sent? Which key did the page use?

## Method

The production `run_extend` drove the editor. A context-level route logged every
`batchexecute` the driver let through: the URL `rpcids`, the id of every `f.req` frame,
and every snake_case key-shaped token in the body.

**Positive control:** the capture logged exactly one `fZytfe`. The capture sees the
submit, so the absences below are real and not a capture miss.

## Result

- 51 requests, **all single-frame**. That refutes the "batched behind another rpc"
  hypothesis.
- **No `rqZuUc` and no `uwAyfb`** anywhere. The page sent `fZytfe` without creating a
  scene from the media id first.
- `fZytfe` carried **`macaron_cookie_extend_4s`**, an Omni-family key. The shared
  `MODEL_KEY` matches only `*_(t2v|i2v|r2v)_*` or `veo_*`, so it read `None`.
  A later `WuwhI` carried the same key.
- The production guard still checked `fZytfe` slot 1 against the workflow id and let it
  through, so the billed-request check is unchanged.

## What changed in gflow

- `migrated_extend.submit_facts` reads the key from its slot in the `fZytfe` entry (next
  to the aspect) instead of searching the body, so the started event and the result name
  the real key instead of `unknown`. The old search could also match a word in the user's
  prompt, which comes earlier in the body: `veo_style`, or any word with `_t2v_`, `_i2v_` or
  `_r2v_` in it.
- The e2e test now requires that **any** `rqZuUc` names the clip, not that one is sent.
  The scene guard stays for a page that still sends one.

## Open

- **One paid extend, measured the same day (12 credits).** It reported
  `model: macaron_cookie_extend_4s`, so this fix works on the billed path. The source clip
  was 7.93 s the day before. After the extend, **the same media id** served an 11.83 s file
  (5.7 MB, first frame identical to the source's). The Omni extend added about 3.9 s
  **in place**, under the source's id, and gflow downloaded that joined clip. Three things
  this PR does not fix: the help says the extension alone is downloaded; the JSON reports
  the Veo constants `seconds_added: 7` / `seconds_billed: 8`; and the catalog write failed
  (`extend_record_failed`, `DataIntegrityError`). They are tracked in #1184.
- This is one account on one day. The labs arm and other cohorts were not observed.
