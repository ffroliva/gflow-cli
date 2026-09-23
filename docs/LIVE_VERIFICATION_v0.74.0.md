# Live verification — v0.74.0

**Date:** 2026-09-14 · **Profile:** `ffroliva` (served `flow.google.com`) ·
**Cost:** **zero** — every arm below is a read or an upload-and-stop. No Veo credits and
no image generation were billed.

v0.74.0 carries four user-facing changes. Two were exercised against real Flow on this
machine, one is blocked on a cohort nobody here is in, and one touches no Flow surface at
all. Each is named below with which of those it is — the release's rule is that an
unverified item is *named*, never left blank, and that a blocker is a blocker only when it
is one.

| # | Change | Surface | Verified live? |
|---|---|---|---|
| 1a | Run-unique uploads + Frames-picker confirm (#792) | migrated **i2v** | ✅ $0 e2e, this tree |
| 1b | The same shared upload leg (#792) | migrated **r2v** and **i2i** | ⚠️ **not driven** — see §1 |
| 2 | `gflow credits` remediation at the 401 raise site (#795) | CLI + JSON envelope | ✅ $0, both surfaces |
| 3 | Agent-only composer exits 25 (#799) | migrated agent-only cohort | ❌ **named blocker** — no account in that cohort |
| 4 | Sponsors section / hall of fame / PyPI funding (#809) | README, docs, CI workflow | n/a — touches no Flow surface |

Row 1b is split out deliberately. An earlier draft of this table checkmarked all three
commands as one unit, which would have told a reader that r2v and i2i were e2e-verified.
They were not; only i2v was.

---

## 1. Run-unique uploads and the picker confirm (#792)

The defect and both fixes were reported, root-caused and verified end-to-end on the
affected cohort by **@ai4U23**; #800 implemented them from that diagnosis. What is
verified *here* is that the shipped tree still drives the live surface.

`tests/e2e/test_migrated_i2v_e2e.py`, `-m e2e`, against real Flow on `ffroliva`:

```
tests/e2e/test_migrated_i2v_e2e.py::test_e2e_start_frame_uploads_and_binds_on_the_migrated_host PASSED [ 50%]
tests/e2e/test_migrated_i2v_e2e.py::test_e2e_the_pickers_confirm_is_clickable_on_the_real_frames_picker PASSED [100%]
====================== 2 passed, 1 deselected in 29.54s =======================
```

The deselected arm is `test_e2e_i2v_from_a_local_start_frame_runs_on_flow_google_com` — `e2e_video`,
which bills a clip. It was deselected deliberately; this release needed no costed arm.

**Both arms failed twice before passing, and the reason is worth writing down rather than
quietly re-running until green.** The first two attempts ran *concurrently with the full
offline suite on the same machine*, and both died inside Playwright's actionability gate —
`element is not stable`, on an Angular Material menu mid-animation. One attempt lost the
toolbar upload menu item (`Locator.click: Timeout 4000ms`, then *"the upload entry opened
no file chooser within 8s"*); the other reached a valid `media_id` and a bound chip and
then failed the test's own stricter assertion, `assert not missed`. Run alone, both pass in
29.54 s.

So: **a flake caused by machine load, not by Flow and not by the change** — recorded here
because the same load will exist on anyone's laptop running these two things at once, and
because the second failure exposes a real gap between test and contract. `assert not
missed` is stricter than what the driver promises: `migrated_composer.py:1922-1926` treats
a confirm that will not take the click as a non-failure by design — *"the picker this
cohort's click DID commit may simply be on its way out"* — and falls through to let the
picker have the last word, which is exactly what happened (the attach succeeded). On an
auto-closing cohort with the grace forced to 1 ms, the confirm is routinely mid-teardown,
so that assertion is timing-dependent by construction. Filed rather than patched in a
release branch.

The second test is the one that matters for the confirm branch. This cohort's picker
closes on its own, so the branch would never execute against live Flow by accident — the
test drives `FRAME_COMMIT_GRACE_S` to `0.001` so that the grace expires before Flow can
auto-close, and **every** run therefore enters it. That is the difference between a named
blocker and an unrun claim: the *stuck picker* is a cohort we are not in, but *"does the
confirm gflow would click resolve, take a click, and leave the chip bound"* is answerable
here, and it was answered.

| Layer | Evidence |
|---|---|
| File count | probe PNG uploaded; the asset lands in the project library like any upload |
| Field value | `media_id` matches the UUID shape and is the id the app's own `maseQ` reply named |
| Shape | `BOUND_CHIP` count ≥ 1 — the frame is bound on the Start chip, not merely uploaded |
| Structlog | `migrated.frame_confirm_clicked` observed or correctly skipped; both are passes and both are now *observed* rather than argued |
| User-confirmable | the upload is visible in the project's library on `flow.google.com`, under its run-unique tagged name |

**Named external blocker, unchanged from #800:** a picker that is *genuinely stuck* cannot
be produced here, because this cohort's closes on its own. That half remains `Refs #792`,
not `Closes`, and its verifier is the reporter whose cohort enters it.

**r2v and i2i: what was and was not driven.** Both share the `_upload_via_toolbar` leg the
i2v e2e exercised live, and that shared leg is what the fix changed — but neither command's
own attach path was driven to completion here.

- **`video r2v --ref`** was **not attempted**. Its e2e spends Veo credits at the 8 s base
  tier and this release needed no costed arm.
- **`image i2i --ref` WAS attempted, twice, and failed both times** — exit 23, before
  reaching the upload leg at all. Both runs died at `ensure_editor`, because the
  `--project` UUID came from `gflow project list`, which reads the **aisandbox** catalog,
  and those ids do not exist on `flow.google.com`: the app served
  `https://flow.google.com/404?reason=project` and the `.settings-trigger-button` probe
  then timed out at 30 s. **That is a harness error, not a product defect** — the wrong
  project id was supplied — and the run never got near the code under test. It is recorded
  because an attempt that failed is evidence, and "not re-run" would have been false.

  It left a real finding behind, filed rather than fixed in a release branch: gflow lands
  on a URL whose own query string says `reason=project` and reports it as
  `UiSelectorDriftError` — *our* selectors blamed for Flow's 404. Same family as #799.

  A project id that does exist on the migrated host has to be read from that host (the home
  page's own `/project/<uuid>` links), not from `project list`. The i2v e2e above used one.

## 2. `gflow credits` stops sending migrated accounts to re-login (#795)

This account is in exactly the cohort the fix is for: `auth status` green, `credits` 401.
Measured on this tree, $0, read-only.

```
$ gflow auth status
Flow session verified as ffroliva@gmail.com.

$ gflow credits user            # exit 3
aisandbox-pa authentication failed: credits endpoint returned 401
-> Flow's labs.google session issued an API token and aisandbox-pa rejected it.
   Your Google sign-in is not the problem — minting that token is what proves it
   works. Most commonly Flow now serves this account from flow.google.com, where
   the aisandbox-pa read endpoints have not answered for us; generation keeps
   working, and `gflow credits` has no equivalent there yet — check your balance
   in Flow. A 403 can also be an entitlement or region refusal. See issue #795.
```

Three things are being asserted, and all three are visible above:

1. **The raise site is the right one.** `detail` reads `credits endpoint returned 401`
   with `"route": "credits"` — the #802 string. Through v0.73.2 this same state printed
   the class-default *"aisandbox-pa returned 401 after token refresh — SAPISID cookie
   missing, expired, or unreadable. Re-run `gflow auth login`"*, every word of which is
   wrong here.
2. **No browser was consulted.** The command returns immediately; the old path re-derived
   the verdict through a browser navigation before reporting it.
3. **The advice is no longer harmful.** On a migrated account, re-login can roll
   `.gflow_browser_strategy` back and start the #791 spiral. It no longer says to.

The multi-profile surface carries it too — `gflow credits list --json`, per profile:

```json
{
  "status": "error", "profile": "ffroliva", "authenticated": false,
  "credits": null, "error": "aisandbox-pa authentication failed",
  "error_type": "AisandboxAuthError",
  "remediation_hint": "Flow's labs.google session issued an API token and aisandbox-pa rejected it. …"
}
```

That `remediation_hint` is the change: it rendered only the class title before, so the
multi-profile surface — and the `gflow_get_credits(all_profiles=true)` MCP envelope, which
reads the same field — lost the diagnosis entirely.

**Still true and still open:** `credits` does not work on migrated accounts. The balance
surface for that cohort has not been located. #795 stays open for it; this release fixed
what the failure *says*, not the capability.

## 3. Agent-only composer → exit 25 (#799) — NOT verified, blocker named

Reported with DOM evidence by **@Cstanish127**. The discriminator is the absence of
`button.agent-mode-chip` on a `flow.google.com` composer that has no classic arm at all.

**This cannot be verified here, and the reason is a cohort, not an omission.** The
maintainer's accounts are served `flow.google.com` but still have the recoverable agent
mode — the chip is present, so the new raise site is structurally unreachable on them.
Producing the state would require an account Google has placed in that cohort.

Covered offline, and the exit-code surface was swept by hand across `errors.py`,
`EXIT_CODE_MAP`, the `docs/USAGE.md` exit-code table and `docs/MCP.md` — all four agree
that 25 is `FlowAgentUiError` and that the migrated agent-only raise site passes
`retryable=False` per-instance while the class stays in `RETRYABLE_ERRORS` for the labs
flapping cohort.

#799 stays open: gflow still has no driver for that composer. This release only changed
what it says when it meets one.

## 4. Sponsors (#809) — out of scope, stated rather than left blank

README Support section, `docs/SPONSORS.md`, the daily `sponsors.yml` hall-of-fame refresh
and a PyPI Funding URL. No CLI command, no MCP tool, no Flow surface — there is nothing to
drive against live Flow. Covered by `tests/scripts/test_update_sponsors.py`.

---

## What this release does not reach

**#791 — login verification for migrated accounts.** Not fixed, and not attempted blind.
The #791 state is SAPISID **plus** a live `flow.google.com` session (`OSID` /
`__Secure-OSID`) **plus** no labs NextAuth token: the migrated app working while the labs
oracle says no. All nine profiles on this machine were probed on 2026-09-14 at $0 and none
is in it — the two working accounts hold *both* sessions, and the rest hold neither. Four
of them print #791's exact symptom string and none of them is #791, because that string is
also what a plain stale profile says.

**Provenance of that probe, because it is not this release's own run.** The nine-profile
sweep was made earlier on 2026-09-14 by a separate session, using a throwaway script that
read each profile's cookie jar with `browser_cookie3`, passing the profile's own `Local
State` as the key file — the same thing `auth/cookies.py:105` does on Windows, and without
which every profile reads as *"Unable to get key for cookie decryption"* and the whole sweep
would have looked uniformly dead. **That script is not in the repo** and no longer exists;
it lived in that session's scratchpad, so the method is written out here rather than cited
as a path a reader cannot open. What *this* release re-confirmed independently is only the
profile count: `gflow credits list --json` returns `"count": 9`. Cited rather than re-run,
and labelled so a reader can tell the difference.

An oracle that decides `AUTHENTICATED` on a fail-open path is the one thing this project
will not ship unverified. The auth slice of PR #793 is wanted for the next release, from
the reporter who holds the only account that can test it.
