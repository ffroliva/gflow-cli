# Does clicking the `/about` CTA recover the session? — No. It opens Google's "confirm it's you" re-auth.

- **Date:** 2026-09-23. All times are UTC.
- **Script:** [`scripts/dev/spike_about_cta_recovery.py`](../../../scripts/dev/spike_about_cta_recovery.py) (`--profile denon82`)
- **Profile:** `denon82`, the account that reproduced `/about` on `gflow project create` on 2026-09-20 (#888)
- **Cost:** $0. Each run makes three navigations and one click, and the script creates and submits nothing. A separate `gflow project create` confirmation at the end created one empty project.
- **Raw:** `scripts/dev/_spike_out/about_cta_recovery_20260923_{123025,144105,144516}.json` plus screenshots. The file names use local time (UTC+1). The files are gitignored.
- **Refs:** [#881](https://github.com/ffroliva/gflow-cli/pull/881), [#888](https://github.com/ffroliva/gflow-cli/issues/888), [#756](https://github.com/ffroliva/gflow-cli/issues/756), [#902](https://github.com/ffroliva/gflow-cli/issues/902)

## The question

PR #881 recovers from `flow.google.com/about` by clicking "Create with Google Flow". Its
premise is that `/about` means the session has no `flow.google.com` session and that the
CTA leads to the account chooser, which #764 already auto-selects. Nobody had measured
the click. The readings were written into the script's docstring before the run.

## Observed (11:30 run)

| Step | Landed | `flow_landing_kind` |
|---|---|---|
| bootstrap | gflow's own log: `flow_session_cookie_present=True`, `expired=False`, `google_sapisid_present=True` | — |
| control 1 (no click) | `flow.google.com/about`, `aisandbox-root` present | `public` |
| control 2 (no click) | `flow.google.com/about` | `public` |
| click `button.flow-button.variant-primary` #0 | `accounts.google.com/v3/signin/confirmidentifier` | `signin` |
| follow-up visit to `flow.google.com/` | `flow.google.com/about` | `public` |

The navigation chain after the click has two entries, both `confirmidentifier`. The
chooser was never shown, so `_handle_account_chooser` never ran. The `after_click`
screenshot shows Google's **"Confirm it's you — to protect your account, Google needs to
verify it's really you. Sign in again to continue"** page, in pt-BR. It shows the
account's own email and a single "Next" button, which leads to password entry.

The cookie names for `flow.google.com` were identical before and after the click: 25 names,
including `SID`, `OSID`, `__Secure-1PSID` and `__Secure-OSID`.

## Verdict

The script printed `DOES NOT RECOVER — /about again on the follow-up visit`. That version
graded only the follow-up visit. The observation also matches the pre-registered row
**"click -> Google password / sign-in form: needs a human"**. The rows overlap, and the
script now checks the click landing first (see the amendment in its docstring).

1. **The click does not recover this account.** It moves the failure from Flow's landing
   page to Google's identity re-verification, and that page needs a password. A retry or
   an automated click cannot pass it.
2. **The click reveals a pending identity re-verification on an account whose cookies
   look healthy.** `gflow auth status` and the client's cookie pre-read both call the
   session fine. That matches #756 ("the session is verified while this happens") and the
   [client-side decision](2026-09-11-about-redirect-is-decided-client-side.md).
3. **The PR's selector cascade depends on DOM order.** `button.flow-button.variant-primary`
   matched **18** buttons: the hero CTA, fifteen "Try in Google Flow" cards, "Apply here"
   and a "Get started" plan button. Four `<a href="https://one.google.com/ai…">`
   subscription links use the same styling. Index 0 happened to be the hero, so `.first`
   works only while the DOM order stays that way. `button.cta-button` matched exactly
   **1**, a second "Create with Google Flow" further down the page.
4. **The text arm is locale-dependent, as the review said.** On this account the `/about`
   page rendered English labels, while Google's re-auth page rendered pt-BR. A
   `has-text` arm would miss on any account whose Flow page is localised.

## `gflow auth login` does not clear it — 2/2

Straight after the first run, `gflow auth login --browser chrome --profile denon82`
(v0.78.0) reported `[OK] Flow session verified` **0.4 s** after Chrome launched. Its log
shows `auth_login_session_detected outcome=authenticated elapsed_s=0.4`, with probe
`in_context` and then `on_disk`. Nobody signed in. The login saw the healthy cookie jar
and closed Chrome before Flow could route to `/about`, so Google's re-verification page
never appeared.

The 13:41 re-run (`…144105.json`) gave the same result on every row: `/about` twice, the
click went to `confirmidentifier`, and the follow-up visit was `/about` again.

So `gflow auth login`, the remediation that #881's messages print, **cannot fix this
state** on the current release. The login's success probe checks whether the cookies are
valid, and this account's cookies are valid. The login instructions say to keep going
"until the Flow editor itself loads", but the detector fires long before the editor could
load. Filed as #902.

## Completing the re-verification by hand clears it

The operator then opened real Chrome on the profile directly (`chrome.exe
--user-data-dir=<profile_denon82> --password-store=basic https://flow.google.com/`),
clicked the CTA, completed "Confirm it's you" with the password, and closed Chrome. The
13:45 re-run (`…144516.json`) found:

| Run | Control 1 | Control 2 |
|---|---|---|
| 11:30, before | `/about` | `/about` |
| 13:41, after `gflow auth login` | `/about` | `/about` |
| 13:45, after the manual re-verify | `flow.google.com/`, app | `flow.google.com/`, app |

Same profile, same build, same code: 6/6 `/about` landings (controls plus follow-up visits)
before the manual step, and 0/2 after it. One intervention sits in between. So
**completing Google's re-verification cleared `/about` for this account.**

What that does and does not establish: the clearing is measured, and the causal link is
strong. What is still unnamed is the client-side signal Flow reads to decide on `/about`.
The 13:45 run did not reach the cookie step, so nothing here shows what changed in the
browser state.

The failing command confirms the result end to end. At 13:46, `gflow project create
--profile denon82` ran on v0.78.0, the build that exited 31 at `/about` on 2026-09-20. It
logged `project.labs_route_refused status=404`, then `migrated.project_created` and
`migrated.project_renamed`, and printed `Project created`.

## What this does NOT measure

- Whether other `/about` occurrences have the same cause. That includes #756's `ci-probe`
  case, `pr389fresh2` (see
  [stable-for-an-account](2026-09-11-about-redirect-is-stable-for-an-account.md)) and
  other users. One profile is one account.
- Whether a healthy account's CTA also leads to `confirmidentifier`. No control click was
  made on an account that is not on `/about`.
- Whether the in-page "Create" nav item (`button.nav-item`) goes somewhere different.

## Consequences

- **#881:** on this account the PR would raise `AuthExpiredError` (exit 3). That is a
  better exit class than today's 31. Its **remediation text is wrong for this state**,
  though: it tells the user to run `gflow auth login`, which the section above measured
  cannot help. The text has to send the user to finish Google's "Confirm it's you" in real
  Chrome on the profile. The click is a probe, not a recovery.
- **#881:** "this session is not authenticated" is still not what was measured. What was
  measured is narrower: Google requires the account to **re-verify its identity**, and the
  cookie state looks healthy while it does.
- **#881:** if the click stays as a probe, anchor it on `button.cta-button` (unique) and
  never on `variant-primary` `.first`.
- **Test home:** the scenario "`auth login` on an account with a pending identity
  re-check must not report success" belongs to #902's fix, through the Bug Lane. This
  spike is step 0 of that fix, not its test.
