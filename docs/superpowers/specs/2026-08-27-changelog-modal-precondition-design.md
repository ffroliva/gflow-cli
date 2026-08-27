# Changelog announcement modal — precondition design brief (#593)

> **Status:** council input, pre-implementation. No code written yet.
> **Date:** 2026-08-27 · **Issue:** [#593](https://github.com/ffroliva/gflow-cli/issues/593)
> **Evidence:** live one-shot capture on `ffroliva`, 2026-08-27 21:16 BST.

This brief exists to be reviewed, not merged. It states what was **measured**, the
options that measurement opened, and the constraints any option must survive.

---

## 1. The failure

Flow shows a full announcement dialog over the editor when Google ships a feature.
While it is up, **every** UI action times out with a bare `TimeoutError`.

Measured on two accounts (`denon82` pt, `ffroliva` en — so this is not locale-specific):

```
body { pointer-events: none }     <- whole app behind is dead at CSS level
aria-hidden: null   inert: false  <- accessibility tree says the app is FINE
mode-switch button: visible=True enabled=True  -> COVERED by <div>
```

That combination is the whole problem. Playwright's actionability check sees a
**visible and enabled** element and waits for a click that can never land. Nothing but
hit-testing reveals it, so the operator gets a timeout with no cause.

## 2. What the one-shot capture proved

The dismissal is **not** DOM state, a cookie, or localStorage. It is one tRPC mutation,
and it has a readable counterpart:

| Endpoint | Field | Observed on `ffroliva` |
|---|---|---|
| `videoFx.getFlowAppConfig` (GET) | `result.changeLogId` | `08-26-26_omni_439461c7-439a-4e04-8f2e-8fc288897eb6` |
| `videoFx.getUserSettings` (GET) | `result.lastAcknowledgedChangeLogId` | `07_21_26_community_tools_launch_9d1984d6-…` |
| `videoFx.setLastAcknowledgedChangeLogId` (POST) | `{"json":{"changeLogId":"<id>"}}` → 200 | fired by the click |

**The modal renders iff those two ids differ.** So the blocking condition is knowable
*before* navigating, and clearable without touching the DOM.

`getUserSettings` also returns `dismissedBannerIds`, `completedOnboardingIds`, and
`isAgentModeToggled` — the same setting `mode_control.py` currently drives by clicking.

Before/after of the production dismissal path (`_dismiss_blocking_overlays`):

```
BEFORE  body pointer-events=none   dialogs=1   app control COVERED by <div>
CLICK   overlay_detected   iframe[src*='/flow/changelogs/']
        overlay_dismissed  method=close_button_page
                           [role='dialog']:has(a[href*='changelog']) button
AFTER   body pointer-events=auto   dialogs=0   app control REACHABLE
RELOAD  dialogs=0   -> persistence confirmed within the session
```

The existing detector and close-button selectors matched **first try, text-independently**,
in English, having been written against Portuguese. The structural anchors are correct.

Artifacts (gitignored, local): `scripts/dev/_spike_out/changelog_capture_20260827_2116*/`
— `before/`, `after/`, `after_reload/` (full DOM, every frame, dialog subtree, screenshot,
probe JSON), `dismiss_traffic.json`, `session.har` (159 MB; contains auth cookies — never
attach to a public issue).

## 3. The actual gap

`_dismiss_blocking_overlays` is called at **9 specific boundaries** (`ui_automation.py` ×7,
`ui_automation_video.py` ×2) rather than as a precondition. Anything acting without
passing one of those boundaries is unprotected — including two raw-`goto` e2e tests, one
of them in the nightly canary's tier. The failure mode is an unexplained timeout, not a
clear error.

## 4. Options on the table

### A — DOM precondition gate (the issue's original proposal)
Hoist `_dismiss_blocking_overlays` to a single precondition at each public transport entry
point (`generate_images`, `generate_images_batch`, `generate_character_images`, video
equivalents), replacing the scattered calls.

### B — tRPC acknowledgement pre-flight (opened by the capture)
At client bootstrap, read `getFlowAppConfig.changeLogId` and
`getUserSettings.lastAcknowledgedChangeLogId` from the page context; if they differ, POST
`setLastAcknowledgedChangeLogId`. No DOM, no Escape, no dialog ever rendered on
subsequent navigations.

### C — B primary, A as fallback
Pre-flight normally; keep the DOM path for when the endpoints drift or the modal appears
mid-session.

### D — Null option
Keep the 9 boundaries; only replace the bare `TimeoutError` with a diagnostic that names
the overlay. Cheapest, fixes nothing structurally.

## 5. Constraints any option must survive

1. **#395, the credit-spending regression.** A bare `[role='dialog']` / `[role='alert']` in
   the detector once matched Flow's *own* working surfaces, so dismissal pressed Escape on
   the character composer and the generation went out without `entityContext` — a silent,
   billed failure. Any systematic precondition must keep the narrow structural anchors and
   run **before** the composer is populated, never during. See `ui_automation.py:427-445`.
2. **Locale-invariance discipline** (AGENTS.md): selectors anchor on structure, roles,
   hrefs, icon ligatures. Never text labels, never multi-locale cascades.
3. **Ordering.** Acking *after* the page has rendered does not remove a dialog already
   mounted — it needs a reload. Any pre-flight must run before the editor navigation, or
   pair with one.
4. **Fail-open.** `getFlowAppConfig` / `getUserSettings` are private, undocumented, and may
   change shape without notice. Neither reading nor acking may ever break a run.
5. **Known live risk:** tRPC calls made through the browser context have returned 401 in
   the nightly canary (`project.createProject`); the cause is still unexplained. An
   approach that depends on a tRPC POST must state what happens when it 401s.
6. **Consent surface.** Acking marks an announcement read that the human never saw. The
   DOM click does exactly the same thing — but B does it without a dialog ever existing.
   Is that a difference that matters for a CLI driving the user's own account?
7. **YAGNI** (AGENTS.md D14): the smallest change that actually closes the gap wins.
   Two mechanisms where one would do is a finding, not a feature.

## 6. Questions for the council

1. Which option, and why is the runner-up wrong?
2. Does B's pre-flight belong in `FlowApiClient._bootstrap_and_resolve_locale` (where the
   account-locale probe already runs, one navigation already paid for), in the transport,
   or somewhere else entirely?
3. Does C's fallback reintroduce the #395 hazard, or does gating it pre-composer hold?
4. What is the smallest test that fails if this logic breaks — offline, against the
   captured `before/dialog_0.html` fixture?
5. Do the two raw-`goto` e2e tests and the nightly canary get covered automatically by the
   chosen option, or do they need their own guard?
6. What is the rollback story if Flow renames or reshapes either endpoint?

---

## 7. Council verdict — CAUTION, option A (amended)

Seven reviewers: five internal personas (`predict`), plus `codex` (OpenAI) and `agy`
(Gemini 3.1 Pro) as external model families. `gemini` CLI was dropped — the account is
ineligible and the binary was uninstalled.

| Verdict | Reviewers | Confidence |
|---|---|---|
| **A** — DOM precondition gate | Security · CLI-UX · `agy` · `codex` | 8, 8, 9, 8 |
| **B** — tRPC ack pre-flight | Architect · Performance | 8, 8 |
| **D** — diagnostic only | Devil's Advocate | 8 |

**Verdict: CAUTION — option A, amended as below. B is deferred, not rejected.**

### Why A won on argument, not just on count

- **B cannot unmount a live dialog** (`agy`). The ack prevents *future* mounts; a DOM
  click gets React's optimistic unmount and unblocks the current session even if the
  network call fails. This contradicts §2's claim that the modal is "clearable without
  touching the DOM" — that claim is **wrong** and is corrected here.
- **B must fail open, and the only thing to fail open *to* is A** (Security). B alone is
  A plus a wire contract; it deletes no selector surface (CLI-UX verified: the DOM path
  must survive for banners, welcome screens, and Google's consent interstitial anyway).
- **B's rollback is asymmetric** (`codex`): an acknowledgement already written cannot be
  made unread. A has no account-side state at all.
- **B's POST rides the same lane as the unexplained canary 401** on `createProject`, and
  would fire *earlier* — making that open investigation harder to localise (Security).

### Amendments to A — none of these are optional

1. **Key the gate to navigation epochs, not entry points** (`codex`). "Once per public
   entry point" is unsafe: method entry may precede navigation, and video recovery
   reloads later and can remount the overlay (`ui_automation_video.py:3811`). Run it
   after every editor navigation or sanctioned reload, before any composer mutation.
2. **Make it an actual precondition** (`codex`). Today `_dismiss_blocking_overlays`
   returns `True` after a click or Escape *without verifying clearance*, and callers
   ignore `False`. Re-probe after dismissal; if the overlay remains, raise a typed
   UI-drift error **pre-submit** rather than timing out later.
3. **Gate the cascade on `body{pointer-events:none}`** (Devil's Advocate, measured across
   all three captures: `none` when blocked, `auto` when clear). This makes the #395
   regression structurally impossible instead of comment-prevented, is locale- and
   selector-free, and catches future overlays nobody has written an anchor for.
4. **Do not replace the 9 existing calls** (`codex`, Architect, Performance). They cover
   post-reload and character-route cases a gate does not. Add and verify; don't hoist away.
5. **Scope the fallback to the changelog dialog root; forbid generic Escape once composer
   state exists** (`codex`) — otherwise `[role='banner']` and page-global close-icon
   selectors can close legitimate Flow UI.
6. **Fix the ignored `is_visible(timeout=…)`** (Performance and CLI-UX, found
   independently). Playwright's installed implementation documents the parameter as a
   no-op, so `_detect_overlay` is an *instantaneous snapshot* taken right after
   `domcontentloaded` — before React hydration mounts the dialog. Without this fix, a
   hoisted gate races hydration and under-delivers.
7. **Bound the New-project click loop** (Performance). `_enter_editor`'s gallery branch
   (`ui_automation.py:1300-1321`) has no dismissal, and `loc.click()` inherits
   Playwright's 30 s default inside `except: continue` over 18 selectors → 30-90 s of
   dead time ending in the wrong error. Pass an explicit `timeout=`.
8. **Guard the two raw-`goto` sites explicitly** and drop their hardcoded `"en"`
   (Architect, CLI-UX, `codex`). `tests/e2e/test_sidebar_recovery_e2e.py:90` is
   `e2e_auth` — the nightly canary's default tier.
   `tests/e2e/test_agentic_count_enforcement_e2e.py:60` is `e2e_image`. No option covers
   them automatically.
9. **Diagnostics on existing exit codes** (CLI-UX). Detected-but-not-cleared → **23**
   (`UiSelectorDriftError`, probe `overlay_close_button`); covered-but-undetected →
   **9** with a post-mortem `detail` naming `body`'s computed `pointer-events` and the
   dialog count. No new exit code, no new env var. Log selectors, booleans and the ASCII
   changelog id only — **never page text** (cp1252 `UnicodeEncodeError` on Windows).

### Blocking gate before implementation

**The evidence base covers the safe page, not the dangerous one.** Every capture is of the
project editor; #395 spent credits on the **character composer**, which appears in none of
them. Devil's Advocate and `codex` converge here: pre-composer timing is *necessary but
not sufficient*, and the detector's behaviour on a populated composer is unmeasured.

> **Experiment (zero credits, ~20 min):** open the character composer on `ffroliva`,
> populate it, **do not submit**. Dump `page.html` and `body`'s computed `pointer-events`.
> Offline, run the 7 `_detect_overlay` probes and `OVERLAY_CLOSE_BUTTON_SELECTORS`
> against that DOM.
>
> **Pass** — detector `False`, `pointer-events: auto` → the gate is survivable, proceed.
> **Fail** — detector `True` on a healthy composer → the 9 hand-placed boundaries are
> load-bearing; abort A, B and C.

### Deferred / out of scope

- **B** is deferred behind the canary-401 diagnosis. If it ever ships: `page.evaluate`
  fetch (never `page.request`/`_get_json` — a labs-lane 401 there hits
  `_raise_for_non_retryable` and aborts bootstrap with a false "session expired"), single
  attempt, no tenacity, no token refresh, POST gated strictly on the ids differing, and
  its response never routed through `_build_wire_format_discovery`.
- `diagnostics.py:170` misses locale-prefixed and batched tRPC route forms (Security S3).
- `WELCOME_SCREEN_SELECTORS[0]` = `[role='dialog']:has(a[href*='flow'])` is safe only
  because the healthy editor has zero `role="dialog"` — 61 `a[href*='flow']` links sit on
  that page (Devil's Advocate). Re-examine regardless of option.
- `_ONBOARDING_TEXT_SELECTORS` (~30 `has-text` consent buttons) is a standing
  locale-invariance violation nobody has budgeted (CLI-UX).

### Corrections to this brief, on the record

- §2 "clearable without touching the DOM" — **wrong**; it prevents future mounts only.
- §2 "the modal renders iff those two ids differ" — **overstated**. A missing
  `lastAcknowledgedChangeLogId` is a null check, not a string diff (`agy`), and one
  account cannot rule out rollout-eligibility conditions (`codex`).
- §6 Q4 presumed a DOM fixture, which quietly assumed A's framing (Architect).

