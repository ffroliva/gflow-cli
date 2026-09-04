# How labs.google hands an account to flow.google.com (2026-09-04, $0)

**Question:** what actually decides which frontend we get? The transport told
users the migration *"flaps per page load, so retrying often lands the old
frontend"* (`api/transports/_common.py:120`), while recon recorded `ffroliva` on
the migrated host 7 loads out of 7. Both could not be true, and neither
explained the mechanism.

**Profile:** `ffroliva` · **Spend:** zero — navigation, cookie reads and public
static asset fetches only. Nothing submitted.

## Answer

`labs.google` serves a **normal HTTP 200** to a **fully authenticated** session,
and then the labs.google application **navigates itself** to `flow.google.com`.
The decision is a **boolean on the app's runtime config object**.

From `/fx/_next/static/chunks/pages/_app-<hash>.js` (the only one of 13 chunks
containing the string), deobfuscated:

```js
let { config } = useConfig();
let suppress   = someHook();
let FLAG       = config?.[<obfuscated key>];

useEffect(() => {
    if (!FLAG || suppress) return;
    let path = location.pathname.replace(/^\/(?:fx\/)?.*?tools\/flow\/?/, '/');
    let url  = 'https://flow.google.com' + path + location.search;
    window.location.replace(url);
}, [FLAG, suppress]);
```

It rewrites the path (stripping `/fx/**/tools/flow/`) and preserves the query,
which is why a migrated account lands on `flow.google.com/?hl=en`.

## What this rules out

| Theory | Verdict | Evidence |
|---|---|---|
| DNS | **No** | Both hosts resolve independently; nothing DNS-level is involved |
| Server-side 302 | **No** | `labs.google/fx/tools/flow?hl=en` returns **200**. The only 3xx in the chain are unrelated assets (feedback JS, avatar) |
| Missing labs.google session cookie | **No** | `has_labs_next_auth=True`, `/fx/api/auth/session` → `200` with `access_token` + `user`. Authenticated, and still handed off |
| Flapping per page load | **No** | 5/5 `flow.google.com`, `flapped=False`, matching the earlier 7/7 |

The `has_labs_next_auth: false` recorded on 2026-09-03 was a property of *that*
experiment's setup — a headless `httpx` run that deliberately loaded only
`.google.com` cookies — not a property of the account. Do not re-derive a
cookie-cause hypothesis from it.

## Where the flag is NOT

- **Not in the bootstrap HTML.** `flow.google.com` appears **0 times** in 442 KB.
- **Not in `__NEXT_DATA__`.** It holds only Next.js internals (`__N_SSP`,
  `isFallback`, `isExperimentalCompile`, `gssp`).
- **Not in any XHR.** 34 captured on a live navigation; zero mention.

The destination is **compiled into the bundle**; only the boolean is delivered
at runtime, via a `useConfig()` hook whose endpoint is not a `/fx/api/` path
(the bundle contains exactly one such literal, `/fx/api/auth`). Candidate names
seen in the bundle: `appConfig`, `missing-app-config-values`, `activeConfig`.

**Not pursued:** the exact key name needs the obfuscator's string table decoded.
Stopped deliberately — the mechanism is settled and the name changes nothing we
can act on, since the value is server-assigned per account either way.

## `pinhole` is Flow, not a migration

A keyword sweep turned up `pinhole_migration_status_banner_*` and it is
tempting to read as a migration programme. It is not. **`pinhole` is Flow's own
i18n namespace prefix** — 840 occurrences, e.g. `pinhole_about_flow` → "About
Flow", `pinhole_media_picker_title` → "Media Grid". The migration banners say
*"Your media transfer from ImageFX to Flow is complete"* — a **media library**
migration between products, unrelated to the host handoff.

## Consequences for gflow

1. **`_common.py:120-121` is wrong and user-facing.** It tells the operator the
   migration flaps and that retrying often lands the old frontend. It does not
   flap (5/5, 7/7), and retrying cannot succeed on a flagged account. This sends
   people into a loop and generates junk reports.
2. **There is no override.** The flag is server-assigned. Re-authenticating will
   not help — the handoff happens *with* a valid session. Pointing our routes at
   `flow.google.com` lands on an app the **current** drivers cannot drive — but
   see the inventory below: it is the same product with a different widget
   toolkit, and a driver for it is bounded work.
3. **Detection can be instant instead of timed.** We currently navigate, wait,
   and re-read `page.url`. Because the handoff is a real navigation
   (`location.replace`), Playwright's `framenavigated` fires on it — an
   event-driven check replaces a fixed wait. Same defect shape as the #639
   locale probe, which also guesses a duration instead of awaiting a condition.

## Inventory of the migrated editor (same account, `/project/<id>`, `lang=en-GB`)

`scripts/dev/spike_migrated_editor_dom_inventory.py`, $0, screenshot in
`_spike_out/`. Memory called the migrated DOM a dead-end ("a new driver, not a
selector patch"). Half right. It IS a new driver — but over an **identical domain
model**, which makes it plannable instead of a re-derivation from zero.

**What is the same.** The composer ("What do you want to create?"), the `+`
add-media control, an **Agent** pill, the `arrow_forward` submit, and the settings
chip **`Video · 720p · 8s ▭ x1`** — the very chip gflow already reads. All 45
Material Symbols ligatures gflow anchors on are present and locale-invariant:
`crop_16_9`, `crop_9_16`, `crop_free`, `videocam`, `image`, `chrome_extension`,
`arrow_drop_down`, `apps_spark_2`, `more_vert`, `add`, `settings_2`, …

**What changed — the widget toolkit, not the model.** Angular Material replaces
Next.js/Radix:

| labs.google | flow.google.com |
|---|---|
| ligature carrier `<i class="google-symbols">` | `<mat-icon>` (`i.google-symbols` = 0, `mat-icon` = 45) |
| settings popover `[role='menu']` | `.cdk-overlay-container .cdk-overlay-pane` (0 `role=menu`) |
| option groups `[role='tab']` | `[role='radio']` inside `[role='radiogroup']` (0 tabs; 16 radios in 6 groups) |
| settings trigger `button[aria-haspopup='menu']` | `button[aria-label="Settings trigger"].settings-trigger-button` (no `aria-haspopup`) |
| model picker | `button` whose text is `<model name> arrow_drop_down`, inside the overlay |

The six radiogroups, verbatim, map straight onto the settings DTO gflow already has:

```
[imageImage, videocamVideo]                     mode
[crop_freeFrames, chrome_extensionIngredients]  submode
[crop_16_916:9, crop_9_169:16]                  aspect
[360pinfo, 720p]                                resolution   <- NEW axis, not on labs.google
[4s, 6s, 8s, 10s]                               duration     (Omni 1.1 Flash was selected)
[x1, x2, x3, x4]                                count
```

Each option is `<ligature><label>` text — the same shape as the labs.google tabs, so
the existing ligature-keyed matching carries over once the role and carrier change.
`aria-label`s are translated ("Favourite", "Reuse prompt", "Add media menu") and
must not be anchored on — the locale-invariance rule holds here exactly as it does
on labs.google.

Also present: a dismissable "high demand" info banner at the top — the same overlay
class `_dismiss_blocking_overlays` already handles. The #639 locale probe timed out
(`account_locale_lang_unchanged … waited_ms=4000`) on every one of these loads, live.

**So "make flow.google.com the frontend gflow drives" decomposes to:** a `mat-icon`
carrier in the ligature cascades, a `cdk-overlay` + `radiogroup`/`radio` open/select
protocol replacing `menu`/`tab`, the settings-trigger anchor, and a resolution axis.
Generation is not reCAPTCHA-blocked on this path: the page mints its own token in a
real browser, as labs.google does today — the token gate is a *headless HTTP*
problem, not a UI-automation one. Route it through `/gflow:predict` as a new driver.

## Reproduce

```powershell
$env:PYTHONUTF8=1
.venv\Scripts\python.exe scripts\dev\spike_migrated_host_trigger.py --profile ffroliva --samples 5
.venv\Scripts\python.exe scripts\dev\spike_migration_flag_bootstrap.py --profile ffroliva
.venv\Scripts\python.exe scripts\dev\spike_migrated_editor_dom_inventory.py --profile ffroliva
```

Both are read-only and spend nothing. The second dumps the raw bootstrap HTML to
`scripts/dev/_spike_out/` so further hypotheses can be tested offline instead of
paying a browser round trip each time.
