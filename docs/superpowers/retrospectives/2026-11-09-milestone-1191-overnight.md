# Retrospective: milestone #1191 overnight (spike #1192 + six local tickets)

**Date:** 2026-11-09/10 · **Scope:** tracking issue #1191: spike #1192, tickets #1201, #1197, #1196,
#1193, #1203, #1198, the ROADMAP rewrite.

## Outcome

Ten PRs are open against `develop`, and none was merged, as the run required:

| PR | What |
|---|---|
| #1205 | spike docs |
| #1207 | ROADMAP |
| #1204 | #1196 |
| #1206 | #1197 |
| #1208 | #1201 |
| #1210 | #1203; also closes #1209 |
| #1211 | #1193 |
| #1214 | #1198, stacked on #1208 |
| #1215 | CI timezone fix |

Follow-ups were filed as #1209, #1212 and #1213. The spike verdict was posted on #1192, and the
endpoints each lifecycle ticket can use were posted on #1194, #1195, #1199 and #1200.

The spike found:
- **The `aisandbox-pa` Bearer route is a no-go.** No token can be obtained on the host Flow served.
- **flow.google.com `batchexecute` reads work with no page and no browser.**
  ([spike](../spikes/2026-11-09-page-free-reads.md))

## Worked

| Practice | Evidence |
|---|---|
| Two independent xhigh reviews per feature PR (`/code-review` and `ponytail-review`, fresh agents given the code) | All six feature PRs were green on the ten gates and SonarCloud before review, and every one still had real defects. Examples: billed paths reporting `charged: no` (#1208, three rounds), a cancel race that reported a running, possibly billed job as "already finished" (#1214, reproduced), `gflow -v video --help --json` misrouting (#1210), `capabilities://self` naming tools a `--no-spend` server removes (#1206) |
| Routing review findings to the PR that owns the code | #1214's review found #1208 defects, which went to #1208; #1214 then rebased |
| Positive controls in the spike | The `as29s` and `nzlxg` reads had a junk-`at` arm (400) and a no-cookie arm (401); the signed download is different, it answered 206 with no cookies. The app's own `as29s` returned 200 on the same clip |
| Proving a CI failure is not ours before acting | The Faeroe failure was re-run on a develop-identical docs PR (#1207) and reproduced in WSL Ubuntu 24.04 before any test was touched |
| A $0 live e2e where the tier allows it | `image gen` ran live (`e2e_image`, zero credits): 1 passed |

## Didn't work

| What happened | Root cause | Mechanism now in place |
|---|---|---|
| CI went red on every PR (develop included) on `test_consent_region_from_iana_timezone[Atlantic/Faeroe-False]` | Ubuntu 24.04's tzdata 2026e moved backward zone links into `tzdata-legacy`, which runners lack. The test read the machine's zone database | #1215: the mapping test stubs `ZoneInfo`; a new test pins fail-closed for an unloadable alias |
| Memory said browserless flow.google.com reads were dead | A September absence of `SNlM0e` was recorded as permanent | The memory was corrected, and the spike doc says `SNlM0e` comes and goes, so a reader must fall back to the page |
| Four implementer agents ran against a cap of three | Review-fix rounds resumed through SendMessage were not counted | A memory rule now counts resumed rounds before any dispatch |
| An agent skipped a zero-credit e2e | Its brief said "no credits", which it read as "no e2e" | A memory rule: video e2e needs authorized credits; image, auth and telemetry e2e run |
| `pyright src` in a worktree reported 117 to 118 false errors | It picked the system Python instead of the worktree venv | Run `uv run pyright src` from the worktree, or pass `--pythonpath .venv/Scripts/python.exe` |
| Load-sensitive tests (`test_built_distributions_contain_sql_migrations`) failed once under four concurrent suites | Machine load | The agent-cap rule above; such failures are re-run alone before being acted on |

## Rulings

- Every feature PR gets `/code-review` xhigh plus `ponytail-review` xhigh by fresh agents, then one consolidated fix round (owner, 2026-11-09).
- `cancel` uses exit 22 with a new `ServerCancelUnavailableError`, because 22 already means "this capability isn't available here". This is pending owner review; #1202 redesigns exit codes.
- A cancel after a submit attempt reads `reconcile`, not `none`, because Flow may have billed. Pending owner review.
- Image generations count as `spends_credits: true` in `--help --json`: the issue says "submit generations", although images cost quota, not credits. Pending owner review.

## Open

- Merge #1215 first, then re-run CI on every open PR. SonarCloud reads 0% new-code coverage on the affected PRs because their test jobs failed.
- Whichever of #1206 and #1210 lands last must add capability entries and `@spends_credits` decisions for `models list|show`, `image gen`, `video gen` and `cancel`. Both PRs touch the test Click-leaf walker; unify it on the second.
- Not started, by rule: #1194, #1195, #1199, #1200, #1202.
- Video `gen` e2e and the MCP queued paths for video still need a credit-authorized run.
