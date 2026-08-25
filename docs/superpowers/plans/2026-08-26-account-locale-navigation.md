# Account-Locale Editor Navigation — Implementation Plan

> **For agentic workers:** implement one task at a time, in order. Run `/gflow:check`
> before every commit. Tasks 1–2 are red tests only — no production code.

**Issue:** [#580](https://github.com/ffroliva/gflow-cli/issues/580)

**Goal:** Editor navigation lands on the account's own locale segment on the first
request, so no post-`goto` redirect can move the page out from under the very next
DOM action.

**Architecture:** `FlowApiClient` already performs a bootstrap navigation to
`EDITOR_BOOTSTRAP_URL` (`client.py:719`) immediately before `_setup_transport()`.
That navigation *itself* races (measured: 797 ms to return, redirect lands after).
Waiting for it to settle both fixes the bootstrap **and** yields the account locale
for free — no extra request. The segment is then handed to the transport through
the existing typed `TransportSetup` seam (the same seam extended in #575), so the
transport stays a consumer rather than re-implementing detection.
`routes.project_editor_url` / `character_editor_url` receive that real value instead
of the hardcoded `"en-US"` default. A URL-settle wait in `_enter_editor` keeps the
fix defence-in-depth. No new module, no new dependency, no storage layer.

**Review correction (2026-08-26):** the first draft placed detection inside
`UiAutomationTransport`, on the assumption it owned the bootstrap navigation. It
does not — the client does. Implementing that draft would have added a second,
redundant navigation.

**Predict verdict:** not run — this is a contained bug fix inside one existing
transport, not a new transport / auth change / selector redesign / schema migration.
Recorded rather than skipped silently.

**Scope decision (2026-08-26):** navigation race only. Persisting the locale for
offline consumers (`gflow project list` links, MCP `gflow_list_projects`) and the
EN/PT-only selector-coverage gap are explicitly **out of scope** and stay as
follow-ups.

## Risk register

| Severity | Risk | Mitigation |
|---|---|---|
| High | Locale detection itself fails or hangs → every navigation blocked | Detection is best-effort with a short timeout; on failure fall back to the bare URL (no segment), which is never worse than today |
| Medium | We cache a wrong segment and pin every navigation to it | Only accept a segment matching `^[a-z]{2,3}$` extracted from Flow's own settled URL; anything else → no segment |
| Medium | Flow stops redirecting, or starts redirecting differently | The URL-settle wait (Task 4) protects independently of detection, so the two mitigations do not share a failure mode |
| Low | Extra wait slows every navigation | Settle wait is bounded and short-circuits when the URL is already correct — the common case after Task 3 |

## Measured baseline (do not re-derive)

Profile `denon82` (pt-BR account), `page.goto(wait_until="domcontentloaded")`:

| URL form | goto returns | url at return | url settled | race |
|---|---|---|---|---|
| `/fx/en/...` (today's build) | 591 ms | `/fx/en/...` | `/fx/pt/...` | **yes** |
| bare `/fx/tools/flow/project/...` | 642 ms | bare | `/fx/pt/...` | **yes** |
| `/fx/tools/flow?hl=en` (bootstrap) | 797 ms | `?hl=en` | `/fx/pt/tools/flow?hl=en` | **yes** |
| `/fx/pt/...` (account locale) | 710 ms | unchanged | unchanged | **no** |

Sources ruled out for locale detection:
- `GET /fx/api/auth/session` — no locale field (`user.name`, `user.email`, `user.image`, `expires`, `access_token`)
- `navigator.language` — reports `en-US`, the value **gflow itself sets** at launch. Reading it returns the wrong answer confidently.
- `?hl=en` — does **not** pin the rendered locale; document is still `lang=pt`.

Only Flow's settled URL is trustworthy.

---

## File structure

### Modified files
```
src/gflow_cli/api/routes.py
  locale segment extraction helper; document that callers must pass a real locale
src/gflow_cli/api/client.py
  settle the bootstrap navigation; resolve the account locale segment from it
src/gflow_cli/api/transports/base.py
  TransportSetup gains `account_locale: str | None`
src/gflow_cli/api/transports/ui_automation.py
  store the injected locale; pass it to editor URL builders; settle wait
src/gflow_cli/api/transports/ui_automation_video.py
  character/video navigation uses the cached locale
tests/api/test_routes_locale.py            (new)
tests/api/transports/test_locale_navigation.py  (new)
```

---

## Task 1 — Locale segment extraction (red tests)

Pure-function tests for pulling a locale segment out of a settled Flow URL. No
production code in this task.

**Files:** `tests/api/test_routes_locale.py`

**Steps**
- [ ] Test `/fx/pt/tools/flow/project/<id>` → `"pt"`
- [ ] Test `/fx/tools/flow/project/<id>` (no segment) → `None`
- [ ] Test `/fx/pt-br/tools/flow` → `"pt"` (BCP-47 tail dropped) **or** `None` — pick one and pin it
- [ ] Test junk segments rejected: `/fx/PROJECT/tools/flow`, `/fx/toolong/tools/flow`, `/fx/1/tools/flow` → `None`
- [ ] Test a non-Flow URL → `None`

**Tests**
- [ ] All red for the right reason (helper does not exist yet)

## Task 2 — Navigation contract (red tests)

**Files:** `tests/api/transports/test_locale_navigation.py`

**Steps**
- [ ] Test the transport passes its **cached** locale to `project_editor_url`, not `"en-US"`
- [ ] Test that with no cached locale, the bare URL (no segment) is used — never a guessed `en`
- [ ] Test the cache is populated from a settled URL and reused (detection runs once, not per navigation)
- [ ] Test detection failure is non-fatal — navigation still proceeds

**Tests**
- [ ] All red

## Task 3 — Implement detection + threading

**Files:** `routes.py`, `ui_automation.py`, `ui_automation_video.py`

**Steps**
- [ ] Add `locale_segment_from_url(url) -> str | None` to `routes.py` (strict `^[a-z]{2,3}$`)
- [ ] In `client.py`, after the bootstrap `goto`, wait for the URL to settle and extract the segment — **no extra request**; this also fixes the bootstrap's own race
- [ ] Add `account_locale: str | None = None` to `TransportSetup` (`base.py`) and populate it in `_build_transport_setup`
- [ ] Transport stores it in `apply_setup` alongside the other injected wiring
- [ ] Build editor URLs from the cached segment; omit the segment entirely when unknown
- [ ] Remove the `locale: str = "en-US"` default from `_enter_editor` — a parameter with exactly one caller-value is dead flexibility
- [ ] Log the resolved segment once (`ui_automation.account_locale_resolved`) so a wrong value is diagnosable from a bundle

**Tests**
- [ ] Tasks 1–2 green
- [ ] Full `tests/api` green

## Task 4 — URL-settle wait (defence in depth)

Independent of Task 3 so the two do not share a failure mode.

**Files:** `ui_automation.py`

**Steps**
- [ ] After `page.goto`, wait briefly for the URL to stop changing before the first DOM action
- [ ] Bounded and short-circuiting — returns immediately when the URL is already final
- [ ] Log when a settle actually occurred, so residual redirects stay visible

**Tests**
- [ ] A redirecting navigation is not acted on until settled
- [ ] A non-redirecting navigation adds no meaningful delay

## Task 5 — E2E VERIFICATION (the gate)

**This is the gate, not a formality.** The fix is not done until a real gflow
run on a real pt-BR account is measured race-free. Unit tests cannot prove this —
the whole defect is a live-timing property.

### The trap that would produce a false green

Today's runs all created **new** projects, which take the `+ New project` click
branch and **never call `page.goto` at all**. Exercising that path proves nothing.

The racing path is navigation to an **existing** project (`project_id` provided →
`page.goto(project_editor_url(...))`). The e2e run **must** pass `--project <id>`.

### Pass criteria — all four required

- [ ] `ui_automation.account_locale_resolved` logs `pt` on a `denon82` run
- [ ] The navigation URL **already contains** `/fx/pt/` at `goto` return — not after settling
- [ ] No settle event fires (no redirect occurred at all)
- [ ] The generation completes end-to-end (proves we did not break navigation while fixing it)

### Control — the fix must be shown to be load-bearing

- [ ] Re-run with detection force-disabled and confirm the race **reappears**.
      A pass that would also pass without the fix is not evidence.

**Steps**
- [ ] `/gflow:check` green
- [ ] Both e2e runs recorded in `docs/LIVE_VERIFICATION_v<next>.md` with the measured URLs
- [ ] CHANGELOG `### Fixed` entry citing #580
- [ ] Correct the stale `?hl=en` comment in `routes.py:72` — it does **not** pin the rendered locale

**If the e2e fails, the loop restarts at review — it does not ship with a caveat.**

---

## Out of scope — deliberate

- Persisting locale per-profile for offline consumers (`project list` links)
- Widening `IMAGE_TAB` / `VIDEO_TAB` / `PICKER_INCLUDE` selectors beyond EN/PT
- Any change to the 14-locale onboarding selector list


---

## E2E GATE RESULT — PASS (2026-08-26, profile `denon82`, pt-BR, ZERO credits)

### The gate caught a bug unit tests could not

First run **FAILED**: `client.account_locale_unresolved last_url='.../fx/tools/flow?hl=en'`.

`await_url_settled` compared two URL samples 200 ms apart and treated equality as
"settled" — but the redirect takes ~800 ms, so it returned *before the redirect
began*, reporting a not-yet-changed URL as final. Detection silently produced
`None` on every account while all unit tests stayed green, because the defect is
purely a real-world timing property.

Fixed by waiting for the destination **shape** via Playwright's native
`page.wait_for_url(FLOW_LOCALISED_URL_RE)` instead of a stability heuristic.

### Measured result, treatment vs control

| arm | locale used | url at goto return | redirect after goto |
|---|---|---|---|
| treatment (fix) | `pt` | `/fx/pt/tools/flow/project/<pid>` | **False** |
| control (pre-fix) | `None` | `/fx/tools/flow/project/<pid>` | **True** |

Same account, same project, seconds apart. The control still races, which is what
makes this proof rather than coincidence.

### Real CLI run through the fixed path

```
client.account_locale_resolved       locale=pt
ui_automation.entering_existing_project  url=.../fx/pt/tools/flow/project/<pid>
ui_automation.url_stable_after_goto  (NOT url_redirected_after_goto)
ui_automation.batch_response_seen    status=200
→ real 768x1376 JPEG written
```

All four pass criteria met, and the generation completed — proving navigation was
not broken while being fixed.

Evidence: `scripts/dev/_spike_out/verify_locale_navigation_race.json`
Harness: `scripts/dev/verify_locale_navigation_race.py`


---

## CODE REVIEW ROUND — what it caught after the first e2e pass

The e2e gate passed, and code review still found the fix was **dead on the
character path** — the surface #395 was actually reported against.

**HIGH.** `cli_character.py` declared `@click.option("--locale", default="en-US")`
and `services/character_create.py` declared `locale: str = "en-US"`, forwarding
unconditionally. So `client.py`'s `locale if locale is not None else
self._account_locale` **always** received `"en-US"` and never consulted the
account locale. The new unit test passed only because it set `_account_locale`
directly *and* omitted `locale` — a combination no caller produces. Fixed by
widening the default to `None` down the whole chain.

Why the first e2e missed it: it exercised `image t2i --project`, which routes
through `_enter_editor`. The character path uses `_enter_character_editor`, which
was never run.

**MEDIUM.** `_enter_character_editor` never got the settle wait at all, and
`_settle_on_character_route` cannot substitute for it — it only checks that
`entity_id` is still in the URL, which a locale-only redirect *preserves*, so it
returns instantly.

**MEDIUM.** `await_url_settled` swallowed every exception identically, so a
timeout and a renamed Playwright method both returned `None` — and the caller
logs `url_stable_after_goto` on `None`. A permanently broken settle would have
read as healthy forever.

**MEDIUM — a regression the fix introduced.** Measured on `ffroliva` (an `en`
account): Flow serves the bare URL and never redirects, so the wait burned its
full timeout on every command. Setup went 2 s → **11.2 s**. Fixed by bounding the
probe (8 s → 4 s) and skipping the per-navigation wait entirely when the bootstrap
proved this account is not redirected. `ffroliva` setup is now 6.1 s; `denon82`
resolves `pt` in 3.4 s total.

**LOW.** Two independent copies of the same regex (`routes` and `_common`) that had
to agree or locale resolution would silently switch off — unified to one.

**LOW.** `test_settle_wait_is_non_fatal` passed for the wrong reason: the fake page
had no `wait_for_url`, so `await_url_settled` raised `AttributeError` before
reaching the settle path. Both fakes would have passed against a stub
implementation. Fixed, plus a happy-path test so the real path is exercised.

## E2E — character path (the HIGH finding's surface)

```
client.account_locale_resolved          locale=pt
ui_automation.entering_character_editor url=.../fx/pt/tools/flow/project/<pid>/character/<eid>
character_create.entity_patched         (created end-to-end)
```

No redirect, no route-bounce retry. This is the surface #395 was reported against.
