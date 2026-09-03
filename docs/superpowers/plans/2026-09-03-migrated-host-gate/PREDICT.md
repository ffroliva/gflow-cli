# Predict: settle the migrated-origin host gate (#639-A) + make `NOT_REDIRECTED` recoverable (#639-B)

## Verdict: **CAUTION**
**Confidence:** 7/10 (persona average 7.8; held down by an unmeasured number and an
unverifiable-today surface, not by disagreement about the fix)

## Summary

Both defects are real and the fix shape is agreed: **A2 (re-check the host where the run
already blocks) + B2 (split "skip the settle" from "skip the locale probe")**. The council
splits hard on one thing — Performance ranks A3 (cache "migrated", navigate straight to
`flow.google.com`) first at ~0 ms; CLI-UX and Devil's Advocate disqualify it. **A live
measurement taken during this predict settles it against A3**: 32/32 navigations across both
maintainer profiles landed on `labs.google` today, so a sticky migrated-cache would have locked
both accounts out of a working frontend. A3-as-specified is rejected. The residual CAUTION is
that the migrated path **cannot be live-verified from this machine today** — which is exactly
how v0.66.1 shipped a "0 ms" claim that was false in the field.

## Persona findings

### Architect — A2 + B2 (8/10)
Ranks **A2 first**: reuses existing wait points, zero latency, single module, no lifecycle
obligations; `flow_host_kind` is already imported in `factory.py:161`. **A1** is the "textbook"
relocation but has no cheap short-circuit, so it reintroduces #587-shaped dead time. **A3
CAUTION** — caching a signal the code itself twice documents as *flapping* (`factory.py:159,172`)
reproduces Defect B's own failure shape, and spans 4 modules. **A4 CAUTION** — the flag becomes
shared mutable state across a module boundary, which `docs/ARCHITECTURE.md:45` forbids outright.
**B2** stays entirely inside `client.py`, the only caller of `next_locale_state` and only writer
of the locale file; it makes `NOT_REDIRECTED` mean what its own docstring says
(`profile_store.py:291`). No STOP.

### Security / reCAPTCHA — no hard STOP (8/10)
No option touches auth headers, cookie extraction, token minting, or profile isolation
(`auth/verification.py:91-99` intact). **A3 is the one to scrutinise**: it would pick a *scheme
and host* from a value read off disk. Safe only if the cache is a tight enum selecting between
two hardcoded origin literals, never interpolated, self-healing on anything unrecognised —
mirroring `read_account_locale`'s `_LOCALE_SEGMENT_RE` validation and `routes.py:263-265`'s
`_PROJECT_ID_RE` allowlist. Also: bootstrap always targets `labs.google`
(`routes.py:94`), which bounds A3's blast radius. **B2 condition:** route `<html lang>` through
`locale_segment_from_lang_attr` *before* `next_locale_state`/`write_account_locale` —
`write_account_locale` (`profile_store.py:309-317`) writes its argument **verbatim**.

### Performance / Playwright — A3 first, A2 last (7/10)
The checks themselves are free (`urlsplit` + dict lookup; `page.url` is a cached sync property).
**The cost is what you wait before running them.** Proves **A2's pre-`_exit_agent_mode`
checkpoint fires at the same instant as the entry check** — a no-op by construction against the
field trace — so A2's only real window is `detect_ui_mode`'s poll loop, which cannot start until
`_exit_agent_mode`'s ~10.6 s completes. **A2 ceiling: ~14-22 s, not sub-second.** A1 is paid at
all four `_settle_if_redirecting` sites (`ui_automation.py:1441,1475,3516,3563`) including up to
4 character-route retries. A4 buys nothing without an event-driven rewrite the call chain does
not have. `FlowHostMigratedError` has **zero internal retry consumers** — every retry is a fresh
process paying full browser launch, so per-attempt cost multiplies.
**Scoped STOP:** do not ship an A-fix without B landing first or alongside — latched profiles are
blind to the host signal today.

### CLI UX / MCP — no STOP (8/10)
**MCP verdict: one fix in the shared transport repairs both surfaces. No `mcp/tools.py` change,
no new payload key, no new option, no `test_cli_parity.py` mapping.** Verified the worker
(`worker/daemon.py:324,355`) builds the same `FlowApiClient` the CLI does. `exit_code: 36` and
`retryable: true` **do** cross the MCP boundary intact for the queued generation path
(`daemon.py:449-459` stamps them; `mcp/tools.py:306-401` returns them verbatim) — the reporter's
whole benefit survives on MCP.
Two doc-truth items: `docs/MCP.md:127-131` (and its `website/docs/` mirror) enumerate the
retryable classes and **omit `FlowHostMigratedError`** — latent only because exit 36 never fires
today; and A3 would make `FlowHostMigratedError`'s own "retrying often lands the old frontend"
remediation (`errors.py:694-702`) **false**. Pre-existing, out of scope: the `wait=False` MCP path
has no documented way to retrieve a task's terminal error.

### Devil's Advocate — proceed now (8/10)
The defer case needs a **monotonic** rollout; today's measurement falsifies that — it flaps, so
gflow must survive both hosts for an unknown non-monotonic window. A is a small orthogonal diff
that does not compete with #642's transport bet. **A3 disqualified as specified** — salvageable
only as "cache the *prediction*, never change the navigation target, spend it purely as
permission to pay a bounded wait". **#644 is not a prerequisite** — its own text scopes it to the
browserless httpx harvest, which gates #642, not this. **No ADR in `PLAN.md` defers or
contradicts this** (grep clean); **no PR in flight** on 639/642/643/644. Refuses to let either
fix be called live-verified on the migrated path from this machine.

## Live measurement taken during this predict

> Point-in-time record: 32 navigations were available when this verdict was issued.
> Sweeping continued afterwards and reached **72** with the same result — see `PROBE.md`.

`scripts/dev/measure_migrated_host_flip.py`, 32 navigations, two authenticated profiles, 25 ms
sampling, zero credits (full record: `PROBE.md`):

| profile | cached locale | resolved | navs | landed migrated | post-`goto` URL change | `html lang` |
|---|---|---|---|---|---|---|
| `ffroliva` | `''` (latched `NOT_REDIRECTED`) | `None` | 20 | **0** | **none, ever** | `en` |
| `denon82` | `'pt'` | `'pt'` | 12 | **0** | **none, ever** | `pt` |

1. **Defect B reproduces live on the maintainer's own primary profile** — every bootstrap logged
   `client.account_locale_cached locale=None settle_skipped=True`, so the #643 recovery never ran,
   while `html lang` reads `en` on that very page.
2. **A1 would be pure dead time** — 32/32 with no post-`goto` URL change at all.
3. **A3-direct-navigate would have locked both accounts out of a working frontend today.**
4. **`ffroliva`'s latch is correct *about redirects*** — the bare URL is served as-is, never
   redirected. So B1 (delete the early return) is the wrong shape; only the locale *read* is
   wrongly disabled. B2 confirmed as the right split.

## High-confidence risks (2+ personas)

1. **A3's sticky cache converts a recoverable failure into a permanent one** — Architect, CLI-UX,
   Devil's Advocate; now demonstrated by the 32/32 old-host measurement. **Rejected.**
2. **Latched profiles are blind to the host signal until B lands** — Performance (scoped STOP),
   Architect, Devil's Advocate. **B ships with or before A.**
3. **Neither fix's migrated path is live-verifiable here today** — Performance, Devil's Advocate.
   v0.66.1 already shipped a false "0 ms" on exactly this gap.

## Conflicts resolved

- **Architect (A2 first) vs Performance (A2 last).** Performance wins on mechanism: the
  pre-`_exit_agent_mode` checkpoint is provably a no-op. Independently confirmed here —
  `_media_panel_present` (`ui_automation_video.py:1353-1364`) uses `.count()`, no wait, so it
  returns at ~3.16 s and the first real blocking wait is inside `_dismiss_agent_affordances`.
  Architect wins on placement and cost. **Net: A2 is the right shape, worth ~14-22 s, not
  sub-second — and the plan must say so rather than repeat v0.66.1's overclaim.**
- **Performance (A3 first) vs CLI-UX + Devil's Advocate (A3 disqualified).** Resolved against A3
  by measurement, not preference. Only the wait-budget variant survives, and it is **deferred**
  until the flip timing is measured.
- Devil's Advocate did not surface a simpler path the others missed — it endorsed the minimum
  already tabled — so no −2 modifier. Not all five agree on the A ranking, so no +1 either.

## Required mitigations before EXECUTE

1. **B2 lands with or before A** (Performance's scoped STOP).
2. **Reject A3-direct-navigate.** Never change the navigation target from a cached value. If a
   bounded wait is added later, gate it on a learned per-profile flag and keep `goto` on
   `labs.google` so the flap remains reachable.
3. **Fix the test precondition, not just the code.** All five existing bail tests
   (`tests/api/transports/drivers/test_ui_mode.py:343-410`) hand `get_ui_driver` a page whose
   `.url` is *already* migrated — which is why they are green while the field path is broken. Add
   a red-first test whose URL **starts** on `labs.google` and **flips** after `goto`.
4. **Claim only what is measured.** Time-to-exit-36 must be reported from a real CLI run, never
   from a function measured in isolation. Correct
   `docs/LIVE_VERIFICATION_v0.66.1.md`'s "`time to exit 36 — 0 ms`" row in the same PR.
5. **Merge gate = old-host no-regression** (live-verifiable today). Migrated path ships
   unit-tested with `Refs #639` and an explicit NEEDS-E2E flag; optionally hand a build to the
   reporter, whose account is migrated.
6. **B2 must sanitise via `locale_segment_from_lang_attr`** before `next_locale_state` /
   `write_account_locale`.
7. **Emit a new structlog event** at the point the host is detected mid-run, so the fast path is
   observable in a field timeline rather than inferred.
8. **Add `FlowHostMigratedError` to the retryable list** in `docs/MCP.md` and its
   `website/docs/` mirror (`generate_website_docs.py --check` gates the mirror).

## Recommended next step

Proceed to **Phase 3 — `/gflow:scenario`** for A2 + B2, with the flip-timing measurement carried
as an explicit open question (the instrument exists; it needs a migrated load). Do **not** design
for 0 ms until that number exists.
