# The G12 block is alive, and `navigator.webdriver` is the discriminator (2026-09-08)

**Question.** `gflow auth login --browser chrome` asks the user to close the Chrome
window by hand, because `RealChromeStrategy` launches Chrome as a bare subprocess with
no debugging port and therefore has no signal channel. Sibling project `notebooklm-py`
auto-closes instead, and its mechanism is not a trick — it *owns* the browser
(`page.wait_for_url(...)`, then `context.close()`), accepting an automation surface and
mitigating it. Is gflow's zero-automation-surface constraint still load-bearing at
sign-in?

**Answer: the constraint is real, but it is finer-grained than "no automation surface".**
What Google rejects is a browser that admits to being automated. A Playwright-driven
browser that does not — either binary — signs in normally, and can then close itself.

**Instrument.** `scripts/dev/spike_playwright_chrome_login.py`. Three arms, each on its
own **unauthenticated** throwaway profile, each driven by a human signing in by hand.
Cost `$0` — navigation and cookie reads only, nothing submitted. Profiles deleted after.

## Observed

| Arm | Browser | Stealth flags | `navigator.webdriver` | `/v3/signin/rejected` | Session cookie | Verdict |
|---|---|---|---|---|---|---|
| `bare` | real Chrome | **no** | `True` | **reached at t=17.5 s** | — | **BLOCKED** |
| `stealth` | real Chrome | yes | `False` | never | t=59.43 s | PASS |
| `bundled` | bundled Chromium | yes | `False` | never | t=276.01 s | PASS |

Stealth flags = `--disable-blink-features=AutomationControlled` +
`ignore_default_args=["--enable-automation"]`. All three runs recorded
`pre_authenticated: false`, so every arm genuinely exercised the sign-in gate.

URL trails (query strings stripped — the rejection URL carries OAuth `state` and
`code_challenge`):

```
bare      1.67  labs.google/fx/tools/flow
          7.81  accounts.google.com/v3/signin/identifier
         17.50  accounts.google.com/v3/signin/rejected      <-- G12

stealth   1.78  labs.google/fx/tools/flow
         18.38  accounts.google.com/v3/signin/identifier
         26.94  accounts.google.com/v3/signin/challenge/pwd
         32.78  accounts.google.com/v3/signin/challenge/dp
         54.36  accounts.google.com.br/accounts/SetSID
         59.27  labs.google/fx/tools/flow

bundled   1.68  labs.google/fx/tools/flow
        265.37  accounts.google.com/v3/signin/identifier
        275.31  accounts.google.com.br/accounts/SetSID
```

Evidence: `scripts/dev/_spike_out/spike_pw_chrome_login_{bare,stealth,bundled}_20260908_*.json`
(gitignored — the trails carry account-scoped OAuth URLs).

## What the control establishes

**`bare` is a positive control, and it is the reason this spike concludes anything.**
Without it, two passes would have read as "the G12 block no longer fires" — and that
conclusion would have justified dropping the mitigations. It fires. It took 17.5 s.

Three things follow, in order of how much they change:

1. **The block is current, not historical.** [`KNOWN_ISSUES.md`](../../../KNOWN_ISSUES.md)
   *"G12 'browser not secure' block"* is marked Resolved/v0.6.0a2; the underlying Google
   behaviour is still live and still rejects on `/v3/signin/rejected`.
2. **The binary is NOT the discriminator.** Bundled Chromium — the browser that entry
   names as the thing Google rejects — passed, *with* flags. Real Chrome — the browser
   the entire `RealChromeStrategy` exists to use — was **blocked**, *without* them.
   `navigator.webdriver` tracked the outcome in all three arms.
3. **Therefore the stealth flags are load-bearing and the automation surface is not.**
   A Playwright connection is fine; an advertised one is not.

**Auto-close works.** In both passing arms the session cookie was detected from the owned
context and the script closed the window itself — `notebooklm-py`'s mechanism running on
gflow's surface.

## Not measured — do not read these as answered

- **Headless.** Every arm ran `headless=False`. This says *nothing* about headless in
  either direction.
- **Generation.** Sign-in and generation are gated by different machinery: generation is
  reCAPTCHA-Enterprise-gated (see [[flow-google-com-batchexecute-headless-proven]] —
  reads already work over pure `httpx`; the generation RPC carries a ~2.4 KB Enterprise
  token minted ~120 ms before submit). A login result does not move that.
- **Which flag does the work.** `--disable-blink-features=AutomationControlled` and
  `ignore_default_args=["--enable-automation"]` were only ever applied together. Whether
  either alone suffices is untested — keep both.
- **Anything beyond N=1.** One account, one Windows machine, one residential IP, one
  Chrome build (`Chrome/149.0.0.0`), one day. Google's sign-in risk scoring varies with
  account age, IP reputation and history, so this does **not** predict CI, a VPS, or a
  fresh account. Same discipline as [[flow-capabilities-are-cohort-dependent]].
- **Like-for-like risk evaluation between the passing arms.** `stealth` traversed
  `challenge/pwd` and `challenge/dp`; `bundled` went identifier → SetSID in ~10 s. The
  two passes are not directly comparable to each other. Neither is compromised as a
  contrast against `bare`, which never got past `identifier`.

## Defect found on the way

[`KNOWN_ISSUES.md`](../../../KNOWN_ISSUES.md) documents the G12 resolution as
*"`RealChromeStrategy` — launches the system's real Google Chrome via Playwright's
`channel="chrome"` with stealth flags."* **That implementation does not exist.**
`src/gflow_cli/auth/real_chrome.py` was created at `eb0de133` (2026-07-19) already as
passive capture, and `git log -S'channel="chrome"' -- src/gflow_cli/auth/` returns zero
commits — the auth strategy has never used Playwright. The entry describes the design
this spike now recommends, which is why the drift went unnoticed: it reads as correct.

## Implication for the design

Auto-close is reachable. The shape it wants is the one `KNOWN_ISSUES.md` already claims
we have: launch real Chrome through Playwright with `channel="chrome"`,
`no_viewport=True`, `chromium_sandbox=True` and **both** stealth flags; detect the
session from the owned context; `context.close()`.

`channel="chrome"` stays — **not** for the sign-in gate, which `bundled` shows does not
care about the binary, but because the profile produced must be a chrome-strategy profile
or `channel_for_profile()` returns `None` and generation silently downgrades to bundled
Chromium (see [[real-browser-auth-mandatory]]).

Two instrument bugs found mid-spike, both of which would be defects in an implementation:

1. **`viewport=` on a headed context makes the UI unusable.** Passing an explicit
   `viewport={"width": 1920, "height": 1080}` makes Playwright *emulate* that size
   independently of the real OS window; on a smaller or scaled display the sign-in form
   renders outside the visible area and zoom cannot recover it. A human-driven window
   needs `no_viewport=True`. **`internal_chromium.py` currently passes an explicit
   `viewport` to a window a human must sign into** — same shape, unverified there, worth
   checking.
2. **Playwright injects `--no-sandbox`** unless `chromium_sandbox=True`, producing
   Chrome's "You are using an unsupported command-line flag" banner and an extra
   automation signal that `real_chrome.py`'s raw subprocess does not carry.

Next gate: `/gflow:predict` before any auth code — this is a transport/auth change.
