# HAR + DOM spike harness (agent-browser over CDP)

**This is the second of gflow's two spike modes. Read [Which spike mode](#which-spike-mode)
before writing a new capture script — the first mode is usually cheaper.**

Drives a CDP-attached **real Chrome** through `agent-browser` to capture a full HAR of a
Flow generation you trigger **by hand**. Nothing here is imported by `gflow_cli`: the
harness adds no Node.js packages, no `node_modules`, no `package.json` and no
`agent-browser.json` to the project. Scripts call a pinned package through:

```powershell
npx --yes --package agent-browser@0.27.0 agent-browser
```

npm cache, agent-browser sockets and generated evidence all stay inside this folder, and
all three are gitignored.

## Which spike mode

| | `scripts/dev/spike_*.py` | this harness |
|---|---|---|
| Driver | in-process Playwright, gflow's own transport | CDP-attached real Chrome |
| Who clicks | the script | **you**, by hand |
| Sees | requests/responses the page makes while your code drives it | the complete HAR of a real user's generation |
| Reach for it when | you can drive the surface, or want to observe gflow's own path | the driver cannot reach the surface yet, or you need ground truth for an undocumented wire |

Start with a `spike_*.py`. Escalate here when a capture is ambiguous or the driver cannot
get far enough to observe anything — a hand-driven HAR is the ground truth that settles it.

Worked example: `spike_migrated_character_*.py` found the migrated character portrait
generation on `batchexecute` rpcid `ogiZ0b` (2026-09-06) by listening on gflow's own page.

## Goal

The questions this harness was built to answer, kept because they document the technique:

1. Does CDP-attached real Chrome report `navigator.webdriver === false`?
2. Can `agent-browser` see a logged-in Flow page when attached to a gflow profile?
3. Can it capture useful HAR/network evidence during a manual Flow generation?

## Prerequisites

- Node/npm available for `npx`.
- Google Chrome installed.
- A logged-in gflow profile created with `gflow auth login --browser chrome`.

## 1. Launch Chrome With CDP

From `scripts/dev/har-spike/`:

```powershell
.\scripts\launch-flow-chrome.ps1 -ProfileName default -Port 9334
```

If the script cannot infer the profile path, pass it explicitly:

```powershell
.\scripts\launch-flow-chrome.ps1 -ProfileDir "C:\path\to\profile_default" -Port 9334
```

Keep this Chrome window open while running the smoke script.

## 2. Run The Smoke Test

```powershell
.\scripts\run-cdp-smoke.ps1 -Port 9334
```

## 3. Capture A Manual Flow Generation

```powershell
.\scripts\start-har-capture.ps1 -Port 9334
```

The start script opens Flow through the existing CDP-attached Chrome session, clears the
request tracker, starts HAR recording, writes capture state under `artifacts/`, then
exits. Use the Chrome window to trigger exactly one Flow generation. When the generation
request has fired, stop and summarize the capture:

```powershell
.\scripts\stop-har-capture.ps1
```

The stop script saves:

- raw HAR: `artifacts/flow-generation-<capture-id>.har`
- filtered request list: `artifacts/network-requests-<capture-id>.json`
- redacted comparison summary: `artifacts/flow-generation-<capture-id>.summary.json`

Raw HAR and request artifacts are sensitive because they can contain auth cookies,
headers, tokens, prompts, and generation metadata. **Keep them local.** `artifacts/`,
`.agent-browser/` and `.npm-cache/` here are gitignored, and `*.har` is gitignored
repo-wide, so a capture cannot be committed by accident. Use the redacted summary for
transport comparison notes.

You can also summarize an existing HAR directly:

```powershell
python .\scripts\extract_har_summary.py .\artifacts\capture.har --host aisandbox-pa.googleapis.com --out .\artifacts\capture.summary.json
```

## Expected Signal

Good:

```text
navigator.webdriver: false
```

Bad:

```text
navigator.webdriver: true
```

If the value is `true`, CDP attach likely has no advantage over the current Playwright
launch path for reCAPTCHA scoring.

## Cleanup

Close Chrome manually, then remove generated evidence if needed:

```powershell
Remove-Item -Recurse -Force .\.agent-browser, .\.npm-cache
Remove-Item -Force .\artifacts\* -Exclude .gitkeep
```
