# Metrics — headless direct transport (Task 26)

Measured on the branch against its merge base with `develop`, offline suite
(`pytest -n 4 --dist worksteal -m 'not live and not e2e and not smoke' --cov=gflow_cli`):
9,381 passed, 51 skipped. Whole-package line coverage: 94.0 %.

## Coverage of new and changed lines

Lines added or changed in `src/` by this branch, as measured by `coverage.xml`.

| Module | Covered / changed lines | Coverage |
|---|---|---|
| `api/submit_policy.py` | 116 / 122 | 95.1 % |
| `api/generation_route.py` | 141 / 142 | 99.3 % |
| `api/_flow_retry.py` | 79 / 80 | 98.8 % |
| `api/refusal.py` | 8 / 8 | 100 % |
| `api/client.py` (relaunch) | 88 / 92 | 95.7 % |
| `api/transports/direct_http/` (package) | 749 / 768 | 97.5 % |
| All changed `src/` files | 1,746 / 1,793 | 97.4 % |

Three relaunch tests were added to bring `client.py` above the 90 % floor (it measured
89.1 %): a relaunch of an unusable client, a relaunch with no lease, and a failing transport
teardown during a relaunch.

## CRAP of new functions

CRAP = CC² × (1 − coverage)³ + CC, with cyclomatic complexity from `radon cc` and per-function
line coverage. Every new function now scores below 10. Three new functions measured 10 or
more and were split without a change in behaviour:

| Function | Before (CC / CRAP) | After (CC / CRAP) | Split into |
|---|---|---|---|
| `submit_policy.classify_reply` | 22 / 22.0 | 8 / 8.0 | `_raise_content_refusal` (5), `_classify_rpc_errors` (7), `_has_rpc_frame` (3) |
| `DirectHttpTransport.generate_video` | 14 / 14.6 | 9 / 9.3 | `_why_not_direct_video` (4), `_armed_target` (2) |
| `generation_route.choose_upscale_route` | 10 / 10.0 | 6 / 6.0 | `_measured_upscale_source` (5) |

The highest remaining new functions: `run_with_retry` 9.0, `next_step` 7.0.

## Changed functions against their `develop` complexity

Pre-existing functions whose cyclomatic complexity rose on this branch. The branches this branch
added were moved into small private helpers without a change in behaviour, error handling, log
events or call shapes, so each function now sits at or below its `develop` value
(`radon cc -s`).

| Function | CC on `develop` | CC before the split | CC now | Extracted into |
|---|---|---|---|---|
| `GenerationSurface._drive_images_generation` | 16 | 18 | 14 | `_flow_generation._submit_images` (5) |
| `cli_movie._run_movie` | 14 | 15 | 14 | `cli_movie._planned_routes` (2) |
| `cli_video._run_chain` | 12 | 13 | 12 | none: the route list is `list(map(video_route, planned))` |
| `VideoRecordingMixin._build_completed_video_asset` | 11 | 13 | 11 | `_recorder_core._with_run_path` (3) |
| `GenerationSurface.generate_video` | 9 | 11 | 9 | `_flow_generation._note_submit_attempted` (3) |
| `ImageRecordingMixin._persist_generated_image` | 10 | 11 | 9 | `_recorder_core._with_run_path` (shared) |
| `mcp.tools._run_upscale_tool` | 9 | 10 | 9 | `generation_params.visibility_setting` (2) |
| `FlowApiClient._enter_setup` | 9 | 10 | 5 | `FlowApiClient._acquire_lease` (6) |

## Mutation testing

`mutmut` does not run on Windows, so a scripted run applied one mutant at a time to the
retry step, the no-resend rule and the gateway's `sent` phase: each comparison operator
flipped, each `not` dropped, each `and`/`or` swapped, each boolean and integer constant
changed, each checkpoint and stop-kind string replaced, and each statement deleted. Each
mutant ran against `test_submit_policy.py`, `test_flow_retry.py`, `test_client_relaunch.py`,
`test_direct_http_gateway.py`, `test_billed_post_no_retry.py` and
`test_direct_routing_steps.py`.

| Target | Mutants | Killed | Surviving |
|---|---|---|---|
| `submit_policy` (`classify_reply` and its helpers, `next_step`, `launch_visibility`) | 42 | 42 | 0 |
| `_flow_retry` (`run_with_retry`, relaunch steps, `_stop_error`, `_reason`) | 52 | 49 | 3, unreachable |
| `direct_http/_gateway.SubmitAttempt` (`__aexit__`, `send`, `_open`, `mint`) | 33 | 33 | 0 |

The three survivors are guards that no caller can reach: the two `AssertionError` branches
of `_stop_error` (already marked `pragma: no cover`), and the `Indeterminate` branch of
`_reason`, which exists for type narrowing, because an outcome past the send is re-raised
before `_reason` is called.

The first run left 14 survivors, and the run after the split two more; each killable one
received a behaviour test:

- `classify_reply` reports the first matching error frame's cause and code, keeps HTTP 200 on
  a refusal that arrives with a generation record, and names a bare status code in its cause.
- `run_with_retry` tells `observe` whether a refusal was retried, logs the UI fallback with
  its reason, and announces the retry as a new process at the launch visibility.
- The final refusal names one attempt when the relaunch fails and two after a second
  refusal; a relaunch failure with no prior refusal propagates as the same exception.
- `SubmitAttempt.mint` keeps an outcome its token source already decided, and a typed error
  after the send is logged as `direct.indeterminate`.
