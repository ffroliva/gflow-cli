# Scenario: migrated-host gate settles before it decides (#639-A) + `NOT_REDIRECTED` stops disabling the locale probe (#639-B)

Feeds `PLAN.md` in this directory. Predict verdict: **CAUTION 7/10**
(`PREDICT.md`). Live probe evidence:
`PROBE.md`.

## What is being built

**A2** — `get_ui_driver`'s migrated-host bail (`drivers/factory.py:155-175`) currently reads
`page.url` once, at entry, before the post-`goto` redirect to `flow.google.com` has landed, so it
declines and the run pays ~54 s of doomed work. Fix: re-check the host at the points the run
**already blocks**, so the bail costs nothing on a healthy account and fires at the first
blocking wait after the flip. Explicitly **not** a bounded wait, and explicitly **not** a
per-profile cache that changes the navigation target — both rejected in predict, the second by
measurement.

**B2** — `client.py:772-776` returns before `_resolve_account_locale`, which is the only site of
the #643 `<html lang>` recovery. Fix: `NOT_REDIRECTED` skips the **settle**, not the **locale
read**. The state keeps meaning what its own docstring says (`profile_store.py:291`).

## Coverage map

| Dim | Active? | Why |
|---|---|---|
| D1 auth & session | **Yes** | The latched profile is an auth-adjacent per-profile cache; `_check_logged_in` already spans both hosts and must stay that way. |
| D2 WAF / reCAPTCHA | No | No new navigation, no new token mint, no header change. Security persona confirmed zero wire footprint. |
| D3 selector drift & locale invariance | **Yes** | `<html lang>` is a server-rendered attribute read, not a text-label selector — the distinction must hold. Locale feeds URL shape. |
| D4 batch & resume | **Yes** | The flap is **per page load**, so a batch can straddle both hosts mid-run. |
| D5 concurrency & Page pool | **Yes** | Same reason: two pooled Pages can land different hosts in one run. Any global/cached flag would be wrong; the guard must read the Page it is about to drive. |
| D6 data layer | Partial | No `DataStore` change. The `.gflow_locale` profile file is touched — carried under D8/D11 instead. |
| D7 error propagation & exit codes | **Yes** | Exit 36 exists; the new guard must not displace a *more* specific error, and must not turn a transient into a permanent-looking abort. |
| D8 cross-platform paths | **Yes** | Locale-file write on Windows; must reuse the existing best-effort idiom, not invent an atomic one. |
| D9 transport edge cases | No | No HTTP route, response shape, or download URL is touched. |
| D10 headless vs headed | No | Neither fix depends on display or browser channel. |
| D11 input validation | **Yes** | `<html lang>` is page-controlled input that ends up in a URL path. |
| D12 observability | **Yes** | The whole reason this defect survived a release is that the fast path was *inferred*, not observed. |
| D13 MCP parity | **Yes** | Shared transport, but the queued error record is different code. |

## Scenario table

| # | Dim | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D7 | `page.url` is `labs.google` at `get_ui_driver` entry and `flow.google.com` by the first blocking wait (**the field case**) | **Critical** | `FlowHostMigratedError`, exit 36, `retryable: true`, raised at the first blocking wait — **not** `UiSelectorDriftError` exit 23, and not after the full ~54 s | Integration (mocked Playwright, URL flips between calls) |
| 2 | D3/D7 | Old host throughout — 72/72 of today's measured loads | **Critical** | Byte-identical behaviour to v0.66.1: no added latency, no added navigation, no extra `page.evaluate`, driver binds and generation proceeds | Integration + **E2E live (old host) — the merge gate** |
| 3 | D1 | Profile latched at `NOT_REDIRECTED`; `<html lang>` = `pt` | **Critical** | Locale resolves `pt`, is written through `next_locale_state`, **and the settle stays skipped** | Unit |
| 4 | D1 | Profile latched at `NOT_REDIRECTED` on an account that genuinely never redirects (`ffroliva`, measured 60/60 bare-URL, `<html lang>` = `en`) | **Critical** | Records `en`; **no `await_url_settled` call, no 4 s regression**. This is the case B1 would break — the latch is *correct about redirects* | Unit + E2E live (old host) |
| 5 | D5 | Two pooled Pages in one run land different hosts | **High** | The guard reads the Page it is about to drive; a bail on Page A must not abort work on Page B, and no module-level flag may be introduced | Unit |
| 6 | D4 | Batch/chain item 3 of 10 lands migrated, items 1-2 and 4-10 land old host | **High** | Item 3 fails exit 36 `retryable`; the run does not abort the remaining items; no double-billing on retry | Integration |
| 7 | D7 | Flow's web app crashed **and** the URL is migrated | **Medium** | Ordering stays deliberate and documented — `_mode_switch_error` checks the crash first today (`ui_automation_video.py:1447-1456`); the new early guard must not silently invert that | Unit |
| 8 | D7 | `page.url` is unreadable (MagicMock/None/non-str) | **High** | Never classified as migrated — that would turn a transient into a permanent-looking abort. Existing test `test_unreadable_url_still_probes_rather_than_bailing` must stay green | Unit (exists) |
| 9 | D11 | `<html lang>` is empty, absent, or garbage | **High** | `locale_segment_from_lang_attr` returns `None`; state unchanged; bootstrap does not fail | Unit |
| 10 | D11 | `<html lang>` probe raises (page closed, evaluate blocked) | **High** | Best-effort — logged, swallowed, navigation unaffected (`client.py:802-806` pattern preserved) | Unit |
| 11 | D11 | `<html lang>` = `zh-Hans` (region is load-bearing) | Medium | Reduces to `zh`; **known-wrong and already flagged** in the v0.66.1 docstring. Not fixed here; must not regress into a crash | Unit |
| 12 | D11 | `<html lang>` carries path separators or control characters | **High** | Sanitised via `locale_segment_from_lang_attr` **before** `write_account_locale`, which writes verbatim (`profile_store.py:309-317`) | Unit |
| 13 | D12 | A maintainer reads a field timeline and must be able to prove the fast path fired | **High** | A stable structlog event at the detection point, distinct from `ui_driver.migrated_host_bail`, carrying the URL before/after and the elapsed offset | Unit (event asserted) |
| 14 | D12 | Entry URL is already migrated (the isolated case v0.66.1 tested) | Medium | `ui_driver.migrated_host_bail` still fires at entry — no regression on the path that already worked | Unit (exists) |
| 15 | D13 | The same failure via the MCP queued generation path | **High** | Task error record carries `exit_code: 36` **and** `retryable: true` (`worker/daemon.py:449-459` → `mcp/tools.py:306-401`); no new payload key, no `mcp/tools.py` change | Unit |
| 16 | D13 | An MCP agent consults `docs/MCP.md` to decide whether to auto-retry | Medium | `FlowHostMigratedError` appears in the retryable list in `docs/MCP.md` **and** the `website/docs/` mirror | Doc gate (`generate_website_docs.py --check`) |
| 17 | D8 | Locale file write on Windows with a Unicode profile path | Medium | Reuses the existing best-effort `write_text(..., encoding="utf-8")` + `except OSError` idiom — no new atomic-write machinery the sibling cache does not have | Unit |
| 18 | D1/D8 | Two runs on the same profile write `.gflow_locale` concurrently | Low | Best-effort by design; last writer wins, a lost write costs one extra probe. Explicitly out of scope | — (documented, not tested) |
| 19 | D12 | **Process:** the fix is claimed with a number measured in isolation | **Critical** | Any time-to-exit-36 figure comes from a real CLI run. `docs/LIVE_VERIFICATION_v0.66.1.md`'s `time to exit 36 — 0 ms` row is corrected in the same PR | Doc review |
| 20 | D12 | **Process:** the new tests reproduce v0.66.1's blind spot | **Critical** | All five existing bail tests (`tests/api/transports/drivers/test_ui_mode.py:343-410`) hand in an **already-migrated** URL — the precondition that never holds in the field. At least one new test must **start** on `labs.google` and **flip** | Unit (red first) |

## Must-cover before merge (Critical + High)

1. **#1** — a red-first test whose page URL starts on `labs.google` and flips to
   `flow.google.com` between calls. This is the test that would have caught v0.66.1.
2. **#2** — old-host no-regression, proven by a **live run on this machine today** (the migrated
   path is not reachable here; the old host is). This is the merge gate.
3. **#3 + #4** — both latch cases: locale recovered, settle still skipped. #4 is the
   anti-regression for #587 and the reason B1 was rejected.
4. **#8** — unreadable URL never reads as migrated.
5. **#5** — per-Page guard; no module-level flag.
6. **#6** — a mid-batch flip fails one item, not the run.
7. **#9, #10, #12** — `<html lang>` is untrusted page input: empty, raising, and unsanitised.
8. **#13** — a stable, distinct structlog event so the fast path is observable, not inferred.
9. **#15** — exit 36 + `retryable` survive the MCP queued path.
10. **#19 + #20** — the two process guards. These are why this scenario exists at all.

## Deferred (Medium + Low — not blockers)

1. **#11** `zh-Hans` region reduction — already flagged in the shipped docstring; needs an
   account on such a locale. Keep as a known limitation, do not guess.
2. **#7** crash-vs-migrated ordering — document the chosen order; no evidence either ordering is
   wrong today.
3. **#17, #18** — reuse the existing idiom; do not harden what the sibling cache does not harden.
4. **#16** doc list — must-do in this PR, but a doc gate rather than a code risk.

## Open question carried from predict (do not design around a guess)

**When does `page.url` actually flip after `goto` on a migrated load?** Unmeasured — 72/72 of
today's navigations landed old host, so zero migrated loads were sampled. The instrument exists
(`scripts/dev/measure_migrated_host_flip.py`). Until that number exists:

- Do **not** introduce a bounded wait (its bound would be a guess, and 72/72 says it would be
  pure dead time on a healthy account).
- Do **not** promise a sub-second time-to-exit-36. A2's honest value is ~14-22 s, down from ~57 s.
- Report the achieved number from a real run, or report that it is unmeasured.

## Suggested BDD scenarios (`tests/features/`)

```gherkin
Feature: Flow's migrated origin is detected before the run pays for it

  Scenario: the host flips after goto returns
    Given a project navigation that returns on labs.google
    And Flow redirects the page to flow.google.com before the first blocking wait
    When gflow drives a generation
    Then it fails with FlowHostMigratedError and exit code 36
    And the error is retryable
    And it does not report selector drift

  Scenario: the old host is untouched
    Given a project navigation that lands on labs.google and never redirects
    When gflow drives a generation
    Then no additional wait is performed
    And the driver binds and the generation proceeds

  Scenario: an unreadable URL is not mistaken for the migrated origin
    Given a page whose url cannot be read as a string
    When gflow probes the UI cohort
    Then it continues probing rather than aborting

Feature: A latched profile can still learn its locale

  Scenario: the cached state says the account is not redirected
    Given a profile cached as NOT_REDIRECTED
    And Flow renders the document with lang "pt"
    When gflow bootstraps
    Then the account locale resolves to "pt"
    And no URL settle is awaited

  Scenario: the document declares no usable locale
    Given a profile cached as NOT_REDIRECTED
    And Flow renders the document with an empty lang attribute
    When gflow bootstraps
    Then the account locale stays unresolved
    And the bootstrap completes without error
```

## Known-issues cross-reference

- **KNOWN_ISSUES.md → "Flow is migrating to `flow.google.com`; the migrated frontend is not
  drivable"** (Open, #639). This work **mitigates** it — the failure becomes fast and honest —
  and does **not** resolve it: gflow still cannot drive the new frontend. The entry's "What gflow
  does today (v0.66.0)" paragraph needs the v0.66.1/v0.66.2 correction, since the fast-fail it
  implies has never fired in the field.
- **#643** (locale blind on `flow.google.com`) — B2 is what makes the shipped #643 fix reachable
  on latched profiles. #643 should not be closed on the v0.66.1 change alone.
- **#642** (batchexecute spike) — independent. Devil's Advocate confirmed no ordering dependency.
- **#644** (auth check keyed on a labs.google cookie) — **not** a prerequisite; it gates #642's
  httpx transport, not this Playwright path.
