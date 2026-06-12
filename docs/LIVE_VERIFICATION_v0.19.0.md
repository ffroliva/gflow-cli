# Live Verification — v0.19.0

Feature under test: **opt-in Patchright browser engine** (`GFLOW_CLI_BROWSER_ENGINE=patchright`).
Verified 2026-06-12 against live Google Flow on a real authenticated profile. Image
generation is credit-free, so the end-to-end run cost **$0**.

## Layer 1 — file count

One image produced by `gflow image t2i` through the integrated patchright path:

```
scripts/dev/_spike_out/patch_verify/images/2026-06-12/6cb21d9e-e4f9-40ba-897c-eb25c6333892_1.jpg
```

(written to a throwaway `GFLOW_CLI_OUTPUT_DIR`, since cleaned up).

## Layer 2 — magic bytes

`b[:3] == FF D8 FF` → **valid JPEG**. Size 723,911 bytes.

## Layer 3 — dimensions / shape

Pillow: `format=JPEG dims=(768, 1376)` → correct **9:16** portrait (the default aspect).

## Layer 4 — structlog invariants

From the live run (`correlation_id=40877345-0af4-472f-966a-94f88112f0d9`):

- `browser.engine_selected engine=patchright` — the **settings resolver** selected
  patchright (the real wired path, not a test monkeypatch).
- `ui_automation.batch_request_body` — prompt submitted (`bytes=5054`).
- `ui_automation.batch_response_seen status=200` and
  `ui_automation.batch_response_captured status=200` — `batchGenerateImages`
  returned 200 and the response body parsed under patchright.

## Layer 5 — user-confirmable artifact

A studio-lit image of "a small green cactus in a terracotta pot" — visually
inspectable, matches the prompt.

## Supporting checks (same release)

| Check | Evidence | Result |
|---|---|---|
| Default path byte-identical | client-level `navigator.webdriver` read through real `FlowApiClient`: `playwright → None` (masked), `patchright → False` (both non-bot) | ✅ default still stealthed |
| Missing-dependency UX | `GFLOW_CLI_BROWSER_ENGINE=patchright` with patchright uninstalled → `image t2i` | ✅ **exit 24**, `BrowserEngineUnavailableError` + `pip install patchright` hint, no traceback, no browser launched |
| Invalid enum setting | `GFLOW_CLI_BROWSER_ENGINE=patchwright` (and `GFLOW_CLI_PROVIDER=bogus`) | ✅ **exit 11**, clean "Configuration error" naming the variable, no raw pydantic traceback |
| reCAPTCHA mint under patchright | `TokenMinter.mint` with `isolated_context=False` | ✅ returns a real token (the predicted isolated-world break, fixed) |
| Retry across engine boundary | patchright's distinct `TimeoutError` through the real retry policy | ✅ retried (unit test) |

## Premise still pending (documented, not a blocker)

The *value* premise — does patchright reduce real 403s vs Playwright — could **not**
be measured this cycle: the documented hot profile (`denon82`) had cooled, so plain
Playwright also returned 200 (no 403 to flip). A faithful 403 cannot be emulated
locally (a route-injected 403 is engine-blind; inflating real WAF heat risks the
account). The feature ships as a **proven non-breaking, opt-in escape-hatch** with
the default unchanged. Re-fire trigger: the instant any profile actually 403s, run
`scripts/dev/spike_patchright.py --profile <hot> --engines playwright,patchright`
to capture the `WAF_403 → OK_200` differential.
