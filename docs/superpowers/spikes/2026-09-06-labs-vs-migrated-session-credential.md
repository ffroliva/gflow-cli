# Spike — is the `labs.google` Flow session token a credential for `flow.google.com`?

**Date:** 2026-09-06 · **Issue:** #644 (Refs #639, #642) · **Cost:** $0, read-only, no credits
**Profile:** `denon82` (migrated account) · **Verdict:** NO — and #644 stays LATENT

This spike answers the question a future #644 fix must not re-derive.

> ### ⚠️ Read this first — a retraction, and the trap that caused it
>
> The **first pass of this spike measured the wrong directory.** It read
> `%LOCALAPPDATA%\gflow-cli\profile_denon82`. The real home is
> `%LOCALAPPDATA%\`**`ffroliva`**`\gflow-cli\` — `paths.default_home()` is
> `user_data_dir(APP_NAME, APP_AUTHOR)` with `APP_AUTHOR = "ffroliva"`, and on
> Windows that author segment is part of the path.
>
> The author-less path also exists, holds a single stale `profile_denon82`, and has
> none of the real home's scaffolding (`auth/`, `locks/`, `incidents/`, nine other
> profiles). Reading it returned a plausible, entirely misleading "expired session".
>
> **What was retracted:** the original Q2 finding — *"both hosts agree on signed-out,
> therefore evidence against independent session lifetimes"* — was measured on an
> abandoned profile and proves nothing. It is struck below.
>
> **Rule for the next run:** never hardcode a profile path. Resolve it:
> ```python
> from gflow_cli.paths import default_home, profile_subdir
> PROFILE = profile_subdir(default_home(), "denon82")
> ```
> A stale profile fails *quietly* — it decrypts, yields cookies, and answers every
> probe. Nothing in the output says "wrong account".

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

### 1b. It is not honoured when forced — **MEASURED**

Re-run against the correct profile with a **live, verified session**
(`denon82@gmail.com`, verified 11:09). Each request sends exactly one credential set
to `https://flow.google.com/`:

| credential sent | bytes | account email in body | GAIA id in body |
|---|---|---|---|
| labs `__Secure-next-auth.session-token` only | 146,488 | ❌ | ❌ |
| Google SSO cookies only | 150,744 | ✅ | ✅ |
| **no cookies at all (control)** | 146,497 | ❌ | ❌ |

**The labs token is indistinguishable from sending nothing** — 9 bytes off the
anonymous control, nonce-level noise, and carrying no identity. The SSO set embeds
the account email and GAIA id directly in the shell.

This upgrades 1b from reasoned to measured: the token confers **zero** authentication
on `flow.google.com`. Consistent with the mechanism — it is an Auth.js/NextAuth handle
minted by and validated against the `labs.google` BFF's own signing secret, while
`flow.google.com` authenticates on Google's standard SSO cookie set.

---

## Q2. Did the divergence #644 predicts appear?

**No — and no evidence either way.** ~~Both hosts agree on signed-out, which is evidence
against independent session lifetimes.~~ **RETRACTED** — that was the stray-directory
read (see the warning at the top). An abandoned profile being logged out says nothing
about the account.

The divergence needs **Google SSO alive + labs Flow session dead**. A fresh login
cannot produce it — login makes *both* alive — so this remains **unobserved**, exactly
as the 2026-09-03 triage concluded. No new evidence for or against.

### What the live run did establish: the instrument works

The pass condition recorded below was, until this run, an **unvalidated guess**. It is
now calibrated against both arms on the same profile:

| profile state | `flow.google.com/` lands on | `avatar` | `signin_cta` | `aria_buttons` |
|---|---|---|---|---|
| logged out (stray dir) | `/about` (marketing) | 0 | 1 | 90 |
| **live session** | `/` (app) | **1** | **0** | 47 |

The DOM signal cleanly separates authenticated from anonymous. A future
"no divergence" result from this probe can now be trusted to mean *no divergence*,
rather than *broken instrument*.

### New finding: the frontend has migrated, the auth BFF has not

Navigating to `https://labs.google/fx/tools/flow` **redirected to
`https://flow.google.com/`**, while `labs.google/fx/api/auth/session` still answered
`200 / 653 B / has_user=True` for the same profile at the same moment.

So for this account Google has already moved the **frontend** off `labs.google` while
leaving the **auth BFF** on it. gflow's only auth oracle now lives on the host the user
is actively being redirected away from. That is not the #644 divergence, but it is the
precise coupling that makes **Q4** load-bearing — and it means Q4's trigger is no longer
purely hypothetical: the migration is visibly in progress, with the auth BFF the last
piece still on the old host.

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

The precise failure — a live SSO session that the labs BFF refuses — is still
**unobserved**, and building a host-aware fallback for it would guard something nobody
can demonstrate. That is the trap #644's own triage refused, and it still applies.

**But the trigger is no longer hypothetical.** Q2's live run found the frontend already
migrated for this account (`labs.google/fx/tools/flow` → `flow.google.com`) while the
auth BFF stayed behind. The single oracle gating every profile now sits on the host
Google is actively moving users off. The remaining step to the Q4 failure is that one
endpoint following the frontend it belongs to.

The asymmetry is what deserves the owner's attention: the check is nearly free (the
signal is already in hand), the failure is unrecoverable by the user, and it **cannot be
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

Needs a profile under `GFLOW_CLI_HOME` and nothing else. **Resolve the path — never
hardcode it** (see the retraction at the top).

```python
import asyncio, json
import browser_cookie3, httpx
from gflow_cli.auth.cookies import get_chrome_cookie_snapshot
from gflow_cli.paths import default_home, profile_subdir

PROFILE = profile_subdir(default_home(), "<name>")
assert (PROFILE / "Local State").exists(), f"no such profile: {PROFILE}"

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
