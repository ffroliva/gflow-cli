# Spike — what a reCAPTCHA mint failure looks like, and whether it is transient (#915)

**Date:** 2026-10-01 · **Profile:** `ci-probe` (host Flow served: flow.google.com) ·
**Cost:** $0 (mints only, tokens discarded) ·
**Script:** [`scripts/dev/spike_recaptcha_error_shape.py`](../../../scripts/dev/spike_recaptcha_error_shape.py)

## Question

`RecaptchaError` subclasses `RuntimeError`, not `GFlowError` (#915), so it exits 1 with no
Problem Details and aborts a batch past `--continue-on-error`. Making it a `GFlowError`
needs one answer the code cannot give: **is it retryable?** A retry flag that invites a
doomed loop is worse than none (`FlowHostMigratedError`, #639).

## Where it can be raised (code, not live)

`TokenMinter` (`api/recaptcha.py`) is the only raiser, reached only through
`FlowApiClient._mint_recaptcha_token`, whose callers are:

- `extend_video` (`gflow video extend`)
- `_upsample_image_impl` (`gflow image upscale`)
- image generation on a transport **without** `uses_page_owned_image_recaptcha` — the
  experimental HTTP transports. The default `ui_automation` transport skips the client
  mint since #891, so `gflow image t2i` / `image batch` / `gflow run` on the default
  transport never raise it.

No MCP tool exposes upscale or extend on `develop`; the worker reaches it only on an
experimental transport.

## Measurements (three runs on 2026-10-01, real page pool)

| Arm | Path | Result |
|---|---|---|
| A. pool page at `about:blank` (#891/#914 state) | client `_mint_recaptcha_token` ×3 | `RecaptchaError` "Could not discover reCAPTCHA site key…" **3/3, identical** |
| B. flow.google.com root grid | client path | `FlowHostMigratedError` (exit 36) — guarded before the mint |
| C. flow.google.com `/project/<id>`, steady | `TokenMinter.mint` ×5 | **5/5 OK** (≈2.4k-char tokens) |
| D. mint racing a navigation on that page | `TokenMinter.mint`, then re-mint after settle, ×3 | raced: `RecaptchaError` "evaluate failed … Execution context was destroyed" **3/3**; re-mint **3/3 OK** |
| E. site-key **read** alone, 0–0.2 s into a navigation (council on #939: D's failures were all the *execute* step) | `discover_site_key`, then re-read after settle | 0 s: OK ×2 · 0.02/0.05 s: "context destroyed" ×2 · **0.1/0.2 s: "no site key" ×2** · every re-read **OK** |
| F. why E's 0.1–0.2 s rows said "no key" | `document.readyState` + key at the failure point | 0.1/0.15 s `interactive`, no key · **0.2/0.3 s `complete`, no key** · 0.5 s `complete`, key present · `about:blank` `complete`, no key |

## Reading

- **An execute failure, or a failed site-key read, is a page-state race that clears**
  (D, E). The same page mints once it settles. → `retryable=True`.
- **A missing site key is NOT always deterministic.** The first reading (from A alone)
  said it was; E falsified that: 0.1–0.2 s into a load a Flow page has no key yet, and a
  re-read finds it. **F falsified the obvious discriminator too:** `document.readyState`
  is already `complete` at 0.2–0.3 s with no key — Flow injects the script after load —
  so "still loading" cannot be read off the document.
- **What does separate the two is the page itself.** `about:blank` is not a web page and
  never gets the script (A: 3/3); a Flow `https` page mid-load does (E: 4/4 re-reads).
  → missing key is `retryable` **iff** the page URL is `http(s)`. The one `https` page
  known to lack the script for good, the flow.google.com root grid, is refused before the
  mint (B: exit 36), so it never reaches this flag.
- A healthy project page does not fail (C), so there is no evidence of a background
  flake rate that would make the whole class retryable.
- **Where a user meets it.** Through the client, any flow.google.com page is refused
  before the mint (B), so the retryable shapes reach a user only on a page Flow served
  from labs. A navigation destroying a page's context is Playwright behaviour, not a
  host's, so the flag is measured where it can be; the labs arm itself is unobserved.

## Not measured

- An empty token returned by `grecaptcha` (the third raise site): not produced here.
  It keeps the class default (not retryable) until it is observed.
- An `https` page that finished loading and will never get the script (a labs layout
  change): would be flagged retryable and cost one wasted re-run — no request is sent and
  no credit spent by a failed mint. Not observed.
- Labs-served accounts: none reachable (labs answers 308 on the three profiles here
  that hold a live Flow session).
