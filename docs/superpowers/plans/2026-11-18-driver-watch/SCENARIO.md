# Scenario: driver-watch (as-built audit of PR #1330)

**Subject.** The weekly and `workflow_dispatch` job `driver-watch` in
`.github/workflows/deps-watch.yml`, which runs `scripts/ci/driver_watch.py`. The script
reads the `playwright` and `patchright` pins from `pyproject.toml`, asks PyPI for each
driver's latest release, and keeps one `driver-watch` issue per package: created when the
latest release falls outside the pin, retitled when a newer release appears, closed once the
pin admits the latest release. A release whose issue is closed is not reopened. The job never
opens a pull request and never changes a pin.

**Audited revision.** `origin/chore/driver-watch` at `10044308` (commits `45bf3bac`,
`10044308`). Tests: `tests/scripts/test_driver_watch.py` (16 cases).

**Mode.** This is an edge-case audit of a design that is already implemented, not a
pre-implementation exploration. Each scenario records whether the current code and tests
cover it. "Covered (test)" cites the test; "Covered (construction)" means the property holds
by the shape of the workflow or code and no test asserts it; "Not covered" means neither a
test nor the construction guarantees the expected behaviour. Behaviour marked "observed" was
checked by calling `plan()`, `pins()`, `bumps()` and `admits()` directly on the audited
revision.

## Coverage map

| Dimension | Active | Reason |
|---|---|---|
| D1 Auth & session lifecycle | Yes | The job authenticates to GitHub with `GITHUB_TOKEN`; its permission scope is the safety boundary of "never changes a pin". |
| D2 WAF / reCAPTCHA | No | No Flow traffic. |
| D3 Selector drift & locale | No | No browser automation. |
| D4 Batch & resume (re-read as idempotency across runs) | Yes | The job runs weekly against state it left behind; every run must converge on one issue per package. |
| D5 Concurrency | Yes | A scheduled run and a manual dispatch can overlap. |
| D6 Data layer | No | No SQLite or `DataStore`. GitHub issues are the only state. |
| D7 Error propagation & exit codes | Yes | Failures must turn the run red before, not after, partial changes where possible. |
| D8 Cross-platform paths | Partial | The job runs on `ubuntu-latest`; the tests also run on the Windows and macOS CI matrix. |
| D9 Transport edge cases | Yes | Two external services: PyPI JSON API and the `gh` CLI. |
| D10 Headless vs headed | No | No browser. |
| D11 Input validation & boundary values | Yes | PEP 440 specifiers and version strings parsed without `packaging`. |
| D12 Observability | Yes | The plan is printed to the job log; the issue is the notification channel. |
| D13 MCP surface parity | No | CI tooling with no CLI command and no MCP tool. Nothing to mirror. |

The `scenario` skill lists `scripts/` tooling with no production call path as a skip case.
The audit was run on request because the job writes to the public issue tracker on a timer,
where a defect repeats every week without anyone invoking it.

## Scenario table

Severity: **Critical** (the job changes code, pins or merges) · **High** (the announcement
is lost, duplicated or wrong in normal operation) · **Medium** (degraded behaviour in a
plausible but uncommon state) · **Low** (cosmetic or edge-only).

| # | Dimension | Scenario | Severity | Expected behaviour | Coverage |
|---|---|---|---|---|---|
| 1 | D1 | The job can push, open a PR or edit `pyproject.toml` | Critical | Job token has `contents: read` and `issues: write` only; checkout uses `persist-credentials: false`; no code path writes files | Covered (construction); zizmor green. No test asserts the permission block |
| 2 | D9 | Latest release falls outside the pin, no issue exists | High | One `create`, title names package, latest and pin; body passed on stdin | Covered (test): `test_one_issue_per_package_created_then_updated_never_duplicated`, `test_main_creates_one_issue_with_its_body_on_stdin` |
| 3 | D4 | A newer release appears while an issue is open | High | Existing issue is retitled (`edit`), no second issue | Covered (test): `test_one_issue_per_package_created_then_updated_never_duplicated` |
| 4 | D4 | Re-run with the issue already current | High | No action | Covered (test): `test_one_issue_per_package_created_then_updated_never_duplicated` (third assertion) |
| 5 | D4 | The pin is raised and now admits the latest release | High | Open issue closed with a comment | Covered (test): `test_an_issue_whose_pin_now_admits_the_release_is_closed`, `test_main_closes_an_issue_whose_pin_now_admits_the_release` |
| 6 | D4 | A maintainer closes the issue for the current release | High | Not reopened, no new issue for that release | Covered (test): `test_a_release_a_maintainer_closed_is_not_reopened` |
| 7 | D12 | `dry_run` dispatch | High | Only `gh issue list` is called; plan printed | Covered (test): `test_main_dry_run_only_lists`; live dispatch dry runs 44318674050 and 44338192777 succeeded |
| 8 | D9 | The non-dry path (`gh label create --force`, `gh issue create/edit --body-file -`, `gh issue close --comment`) against the real repository | High | Label exists, one issue created with the rendered body | **Not covered.** Only dry runs have executed live; the mutating `gh` calls are exercised against a fake in tests |
| 9 | D4 | A maintainer declined release N, then release N+1 is published | Medium | A new issue is created for N+1 | **Not covered by a test.** Observed: `plan()` returns `create` |
| 10 | D4 | The bot closes the issue because the pin admits release N; the pin is then reverted (the #1319 pattern) while N is still latest | Medium | Release N is announced again | **Not covered, and not the behaviour.** Observed: `plan()` returns `[]`. A bot-closed issue is indistinguishable from a maintainer-declined one, so N stays silent until N+1 ships |
| 11 | D11 | A range pin (`>=1.61.0,<1.62.0`) and a patch release inside the range (1.61.x) | Medium | Out of scope by design: the watch compares the pin, not `uv.lock`; Dependabot ignores the driver, so the lock does not move either | **Not covered.** Observed: no bump. The release appears only in the `watch` job's step summary, which notifies nobody |
| 12 | D11 | Unsupported specifier clause (`~=`, `===`, `==1.6.*`) | Medium | `ValueError`, run fails red | Covered (test): `test_an_unsupported_clause_is_loud` (`~=` only); `===` observed to raise |
| 13 | D11 | One driver pinned two different ways across dependencies and extras | Medium | `ValueError`, run fails red | Covered (test): `test_conflicting_pins_for_one_driver_are_loud` |
| 14 | D11 | PyPI latest is a post-release or pre-release | Medium | Compared by release number | Covered (test): `test_post_and_pre_releases_compare_by_their_release_number` |
| 15 | D9 / D7 | PyPI unreachable, times out (30 s) or returns 404 | Medium | Run fails red before any issue is touched | Covered (construction): all PyPI calls precede the first `gh` mutation. No test |
| 16 | D9 / D7 | A `gh` call fails part-way through the plan | Medium | `gh` stderr printed, run fails red; the next run converges | Covered (construction): `_gh` writes stderr and raises; `plan()` is idempotent. No test |
| 17 | D9 | First run: the `driver-watch` label does not exist yet | Medium | Listing returns `[]`; label created before the issue | Covered (test): `test_main_creates_one_issue_with_its_body_on_stdin` asserts the order; `gh issue list --label <missing>` observed to return `[]` |
| 18 | D9 | More than 30 labelled issues exist (gh's default page) | Medium | Closed issues still found | Covered (test): `test_main_creates_one_issue_with_its_body_on_stdin` asserts `--limit` and `--state all` |
| 19 | D7 / D12 | Scheduled run: `inputs.dry_run` is absent | Medium | `DRY_RUN` expands to empty, issues are written | Covered (construction): `${{ inputs.dry_run && '--dry-run' \|\| '' }}`. Not yet observed on a scheduled run |
| 20 | D7 | The CVE audit step fails in the `watch` job | Medium | `driver-watch` still runs | Covered (construction): separate job, no `needs:` |
| 21 | D4 | A pin change after a decline, with the same latest release | Low | New issue (the title keys on pin and release) | Not covered. Observed: `create`. Defensible: the pin changed |
| 22 | D11 | Requirement carries `[extras]` | Low | Extras stripped from the spec | Covered (test): `test_extras_in_a_requirement_are_not_part_of_its_pin` |
| 23 | D11 | Requirement carries an environment marker (`; python_version<'3.14'`) | Low | Marker stripped | Not covered by a test. Observed: works |
| 24 | D11 | Driver listed bare, without a specifier | Low | Ignored | Not covered by a test. Observed: works |
| 25 | D11 | Short version (`==1.64` against `1.64.0`) | Low | Equal | Covered (test): `test_admits` |
| 26 | D11 | `!=` clause, upper-case package name, spaces inside the spec | Low | Parsed | Not covered by a test. Observed: works |
| 27 | D11 | PyPI latest falls below the pin floor (a yanked release, a rollback) | Low | No announcement, since nothing newer exists | Not covered. Observed: a "release available" issue is created for the older version |
| 28 | D5 | Scheduled run and manual dispatch overlap | Low | One issue | Not covered. No `concurrency:` group, so both runs can `create`. Acknowledged in the PR body |
| 29 | D4 | Two open issues exist for one package (after 28, or by hand) | Low | Duplicate closed | Not covered. Observed: the second stays open until the pin admits the release |
| 30 | D4 | A labelled issue whose title is not the bot's | Low | Ignored, never closed | Not covered by a test. Observed: ignored |
| 31 | D4 | An unlabelled issue about the same release, or a maintainer removes the label | Low | Not seen; the bot creates its own issue once | Not covered. By design |
| 32 | D12 | A driver is removed from `pyproject.toml` while its issue is open | Low | Issue closed | Not covered. Observed: closed with "The pin now admits the latest release.", which is inaccurate wording |
| 33 | D12 | A maintainer edits the issue body | Low | Body overwritten on the next retitle | Not covered. By design |
| 34 | D4 | More than 500 labelled issues | Low | Old declined releases fall off the listing | Not covered. Years away at one issue per release |
| 35 | D1 | The workflow runs on a fork | Low | Scheduled runs are disabled on forks; dispatch needs write access | Covered (construction) |
| 36 | D8 | `pyproject.toml` read on a non-UTF-8 default locale | Low | Read as UTF-8 | Covered (construction): `encoding="utf-8"`; tests pass on the Windows matrix |
| 37 | D12 | The plan is visible without opening the log | Low | Printed to the job log only, not `$GITHUB_STEP_SUMMARY` | Not covered. The issue itself is the notification |

**Count:** 37 scenarios. Critical 1 · High 7 · Medium 12 · Low 17.

## Must-cover before merge (Critical + High)

1. **#1 (Critical)** holds by construction and zizmor. No change required.
2. **#2-#7 (High)** are covered by tests.
3. **#8 (High)** is the only High scenario without a run. The mutating path has not
   executed against GitHub. It is closed by one non-dry `workflow_dispatch` on the branch,
   which opens the expected issue for playwright 1.65.2 (the same issue the first scheduled
   run opens after merge), followed by a second dispatch that must plan no action.

## Deferred (Medium + Low)

- **#10** Bot-closed versus maintainer-declined. The smallest fix: close with
  `gh issue close --reason completed`, list `stateReason`, and treat only `NOT_PLANNED` as
  declined. The current behaviour fails silent, not loud, in the revert case it targets.
- **#11** Patch releases inside a range pin are outside the stated contract ("a release the
  pins refuse"). Record it in `docs/SECURITY.md` or accept it explicitly.
- **#27** Announce only when the latest release exceeds the pin, not when it falls below it.
- **#28/#29** One line: `concurrency: driver-watch` on the job.
- **#9, #15, #16, #23** Cheap tests for behaviour already observed correct.
- **#32** Comment wording on close when the driver is no longer pinned.

## Suggested BDD scenarios

All scenarios exercise our own code with PyPI and `gh` faked, so none carries an `@e2e`
tag; they bind from `tests/features/` if adopted. The existing plain pytest tests already
cover the High rows, so these are optional.

```gherkin
Feature: driver-watch keeps one issue per browser driver

  Scenario: A newer release after a declined one is announced
    Given the patchright issue for 1.65.0 was closed as not planned
    And PyPI reports patchright 1.66.0 against the pin "==1.64.4"
    When driver-watch plans
    Then it creates one issue titled "Driver release available: patchright 1.66.0 (pinned ==1.64.4)"

  Scenario: A release is re-announced after a reverted pin
    Given the bot closed the patchright 1.65.0 issue because the pin admitted it
    And the pin was reverted to "==1.64.4"
    And PyPI still reports patchright 1.65.0
    When driver-watch plans
    Then it creates or reopens an issue for patchright 1.65.0

  Scenario: PyPI is unreachable
    Given PyPI times out for playwright
    When driver-watch runs
    Then it exits non-zero
    And no gh command has been called
```

## Known-issues cross-reference

- `KNOWN_ISSUES.md` carries no driver-watch entry.
- Upstream context: #1319 (patchright pin) and #1324 (close-hang parse) are the incidents
  that justify ignoring driver updates in Dependabot. Scenario #10 is the revert path of
  exactly that class of incident.
- The "required engine-contract check" item is a separate open task and is not part of this
  PR.
