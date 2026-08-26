# Canary self-update takes effect the same night — Implementation Plan

**Goal:** A change to `run_canary.py` takes effect on the run that pulls it, not
the one after.

## Deconstruct

`run_canary.py --pull` does:

```
1. Python loads run_canary.py into memory        <- the OLD copy
2. sync_to_develop() fast-forwards the checkout  <- NEW copy now on disk
3. run_tiers() uses the in-memory argv           <- still the OLD one
```

The script updates its own source and then keeps running the version it started
with. Every runner change is silently one night late.

## Evidence this is real, not theoretical

#572 added `-o junit_logging=all` so a preserved RED would carry the structlog
line that decides #561. Merged 2026-08-25 12:29 UTC. The 2026-08-26 02:00 run
pulled it and produced a RED with **zero** log output.

```
git show 230200b:scripts/canary/run_canary.py | grep -c junit_logging   -> 0
```

`230200b` is what the clone was running when it started that night. The flag was
on disk by the time pytest ran and was not used.

Three REDs are untriageable as a direct result, and the failure mode is invisible:
the natural reading is "#572 did not work", which would prompt rewriting a fix
that was already correct.

## Develop

Re-run the script once, after a pull that actually changed it.

**Windows constraint (load-bearing).** `os.execv` on Windows does not replace the
process image the way it does on POSIX — the CRT spawns a new process and
terminates the current one, so the PID changes. This canary runs under Task
Scheduler, which would see the original process exit and could treat the task as
finished. Use `subprocess.run(...)` + `sys.exit(rc)` instead: one supervising
process, exit code propagated, Task Scheduler semantics unchanged.

**Loop safety (two independent guards).** A canary that re-execs forever is worse
than one that updates late:

1. An env-var guard (`GFLOW_CANARY_REEXECED`) — set before re-running, checked
   first. Even a broken digest cannot loop twice.
2. A digest comparison — no content change, no re-run. This also makes the common
   case (already current) free.

Either guard alone prevents a loop; both together mean a bug in one is not enough.

## Risk register

| Severity | Risk | Mitigation |
|---|---|---|
| High | Infinite re-exec loop starves the host | Two independent guards, either sufficient |
| High | Task Scheduler mis-reads a replaced process | `subprocess.run` + propagate exit code; never `os.execv` |
| Medium | Re-run doubles wall-clock if it fires spuriously | Digest gate means it fires only on a real change |
| Low | Digest read fails (file locked) | Treat as "unchanged": update late rather than loop |

## Tasks

### Task 1 — red tests
- [ ] `_script_digest` is stable across calls and changes with content
- [ ] Guard env var set => `_maybe_rerun_after_pull` returns without re-running
- [ ] Digest unchanged => no re-run
- [ ] Digest changed + no guard => re-runs exactly once, with argv preserved
- [ ] Unreadable script => no re-run (fail safe, not fail loop)

### Task 2 — implement
- [ ] Digest before `sync_to_develop()`, check after
- [ ] `subprocess.run([sys.executable, __file__, *sys.argv[1:]], env=…)`, `sys.exit(rc)`
- [ ] Log `canary.reexec_after_pull` with both short digests so the swap is visible
- [ ] Never re-run when the pull was refused (`sync_to_develop` returned a reason)

### Task 3 — E2E GATE

Unit tests cannot prove the real sequence works. Reproduce the actual failure:

- [ ] Put the canary clone on `230200b` (the pre-#572 runner), detached and clean
- [ ] Run `run_canary.py --pull --dry-run` against `denon82`
- [ ] **Pass criteria:**
  - `canary.reexec_after_pull` appears
  - the run completes exactly once (no loop)
  - the produced JUnit contains `system-out` — proving the pulled flag was used
  - `flow_session_cookie_present` is finally present
- [ ] **Control:** re-run when already current => no re-exec line, still completes

$0 — `e2e_auth` tier only, no credit-spending markers.

### Task 4 — ship
- [ ] `/gflow:check`, CHANGELOG, PR

## Out of scope

Whether the #561 401 is cookie-absent or cookie-rejected. This plan only makes
the evidence *reachable*; reading it is the next task.
