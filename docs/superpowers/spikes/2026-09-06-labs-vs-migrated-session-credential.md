# Spike — is the `labs.google` Flow session token a credential for `flow.google.com`?

**Date:** 2026-09-06 · **Issue:** #644 (Refs #639, #642) · **Cost:** $0, read-only, no credits
**Profile:** `denon82` (migrated account) · **Verdict:** NO — and #644 stays LATENT

This spike answers the question a future #644 fix must not re-derive, and records the
one arm of the experiment the 2026-09-03 measurement could not reach.

---

## Q1. Is `__Secure-next-auth.session-token` usable on `flow.google.com`?

**No, for two independent reasons.**

### 1a. It is never transmitted (measured)

Cookie jar of `profile_denon82` replayed through `http.cookiejar`'s RFC 6265
`DefaultCookiePolicy` against each host:

| Request host | Cookies sent | `__Secure-next-auth.session-token` | `SAPISID` / `__Secure-1PSID` |
|---|---|---|---|
| `labs.google/fx/tools/flow` | 3 | no (already expired, see Q2) | no |
| `flow.google.com/` | 14 | **no** | **yes** |
| `flow.google.com/project/x` | 14 | **no** | **yes** |

The token is scoped to domain `labs.google`; `flow.google.com` is under `google.com`,
a different registrable domain. Domain-matching never attaches it. **The two hosts
receive disjoint credential sets** — this is the correct half of #644's premise.

### 1b. It would not be honoured if forced (mechanism, NOT measured)

It is an Auth.js/NextAuth session handle minted by and validated against the
`labs.google` BFF's own signing secret. `flow.google.com` is a Google-internal
Angular frontend authenticating on the standard Google SSO cookie set — different
issuer, different validator.

> **Unverified.** Testing acceptance requires forcing the token onto a
> `flow.google.com` request *while a live session exists*. This profile's session is
> dead (Q2), so the test would be vacuous — both arms return signed-out regardless.
> Re-run 1b after a fresh `gflow auth login`.

---

## Q2. Did the divergence #644 predicts appear?

**No.** The discriminating experiment requires **Google SSO alive + labs Flow session
dead**. What was measured is **everything dead** — a plain expiry, not a divergence.

```
labs.google/fx/api/auth/session   -> 200, 2 bytes ({}), has_user=False
flow.google.com/                  -> redirects to /about (marketing), avatar=0, signin_cta=1
labs.google/fx/tools/flow         -> rendered, avatar=0, no editor
myaccount.google.com/             -> redirects to www.google.com/account/about (signed out)
```

Remaining `labs.google` cookies: `__Host-next-auth.csrf-token`,
`__Secure-next-auth.callback-url`, `email`. The session token itself is **gone** —
Chrome dropped it on expiry.

**New information vs 2026-09-03.** That measurement could only observe the
`has_user=true` arm. This one reaches the `has_user=false` arm and finds the two hosts
**agree**: when the Google SSO session dies, the migrated host reports signed-out too.
That is evidence *against* the two hosts having independent session lifetimes — the
mechanism #644's false-negative scenario depends on.

**Still unobserved, still the only thing that makes #644 actionable:** an account where
`labs.google/fx/api/auth/session` returns `{}`/no-user **while** `flow.google.com`
renders authenticated (avatar present, editor rendered).

---

## Q3. Does gflow report anything wrong today?

**No.** `gflow auth status` on this profile yields `GOOGLE_SESSION_ONLY`
(stale `SAPISID` present, BFF says no-user) → *"Signed in to Google, but not to the
Flow app"* → re-login. On the measured state that guidance is **correct**.

#644's claimed defect sites are also mis-localized, and a future fix should not start
there:

- `api/client.py:209` `_FLOW_SESSION_COOKIE` is a **log field** (`preread_session=`,
  `client.py:624`) and the #222 macOS seed — not the auth check.
- The auth check is a **live probe** of a live BFF
  (`auth/verification.py::verify_flow_profile` → `fetch_flow_session_httpx`), not a
  cookie-name test. It cannot "inspect the wrong credential"; it asks the server.
- `auth/cookies.py`'s `flow_only=True` filter is a deliberate origin boundary, correct
  for its only consumer (an httpx client that talks solely to `labs.google`). Widening
  it would send `.google.com` SSO cookies to `labs.google` — a regression, not a fix.

**No code change is warranted *for #644 as filed*.** The harvest becomes wrong only when
a browserless path to the migrated host exists; that is #642's scope.

But tracing it surfaced a larger risk that #644 only gestures at — see Q4.

---

## Q4. What actually breaks if `labs.google` is deprecated?

**A re-login loop that the user cannot escape, plus silent profile degradation.**
This is the sharper form of #644's concern, and it is a design question for the owner
rather than something to build speculatively.

`RealChromeStrategy.login` (`auth/real_chrome.py:255`) ends every login with
`verify_flow_profile`, whose **sole oracle** is `labs.google/fx/api/auth/session`.
So that one host's BFF is the gate on every profile's usability — *including profiles
whose actual generation path never touches it* (UI automation, the migrated composer).

Failure sequence once labs stops minting Flow sessions:

1. User runs `gflow auth login` and signs in fully. SSO cookies land;
   `flow.google.com` renders authenticated.
2. `verify_flow_profile` asks labs → no user → `verified = False`.
3. `real_chrome.py:262` **unlinks `.gflow_browser_strategy`** (rollback of the
   speculative write, when the marker did not pre-exist).
4. The user is told to re-run `gflow auth login`. → goto 1, forever.

Step 3 is the load-bearing damage: without that marker `channel_for_profile` stops
returning `"chrome"`, so `FlowApiClient` downgrades to bundled Chromium — which the
code's own comment identifies as the `[[real-browser-auth-mandatory]]` failure. A labs
outage therefore does not merely block login; it **degrades a profile that would still
work through the browser**.

### The discriminator already exists

`ChromeCookieSnapshot.google_session` (`SAPISID` present, computed from the full jar)
already separates the states. What is missing is only that the last row's remediation
is unreachable:

| labs BFF | SSO cookies | outcome today | remediation correct? |
|---|---|---|---|
| user present | — | `AUTHENTICATED` | ✅ |
| `{}` | no `SAPISID` | `NO_SESSION` → re-login | ✅ re-login fixes it |
| `{}` | `SAPISID` | `GOOGLE_SESSION_ONLY` → re-login | ✅ today · ❌ under deprecation, never |
| non-200 | any | `VERIFICATION_ERROR` → "network" | ✅ today · ❌ under deprecation, permanent |

### Not built here, deliberately

The trigger is **unobserved** — Q2 found the two hosts share a session lifetime, so the
"SSO alive + labs dead" state may not even be reachable while labs is healthy. Building
a host-aware fallback now would guard a failure nobody can demonstrate, which is the
same trap #644's own triage refused.

But the asymmetry is worth the owner's attention: the check is nearly free (the signal
is already in hand), the failure is unrecoverable by the user, and it **cannot be
shipped after the deprecation** — by then every affected user is already stuck on a
version that loops.

**Recommended next step:** `/gflow:predict` on *"decouple profile validity from the
labs.google BFF"* before any implementation. Two candidate slices, smallest first:

1. **Never let a probe failure delete a marker that a successful browser login just
   earned** — narrow, defensible today, and removes the degradation in step 3.
2. **Give `GOOGLE_SESSION_ONLY` / `VERIFICATION_ERROR` an escape hatch** so a user with
   a live SSO session is not told to repeat an action that cannot help.

---

## Re-running this probe

Needs a profile under `GFLOW_CLI_HOME` and nothing else. Adjust `PROFILE`.

```python
import asyncio, json
from pathlib import Path
import browser_cookie3, httpx
from gflow_cli.auth.cookies import get_chrome_cookie_snapshot
from gflow_cli.paths import get_cookies_path

PROFILE = Path.home() / "AppData/Local/gflow-cli/profile_<name>"

async def main():
    snap = await get_chrome_cookie_snapshot(PROFILE)
    print("labs cookies:", len(snap.httpx_cookies),
          "next-auth:", "__Secure-next-auth.session-token" in snap.httpx_cookies,
          "SAPISID:", snap.google_session)

    # A) what verify_flow_profile actually asks
    async with httpx.AsyncClient(cookies=snap.httpx_cookies, timeout=20.0,
                                 headers={"referer": "https://labs.google/fx/tools/flow"}) as c:
        r = await c.get("https://labs.google/fx/api/auth/session")
    has_user = bool(json.loads(r.text).get("user")) if r.text.strip().startswith("{") else None
    print(f"A) labs session -> {r.status_code} {len(r.text)}B has_user={has_user}")

    # B) DOM truth on the migrated host — httpx sees only the SPA shell, so use a browser
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel="chrome", headless=False,
            args=["--password-store=basic"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://flow.google.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        print("B) flow.google.com ->", await page.evaluate("""() => ({
            url: location.href,
            avatar: document.querySelectorAll("img[src*='googleusercontent']").length,
            signin_cta: document.querySelectorAll("a[href*='ServiceLogin']").length,
        })"""))
        await ctx.close()

asyncio.run(main())
```

**DIVERGENCE CONFIRMED** iff `A) has_user=False` **and** `B) avatar > 0`.
Anything else is a normal session state.

> Instrument note: `httpx.get("https://flow.google.com/")` returns ~146 KB of Angular
> SPA shell with `WIZ_global_data` for signed-in and signed-out alike. It is **not** an
> auth signal — the 2026-09-03 measurement flagged the same trap. Use the rendered DOM.
