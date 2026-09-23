# Does clicking the `/about` CTA recover the session? — No. It opens Google's "confirm it's you" re-auth.

- **Date:** 2026-09-23
- **Script:** [`scripts/dev/spike_about_cta_recovery.py`](../../../scripts/dev/spike_about_cta_recovery.py) (`--profile denon82`)
- **Profile:** `denon82`, the account that reproduced `/about` on `gflow project create` on 2026-09-20 (#888)
- **Cost:** $0. Three navigations and one click. No project was created and nothing was submitted.
- **Raw:** `scripts/dev/_spike_out/about_cta_recovery_20260923_123025.json` plus screenshots (gitignored)
- **Refs:** [#881](https://github.com/ffroliva/gflow-cli/pull/881), [#888](https://github.com/ffroliva/gflow-cli/issues/888), [#756](https://github.com/ffroliva/gflow-cli/issues/756)

## The question

PR #881 recovers from `flow.google.com/about` by clicking "Create with Google Flow". Its
premise is that `/about` means the session has no `flow.google.com` session and that the
CTA leads to the account chooser, which #764 already auto-selects. Nobody had measured
the click. The readings were written into the script's docstring before the run.

## Observed

| Step | Landed | `flow_landing_kind` |
|---|---|---|
| bootstrap | gflow's own log: `flow_session_cookie_present=True`, `expired=False`, `google_sapisid_present=True` | — |
| control 1 (no click) | `flow.google.com/about`, `aisandbox-root` present | `public` |
| control 2 (no click) | `flow.google.com/about` | `public` |
| click `button.flow-button.variant-primary` #0 | `accounts.google.com/v3/signin/confirmidentifier` | `signin` |
| follow-up visit to `flow.google.com/` | `flow.google.com/about` | `public` |

Two navigations in the chain after the click, both `confirmidentifier`. The chooser was
never shown, so `_handle_account_chooser` never ran. The page Google renders there is
**"Confirm it's you — to protect your account, Google needs to verify it's really you.
Sign in again to continue"**. The page shows the account's own email and a single
"Next" button that leads to password entry.

The cookie names for `flow.google.com` were identical before and after (25 names,
including `SID`, `OSID`, `__Secure-1PSID` and `__Secure-OSID`).

## Verdict — pre-registered row: "click -> Google sign-in form: needs a human"

1. **The click does not recover this account.** It moves the failure from Flow's landing
   page to Google's identity re-verification, and that page needs a password. A retry or
   an automated click cannot pass it.
2. **This is the first measured cause for denon82's `/about`.** Google is demanding an
   identity re-confirmation for the account. The browser cookies are all present, so
   `gflow auth status` and the client's cookie pre-read both call the session healthy.
   That fits #756 ("the session is verified while this happens") and the
   [client-side decision](2026-09-11-about-redirect-is-decided-client-side.md): the cookie
   jar looks fine, and the account is waiting on a step-up sign-in.
3. **The PR's selector cascade is order-dependent.** `button.flow-button.variant-primary`
   matched **18** buttons. One is the hero CTA, fifteen are "Try in Google Flow" cards,
   one is "Apply here" and one is a "Get started" plan button. The same styling is also
   used on `<a href="https://one.google.com/ai…">` subscription links. Index 0 happened
   to be the hero, and `.first` depends on DOM order staying that way. `button.cta-button` matched exactly **1**, a second "Create with
   Google Flow" further down the page.
4. **The text arm is locale-dependent, as the review said.** On this account the `/about`
   page rendered English labels, while Google's re-auth page rendered pt-BR. A
   `has-text` arm would miss on any account whose Flow page is localised.

## `gflow auth login` does not clear it — 2/2

Straight after the first run, `gflow auth login --browser chrome --profile denon82`
(v0.78.0) reported `[OK] Flow session verified` **0.4 s** after Chrome launched
(`auth_login_session_detected outcome=authenticated elapsed_s=0.4`, probe `in_context`, then
`on_disk`). Nobody signed in. The login saw the healthy cookie jar and closed Chrome
before Flow could route to `/about`, so Google's re-verification page never appeared.

The re-run at 14:41 (`about_cta_recovery_20260923_144105.json`) was identical on every row:
`/about` ×2, click → `confirmidentifier`, and `/about` again.

So the remediation that #881's messages print, "run `gflow auth login`", **cannot fix this
state** on the current release. The login's success probe answers "are the cookies valid",
and this account's cookies are valid. The login instructions say to keep going "until the
Flow editor itself loads", but the detector fires long before the editor could load.

## Completing the re-verification by hand clears it

The operator then opened real Chrome on the profile directly (`chrome.exe
--user-data-dir=<profile_denon82> --password-store=basic https://flow.google.com/`),
clicked the CTA, completed "Confirm it's you" with the password, and closed Chrome. The
14:45 re-run (`about_cta_recovery_20260923_144516.json`) found:

| Run | Control 1 | Control 2 |
|---|---|---|
| 12:30, before | `/about` | `/about` |
| 14:41, after `gflow auth login` | `/about` | `/about` |
| 14:45, after the manual re-verify | `flow.google.com/`, app | `flow.google.com/`, app |

The same profile, build and code gave 6/6 `/about` landings (controls plus persistence
visits) before the manual step and 0/2 after it, with one intervention in between. That
makes Google's pending identity re-verification **the measured cause for this account**.
The failing command itself confirms it. At 13:46Z, `gflow project create --profile denon82`
(v0.78.0, the same build that exited 31 at `/about` on 2026-09-20) logged
`project.labs_route_refused status=404`, then `migrated.project_created` and
`migrated.project_renamed`, and printed `Project created`.

Per the pre-registered table, the third run "settles nothing about the click". It is not
meant to. It answers the question the click could not.

## What this does NOT measure
- Whether other `/about` occurrences, such as #756's `ci-probe` case or other users, have
  the same cause. One profile is one account.
- Whether the in-page "Create" nav item (`button.nav-item`) goes somewhere different.

## Consequence for #881

- On this account the PR would raise `AuthExpiredError` (exit 3) with "run `gflow auth
  login`". That remediation is **correct for this cause**, and better than today's exit
  31. The route to it (clicking a marketing CTA) is not a recovery, though. It is a probe.
- The wording "this session is not authenticated" is still not what was measured. The
  measurement is narrower: Google requires the account to **re-verify its identity**, and
  the cookie state looks healthy while it does.
- If the click is kept as a probe, anchor it on `button.cta-button` (unique) and never on
  `variant-primary` `.first`.
