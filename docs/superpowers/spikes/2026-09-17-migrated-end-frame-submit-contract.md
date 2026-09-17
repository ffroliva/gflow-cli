# The migrated host's start+end submit contract

**Date:** 2026-09-17
**Account:** `ffroliva` (served `flow.google.com`), project `c5550ed7-…`
**Cost:** $0 — both frames really uploaded, the submit intercepted with `route.abort()`
**Harness:** `scripts/dev/spike_migrated_end_frame.py` (`--mode chips`, `--mode submit`)
**Issue:** [#639](https://github.com/ffroliva/gflow-cli/issues/639) · PR #831

## Why this spike exists

PR #831 ported `--end-frame` to the migrated composer on the strength of one paid run on a
**contributor's** account, reported in the PR description. Two things made that
insufficient: the evidence was not re-runnable (no capture, no findings doc), and
`flow-capabilities-are-cohort-dependent` says a contract measured on one account does not
transfer. The committed spike script could not have produced it either — it raised
`NameError` on its first statement (`ruff F821`, two hits) and contained no network
capture at all.

## Method

Uploads are free; generating is not. So the probe attaches both frames through the
production path (`attach_start_frame` → `attach_end_frame`), types a prompt, then routes
`**/batchexecute*` and calls `route.abort()` on the first request whose rpcid is in
`SUBMIT_RPCS`. Playwright surfaces the request body *before* it leaves the browser, so the
contract is readable while Flow never sees the submit. Memory:
`credit-free-route-abort-verification`.

## Findings

| # | Question | Answer (measured) |
|---|---|---|
| 1 | Does an End chip exist on this cohort? | **Yes.** Frames sub-mode renders exactly two `button.empty-chip`, `Start` and `End`. |
| 2 | Does clicking End open the picker? | **Yes** — `flow-add-menu-popover-content` with its search input. |
| 3 | What rpc carries a start+end submit? | **`nprQif`** — confirms the PR. |
| 4 | Where does the rpc name travel? | **The URL `rpcids` query param.** `_body_rpcid(body)` did **not** find it. This **refutes** the PR's headline claim that "the rpc name travels in `f.req`, not the URL". |
| 5 | What model key? | **`omni_flash_i2v_8s_first_last`** — *not* the `veo_3_1_interpolation_lite` the PR reports. |
| 6 | Are both media ids in the body? | **Yes**, start and end, in a 3 910-byte body. |

## What this settles

- **The port is real and the rpc id is right.** `nprQif` reproduces on a second account.
- **The key is cohort-dependent**, so the driver must match on key *shape*
  (`*_interpolation_*` or `*_first_last`) rather than a literal. Pinning either literal
  would refuse a valid run on the other cohort. This is the `flow-capabilities-are-cohort-dependent`
  pattern again, now with two data points instead of one.
- **The rpcid carrier is cohort-dependent too, or the original reading was wrong.** Here it
  is in the URL. `_body_rpcid` is kept as a *second* carrier — harmless, and it covers the
  contributor's report — but it is no longer described as the rule.
- **Adoption-by-content was removed.** Its predicate required `_i2v_` in the model key,
  which `veo_3_1_interpolation_lite` does not contain, so it could never fire for the veo
  case it was written for; and for the omni key it matched broadly enough to adopt any
  future rpc echoing composer state, where a parse failure is fatal rather than skipped.
  Both measured submit rpcids are in `SUBMIT_RPCS`, so nothing needs adopting.

## Re-run it

```bash
uv run python scripts/dev/spike_migrated_end_frame.py \
    --profile <name> --project <migrated-project-uuid> --mode chips

uv run python scripts/dev/spike_migrated_end_frame.py \
    --profile <name> --project <migrated-project-uuid> --mode submit \
    --start-frame <a.png> --end-frame <b.png>
```

Both are $0. The same contract is asserted as a re-runnable regression by
`tests/e2e/test_migrated_host_e2e.py::test_e2e_end_frame_binds_the_second_chip_and_submits_interpolation`
(`-m e2e_auth`, zero credits).

Raw output stays in the gitignored `scripts/dev/_spike_out/end_frame/`; it carries media
ids and a project id and is not committed.
