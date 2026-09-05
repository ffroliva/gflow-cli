# Spike — the migrated `flow.google.com` editor: DOM + wire for a driver (2026-09-05)

**Question:** can gflow drive Flow's migrated Angular frontend, and what does a
generation look like on the wire so a driver can submit, observe and download?

**Answer:** yes, end to end, in a real Chrome, with no reCAPTCHA obstacle — the
page mints its own token. Two real 8 s Omni 1.1 Flash generations completed in
~40 s each. And the new host is not only for flagged accounts: an **unflagged**
account loaded directly on `flow.google.com/project/<id>` is served the same
Angular editor.

Script: `scripts/dev/spike_migrated_submit_capture.py` (modes: `--no-submit`
landing probe, default $0 aborted submit, `--spend` real generation, `--probe-models`
dumps the model menu, `--via-labs` bootstraps through labs.google instead of direct). Outputs and
screenshots in `scripts/dev/_spike_out/` (gitignored). Companion recon of the
handoff mechanism and DOM inventory:
`2026-09-04-migrated-host-handoff-mechanism.md`.

## Bootstrap — go direct

| Account | Load | Served | Editor ready |
|---|---|---|---|
| flagged (`ffroliva`, en-GB) | `https://flow.google.com/project/<id>` direct | Angular editor, 45 `mat-icon`, 1 settings trigger, 1 textarea | goto 8.1 s, settled 11.1 s |
| unflagged (`denon82`, pt) | same, direct | Angular editor, 24 `mat-icon`, trigger + textarea | goto 11.4 s, settled 14.7 s |

No labs.google visit is needed on either account. For a flagged account this
skips the labs boot + client-side hop (and its 4 s `<html lang>` probe timeout).
The `.google.com` SSO cookies already in the profile authenticate the host.

Readiness anchor: `.settings-trigger-button` visible (a `cdk-overlay-container`
is NOT present until the first overlay opens). A dismissable "high demand" info
banner may sit at the top.

## DOM — what a driver touches

All anchors below are structural or ligature-based; the only text matched is a
numeric token (`8s`, `x1`) or a product name.

| Control | Anchor | Notes |
|---|---|---|
| settings trigger | `.settings-trigger-button` (the composer chip `Video · 720p · 8s ▭ x1`) | click opens a `.cdk-overlay-pane` |
| option groups | `[role='radiogroup']` × 6 in the pane, `[role='radio']` inside | `aria-checked` read-back works |
| mode | radio `:has(mat-icon:text-is('videocam'))` / `image` | |
| submode | `crop_free` Frames / `chrome_extension` Ingredients | |
| aspect | `crop_16_9` / `crop_9_16` | |
| resolution | radios with text `360p` (+ `info` ligature) / `720p` | cohort-keyed, also on a labs cohort |
| duration | radios `4s` `6s` `8s` `10s` | present with Omni 1.1 Flash selected; model-state (#650) |
| count | radios `x1` `x2` `x3` `x4` | |
| model picker | `button:has(mat-icon:text-is('arrow_drop_down'))` in the pane; text `<model name> arrow_drop_down` | opens a second `.cdk-overlay-pane` with `[role='menu']` and 4 `[role='menuitem']` buttons: **Omni 1.1 Flash · Veo 3.1 - Lite · Veo 3.1 - Fast · Veo 3.1 - Quality** (each prefixed by a `volume_up` ligature = audio-capable). Product names are proper nouns — matched as text, case-insensitive, never translated |
| cost line | text under the count row: "Generating will use **12 credits**" (Omni 1.1 Flash, 720p, 8 s, x1) | both real clips in this spike were billed 12 credits |
| close panel | `Escape` worked; fallbacks: click trigger again, click `.cdk-overlay-backdrop` | |
| composer | `[contenteditable='true']` (click, then `keyboard.type`) — the `textarea` is NOT clickable | placeholder "What do you want to create?" |
| submit | `button:has(mat-icon:text-is('arrow_forward'))` | enabled once the prompt is non-empty |

**Selector trap:** Playwright CSS `:text-matches('^\s*8s\s*$')` passes through
CSS string escaping, which turns `\s` into `s`. Match labels with a Python-side
`locator.filter(has_text=re.compile(r"^\s*8s\s*$"))`.

## Wire — one generation, observed twice

Everything is `POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=<id>&source-path=/project/<id>&f.sid=…&bl=boq_labs-ai-sandbox-frontend_<date>&hl=<locale>&rt=c&_reqid=…`
with body `f.req=<url-encoded JSON>&at=<CSRF>`. Responses are the `)]}'` envelope
with `wrb.fr` frames. Headers carry `x-same-domain: 1`, cookies, no bearer.

| t (s from page open) | Event | Detail |
|---|---|---|
| −0.19 | `POST www.google.com/recaptcha/enterprise/reload` | token minted in-page |
| 0 | click `arrow_forward` | |
| +0.65 | **`YhhmEf`** request | the generation submit (same rpcid as image gen) |
| +4.6 | `YhhmEf` **200** | returns media id, workflow id, project id, status `[6]` |
| +5 … +38 | **`jwpduf`** every 5 s | status poll; status `[2]` while running |
| +38 | `jwpduf` 200 | status `[3]`, plus a byte size (`2213107`) |
| +43 | **`as29s`** 200 | the same record with two signed `https://flow-content.google/...?Expires=…&KeyName=labs-flow-prod-cdn-key&Signature=…` URLs |
| after | polling stops; a new tile appears in the grid (`mat-icon` +4) | tile title is auto-generated ("Teal origami crane on table"), not the prompt |

Also seen: `WuwhI` (~+18 s, carries the prompt; empty `[]` reply — a prompt
history/save), `nzlxg` bursts right after submit in the aborted run.

### `YhhmEf` request body (decoded, tokens elided)

```
[[["YhhmEf","[[[[null,null,[[[\"<prompt>\"]]]],\"abra_t2v_8s\",2,null,
   [null,null,null,null,\"<uuid>\",\"<uuid>\"]]],
   [null,22,null,null,null,\"<project id>\",null,null,null,null,[\"<2448-char reCAPTCHA token>\",1]],
   [\"<uuid>\",1]]",null,"generic"]]]
```

- `abra_t2v_8s` — model **and duration** in one key (t2v, 8 s). The image
  equivalent and the Veo keys are still to be enumerated (model-picker probe).
- `2` after the key — aspect/orientation candidate (16:9 was selected).
- The 2448-char token is the reCAPTCHA Enterprise token; a real browser mints
  it, the driver never touches it. `at=` is the page's CSRF.

### Response record (shared by `YhhmEf`, `jwpduf`, `as29s`)

```
[ "<workflow id>", "<project id>", "<media id>", "CAE", null,
  [ [<created ts>], "<prompt>", null, null, null, null,
    [null, [["abra_t2v_8s", 1, null, null, 2, 1]], [[null,null,[[["<prompt>"]]]]], null, 1],
    null, [<STATUS>], 1, <signed POSTER (JPEG) URL when done>, [], null, <mp4 bytes when done> ],
  null,
  [ [null, 926545, null, null, null, null, null, "<prompt>", <signed VIDEO (mp4) URL when done>,
     null, null, null, "abra_t2v_8s", "", null, false, 2],
    [null, null, [8]],
    ["<workflow id>"] ] ]
```

`STATUS`: `6` at submit, `2` while running, `3` when complete. (Failure values
not yet observed.) **Which URL is which:** the first driver build downloaded
`DETAILS[10]` as the clip and got a 37 KB JPEG (`ff d8 ff fe …Google`); the mp4
(size = `DETAILS[13]`) is `MEDIA_INFO[0][8]`. The driver now verifies `ftyp` and
falls back to the other URL. Also measured: a `jwpduf` poll reports status 3 first
and the URL-carrying record arrives 2–8 s later, and the labs
`media.getMediaUrlRedirect` route answers **404** for a migrated media id. The `YhhmEf` reply wraps this in
`[null, 881, [[<media id>, null, null, [<title>, [ts], …, <workflow id>, …], <project id>]], [[<record>]]]`.

### What this means for the driver

- **Observe, don't poll:** the app polls `jwpduf` itself every 5 s. A
  `page.on("response")` filter on `rpcids=jwpduf` / `as29s` whose body carries
  the workflow id gives status + URL with zero extra traffic — same shape as
  the labs driver's aisandbox response listeners, different rpcids and envelope.
- **Download host:** `flow-content.google` (signed URL, `Expires`), so the
  download allowlist gains that host; cookies not required for a signed URL —
  verify on first download.
- **Cost:** two 8 s Omni 1.1 Flash clips were billed at the account's cohort
  rate; nothing else in this spike spent credits (aborted submits never left
  the browser).
- **Routing:** flagged accounts get this path automatically (today they get
  exit 36); unflagged accounts can opt in (`flow.google.com` serves them too)
  until the feature matrix (i2v frame slots, ingredients, uploads, characters,
  scenes, extend, tools) is ported and the e2e matrix is green — then it can be
  the default for everyone.

## Reproduce

```powershell
$env:PYTHONUTF8=1
.venv\Scripts\python.exe scripts\dev\spike_migrated_submit_capture.py --profile <migrated> --project <id> --no-submit   # $0 landing
.venv\Scripts\python.exe scripts\dev\spike_migrated_submit_capture.py --profile <migrated> --project <id>               # $0 aborted submit
.venv\Scripts\python.exe scripts\dev\spike_migrated_submit_capture.py --profile <migrated> --project <id> --spend       # bills one clip
```
