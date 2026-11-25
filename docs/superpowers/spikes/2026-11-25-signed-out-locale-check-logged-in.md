# Spike — signed-out `_check_logged_in` behavior outside English (#1289)

**Date:** 2026-11-25 · **Cost:** $0 (read-only navigation and DOM counts only; nothing typed or submitted)
**Instrument:** `scripts/dev/spike_1289_signed_out_locale.py` (in the history of #1481; removed with the method)

> **Outcome.** No production code called `UiSessionMixin._check_logged_in`, so the false `True` measured below never reached a user. #1481 removed the method and its tests instead of hardening it. The measured anchors remain valid for any future signed-in check: `a[href^='https://accounts.google.com/ServiceLogin']` and `<flow-landing-page>` mark a signed-out landing in every locale probed, and neither appears on a signed-in page, whose Google Bar links to `SignOutOptions`.
(browser `locale` + `Accept-Language` header + `?hl=` forced per pass)

## Question

`UiSessionMixin._check_logged_in` previously probed `button:has-text('Sign in'), a:has-text('Sign in')`. Issue #1289 raised two questions before changing the selector:

1. Does the URL check already make the English text selector unreachable, or do non-English signed-out pages pass the URL check and reach the selector?
2. What Tier-1 locale-invariant structural anchor identifies a signed-out Flow page without colliding with a signed-in Flow page?

## Observations

### Signed-out passes (fresh Chrome contexts, no session cookies)

| Start URL | Locale (`html.lang`) | Hops -> Final URL | `flow_landing_kind` | `has-text('Sign in')` | `a[href^='https://accounts.google.com/ServiceLogin']` | `<flow-landing-page>` | Old `_check_logged_in` |
|---|---|---|---|---|---|---|---|
| `https://flow.google.com/` | `en-US` | `/about` | `"public"` | 1 (`"Sign in"`) | 1 | 1 | `False` |
| `https://flow.google.com/?hl=pt-BR` | `pt-BR` | `/about` | `"public"` | 0 (`"Fazer login"`) | 1 | 1 | **`True` (false positive)** |
| `https://flow.google.com/?hl=zh-CN` | `zh-CN` | `/about` | `"public"` | 0 (`"登录"`) | 1 | 1 | **`True` (false positive)** |
| `https://labs.google/fx/tools/flow?hl=pt-BR` | `pt-BR` | `flow.google.com/` -> `/about` | `"public"` | 0 | 1 | 1 | **`True` (false positive)** |
| `https://labs.google/fx/pt/tools/flow` | `pt-BR` | `flow.google.com/` -> `/about` | `"public"` | 0 | 1 | 1 | **`True` (false positive)** |
| `https://flow.google.com/project/0000...` | `pt-BR` | `/about` | `"public"` | 0 | 1 | 1 | **`True` (false positive)** |

### Signed-in control pass (`<profile>`, served `flow.google.com`)

| Start URL | Settled URL | `flow_landing_kind` | `a[href^='https://accounts.google.com/ServiceLogin']` | `a[href^='https://accounts.google.com/SignOutOptions']` | `<flow-landing-page>` |
|---|---|---|---|---|---|
| `https://flow.google.com/` | `https://flow.google.com/` | `None` | 0 | 1 | 0 |
| `https://flow.google.com/?hl=pt-BR` | `https://flow.google.com/` | `None` | 0 | 1 | 0 |

## Conclusions

- **Reachability confirmed:** On non-English locales (`pt-BR`, `zh-CN`), signed-out `flow.google.com/about` passed `flow_host_kind` and lacked `'Sign in'`, causing `_check_logged_in` to falsely return `True`.
- **Two complementary locale-invariant gates:**
  1. Reject `flow_landing_kind(page.url) in ("signin", "public")` before the `/project/` check (handling `flow.google.com/about` and NextAuth `/fx/api/auth/...` URLs even before DOM inspection).
  2. Probe `a[href^='https://accounts.google.com/ServiceLogin'], flow-landing-page` instead of text labels — present across all three tested locales when signed out and absent when signed in. Matching bare `accounts.google.com` without `/ServiceLogin` would collide with the signed-in Google Bar's `SignOutOptions` link.
- **Not measured:** A signed-out or signed-in page served directly by `labs.google` without redirecting to `flow.google.com` (all `labs.google` probes here redirected to `flow.google.com`).
