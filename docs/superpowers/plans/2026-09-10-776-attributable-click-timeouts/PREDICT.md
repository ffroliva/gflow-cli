# Predict: attribute the migrated driver's click timeouts (#776)

## Verdict on the proposal as submitted: **STOP**
**Confidence: 5.2/10** (mean 7.0, −2 Devil's Advocate found a simpler path the others missed; any STOP is a STOP)

## Verdict on the revised proposal below: **CAUTION → proceed with mitigations**

## Summary

The proposal was: *port #593's overlay guard to the migrated driver, add a shared
`body{pointer-events:none}` + hit-test probe to `_common.py`, and call it pre-click.*

Four personas returned GO/CAUTION on the mechanics. The Devil's Advocate returned STOP on
the **premise**, and it is right: the proposal picks a cause. The live spike run the same
hour independently agrees — the overlay mechanism is **unmeasured on this host** (0/3,
[`2026-09-10-migrated-click-blocked.md`](../../spikes/2026-09-10-migrated-click-blocked.md)).
A guard built on an unmeasured cause does not just fail to fire; it produces a
**confidently wrong** message, which #770 is already a live precedent for.

## Persona findings

### Architect — GO (8/10)
`_common.py` is the right home, and for a load-bearing reason nobody had stated: there is
an **existing import cycle** — `ui_automation.py:44` imports `migrated_composer`, and
`migrated_composer.py:1921` imports back with `# noqa: PLC0415 - cycle`. So the migrated
driver *cannot* top-level-import from `ui_automation.py`; `_common.py` has zero
intra-`transports` imports and is the only acyclic leaf both drivers already reach.
Recommends a `@staticmethod async def _click(...)` on `MigratedComposer` over a decorator,
matching `_dismiss_dialog`'s existing shape. Warns explicitly against harmonising the labs
driver's clicks in the same PR.

### Security / reCAPTCHA — CAUTION (8/10)
The strongest finding of the five. **Converting a bare `TimeoutError` into a typed error
removes an accidental privacy net.** Verified in source: `_handle_unhandled_error`
(`_cli_helpers.py:324`) prints a generic message and SHA-256-hashes the telemetry, while
`_handle_gflow_error` (`_cli_helpers.py:304`) prints `exc.detail` **raw**, and
`json_output.py:55` ships it verbatim under `--json`. `redact_error_detail` is wired only
at the SQLite boundary — **not** on the console, structlog, or `--json` paths.

So any DOM text this fix puts in `detail` is guaranteed to be printed, logged, and (by the
class's own remediation hint) invited into a GitHub issue. An occluding element can carry
an account email in `aria-label`/`title`, or a signed media URL in `src`. This is the exact
class of bug PR #777 fixed two hours ago.

> **Mandatory:** the occluder report must be a **closed allowlist** — tag name plus a match
> against a fixed set of structural overlay markers. Never `outerHTML`, `textContent`,
> `aria-label`, `title`, `alt`, `src`, `href`, or an attribute dump. Any free-form DOM
> string must pass `redact_sensitive_text()` at the raise site.

### Performance / Playwright — GO with a scope correction (8/10)
"Pre-click at modal-prone epochs" is ambiguous and the two readings differ by 4×: a real
r2v run makes **~16** clicks, while the labs guard it is modelled on runs at exactly **3**
sites. Worse, `_require_unblocked` has no de-duplication — on a genuinely blocked page each
call independently re-probes, waits ~1 s of jitter, re-attempts dismissal and re-probes, so
8+ pre-emptive sites would add 16–24 s of redundant latency before finally raising. No Page
pool or `__aexit__` risk: every `_checkout_page` is `try/finally`-paired.

### CLI / MCP UX — CAUTION (8/10)
Three findings that change the implementation:

**Exit 23 is right; do not mint a new code.** #593 already raises `UiSelectorDriftError`
for "an overlay is still covering the app" (`ui_automation.py:1345`). The project's own bar
for a new code is a *materially different caller action* (`errors.py:562`, `:594`), and
"dismiss the modal and re-run" is not different from 23's existing remediation. One
docstring line should acknowledge that the class covers *occluded*, not only *missing* —
#593 stretched it there already and the docs never caught up.

**MCP is currently worse than the CLI, and this fix is the whole repair.** On the queued
path a non-`GFlowError` hits `worker/daemon.py:441-475`'s `else` branch, which ships
`"detail": f"sha256:{exception_message_hash(exc)}"` — a hash, not even the class name. Once
the raise site becomes a `GFlowError`, `daemon.py:449`'s `isinstance` branch fires instead
and the agent gets full problem details plus `exit_code=23`. Same transport, one fix, both
doors — but it must be *run* on the MCP path, not inferred.

**The reporter may have seen nothing at all in `--json`.** `unexpected_payload()`
(`json_output.py:83`) emits no detail and no exception class without a debug flag, so the
`exception_class=TimeoutError` they quoted came from the **stderr structlog** event, not
stdout. An adapter reading only stdout got a bare failure. Worth telling them.

It also flagged, independently of the Devil's Advocate, that a pre-click guard contradicts
a rule this codebase already learned: `_common.py:205-221` — *"Call this from inside a
failure branch … never before it … a guard placed ahead of the probe deletes the evidence
that would correct it."*

### Devil's Advocate — STOP (3/10)
**Found the thing that changes the design.** [#752 finding #7](https://github.com/ffroliva/gflow-cli/issues/752),
a maintainer-authored review written *before* #776 was filed, predicts this exact symptom
at this exact function:

> `_open_pane` still guards with `count()`, not visibility … a mode flip between
> `ensure_editor` and `apply_video_settings` escapes as a **bare Playwright TimeoutError
> with no exit-23 mapping and no mention of agent mode**.

Half of that was fixed — `:870` became `wait_for(state="visible")`, and its comment at
`:866-869` spells the failure out. **The very next line, `:884`, is the click, still
unguarded.** The file documents the bug it still has, one line above it.

Agent mode hides the trigger with a bare `hidden` attribute — it never touches
`body{pointer-events:none}`. So the proposed probe would return "not blocked" and the fix
would report the wrong cause.

## High-confidence risks (2+ personas)

1. **The proposal picks a cause it cannot see.** (Devil's Advocate STOP; Security Finding 4
   caveat; the spike's 0/3.) Playwright's actionability gate has four conditions — visible,
   stable, receives-events, enabled. A body-`pointer-events` probe speaks to exactly one.
2. **A wrong typed message is worse than an honest bare one.** (Devil's Advocate; Security
   Finding 2.) #770 is the live precedent.
3. **Blanket-converting 18 sites collides with open #759**, which was filed against this
   very file for narrative duplication. (Devil's Advocate; Architect's scope-creep warning.)

## Conflicts resolved

- **Performance says "pre-emptive at epochs"; Devil's Advocate says "don't build the guard at all."**
  Resolved in favour of the Devil's Advocate, on evidence Performance did not have: the spike
  measured `body_pointer_events: auto` in **159/159** samples — including *while the settings
  pane was open*. Angular CDK blocks with a `.cdk-overlay-backdrop` element, not by muting the
  body, so on this host the **hit-test is the load-bearing detector and the body property is
  the labs mechanism**. A pre-emptive body probe here would guard a mechanism this frontend
  does not appear to use.
- **Architect says extract to `_common.py`; Devil's Advocate says that is a bigger structural
  change than it looks.** Both hold: extraction is right *if* something shared is needed. Under
  the revised proposal the read is migrated-host-specific and single-caller, so it stays local
  until a second caller exists. The Architect's cycle finding remains the constraint if that
  changes.

## The revised proposal

**Do not guess the cause. Read it, at the moment of failure, and report what was true.**

1. One `_click` helper on `MigratedComposer`. On a Playwright timeout it performs a
   post-mortem read and raises `UiSelectorDriftError` (exit 23) naming the locator and the
   condition that actually failed:
   - the agent-mode chip (`_agent_chip_pressed`, already exists at `:684`) — #752's cause
   - `hidden` / `disabled` — the *visible* and *enabled* conditions
   - `body{pointer-events}` + an allowlisted hit-test occluder — the *receives-events* condition
   - none of the above ⇒ say exactly that; it rules out three and points at *stable*
2. **Zero cost on the happy path** — the read runs only in the `except` branch.
3. Applied to four sites with a named reason each, not eighteen: `:884` (#776's site),
   `:1598` (named in `_close_pane`'s own docstring as historically failing this way), and
   `:1725` / `:1858` (the credit-spending submits, where "did it submit?" is unanswerable today).

### Required mitigations before EXECUTE

1. **Allowlist the occluder report.** Tag name, plus only those classes matching a fixed
   structural prefix set (`cdk-`, `mat-`, `mdc-`, `flow-`), each capped — mirroring the
   existing `.slice(0, 200)` convention at `ui_automation.py:2472`. Never `outerHTML`,
   `textContent`, `aria-label`, `title`, `alt`, `src`, `href`, or a generic attribute dump.
   Pass the assembled detail through `redact_sensitive_text()` at the raise site. (Security +
   CLI/MCP UX, reconciled: an allowlist *and* a bound.)
2. **Put the locator before the variable-length class blob in the message.** The queued MCP
   path raw-slices `detail` to 500 chars (`data/redaction.py:117`) while the CLI path does
   not; ordering keeps both surfaces showing the same essential text. (CLI/MCP UX)
3. **No pre-emptive guard, no shared `_common.py` probe** until a second caller or a measured
   cause justifies one. Three personas and the spike converged here, and `_common.py:205-221`
   already states the rule. (Devil's Advocate, CLI/MCP UX, Performance, spike)
4. **Exit 23, and add the missing docstring line** acknowledging *occluded* alongside
   *missing*. No new exit code. (CLI/MCP UX)
5. **Preserve `retryable`.** Today's failure is non-retryable; the condition does not reproduce,
   so per the Bug Lane's "A flag is a claim" middle row this is *preserved, not measured*.
   `UiSelectorDriftError` is not in `RETRYABLE_ERRORS`, so the default already preserves it —
   assert that in a test rather than leaving it to survive by luck.
6. **One helper, not eighteen message blocks** — #759.
7. **Run the MCP twin.** The fix flips `daemon.py:449`'s branch from the hashed `else` to the
   `GFlowError` path; that is the larger half of the repair and the Iron Law applies to it
   separately. (CLI/MCP UX)
8. **Verify via the raised error and the log line, not the incident bundle** — #722 blanks the
   capture on this path.

## Recommended next step

Phase 3 — `/gflow:scenario`. The scenario is browser-only (a click that fails Playwright's
actionability gate cannot be expressed by a mocked page), so per the Bug Lane it binds to a
route-intercepted e2e in `tests/e2e/`, tagged `@e2e @e2e_auth`.
