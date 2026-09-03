# Migrated-Host Gate Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature migrated-host-gate` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** On a migrated `flow.google.com` load, `gflow` fails with exit 36 at the first moment
the host is knowable instead of after ~57 s of doomed probing — and a profile latched at
`NOT_REDIRECTED` can still learn its locale from `<html lang>`.

**Architecture:** Two independent fixes in the shared transport, so CLI and MCP are repaired
together. **A2** extracts the existing one-shot host check in `drivers/factory.py` into a shared
`raise_if_migrated(page)` helper beside `flow_host_kind` in `transports/_common.py`, and calls it
at the three points the run is **about to spend time** — all free, none adding a wait. **B2**
gives `_resolve_account_locale` a `settle` flag so the `NOT_REDIRECTED` cache skips the *settle*
without skipping the *locale read*, making the shipped #643 `<html lang>` recovery reachable.
Nothing else changes: no new navigation target, no new cache, no new CLI option, no new exit code.

**Predict verdict:** **CAUTION — 7/10** (`PREDICT.md`)
**Scenario:** `SCENARIO.md` in this directory — 20 scenarios, 10 must-cover.
**Probe evidence:** `PROBE.md` — 72 navigations, two profiles, zero
migrated loads.

**Rejected in predict, recorded so nobody re-proposes them:**

| Rejected | Why |
|---|---|
| **A1** bounded settle | 72/72 measured navigations had **no post-`goto` URL change at all** — the bound would be pure dead time on every navigation, at all four `_settle_if_redirecting` sites. This is the #587 regression re-created for a second signal. |
| **A3** cache "migrated", navigate straight to `flow.google.com` | Would have locked **both** maintainer accounts out of a working frontend today. Converts a flapping, retry-recoverable failure into a permanent one, and makes `FlowHostMigratedError`'s own remediation text false. |
| **A4** `framenavigated` listener | Buys nothing without an event-driven rewrite the call chain does not have; introduces cross-module shared mutable state (`docs/ARCHITECTURE.md:45`). |
| **B1** delete the `NOT_REDIRECTED` early return | `ffroliva` is latched **and correct** — measured 60/60 bare-URL loads with no redirect. B1 would reintroduce a 4 s settle on an account with nothing to settle. |

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| **Critical** | A regression on the old host — the only path that still works, and the one 100% of today's loads take | Old-host live run is the **merge gate** (Task 8). The guard adds zero waits and zero navigations; every new call site is a `urlsplit` + dict lookup. |
| **Critical** | Repeating v0.66.1's mistake: green tests + broken field path, because every existing bail test hands in an **already-migrated** URL | Task 1 adds a red-first test whose URL **starts** on `labs.google` and **flips**. |
| **Critical** | Claiming a time-to-exit-36 the code does not deliver | No number is written down that did not come from a real CLI run. Task 7 corrects `LIVE_VERIFICATION_v0.66.1.md`'s `0 ms` row in the same PR. |
| **High** | Latched profiles stay blind to the host signal | Performance persona's scoped STOP: **B2 lands before A2** (Task 4 before Task 5). |
| **High** | `raise_if_migrated` raising from a shared helper aborts a path that was fine | Only three call sites, each already a "we are about to burn time" point. Not added to `_probe_selector_cascade` in this plan — deferred to Task 9 behind a measurement. |
| **High** | `<html lang>` is page-controlled input that ends in a URL path; `write_account_locale` writes verbatim | Sanitise via `locale_segment_from_lang_attr` **before** `next_locale_state` / `write_account_locale` — reuse, do not re-implement. |
| Medium | The achieved latency is unknown because the flip timing was never measured | Instrument exists (`scripts/dev/measure_migrated_host_flip.py`). Report what a real run shows; if no migrated load is reachable, report it as unmeasured. |
| Medium | `docs/MCP.md` retryable list omits `FlowHostMigratedError` — becomes user-visible the moment exit 36 actually fires | Task 7, plus the `website/docs/` mirror. |

---

## File structure

### New files
```
tests/api/transports/test_migrated_host_gate.py
  A2: the flip case, the per-Page case, the unreadable-URL case, the new log event.
tests/features/migrated_host_gate.feature
  BDD for both fixes (Critical + High scenarios).
scripts/dev/measure_migrated_host_flip.py            [already written this session]
  Live instrument: when does page.url flip after goto? Zero credits.
```

### Modified files
```
src/gflow_cli/api/transports/_common.py
  + raise_if_migrated(page) beside flow_host_kind — the single guard, one log event.
src/gflow_cli/api/transports/drivers/factory.py
  get_ui_driver's inline bail -> raise_if_migrated; add a tick-level check in detect_ui_mode.
src/gflow_cli/api/transports/ui_automation_video.py
  _exit_agent_mode: guard AFTER the media panel is found absent, BEFORE _dismiss_agent_affordances.
src/gflow_cli/api/client.py
  _read_account_locale / _resolve_account_locale: settle flag; NOT_REDIRECTED skips the settle only.
tests/api/test_client_locale_cache.py
  New cases; test_cached_no_redirect_skips_the_probe_entirely needs renaming and re-aiming.
tests/api/transports/drivers/test_ui_mode.py
  Existing bail tests stay (they cover the entry case); add the flip case in the new file.
docs/MCP.md + website/docs/MCP.md · KNOWN_ISSUES.md · docs/LIVE_VERIFICATION_v0.66.1.md · CHANGELOG.md
```

---

## Task 1 — A2 red tests (no production code)

**What:** Encode the field precondition the v0.66.1 tests missed.

**Files:** `tests/api/transports/test_migrated_host_gate.py`

**Steps:**
- [ ] Build a fake Page whose `.url` returns `labs.google/...` on the first read and
      `flow.google.com/project/<id>` on subsequent reads — the flip, not a static migrated URL.
- [ ] Assert against the real `get_ui_driver` / `detect_ui_mode` / `_exit_agent_mode` entry points.

**Tests created (red):**
- [ ] `test_a_flip_after_entry_still_raises_exit_36` (S#1) — URL starts labs, flips before the
      first blocking wait → `FlowHostMigratedError`, exit 36, `retryable`, **not** `UiSelectorDriftError`.
- [ ] `test_the_flip_is_caught_before_the_agent_dismissal_burns` (S#1) — once the media panel is
      absent and the URL has flipped, `_dismiss_agent_affordances` is never entered.
- [ ] `test_old_host_adds_no_wait_and_no_navigation` (S#2) — no `wait_for_url`, no `goto`, no extra
      `evaluate` on a labs-only page.
- [ ] `test_guard_reads_the_page_it_is_about_to_drive` (S#5) — two Pages, different hosts; a bail on
      one does not abort the other. No module-level flag.
- [ ] `test_unreadable_url_is_never_migrated` (S#8) — non-str `.url` keeps probing.
- [ ] `test_bail_emits_a_stable_event_naming_the_before_and_after_url` (S#13).
- [ ] `test_entry_case_still_bails_immediately` (S#14) — no regression on the path v0.66.1 covered.

---

## Task 2 — B2 red tests (no production code)

**What:** Encode the latch cases, including the one that must **not** change.

**Files:** `tests/api/test_client_locale_cache.py`

**Steps:**
- [ ] Re-aim `test_cached_no_redirect_skips_the_probe_entirely` — its name asserts the defect.
      Rename to `..._skips_the_settle_but_still_reads_the_lang_attr` and invert the assertion.

**Tests created (red):**
- [ ] `test_a_latched_profile_recovers_its_locale_from_lang` (S#3) — cached `NOT_REDIRECTED`,
      `<html lang>` = `pt` → resolves `pt`, written through `next_locale_state`.
- [ ] `test_a_latched_profile_still_skips_the_settle` (S#4) — `await_url_settled` is **not** called.
      This is the #587 anti-regression; it is why B1 was rejected.
- [ ] `test_a_latched_profile_with_no_lang_stays_latched` (S#9) — empty/absent → state unchanged.
- [ ] `test_a_lang_probe_failure_never_breaks_bootstrap` (S#10).
- [ ] `test_a_lang_with_separators_is_rejected_before_it_is_written` (S#12) — sanitised via
      `locale_segment_from_lang_attr` before `write_account_locale`.
- [ ] `test_zh_hans_reduces_to_zh_without_crashing` (S#11) — known-wrong, must not regress to a crash.

---

## Task 3 — BDD scaffold (red)

**What:** The Gherkin from `SCENARIO.md`.

**Files:** `tests/features/migrated_host_gate.feature` + step defs

**Steps:**
- [ ] Copy the two `Feature:` blocks from `SCENARIO.md` verbatim.
- [ ] Mirror runtime signatures in the fakes (memory: `bdd-stubs-mirror-runtime-signatures`).

---

## Task 4 — B2 implementation (**must land before Task 5**)

**What:** `NOT_REDIRECTED` skips the settle, not the locale read.

**Files:** `src/gflow_cli/api/client.py`

**Steps:**
- [ ] Add `settle: bool = True` to `_resolve_account_locale`; when `False`, skip
      `await_url_settled` and go straight to the `<html lang>` branch.
- [ ] In `_read_account_locale`, replace the `cached == NOT_REDIRECTED` **early return** with a
      call carrying `settle=False`; keep folding through `next_locale_state` and
      `write_account_locale` so `NOT_REDIRECTED` un-latches when a locale is actually observed.
- [ ] Update the `client.account_locale_cached` log line — `settle_skipped=True` stays true, but
      `locale` must now report what was read, not an unconditional `None`.
- [ ] Update `profile_store.next_locale_state`'s "unreachable from the client" comment
      (`profile_store.py:330-332`) — it is now reachable, and that is the point.

**Tests:** Task 2 goes green. No test from Task 1 changes.

---

## Task 5 — A2 implementation

**What:** One shared guard, called where the run is about to spend time.

**Files:** `_common.py`, `drivers/factory.py`, `ui_automation_video.py`

**Steps:**
- [ ] Add `raise_if_migrated(page, *, at: str) -> None` to `_common.py` beside `flow_host_kind`:
      classify `getattr(page, "url", None)`, and on `"migrated"` log one stable event carrying
      `at` and the URL, then raise `FlowHostMigratedError` with the existing detail text.
- [ ] `drivers/factory.py`: replace the inline block (`factory.py:155-175`) with
      `raise_if_migrated(page, at="get_ui_driver")`. Behaviour on an already-migrated entry URL is
      unchanged — `ui_driver.migrated_host_bail` must still be the event name for that case.
- [ ] `drivers/factory.py`: call it once per `detect_ui_mode` poll tick (`factory.py:114-122`).
      Free — the loop already awaits `poll_interval_s`.
- [ ] `ui_automation_video.py::_exit_agent_mode`: call it **after** the first
      `_media_panel_present` returns `False` and **before** `_dismiss_agent_affordances`
      (`ui_automation_video.py:1527-1531`). `_media_panel_present` uses `.count()` and does not
      wait, so this costs nothing on the old host and skips the ~10.6 s dismissal when the flip
      has already landed.
- [ ] Do **not** add the guard to `_probe_selector_cascade` in this task — ten call sites, some
      best-effort. Deferred to Task 9.

**Tests:** Task 1 and Task 3 go green.

---

## Task 6 — MCP surface (verification, not mirroring)

**What:** State and prove the MCP position rather than assume it.

**Steps:**
- [ ] Confirm no CLI leaf, option, or request-DTO field changes → `tests/mcp/test_cli_parity.py`
      needs no new mapping and `worker/codec.py` no new payload key. Record this explicitly in the
      PR body; silence here is what let #626 ship.
- [ ] Audit `mcp/tools.py` docstrings for any claim these fixes falsify. Expected: none.
- [ ] Run `/gflow:check` step 1b — the six mirror axes (canonical list:
      `skills/check/SKILL.md`). Do not restate them here.

**Tests:**
- [ ] `test_migrated_host_error_crosses_the_queued_path_with_exit_code_and_retryable` (S#15) —
      the task error record carries `exit_code: 36` **and** `retryable: true`
      (`worker/daemon.py:449-459` → `mcp/tools.py:306-401`).

---

## Task 7 — Docs

**Files:** `docs/MCP.md`, `website/docs/MCP.md`, `KNOWN_ISSUES.md`,
`docs/LIVE_VERIFICATION_v0.66.1.md`, `CHANGELOG.md`

**Steps:**
- [ ] Add `FlowHostMigratedError` to the retryable list (`docs/MCP.md:127-131`) **and** regenerate
      the `website/docs/` mirror (`generate_website_docs.py --check` is a CI gate).
- [ ] **Correct `docs/LIVE_VERIFICATION_v0.66.1.md`**: the `get_ui_driver … "ms": 0` figure and the
      `time to exit 36 — 0 ms` row were measured on the function **in isolation on an
      already-migrated page**, not on the CLI route, where the field shows ~57 s. Say so plainly and
      state what the corrected number is. Its line *"`ui_driver.migrated_host_bail` is logged, so
      the fast path is observable rather than inferred"* is the claim the reporter's timeline
      falsified — replace it, do not soften it.
- [ ] `KNOWN_ISSUES.md` #639 entry: the "What gflow does today (v0.66.0)" paragraph implies a
      fast-fail that has never fired in the field. Update to what actually ships.
- [ ] `CHANGELOG.md` `[Unreleased]` — both fixes, `Refs #639`, `Refs #643`.
- [ ] Do **not** close #639 or #643. #639 stays open for the undrivable frontend; #643 is only now
      reachable on latched profiles.

---

## Task 8 — Gates + live verification

**Steps:**
- [ ] `/gflow:check` green (hygiene, doc links, website mirror, ruff, format, pyright, pytest ≥80%).
- [ ] **Merge gate — old-host live run**, reachable today: a real `gflow image t2i` on `labs.google`
      completing exit 0 with a real image, plus the event timeline showing **no added wait**.
- [ ] Re-run `scripts/dev/measure_migrated_host_flip.py` to record whether any migrated load was
      reachable during verification.
- [ ] Write `docs/LIVE_VERIFICATION_v0.66.2.md` with an explicit **NOT verified** section: if no
      migrated load was reachable, the migrated path is unit-tested only. Say that; do not fold it
      into the green column.
- [ ] A/B control (memory: `ab-control-before-shipping-a-fix`): neuter each fix independently and
      confirm exactly the expected tests fail — no test passing vacuously.
- [ ] PR uses `Refs #639`, not `Closes #639`.

---

## Task 9 — Deferred, do not start without a measurement

**What:** Extend the guard to `_probe_selector_cascade`, and/or add a bounded wait gated on a
learned per-profile flag.

**Gate:** Only if a real migrated run shows the Task 5 sites still cost more than is acceptable,
**and** the flip timing has actually been measured. Until then this is designing against a guess —
which is what produced the `0 ms` claim being corrected in Task 7.

---

## Definition of done

- [ ] All task steps checked off (Task 9 excluded by design)
- [ ] `/gflow:check` green
- [ ] `CHANGELOG.md` `[Unreleased]` updated
- [ ] Docs updated: `docs/MCP.md` + mirror, `KNOWN_ISSUES.md`, `LIVE_VERIFICATION_v0.66.1.md` corrected
- [ ] BDD covers all Critical + High scenarios from `SCENARIO.md`
- [ ] Old-host no-regression proven by a **live run**, not by unit tests
- [ ] Every latency figure in the PR and docs traces to a real CLI run, or is labelled unmeasured
- [ ] No `# TODO` without a tracked issue link
