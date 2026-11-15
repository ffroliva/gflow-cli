# CLI `--no-wait`, `gflow status`, `gflow wait` Implementation Plan (#1194)

> **For agentic workers:** Run `/gflow:status --feature 1194-no-wait-status-wait` to find
> the next unchecked task. Implement one task at a time. Run `/gflow:check` before every
> commit.

**Status:** decisions D1, D2, D3 and D5 approved by the owner on 2026-11-15 (the recommendations); D4 taken as the default (task id only). Ready for Task 1.

**Goal:** a shell script can submit a generation, carry on, and come back:
`gflow image t2i "…" --no-wait --json` prints an id at once; `gflow status <id>` reads it
without blocking; `gflow wait <id> --timeout 10m` blocks until it ends.

**Architecture:** the queue row in `generation_queue` is the single record. `--no-wait`
builds the same payload MCP builds (one builder, in `worker/codec.py`), enqueues it and
starts a **detached child `gflow`** (owner decision, 2026-11-15) that claims that one id
and runs `FlowWorker.process_task`, the code MCP `wait=false` already runs. `status` and
`wait` only read the row, through one state mapper in `worker/queue.py`. Nothing changes
on the wire, in auth or in selectors; no migration (the states exist since `0010`).

**Inputs:** triage (`issue_assessment_1194.md`), spike
[`2026-11-15-1194-detached-child`](../../spikes/2026-11-15-1194-detached-child.md),
predict **CAUTION 6/10**, scenario (22 rows, 4 Critical).

**Predict verdict:** CAUTION, confidence 6/10. Mitigations M1–M7 are carried into tasks.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | `status` settles a live child's row as `failed`; the user retries and is billed twice | M3: liveness from a per-job kernel lock the child holds from before its claim to after its last write; never from a pid (Windows reuses pids; `os.kill(pid, 0)` is CTRL_C there). Settle with `only_from=("processing",)` |
| Critical | A row run by an MCP server (`mcp:{profile}` claimant) is settled by the CLI | Only rows whose claimant is `cli-job:` are ever settled |
| Critical | `outcome_unknown` reported as `failed` | M1: one state mapper; `task_payload`'s own `status` is not used |
| Critical | Legacy exit 4 is `RateLimitError` (retryable): a script that resubmits on 4 double-bills | M5 / **[D1]**: legacy 45, stable core 4 |
| High | Worker path differs from the CLI path (image `--out DIR`, default project title, i2v frame self-heal #546, cloud-storage `--out-file` refusal, catalog-write failure, `--tool` provenance, `-n>1` files) | M2: each divergence fixed in the worker or refused with exit 2 before enqueue, pinned by a round-trip test |
| High | Second `--no-wait` on one profile: child fails fast with exit 11 after the parent printed 0 | **[D2]**: the child applies MCP's lease-wait default (180 s) |
| High | Child never starts or dies before claiming: row stays `pending`, `wait` hangs | **[D3]**: spawn handshake; a `pending` CLI row with a free job lock after the grace period settles `failed`, `charged: no` |
| High | The per-job log holds signed URLs and prompts | M4: `GFLOW_CLI_HOME/jobs/`, user-only (`winsec` / 0o700), deleted on success |
| High | Size gates: `cli_image.t2i` complexity 10/10, `process_task` 32/33, `cli_video.py` 2673/2685, `mcp/tools.py` 2691/2691 | M6: every new branch in new modules; the runner and builders move out of `mcp/tools.py`, which frees its lines |
| Medium | `$(gflow … --no-wait)` hangs on the child's inherited stdout | Child stdio to its log; a timed integration test (< 2 s) |

---

## Owner decisions (block Tasks 6+ only where noted)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Exit code of `wait --timeout` (and `--no-wait` acceptance under mode 2) | Legacy **45** (free; 40-44 are mode-2 targets), stable core **4**, as `N1_PLAN.md:30` reserves |
| D2 | Two jobs on one profile | The child waits on the lease like MCP (180 s, `GFLOW_CLI_LEASE_WAIT_SECONDS` overrides); `status` shows `running` with phase `waiting_for_profile` |
| D3 | Spawn handshake | The parent waits ≤ 5 s for the child to take its job lock; otherwise it settles the row `failed` (`charged: no`) and exits non-zero. Grace for an orphaned `pending` row: 60 s |
| D4 | Id forms | Task id only (as `gflow cancel`); a media-id lookup is deferred until someone asks (YAGNI) |
| D5 | MCP twins | `gflow_status` tool (read-only, also no-spend). `wait` exempt in the parity gate with reason: MCP already has `wait=true` and `tasks/get`, and a blocking tool outlives client timeouts |

---

## File structure

### New files
```
src/gflow_cli/worker/states.py
  JobState enum + one mapper from queue row -> (state, charged, next_action)
src/gflow_cli/worker/runner.py
  run_claimed(task_id) and settle_unfinished(), moved verbatim from mcp/tools.py
src/gflow_cli/worker/joblock.py
  per-job kernel lock (the profile_lease primitive) + is_runner_alive(task_id)
src/gflow_cli/cli_jobs.py
  `gflow status`, `gflow wait`, the hidden `gflow _run-job <id>`, and spawn_job()
src/gflow_cli/no_wait.py
  the --no-wait option, its refusals, and enqueue_and_spawn()
tests/worker/test_states.py, tests/worker/test_joblock.py, tests/worker/test_payload_roundtrip.py
tests/cli/test_cli_jobs.py, tests/cli/test_no_wait.py, tests/cli/test_no_wait_subprocess.py
tests/features/no_wait.feature + tests/features/test_no_wait_steps.py
tests/e2e/test_no_wait_e2e.py            (@e2e @e2e_image, $0)
```

### Modified files
```
src/gflow_cli/worker/codec.py      payload builders: request -> payload (inverse of build_*_request)
src/gflow_cli/worker/daemon.py     the M2 divergences (out_dir, project title, name_resolver, ...)
src/gflow_cli/mcp/tools.py         builders and runner imported from worker/; gflow_status tool
src/gflow_cli/mcp/tasks_extension.py  _mcp_status reads worker/states.py
src/gflow_cli/errors.py            JobStillRunning (legacy 45, core 4)
src/gflow_cli/cli_image.py, cli_video.py, cli_gen.py   --no-wait wired via no_wait.py
src/gflow_cli/cli.py               register status / wait / _run-job
src/gflow_cli/cli_capabilities.py  submit.no_wait / status / wait -> yes (CLI + MCP)
tests/mcp/test_cli_parity.py, tests/mcp/test_capabilities.py, tests/cli/test_help_json.py
docs/USAGE.md, docs/ERRORS.md, docs/CAPABILITIES.md, docs/MCP.md, KNOWN_ISSUES.md (~1041),
CHANGELOG.md, website mirror, skills/gflow-cli/SKILL.md + plugin copy
```

---

Two PRs. **PR A (Tasks 1-5)** ships `status` / `wait` over the rows MCP already writes, so it
is useful and e2e-verifiable at $0 before any child exists. **PR B (Tasks 6-12)** adds
`--no-wait`.

## Task 1 — red tests for the state mapper (test scaffold)

**What:** pin the row -> state contract before writing it.

**Files:** `tests/worker/test_states.py`

**Tests created (red):**
- [ ] every queue status maps: pending->queued, processing->running, completed->succeeded,
      failed->failed, cancelled->cancelled, indeterminate/abandoned->outcome_unknown (never failed)
- [ ] `failed` with `reason: cancelled` -> cancelled
- [ ] `charged` / `next_action` come from the row's error record, defaulting per state
- [ ] `_mcp_status` (SEP-2663) is derived from the same mapper and keeps today's mapping

## Task 2 — `worker/states.py`

**What:** `JobState` (StrEnum) and `job_view(task) -> JobView` (frozen dataclass: state,
charged, next_action, phase, media_id, error). `tasks_extension._mcp_status` reads it.
**Steps:** - [ ] implement - [ ] Task 1 green - [ ] `tests/mcp` green.

## Task 3 — `gflow status <id>` (red, then green)

**What:** one read, no browser, no lease. `--json` envelope: `{status, id, state, charged,
next_action, phase, files, flow_project_id, flow_media_id, seed}`, files from
`task_payload`'s result fields only. Unknown id -> `TaskNotFoundError` (fix its stale
"wait=false" remediation text). `@spends_credits(False)`, help-json row, USAGE entry.
**Tests:** - [ ] each state renders (console and JSON) - [ ] unknown id exits with
`TaskNotFoundError`'s code in both exit-code modes - [ ] never opens a browser (no
`FlowApiClient` constructed) - [ ] `-n 3` image row lists 3 files (fix `task_payload`, which
lists only `images[0]`'s asset).

## Task 4 — `gflow wait <id> [--timeout DUR] [--interval S]` + exit code **[D1]**

**What:** poll the row until terminal; terminal state's exit code computed in the **reader's**
mode (`active_exit_code(exit_code_legacy)`); a cancel/recovery record without a code maps by
state. Timeout -> `JobStillRunning` (legacy 45, core 4), `next_action: wait`, same id.
**Tests:** - [ ] succeeded -> 0 - [ ] failed row written in mode 1 read in mode 2 and back
- [ ] timeout -> 45 legacy / 4 core, envelope carries both - [ ] a fake clock: one CLI
invocation per test (sharpit counts per test) - [ ] `docs/ERRORS.md` row; "emitted by
nothing" removed.

## Task 5 — MCP twin, capabilities, docs, e2e for PR A **[D5]**

**What:** `gflow_status` tool (also registered in no-spend mode, not in `_SPEND_TOOLS`);
parity: `status -> gflow_status`, `wait` exempt with its reason; capabilities `status` -> yes;
`COMMAND_CAPABILITY` rows; USAGE / MCP / CAPABILITIES / CHANGELOG.
**e2e ($0):** - [ ] `tests/e2e/test_job_status_e2e.py`: MCP `gflow_generate_image(wait=false)`
-> CLI `gflow status <id>` (queued/running) -> `gflow wait <id>` exits 0 with the file.
**PR A done:** `/gflow:check`, two fresh xhigh reviews, Sonar 0, e2e pasted, merge.

## Task 6 — payload round trip (red) **[needs D2-D4]**

**What:** for every option of t2i/i2i/t2v/i2v/r2v: the CLI's typed request ->
`codec.encode_*` -> `codec.build_*_request` gives back an equal request.
**Tests (red):** - [ ] one parametrized round trip per mode - [ ] the M2 divergences each
pinned: image `--out DIR`, default project title from the prompt, i2v `name_resolver`,
cloud-storage `--out-file` refusal, catalog-write failure warns, `tool_specs` provenance,
`-n>1` files.

## Task 7 — one payload builder (M2)

**What:** `codec.encode_image_payload` / `encode_video_payload` (request -> dict); MCP's
inline builders (`tools.py:~1160`, `_build_video_payload`) call them, so there is one
mapping; MCP's `_confine_to_output_dir` stays in `mcp/`. Fix the divergences in the worker,
extracting helpers so `process_task` stays ≤ 33.
**Steps:** - [ ] Task 6 green - [ ] `tests/mcp` + `tests/worker` green - [ ] size gate.

## Task 8 — move the runner, add the job lock (M3)

**What:** move `_execute_claimed` / `_run_detached` / `_settle_unfinished` verbatim to
`worker/runner.py` (golden master first: `tests/mcp` behaviour unchanged, a deliberate
mutation seen red). Add `worker/joblock.py`: `hold(task_id)` takes a lock file under
`GFLOW_CLI_HOME/jobs/` with the `profile_lease` primitive; `is_runner_alive(task_id)` tries
it non-blocking.
**Tests:** - [ ] real subprocess holds the lock -> alive; killed -> not alive
- [ ] pid reuse irrelevant (no pid read) - [ ] lock file separate from the log.

## Task 9 — hidden `gflow _run-job <id>` and `spawn_job()` (M4) **[D2, D3]**

**What:** the child: hold the job lock, apply the MCP lease-wait default, `run_claimed`,
settle on crash, delete its log on success. `spawn_job`: the spike's flags
(`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` / `start_new_session`), `-P`, the parent's
cwd and `GFLOW_CLI_HOME`, `PYTHONUTF8=1`, stdio to `jobs/<id>.log` (dir hardened), then the
handshake. The command takes only `<id>` (no prompt in argv). Hidden, so the parity walk
skips it; telemetry skips hidden commands.
**Tests:** - [ ] subprocess: parent returns < 2 s with stdout closed - [ ] handshake
timeout settles `failed` and exits non-zero - [ ] log dir is user-only - [ ] telemetry does
not count `_run-job`.

## Task 10 — `--no-wait` on the generation verbs (M6)

**What:** `no_wait.py`: the option (added to t2i/i2i/t2v/i2v/r2v; the `generate` bridges get
it through `bridge_params`), refusals with exit 2 before enqueue (several prompts,
`--prompts-file`, `--stdin`, `--continue-on-error`, `--jitter`), validation and profile
upsert in the parent, absolute paths, then `enqueue_and_spawn`. Output: console id + "gflow
wait <id>"; `--json` `{"status":"accepted","id":…,"next_action":"wait"}`, exit 0 (mode 2: 4,
per [D1]). Parity alias `no_wait -> wait`.
**Tests:** - [ ] each refusal enqueues nothing - [ ] `generate --no-wait` reaches the mode
verb's path - [ ] relative `--out-file` resolves against the parent's cwd.

## Task 11 — dead-runner settling in `status` / `wait` (Critical rows 1-5)

**What:** a `cli-job:` row in `processing` with a free job lock -> `mark_interrupted(...,
only_from=("processing",))`, checkpoint read in the same transaction; an orphaned `pending`
CLI row past the grace -> `failed`, `charged: no`. `mcp:` rows never.
**Tests (BDD, offline, `tests/features/no_wait.feature`):** - [ ] killed at pending /
claimed / submit_attempted / remote_started / after download -> failed,no / failed,no /
outcome_unknown,maybe ×3 - [ ] a live runner is never settled - [ ] an `mcp:` claimant is
never settled - [ ] two concurrent `wait`s settle once - [ ] `cancel` vs the child's submit
gate: either `cancelled` or `abandoned`, never both.

## Task 12 — capabilities, docs, e2e for PR B

**What:** `submit.no_wait` -> yes; the capabilities guard updated; USAGE, CONFIGURATION (the
lease default for jobs), ERRORS, KNOWN_ISSUES (~1041: "the plain CLI has no queue" is no
longer true), MCP, CHANGELOG, skill + plugin copy, website mirror.
**e2e ($0), `tests/e2e/test_no_wait_e2e.py`, `@e2e @e2e_image`:** - [ ] `image t2i …
--no-wait --json --out-file <relative>` exits 0 in < 2 s with an id -> `status` reads
queued/running -> `wait --timeout 5m` exits 0 with the file under the parent's cwd - [ ] MCP
`gflow_status` reports the same state and files. Then one paid `video t2v --no-wait`
round trip (≈10 credits) before merge.
**Measure:** - [ ] Windows console-close and a job object with kill-on-close (spike's open
items) - [ ] POSIX detach in CI (ubuntu, macos).

---

## Definition of done

- [ ] All task steps checked off; each red test seen failing for the expected reason
- [ ] `/gflow:check` green on both PRs; size gate with no baseline raised
- [ ] Two fresh xhigh reviews per PR, one fix round; every CodeRabbit comment answered; Sonar 0
- [ ] BDD covers all Critical + High scenario rows
- [ ] e2e pasted for PR A ($0) and PR B ($0 image + one paid video)
- [ ] CHANGELOG `[Unreleased]`, USAGE, ERRORS, CAPABILITIES, MCP, KNOWN_ISSUES updated
- [ ] Worktree clean; spike output stays in `_spike_out/` (gitignored)

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-15 | Detached child runs `--no-wait` jobs | Owner; spike: a child outlives its parent on Windows and finishes a t2i | Hand to `gflow serve`; no `--no-wait` |
| 2026-11-15 | Child runs `process_task`, not a re-exec of the CLI argv | The worker's checkpoints are what make `outcome_unknown` honest and `gflow cancel` work | argv replay: no checkpoints |
| 2026-11-15 | Liveness = per-job kernel lock | Windows pid reuse; `os.kill(pid, 0)` is CTRL_C on Windows; the lease's own docstring says pid metadata never authorizes reclaim | pid in the claimant (kept as diagnostics only) |
| 2026-11-15 | Payload builder in `worker/codec.py` | Keeps the CLI from importing `mcp/`; one mapping, inverse of `build_*_request` | builders left in `mcp/tools.py` |
| 2026-11-15 | Split into PR A (status/wait) and PR B (--no-wait) | PR A is useful and $0-verifiable on MCP-queued rows alone | one PR |
| 2026-11-15 | D1-D5 as recommended: legacy 45 / core 4; child waits on the lease (180 s); parent spawn handshake <= 5 s, orphan grace 60 s; task id only; `gflow_status` only, `wait` exempt | Owner answers | 9/44 reuse; fail fast; fire and forget; `gflow_wait` |
| 2026-11-15 | Correction: `recover_processing` is not reused | It sweeps every `processing` row for a profile (live MCP jobs too) and runs only from the unreachable `ui/app.py` | the option text offered to the owner said it would settle a crashed child |
