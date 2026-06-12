# Phase 0 Spike Results — Patchright engine

> Run 2026-06-12 via `scripts/dev/spike_patchright.py` on the real Chrome
> profiles. Credit-free (image generation). Two runs: primitives smoke on a
> clean profile (`ffroliva`), and the decisive dual-engine generation on the
> documented hot profile (`denon82`).

## Verdict: FEASIBILITY GREEN · PREMISE UNPROVEN (conditional)

Patchright is a **working, non-breaking drop-in** for the real generation path —
every technical risk predict surfaced is retired. **But the core value premise
(does it reduce 403s?) could not be tested**, because `denon82` is no longer hot:
plain Playwright also returned `200`. The differential can only be captured when a
profile is *actively* 403ing.

## Raw results

| Leg | Scenario | playwright | patchright | Verdict |
|-----|----------|-----------|-----------|---------|
| Launch + channel | 7 | launch ✓, channel=`chrome` | launch ✓, channel=`chrome`, **no `patchright install chromium` needed** | ✅ system Chrome honored; no exit-33 |
| navigator.webdriver | 3 | `None` (masked) | `False` (real-Chrome-like) | ✅ both non-`true` |
| discover_site_key | 8 | ok | ok | ✅ DOM shared across worlds |
| **reCAPTCHA mint (default)** | 1 | ok (main world) | **FAIL: "grecaptcha.enterprise not loaded"** | ⚠️ confirms isolated-context default breaks mint |
| **reCAPTCHA mint (isolated_context=False)** | 1 | n/a | **ok** | ✅ **the fix works** |
| Network listeners fire | 4 | 206 req / 200 resp | 125 req / 122 resp | ✅ both fire |
| **Faithful generation** | 2/6 | **OK_200**, 37.0s, body parsed, `has_media:True` | **OK_200**, 44.4s, body parsed, `has_media:True` | ✅ full chain works under patchright; ⚠️ no 403 to flip |

JSON: `scripts/dev/_spike_out/spike_patchright_20260612_224810.json` (+ `..._224728.json` for the clean-profile smoke).

## What this proves (GREEN)

1. **The predicted killer bug is real AND fixed.** Patchright's `evaluate` defaults
   to an isolated world where the page's `grecaptcha` global is undefined → the
   production mint (`recaptcha.py:97`) fails closed. Forcing `isolated_context=False`
   restores it. This is the one mandatory code change, and it is proven.
2. **`channel="chrome"` is honored** — Patchright drove system Chrome with no bundled
   driver download and no exit-33. Scenario 7 resolved; the Windows ~350 MB driver
   download is unnecessary for our use.
3. **Nothing else breaks.** Network listeners fire and `batchGenerateImages` bodies
   parse (`json_ok:True, has_media:True`); a real image generated end-to-end under
   Patchright. Legs 3/4 GREEN. The `_retry`/exception-class and two-site
   de-confliction risks remain *design* items but the runtime path is sound.

## What this does NOT prove (the gate is not fully met)

- **`denon82` is cool** — it returned `200` under *plain Playwright*. The 2026-05-23
  WAF-heat incident has decayed (consistent with KNOWN_ISSUES: heat decays over
  hours→days). With no 403, there is **zero evidence Patchright reduces detection**.
- Per **ADR #13** ("CDP Attach… parked until the stealth-flag fix is confirmed
  insufficient"), the governing precondition for investing in *any* alternative
  anti-detection engine is **still unmet**: the current stealth fix was *sufficient*
  on this profile today.
- Per the Devil's Advocate: the observed 403s are **per-profile heat/cadence**, not a
  static CDP leak — so the prior remains that Patchright may not move the needle even
  when a profile is hot. Unproven either way.

## Recommendation

This is a **conditional hold**, not a pass and not an abandon:

- **Do not build Phase 1 yet** on premise grounds — the value is unproven and ADR #13's
  precondition is unmet. Building 11 tasks of integration + a patched-Chromium
  supply-chain dependency for an unproven benefit is exactly what predict/ADR #13 warn
  against.
- **The spike is ready to re-fire the instant any profile 403s.** Re-run trigger:
  next time a real `WAF_403` is observed (mid-batch or on a heated account), run
  `spike_patchright.py --profile <hot> --engines playwright,patchright` and capture the
  differential. **PASS = playwright `WAF_403` while patchright `OK_200`.** That single
  data point converts this from "feasible" to "worth building."
- **If the escape-hatch value is wanted now** (have the opt-in engine ready *before* the
  next hot incident, since heat is unpredictable), Phase 1 is low-risk to build —
  opt-in, default unchanged, proven non-breaking — but it ships an *unvalidated*
  mitigation. That is a deliberate product call, not a technical necessity.

## Reversibility

`patchright==1.60.1` was installed into the primary venv only (not `pyproject.toml`).
Revert with `uv pip uninstall --python .venv\Scripts\python.exe patchright`.
