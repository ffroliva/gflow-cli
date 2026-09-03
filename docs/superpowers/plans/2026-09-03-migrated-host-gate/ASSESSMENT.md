# Assessment of #639 (update of 2026-09-03T12:51Z) — `CONFIRMED-BUG` ×2

**Scope:** the reporter's third section — "0.66.1 fast-fail not engaging on the `image t2i`
path" — plus a second defect their locale note exposes. Read-only; nothing changed, nothing
posted.

---

## Verdict table

| Reporter item | Verdict | Confidence |
|---|---|---|
| 1. Anchor holds on a populated project (`button:has(.google-symbols:text('crop_16_9'))` = 1 while `aria-haspopup='menu'` grows 5→51) | `ACCEPTED — data for #642` | — |
| 2. `document.documentElement.lang -> "pt"` on a live pt-BR migrated load | `ACCEPTED`, but it does **not** close the NOT-verified row (see B) | — |
| 3. v0.66.1 fast-fail never engages on the real CLI route | **`CONFIRMED-BUG`** (A) | 9/10 |
| — (implied by their `locale: null` note) | **`CONFIRMED-BUG`** (B) | 9/10 |

---

## A. The v0.66.1 fast-fail is unreachable on every real navigation

The bail exists and is correct: `drivers/factory.py:155-175` raises `FlowHostMigratedError`
when `flow_host_kind(page.url) == "migrated"`, logging `ui_driver.migrated_host_bail` first.

It never fires from the CLI because **`page.url` is still `labs.google` when it is read.**

Every project navigation is built against labs.google — `routes.project_editor_url()`
(`routes.py:247-270`) has no migrated form at all — and the hop to `flow.google.com` is a
*post-`goto`* redirect. `_enter_editor` (`ui_automation.py:1431-1441`) then declines to wait
for it, **on both branches**:

| account state | URL handed to `goto` | what settles it | result |
|---|---|---|---|
| locale unknown (`None`) | bare `labs.google/fx/tools/flow/project/<id>` | `_settle_if_redirecting` returns `None` immediately (`ui_automation.py:1400-1402`) | no wait at all |
| locale known (`"pt"`) | `labs.google/fx/pt/tools/flow/project/<id>` | `await_url_settled` short-circuits — the URL **already matches** `FLOW_LOCALISED_URL_RE` (`_common.py:219-224`, regex `routes.py:180`) | returns instantly |

So `get_ui_driver` is called ~240 ms after `goto` with a pre-redirect URL, `flow_host_kind`
answers `"labs"`, and the run pays the full doomed sequence (~54 s) before
`_mode_switch_error` (`ui_automation_video.py:1459`) re-reads `page.url` — migrated by then —
and returns the right class, slowly. The v0.66.0 slow path is what the reporter is seeing;
v0.66.1 changed nothing on this route.

**The reporter's timeline is the proof, by elimination.** `ui_driver.migrated_host_bail` is
absent; `ui_driver.ui_mode.attempt_exit_agent` (`factory.py:186` — *after* the bail) is
present at 3.149 s. The bail was evaluated and declined.

### `docs/LIVE_VERIFICATION_v0.66.1.md` is wrong about the user-visible number

Layer 1 measured `get_ui_driver` **in isolation, on a page already sitting on
flow.google.com**. Its `"ms": 0` and the table row `time to exit 36 — 0 ms` are true of that
probe and false of every CLI run. The doc's own line — *"`ui_driver.migrated_host_bail` is
logged, so the fast path is observable rather than inferred"* — is exactly the observation
the reporter's timeline falsifies in the field. Correct the doc regardless of when the code
is fixed.

## B. `NOT_REDIRECTED` is an absorbing state, so the #643 `<html lang>` fix is dead code where it is needed

`_read_account_locale` (`client.py:772-776`) returns **before** `_resolve_account_locale` when
the cached state is `NOT_REDIRECTED`:

```python
if cached == NOT_REDIRECTED:
    self._account_locale = None
    logger.info("client.account_locale_cached", locale=None, settle_skipped=True)
    return
```

`_resolve_account_locale` is the **only** caller of `next_locale_state` and the **only** site
of the new `<html lang>` recovery (`client.py:800-812`). Nothing else writes the locale file.
So once a profile reaches `NOT_REDIRECTED` there is no transition out of it — the state is
terminal by construction.

And any profile that saw two migrated loads on ≤0.66.0 is already latched:
`"pt"` → (observed `None`) → `PROVISIONAL` → (observed `None`) → `NOT_REDIRECTED`
(`profile_store.py:320-333`). That is precisely the population #643 was written for.

This is exactly what the reporter measured: `client.account_locale_cached  locale: null` on
0.66.1, with the fix installed. Their `html lang -> "pt"` result confirms the *input* is
there; it does not show gflow recovers it, because on their latched profile the recovery code
never runs. **The NOT-verified row in `LIVE_VERIFICATION_v0.66.1.md` stays open.**

> Their aside — "it fires at 1.7 s, before the project navigation at 2.5 s, so there is no
> `<html lang>` to read yet" — is a fair reading but not the cause. The bootstrap `goto`
> (`routes.EDITOR_BOOTSTRAP_URL`) completes before that log line, so a document *is* loaded;
> the probe is skipped by the cache branch, not by timing.

**A and B are independent.** B is not a prerequisite for A — A reproduces on a
resolved-locale account through the regex short-circuit.

---

## Surfaces

**CLI and MCP both.** `_enter_editor` → `get_ui_driver` is the single shared sequence at
`ui_automation.py:2803/2823`, `3230/3269` and `ui_automation_video.py:3944`; the MCP worker
drives the same transport. Shared-transport case — **one fix repairs both**, and no separate
MCP change is implied.

## Correction to the reporter (worth sending)

`ui_automation_video.selector_probe_failed` on an `image t2i` run is not a routing bug. The
image path deliberately shares `VideoGenerationMixin._mode_switch_error`
(`ui_automation.py:1546` → `ui_automation_video.py:1434`); only the module-prefixed event
name is misleading.

## Their item 1 (anchors) — routed, not assessed here

Belongs to #642. It answers the over-match question **on the migrated side**: three candidate
anchors hold at exactly 1 while the `aria-haspopup='menu'` population grows 10×, because the
per-tile menus carry no `crop_16_9` descendant. The **old-host** half is now unanswerable from
either account — both have migrated — so classic fixtures are the remaining route.

---

## e2e-gate

| To prove | Cost | Available here? |
|---|---|---|
| A fixed: exit 36 in ~3 s on a real migrated `image t2i` | headed Flow browser, migrated account | **yes** — this account is migrated |
| B fixed: locale recovered on a latched profile | headed browser, latched profile | **yes** — reproduce by writing `NOT_REDIRECTED` into the profile's locale file |
| No regression on the **old host** | headed browser, non-migrated account | **no** — both accounts have migrated. Classic fixtures + the reporter's offer are the only routes |
| The `pt` recovery end to end | headed browser, pt-BR migrated account | **no** — reporter's account only |

The old-host regression check is the gate that cannot be closed locally, and it is the one
that matters: A's fix changes navigation settle behaviour on the path that still works.

## Hand-off

Verdict `CONFIRMED-BUG`, localized (three files), fix live-verifiable here on the migrated
side. Both defects sit in the shared navigation/transport path with an unverifiable
old-host regression surface → **Phase 2 first.**

➔ `/gflow:predict "settle the migrated-origin redirect before the host gate; make
NOT_REDIRECTED recoverable"` → then `/gflow:issue-resolve 639`.

The question predict has to answer is not which selector — it is **where the host becomes
knowable without reintroducing dead time on the accounts that still work**, given that the
regression surface cannot be measured from this machine.
