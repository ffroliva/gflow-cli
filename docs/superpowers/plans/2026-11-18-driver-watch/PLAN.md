# Driver Watch Implementation Plan (as built)

**Goal:** A maintainer is notified, through one GitHub issue per package, when PyPI
publishes a `playwright` or `patchright` release that the pins in `pyproject.toml` do not
admit, without any automatic pull request or pin change.

**Architecture:** One standard-library script, `scripts/ci/driver_watch.py`, split into pure
functions (`pins`, `admits`, `bumps`, `plan`, `body`) and two I/O edges (`_latest` for PyPI,
`_gh` for the GitHub CLI), driven by `main()`. A new `driver-watch` job in the existing
`.github/workflows/deps-watch.yml` runs it weekly and on `workflow_dispatch` with a
`dry_run` input. Dependabot's ignore rules for both drivers stay unchanged; the job only
announces.

**Predict verdict:** not run (CI tooling with no production call path). Edge cases audited
after the fact in `SCENARIO.md` (37 scenarios: Critical 1, High 7, Medium 12, Low 17).

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| Critical | The job modifies code or pins | Job permissions `contents: read`, `issues: write`; `persist-credentials: false`; no file writes in the script (SCENARIO #1) |
| High | The mutating `gh` path has never run against GitHub | Task 6: one non-dry dispatch on the branch, then a converging re-run (SCENARIO #8) |
| Medium | A bot-closed issue is read as a maintainer decline, so a release is silent after a pin revert | Task 7 (SCENARIO #10) |
| Medium | Patch releases inside a range pin are never announced | Task 9: document as out of scope or extend (SCENARIO #11) |
| Low | Overlapping runs open a duplicate issue | Task 8: `concurrency:` group (SCENARIO #28, #29) |

---

## File structure

### New files
```
scripts/ci/driver_watch.py
  Reads the driver pins, queries PyPI, plans and applies issue create / edit / close
tests/scripts/test_driver_watch.py
  16 offline tests: pin parsing, specifier matching, planning, main() against fakes
```

### Modified files
```
.github/workflows/deps-watch.yml
  New `driver-watch` job; `dry_run` input on workflow_dispatch
.github/dependabot.yml
  Comment pointing from the driver ignore rules to the driver-watch job
docs/SECURITY.md, website/docs/SECURITY.md
  "Announced, not applied" paragraph in the patchright section
CHANGELOG.md
  [Unreleased] / Added entry
```

---

## Task 1 — Pin and version parsing (done, `45bf3bac`)

**What:** Read each driver's specifier from `pyproject.toml` and decide whether a version
satisfies it, with the standard library only.

**Files:**
- `scripts/ci/driver_watch.py` — `pins()`, `admits()`, `_version()`
- `tests/scripts/test_driver_watch.py`

**Steps:**
- [x] Read dependencies and every optional-dependency extra
- [x] Strip `[extras]` and environment markers from a requirement
- [x] Fail loudly on an unsupported clause and on a driver pinned two different ways
- [x] Compare by release number so `.postN` and `rcN` do not break the job

**Tests:**
- [x] `test_pins_are_read_from_dependencies_and_extras`
- [x] `test_admits` (5 cases, including zero-padding)
- [x] `test_an_unsupported_clause_is_loud`
- [x] `test_post_and_pre_releases_compare_by_their_release_number` (2 cases)
- [x] `test_extras_in_a_requirement_are_not_part_of_its_pin`
- [x] `test_conflicting_pins_for_one_driver_are_loud`

---

## Task 2 — Bump detection and issue planning (done, `45bf3bac`)

**What:** Turn pins plus PyPI's latest releases into a list of `create` / `edit` / `close`
actions that keeps one issue per package and never reopens a closed release.

**Files:**
- `scripts/ci/driver_watch.py` — `Bump`, `bumps()`, `plan()`, `body()`

**Steps:**
- [x] `Bump.title` keys an issue on package, latest release and pin
- [x] `create` when no open issue exists, `edit` when the open one is stale, nothing when current
- [x] `close` an open issue once its package has no bump
- [x] Skip a release whose exact-title issue is closed
- [x] Issue body names the engine-contract and live-verification gates

**Tests:**
- [x] `test_only_releases_outside_the_pin_are_bumps`
- [x] `test_one_issue_per_package_created_then_updated_never_duplicated`
- [x] `test_an_issue_whose_pin_now_admits_the_release_is_closed`
- [x] `test_a_release_a_maintainer_closed_is_not_reopened`
- [x] `test_the_issue_body_names_the_gates_and_never_auto_merges`

---

## Task 3 — I/O edges and `main()` (done, `45bf3bac`; tests and `--limit` in `10044308`)

**What:** Fetch PyPI, list labelled issues of every state, apply the plan through `gh`, and
support `--dry-run`.

**Files:**
- `scripts/ci/driver_watch.py` — `_latest()`, `_gh()`, `main()`
- `tests/scripts/test_driver_watch.py` — `_run_main` fake harness

**Steps:**
- [x] `gh issue list --label driver-watch --state all --limit 500`
- [x] Issue bodies passed on stdin (`--body-file -`); `gh` stderr printed on failure
- [x] Label created idempotently (`--force`) before the first issue
- [x] `--dry-run` prints the plan and calls nothing but the listing

**Tests:**
- [x] `test_main_creates_one_issue_with_its_body_on_stdin`
- [x] `test_main_closes_an_issue_whose_pin_now_admits_the_release`
- [x] `test_main_dry_run_only_lists`

---

## Task 4 — Workflow job (done, `45bf3bac`)

**What:** Run the script weekly and on demand with the least privilege it needs.

**Files:**
- `.github/workflows/deps-watch.yml`

**Steps:**
- [x] Separate `driver-watch` job, independent of the audit job
- [x] `permissions: contents: read, issues: write`; `persist-credentials: false`; `timeout-minutes: 5`
- [x] `workflow_dispatch` input `dry_run` mapped to `--dry-run`
- [x] `uv run --no-project --python 3.13` (no project install)

**Verification:**
- [x] zizmor: no findings
- [x] Dry-run dispatch on the branch: runs 44318674050 and 44338192777 succeeded, planned `create` for playwright 1.65.2

---

## Task 5 — Docs (done, `45bf3bac`; scope restored in `10044308`)

**Files:**
- `docs/SECURITY.md`, `website/docs/SECURITY.md`, `.github/dependabot.yml`, `CHANGELOG.md`

**Steps:**
- [x] SECURITY "Announced, not applied" paragraph, mirrored to `website/docs/`
- [x] Dependabot comment pointing at the job
- [x] CHANGELOG `[Unreleased]` / Added
- [x] Unrelated reformatting of 56 scripts reverted (`10044308`)

---

## Task 6 — Live run of the mutating path (done: runs 44357602341, 44357779970)

**What:** Execute the non-dry path once against the repository (SCENARIO #8).

**Steps:**
- [x] `gh workflow run deps-watch.yml --ref chore/driver-watch -f dry_run=false`
- [x] Confirm one `driver-watch` issue for playwright 1.65.2, label present, body rendered
- [x] Dispatch again; the log must show no action (convergence, SCENARIO #4)
- [x] Record both run IDs in the PR body

---

## Task 7 — Distinguish a bot close from a maintainer decline (done, `33cf107b`)

**What:** After a pin revert, re-announce a release the bot closed (SCENARIO #10).

**Files:**
- `scripts/ci/driver_watch.py`, `tests/scripts/test_driver_watch.py`

**Steps:**
- [x] Red test: a closed exact-title issue with `stateReason: COMPLETED` yields an action
- [x] Close with `--reason completed`; list `stateReason`; treat only `NOT_PLANNED` as declined
- [x] Reopen (or create) when the closed issue is `COMPLETED`
- [x] State the decline convention ("close as not planned") in `docs/SECURITY.md` and the CHANGELOG

**Tests:**
- [x] `test_a_release_the_job_closed_itself_is_announced_again`
- [x] `test_a_release_a_maintainer_closed_is_not_reopened` and `test_a_release_closed_as_not_planned_stays_declined` use `NOT_PLANNED`

---

## Task 8 — Small hardening (open, Low)

**Files:**
- `.github/workflows/deps-watch.yml`, `scripts/ci/driver_watch.py`, `tests/scripts/test_driver_watch.py`

**Steps:**
- [ ] `concurrency: { group: driver-watch, cancel-in-progress: false }` on the job (SCENARIO #28, #29)
- [ ] Do not announce a latest release that falls below the pin floor (SCENARIO #27)
- [ ] Neutral close comment when the driver is no longer pinned (SCENARIO #32)

**Tests:**
- [ ] `test_a_latest_release_below_the_pin_is_not_announced`

---

## Task 9 — Range-pin scope decision (open, Medium)

**What:** Decide whether patch releases inside a range pin (SCENARIO #11) are out of scope.

**Steps:**
- [ ] Either state in `docs/SECURITY.md` that the watch compares pins, not `uv.lock`, or
  compare the locked version too and announce an in-range release the lock refuses
- [ ] Decision log entry

---

## Task 10 — Tests for behaviour observed correct but untested (open, Low)

**Tests:**
- [ ] `test_a_newer_release_after_a_declined_one_is_announced` (SCENARIO #9)
- [ ] `test_an_environment_marker_is_not_part_of_the_pin` (SCENARIO #23)
- [ ] `test_main_fails_before_any_gh_mutation_when_pypi_is_unreachable` (SCENARIO #15)
- [ ] `test_main_propagates_a_gh_failure` (SCENARIO #16)

---

## Definition of done

- [x] Tasks 1-5 checked off
- [x] `/gflow:check` green (pytest 7267 passed, coverage 93.35%, per the PR body)
- [x] `CHANGELOG.md` `[Unreleased]` updated
- [x] Docs updated (`docs/SECURITY.md` and its website mirror)
- [x] Critical + High scenarios covered by tests or construction, except SCENARIO #8
- [ ] Task 6: the non-dry path has run once against GitHub and converged on re-run
- [ ] No `# TODO` in the diff without a tracked issue link
- [ ] Tasks 7-10 merged or filed as issues

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-18 | Announce through an issue, not a PR | A driver bump needs the engine-contract job and a live run (#1319, #1324) | A Dependabot PR: invites an automatic merge |
| 2026-11-18 | Standard library only | `packaging` is not in the project environment; the job runs with `--no-project` | Installing the project to get `packaging`: slower, more surface |
| 2026-11-18 | Only plain numeric clauses supported; anything else raises | The pins use only `==`, `>=`, `<`; a loud failure beats a wrong comparison | A partial PEP 440 implementation |
| 2026-11-18 | Title keys on package, release and pin | One issue per package, retitled in place; a closed exact title records a decline | A hidden marker in the body: needs a body fetch per issue |
| 2026-11-18 | Listing uses `--state all --limit 500` | gh's default 30 hid closed releases (council round, `10044308`) | Paging: unnecessary at one issue per release |
| 2026-11-18 | Label creation stays idempotent with `--force` | One call, no existence check | Pre-creating the label by hand: a manual step |
| 2026-11-18 | Overlapping manual runs accepted as a duplicate risk | Rare; documented in the PR | Superseded by Task 8 if adopted |
| 2026-11-18 | As-built audit found a bot close indistinguishable from a decline | `plan()` returns no action after a pin revert (SCENARIO #10) | Open as Task 7 |
