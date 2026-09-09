# Live verification — v0.72.0

> What was exercised against **real** Flow for this release, and — just as importantly —
> what was **not**.

**Date:** 2026-09-09 · **Profile:** `autoclose-verify` / `e2e-2fa-abandon` — throwaway
profiles created and deleted for these runs, authenticating `ffroliva@gmail.com`
(**migrated**, `flow.google.com`) · **Host:** Windows 11, real Google Chrome 149 ·
**Cost: $0** — no run below reaches a submit, so no Veo credit is reachable.

> **Profile hygiene note, and a false pass it caused.** The first auto-close run returned
> exit 0 with `elapsed_s: 0.2` — a *cached* session, because `profile_autoclose-verify`
> had survived the 2026-09-08 session and was never deleted. It read as a clean pass and
> proved nothing: no human signed in. Every run below was made against a profile deleted
> immediately **before** the run, and each is quoted with its `elapsed_s` so a cached
> session cannot masquerade as an interactive one again.

---

## 1. `gflow auth login` closes the browser itself — VERIFIED ✅

Reproduced three times on fresh profiles: **84.8 s**, **63.7 s**, **51.6 s** — each an
actual human sign-in, not a cached session.

```
auth_login_started                              08:12:03
auth_flow_session_verified  probe=in_context    08:13:30
auth_login_session_detected elapsed_s=84.8
"Signed in. Closing Chrome..."
auth_flow_session_verified  probe=on_disk       08:13:32
[OK] Flow session verified (ffroliva@gmail.com)   exit 0
```

Both oracles are now distinguishable in the log: `probe=in_context` (the live poll that
decides when to close) then `probe=on_disk` (`verify_flow_profile` re-checking what landed).
Before this release they emitted two identical events, leaving "did the on-disk check pass?"
unanswerable.

## 2. Closing the window yourself still verifies — VERIFIED ✅

```
auth_login_browser_closed_by_user   strategy=chrome    08:21:24
auth_flow_session_verified          probe=on_disk      08:21:24
[OK] Flow session verified (ffroliva@gmail.com)          exit 0
```

Note the **absence** of `probe=in_context` — auto-close never fired, so this is genuinely
the manual-close branch and not run 1 in disguise. Exit **0**, not exit 12.

> **This took four attempts to obtain honestly.** Attempts 1–3 all returned exit 0 and all
> three were *not* this path: sign-in takes ~50 s, detection fires the moment Flow loads,
> and auto-close won every race — the logs were shape-identical to run 1. Widening
> `POLL_INTERVAL_SECONDS` to 120 for one run made it deterministic (polls at t=0, before
> sign-in, and t=120, after the window was already closed), so the competing path could not
> fire. Only the sleep cadence was changed; the close-detection and fallback-verify logic
> under test was untouched. Instrument reverted, tree verified clean, suite re-run after.

## 3. `--browser auto` still selects and completes — VERIFIED ✅

`--browser auto` → `strategy=chrome`, launched, detected, auto-closed, exit 0.

**Scoped honestly:** this ran against an already-authenticated profile (0.3 s) and took the
Playwright path, so it did **not** exercise the subprocess fallback live. That fallback
remains covered by unit tests only — `test_no_chrome_channel_falls_back_to_subprocess`,
`test_launch_failure_falls_back_to_subprocess`,
`test_google_rejection_falls_back_to_subprocess_once`.

## 4. A window closed mid-2FA is noticed immediately — VERIFIED ✅

The failure mode: the host guard `continue`s without touching Playwright, so while the page
sits on `accounts.google.com` nothing raises and the reactive close-detection never fires.
Measured **before** the fix: a full run to the deadline with the session endpoint touched
**0 times** — on the default 600 s timeout, a ten-minute wait ending in exit 12 for someone
who closed the window after thirty seconds.

Verified by killing only the Chrome processes whose `--user-data-dir` was the test profile,
five seconds in — hand-timing does not work here, see the note below:

```
killed chrome                        09:59:39
auth_login_browser_closed_by_user    09:59:41   <- 2 s later
EXIT=8  ELAPSED_S=15
```

Exit **8** (`AuthMissingError`, "no sign-in detected") is the honest answer — no sign-in
happened — and critically **not** exit 12. The 15 s total is launch + the deliberate 5 s
wait + 2 s detect + the on-disk verify; none of it is latency in the path under test.

> **Why it was killed rather than closed by hand.** Two hand-timed attempts both returned
> `probe=in_context`, exit 0, at **15.6 s and 15.8 s** — near-identical on a *fresh* profile
> each time. That is not a human closing a window: on this host the account completes
> sign-in through a trusted-device/passkey path with no interaction, so the browser is only
> on Google's host for a few seconds. "Close it quickly" was a coin flip against that.

## 5. No page URL reaches a log event — VERIFIED ✅

OAuth `state` and `code_challenge` live in these URLs and `data/redaction.py` matches
neither. Across all six live runs above, no emitted event contains an
`accounts.google.com` URL. Pinned offline by
`test_never_logs_a_google_url`, which now drives the page from a secret-bearing Google URL
onto the Flow host mid-poll — so the secret-bearing URL is genuinely visited and still
reaches no log event.

## 6. `--browser internal` no longer launches the configuration Google rejects — VERIFIED (2026-09-08 spike, not re-run)

The bundled-Chromium path shipped with **no** anti-automation flags, so `navigator.webdriver`
was `true` — and that is what the sign-in rejects. Evidence is the three-arm run of
2026-09-08 (`docs/superpowers/spikes/2026-09-08-g12-blocks-webdriver-not-playwright.md`),
human-driven on fresh unauthenticated profiles, $0:

| arm | browser | stealth flags | `navigator.webdriver` | result |
|---|---|---|---|---|
| `bare` | real Chrome | **no** | `True` | **BLOCKED** `/v3/signin/rejected` @ 17.5 s |
| `stealth` | real Chrome | yes | `False` | PASS, cookie @ 59.4 s |
| `bundled` | bundled Chromium | yes | `False` | PASS, cookie @ 276.0 s |

The `bundled` arm is the one that matters here: it is the configuration `--browser internal`
now launches, and it signed in. **The `bare` control arm is the whole reason this concludes
anything** — with only the two passing arms it would have read as "the block is gone", which
would have justified removing the mitigations rather than fixing the flags.

**Scope, honestly:** measured on **one** account, one Windows host, one residential IP, one
Chrome build, one day, all arms headed. Bundled Chromium was measured only in the *flagged*
configuration, so its unflagged rejection is inferred from the shared `navigator.webdriver`
signal rather than observed directly. The flags were **not** re-exercised for this release —
this cites the spike that motivated them.

## 7. Migrated-host `gflow image t2i` — VERIFIED ✅ (re-run for this release)

```
GFLOW_CLI_E2E_PROFILE=ffroliva GFLOW_CLI_E2E_PROJECT=cec73a13-…
pytest -m e2e_image tests/e2e/test_migrated_host_e2e.py
-> test_e2e_t2i_runs_on_a_moved_account PASSED        (116 s total, $0)
```

`#692` is the largest user-facing addition in this release and its evidence was
second-hand — verified by its contributor (@arjhinety) during development, not by a
maintainer. This run makes the CLI `t2i` path **first-hand**: a real Chrome, on a migrated
`flow.google.com` account, driving the Angular Image mode and the page-owned `ogiZ0b` wire.
Zero Veo credits — image generation on this host is credit-free.

---

## NOT verified this cycle — stated, not omitted

### Migrated-host `i2i` over the queued MCP path — FAILED, cause unidentified (#770)

The second test in the same run failed, and the failure is **not a code defect**:

```
test_e2e_mcp_i2i_runs_on_the_migrated_host FAILED
MediaUploadRejectedError (exit 27): migrated host: a dialog opened after the file
was chosen and no maseQ request left the page — most likely Flow's one-time
upload-terms confirmation (host=migrated)
```

**The cause is UNKNOWN, and the error's own wording says so — "most likely".** The
maintainer confirms `ffroliva` has **already** accepted Flow's one-time *"Rights to use
this image"* dialog, so the message's leading hypothesis is wrong for this account.

That is not a lie in the message: the guard **counts** dialogs across the upload window
rather than identifying one, deliberately — the dialog's buttons carry no ligature and no
data attribute, and its copy is translated, so no anchor there satisfies the locale rule.
Its remediation already names this case: *"If you see a different dialog instead (an error,
a quota notice, a re-login), that is the one blocking the upload."* What we know is only
the conjunction the guard reports: **some dialog opened after the file was chosen, and no
`maseQ` request left the page.**

What this does establish:

- The MCP queued path reaches the upload stage on the migrated host, so the adapter and
  worker wiring are exercised up to that point.
- The failure is **non-retryable and typed** (exit 27) rather than a 60 s wait ending in
  advice to re-encode the file — which is the v0.71.1 behaviour change working, even though
  its leading hypothesis does not fit here.

What it does **not** establish, and must not be read as: that the upload path is healthy,
or that the consent dialog is involved. Note this is a *consented* account with an upload
that produced no `maseQ` — adjacent to [#719](https://github.com/ffroliva/gflow-cli/issues/719)
shape B (on a consented account ~1 upload in 4 sends `maseQ` and receives no reply), but not
the same signature, since here no `maseQ` left the page at all. **No incident bundle was
written** (exit 27 is not in the capture triggers), so there is no DOM dump naming the
dialog.

**To settle it** a run must capture what actually opened — a DOM/role dump at the moment
the guard fires. That is a spike, not a release step: filed as [#770](https://github.com/ffroliva/gflow-cli/issues/770), which also proposes adding `MediaUploadRejectedError` to the incident-capture triggers so the next occurrence diagnoses itself, and tracking per-account upload consent in the data layer so this guard can stop guessing.

### The `--browser internal` flags and the migrated refusals

See §6 above for the flags (spike evidence, not re-run this cycle). The `--aspect 3:4` and
`image batch` refusals fail *before* submit and were not separately exercised live.

### The reCAPTCHA-mint latch (#673) and the exit-36 refusals

Pinned by regression tests verified to fail without the fix (the latch test was
A/B-controlled at development time), but not separately exercised live this cycle. The
`--aspect 3:4` and `image batch` refusals fail *before* submit, so a live run costs nothing
but was not performed.

### `CHROME_BINARY` availability-check fix

Touches no Flow surface — it is a local Playwright channel-resolution check. Out of scope
for live verification; unit-tested.

### The OAuth-callback mechanism — INFERRED, NOT PROVEN (issue #769)

The session poll now stays off `/fx/api/auth/session` while a sign-in is in flight. That
change is correct on its own terms. **The causal story behind it is not established**, and
the first version of it was wrong: a host-only gate does *not* exclude NextAuth's callback,
because the callback runs on the app's own origin — verified,
`/fx/api/auth/callback/google?state=…&code=…` passes a `labs.google` host test. Under that
version the poll could still land mid-callback, yet all four sign-ins above succeeded.

So: the 600 s `error=OAuthCallback` failure of 2026-09-08 (observed **once**) is *presumed*
to have been caused by the poll, and four post-fix successes cannot distinguish that from
the window simply being narrow. **#769** carries the instrumented spike that would settle
it. Nothing in the CHANGELOG or the code comments should claim more than this paragraph.
