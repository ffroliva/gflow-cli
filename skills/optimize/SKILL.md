---
name: optimize
version: "1.0"
description: >
  Make gflow-cli's tests and CI fast without weakening them: measure the wall clock, find what
  dominates it, and apply the lever that fits the scenario (cut a per-test cost, -n workers,
  shards, rebalancing, profile clones for live e2e, cancelling superseded runs). Targets: CI in
  3-5 minutes, the local offline suite in 3 minutes, live e2e as close to the slowest single
  generation as parallelism allows.
---

# `/gflow:optimize [ci | local | e2e]` — test and CI speed

gflow reverse-engineers a black box, so its quality rests on running tests often: the offline
suite on every commit and the live e2e suite on every change to a Flow surface. A slow loop is a
quality problem, because slow tests get run less. This skill is how speed work is done here.

**Targets** (owner rules):
- **CI: 3-5 minutes** wall time per run, on PRs and pushes to develop. Outside that range,
  optimise before other work.
- **Local offline suite: 3 minutes** (the global test SLA).
- **Live e2e: as close to the slowest single generation as parallelism allows.** An image takes
  about 1-2 minutes and a video 2-4 in Flow itself; that is the floor for a test that generates.
  Everything around it (browser launch, page load, fixed waits, running tests one after another)
  is ours to remove.

## The rule that does not bend: never weaken

A speed change never skips, deselects, `xfail`s or marks a test `slow` to get it out of the run;
never loosens or deletes an assertion or shrinks a test's data; never raises a timeout to hide a
wait. Every test that ran before still runs, and still checks the same thing.

What is **allowed and expected here**, unlike in a generic speed rule: adding workers (`-n`),
adding shards, rebalancing shards, and cloning profiles for e2e. gflow's e2e time is mostly
model latency we cannot remove, so running tests concurrently is the main lever, not a dodge.
When a single test is itself slow, cut its cost first; parallelism hides a cost, it does not
remove it.

## The protocol

1. **Measure the wall clock and its critical path.** Never guess, never `--durations` alone (it
   cannot tell setup from a wait from code under test).
   - CI: `gh run list --workflow CI --json databaseId,createdAt,updatedAt,event -L 20` for wall
     time per run; `gh run view <id> --json jobs` for each job's start and end. Separate queue
     time (waiting for a runner) from run time.
   - Local / e2e: run with `--junitxml=<dir>/report.xml`, then rank files by share of their
     worker: `python -m scripts.ci.pytest_speed rank <dir> --root .` (config:
     `pytest-speed.toml`).
2. **Name what dominates**, in one sentence, with the number: "the critical path is shard 2/3
   at 2m40s, 70 s more than the others", "e2e is 106 tests x ~32 s run one at a time".
3. **Pick the lever from the table below** that removes that cost. One lever per change.
4. **Prove it with a red test when the change is to a test or fixture** (the speed fix must not
   change what the test checks), and **A/B it**: at least 3 runs before and 3 after on the same
   machine or runner class. One run each is noise.
5. **Record** the before/after numbers and the decision in the PR body, and in the active plan's
   decision log when there is one.

## Levers

| Lever | Use when | How, here | Watch |
|---|---|---|---|
| **Cancel superseded runs** | Develop pushes queue behind each other | `concurrency:` in `.github/workflows/ci.yml` (in place; never cancel on `main`) | A burst of merges still queues; merge in sequence |
| **Remove duplicate jobs** | Two jobs run the same tests | Compare job test lists; fold, keep one source of coverage | Required-check names in branch protection |
| **Workers (`-n`)** | One process is CPU- or wait-bound and tests are independent | `-n auto --dist worksteal` in CI; `-n 8 --dist worksteal` locally | A test that passes alone but fails under `-n` shares state: fix the state, never serialise it away |
| **Rebalance** (`--dist`, shard split) | One worker or shard finishes far later than the rest | `worksteal` for tail; refresh `tests/test_durations.json` so `scripts/ci/shard_tests.py` splits whole files by measured time | Only when the measured tail is past run-to-run noise in 2 of 3 runs |
| **Shards** (CI matrix) | A balanced shard still exceeds ~2 min | Add a value to `test-313-shard.matrix.shard` (`GFLOW_TEST_SHARD=k/N`); the count floor is checked over the sum in `test-313` | Runner start-up (~20-30 s) is paid per shard; past ~4-5 shards it stops paying |
| **Cut a per-test cost** | A file or test is slow against its baseline | `pytest-speed`: fixture timer (`-p scripts.ci.pytest_speed.fixture_timer`), first-cost, then remove the cost (share an expensive fixture at module scope, drop a fixed sleep for an awaited condition, fake a network call) | Shared fixtures must not leak state between tests |
| **Serial tail** | A job that runs after the tests dominates (SonarCloud needs combined coverage) | Start what can start earlier; keep the merge-coverage step small | Sonar's gate must still see all coverage |
| **Profile clones (live e2e)** | e2e runs one test at a time because one Chrome profile cannot be shared (exit 11) | One copy of a signed-in profile per xdist worker (the lock is keyed by directory), caches left out; `-n` = number of clones. **Status: spike pending**, see below | Concurrent clones of one Google session may sign each other out; daily image quota and credits are per account; WAF throttling |
| **Exclusive resources (e2e)** | Two tests need the same project, quota or port | `@pytest.mark.xdist_group("<resource>")` **and** `--dist loadgroup` for that run (worksteal ignores groups) | Group only what truly conflicts, or the run goes serial again |
| **Cut overhead around a generation (e2e)** | A generating test spends much more than the model's own time | Fixture timer on one e2e file: browser launch, page load, fixed waits; reuse a warm page only where tests do not depend on fresh state | Never reuse state a test asserts on |

## Scenario recipes

- **`ci`**: measure the last 20 runs (step 1). Order of levers: cancel superseded runs → remove
  duplicates → rebalance shards → add a shard → serial tail → per-test cost.
- **`local`**: `-n 8 --dist worksteal`; then rank and cut per-test costs. The full suite OOMs on
  small machines: cap `-n`.
- **`e2e`**: e2e runs only on a machine with live profiles (`GFLOW_CLI_E2E_PROFILE`), never in
  CI. Order: profile clones + `-n` → exclusive-resource groups → cut overhead around each
  generation. Join the agent registry and lease `browser-session` first
  (`~/.agents/skills/multi-agent-dev`); the A/B tool takes `--lease-cmd` / `--release-cmd`.
  Spend credits only under the standing e2e grant, and say what was spent.

## Profile clones — status

The profile lock (`profile_lease.py`) is keyed by the profile **directory**, so a copy of a
signed-in profile has its own lock. Whether N copies of one Google session can drive Flow **at
the same time** without signing each other out is not yet measured: the spike is
`scripts/dev/spike_profile_clone_concurrency.py`, and its result goes in
`docs/superpowers/spikes/`. Until it reports, do not build on clones.

## Integration

- **Claude Code:** `/gflow:optimize [ci|local|e2e]` (thin wrapper around this file).
- **Codex:** `$gflow:optimize`. **Cursor / Aider / agy:** include this file in context.
- Engineering discipline (measure first, a red test before a fix, one verifiable change per
  commit, a decision log) is the project's default for every change; see
  [`plan`](../plan/SKILL.md).
- **Next step:** after an optimisation lands, re-measure on the next 5 CI runs and announce
  whether the target holds.
