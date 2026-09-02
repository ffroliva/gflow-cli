# Live Verification — v0.66.0 (issue #639, Flow's `flow.google.com` migration)

**Date:** 2026-09-03 · **Account:** ffroliva@gmail.com · **Platform:** Windows 11, Chrome strategy (system Chrome)
**Branch:** `bugfix/639-flow-google-com-migration` · **PR:** [#640](https://github.com/ffroliva/gflow-cli/pull/640)
**Credits spent:** **0** — one Imagen image (images are credit-free; only Veo video spends) plus one read-only DOM probe.

## What had to be proven

The fix classifies a failure. Two independent claims, and offline tests can prove neither:

1. **On a real migrated load**, `_mode_switch_error` returns `FlowHostMigratedError` (exit 36, retryable) — not `UiSelectorDriftError` (23).
2. **On an old-host load**, nothing regressed — in particular the rewritten `_check_logged_in`
   host gate, which sits on the auth path of *every* run.

Both were verified against live Flow. The maintainer's own account turned out to be inside the
rollout, so this did not need the reporter.

## Layer 1 — the migration is real, and it flaps on one account

Same account, same profile, minutes apart, same gflow build:

| | old-host load | migrated load |
|---|---|---|
| final URL | `labs.google/fx/tools/flow/project/<id>` | `flow.google.com/project/<id>?pli=1` |
| `document.querySelectorAll('i').length` | **55** (reporter) | **0** (measured here) |
| `i.google-symbols` | 49 (reporter) | **0** (measured here) |
| `crop_*` trigger | present | **absent** |
| `gflow image t2i` | **exit 0**, image downloaded | **exit 36** |

The migrated URL also **drops the `/fx/tools/flow` path entirely** — it is `/project/<id>`, not
`/fx/<locale>/tools/flow/project/<id>`. That is why a substring gate on `labs.google` could never
have matched it.

## Layer 2 — the migrated path, measured (read-only, `$0`)

Probe: launch the authenticated persistent context, navigate to the migrated project URL, wait 9 s
for the composer, then run the real production functions against the live page.

```json
{
  "final_url": "https://flow.google.com/project/c5550ed7-...?pli=1",
  "flow_host_kind": "migrated",
  "check_logged_in": true,
  "dom": { "i_total": 0, "symbols": [], "crop": 0, "testids": [], "buttons": 19 },
  "mode_switch_error_class": "FlowHostMigratedError",
  "exit_code": 36,
  "retryable": true
}
```

- `i_total: 0` — **the reporter's central measurement reproduced independently.**
- `check_logged_in: true` — the auth-gate fix works on the real migrated page. Before the fix this
  was `false`, i.e. a valid session read as logged-out.
- `FlowHostMigratedError` / `36` / `retryable: true` — **the fix fires on the real thing**, not
  only on the DOM shape reconstructed in tests.

## Layer 3 — no regression on the old host (full generation path, exit 0)

`gflow image t2i --project <existing> --aspect 9:16 --count 1`

```json
{"status": "ok", "model": "NARWHAL",
 "images": [{"media_name": "b7d7316a-...", "seed": 49027,
             "dimensions": {"width": 768, "height": 1376},
             "local_path": "tmp/lv639/images/2026-09-03/b7d7316a-..._1.jpg"}]}
```

Verification ledger:

| Layer | Evidence |
|---|---|
| L1 file count | 1 file written |
| L2 magic bytes | `ffd8ffe0` → JPEG · 401,939 bytes on disk |
| L3 Pillow dims | `(768, 1376)` — **matches** the API-claimed dimensions |
| L4 structlog | `image_mode_entered` → `image_model_selected` → `count_setter_completed` → `prompt_submitted` → `batch_request_intercepted` |
| L5 wire | `flowMedia:batchGenerateImages` intercepted, `request0_keys` carried `imageAspectRatio`, `imageModelName`, `structuredPrompt` |

The mode-switch cascade **found** its controls and generation completed — the rewritten host gate
does not break the old path.

## Layer 4 — A/B control (offline, but the discipline that makes the above meaningful)

With all three fix sites neutered, **exactly 9 tests fail** — one per claimed behaviour. Restored:
256 passed. No test passes vacuously.

## Known limitation hit during verification, and how it was routed around

A first `t2i` attempt died at **exit 3 / HTTP 401 on `project.createProject`** — open issue
[#561](https://github.com/ffroliva/gflow-cli/issues/561), *not* this change: unmodified `develop`
was A/B'd and failed **identically** (same exit, same route). Passing an existing `--project` skips
project creation and routes past it. `gflow project list` succeeded throughout, confirming the
session itself was valid — the #561 signature exactly.

## Recon for the follow-up (support for the migrated frontend)

The migrated composer is **not** control-less; it expresses controls differently. It exposes **no
`data-testid`** and **zero ligature `<i>`**, but 13 `button[aria-label]` values:

```
Back button to go to previous page · More options for the project · Search ·
Filtering and sorting options · Add media menu · Product help · Tile grid settings ·
More options · Favourite · Reuse prompt · Add ingredients to the prompt box ·
Settings trigger · Start generation
```

`Settings trigger` and `Start generation` are the functional equivalents of the classic `crop_*`
trigger and submit button. **Caveat for whoever builds on this:** `aria-label` values are
*translated*, so these strings are Tier-2 at best and violate the locale-invariance rule as literal
selectors. The durable anchors are structural — button ordering within the composer container,
`svg` presence, and ARIA *roles* — and deriving them needs a second probe across locales.

## Verdict

| Claim | Status |
|---|---|
| Exit 36 fires on a real migrated load | ✅ **VERIFIED LIVE** |
| `_check_logged_in` accepts the real migrated host | ✅ **VERIFIED LIVE** |
| Old-host generation path unregressed | ✅ **VERIFIED LIVE** (exit 0, real artifact) |
| gflow *drives* the migrated frontend | ❌ **NOT CLAIMED** — out of scope; #639 stays open |
