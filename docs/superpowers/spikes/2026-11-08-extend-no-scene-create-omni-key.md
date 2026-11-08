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

- `migrated_extend.EXTEND_MODEL_KEY` (`*_extend_*`) is tried after `MODEL_KEY`, so the
  started event and the result name the real key instead of `unknown`.
- The e2e test now requires that **any** `rqZuUc` names the clip, not that one is sent.
  The scene guard stays for a page that still sends one.

## Open

- **`_4s` in the key is not measured as the extension length.** The docs say a Veo
  extend segment adds about 7 s (KNOWN_ISSUES). Whether an Omni extend adds 4 s needs one
  paid extend. Do not change the docs from the key name alone.
- This is one account on one day. The labs arm and other cohorts were not observed.
