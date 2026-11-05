# Plan: recover a text-to-video clip after a lost submit reply (#1141)

**Goal:** a t2v run whose `YhhmEf` reply body is lost follows its clip instead of failing with
exit 9 while Flow bills and finishes it. Never adopt a clip that may be someone else's.

**Inputs:** [spike](../../spikes/2026-11-04-t2v-lost-submit-reply.md), [SCENARIO.md](SCENARIO.md),
`/gflow:predict` CAUTION 5 of 5.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Opt-in | New keyword `adopt_prompt` on `submit_and_observe`, passed only by `run_video` for t2v with `count == 1` | `run_extend` must keep its immediate exit 9 |
| Identity | Exact `[5][1]` prompt, exact `[5][6][1][0]` model key taken from the outgoing body, `[1]` equal to `project_id` | No client id is echoed (spike) |
| Polls | Collect candidates, never adopt. Only `rec.is_running` counts | Ambiguity is judged across everything seen |
| Listing | Required, and it must name the clip. Read once a candidate finishes, or at the 60 s budget if no poll named one. It does not name a clip still generating (measured twice) | It is the only global view |
| Listing unavailable | Exit 9 | Bounds the generate-lock hold to about 90 s |
| Time window | `created` from 15 s before the click up to now | `created` is whole seconds and precedes the click (spike); a re-run takes longer than 15 s to reach its click |
| Outside the window | A matching running record raises exit 9 at once | It may be another run of the same prompt |
| New surface | No flag, env var, exit code or MCP parameter | All refusals use `_outcome_unknown`, `retryable: false` |

A clock running behind is undetectable. The listing check is what covers it.

## Tasks

- [x] 1. `generation_records_matching` in `batchexecute.py`: strict match, unit tests for rows 9, 10.
- [x] 2. Failing `_observe_lost` cases for rows 1-8, 11, 13, 14.
- [x] 3. `submit_and_observe`: capture the key, collect candidates, decide at the listing.
- [x] 4. `run_video` passes `adopt_prompt`; test rows 12 and 15.
- [x] 5. Docs and CHANGELOG. KNOWN_ISSUES if an entry names this failure.
- [x] 6. MCP: no task needed. Same `run_video`, no parameter added (scenario row 16); state it in the PR.
- [x] 7. `/gflow:check`, council, then the paid e2e on the final tree: forced loss, one lite clip.
