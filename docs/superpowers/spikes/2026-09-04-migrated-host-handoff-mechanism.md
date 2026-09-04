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
   `flow.google.com` would land on an app whose DOM we cannot drive at all.
3. **Detection can be instant instead of timed.** We currently navigate, wait,
   and re-read `page.url`. Because the handoff is a real navigation
   (`location.replace`), Playwright's `framenavigated` fires on it — an
   event-driven check replaces a fixed wait. Same defect shape as the #639
   locale probe, which also guesses a duration instead of awaiting a condition.

## Reproduce

```powershell
$env:PYTHONUTF8=1
.venv\Scripts\python.exe scripts\dev\spike_migrated_host_trigger.py --profile ffroliva --samples 5
.venv\Scripts\python.exe scripts\dev\spike_migration_flag_bootstrap.py --profile ffroliva
```

Both are read-only and spend nothing. The second dumps the raw bootstrap HTML to
`scripts/dev/_spike_out/` so further hypotheses can be tested offline instead of
paying a browser round trip each time.
