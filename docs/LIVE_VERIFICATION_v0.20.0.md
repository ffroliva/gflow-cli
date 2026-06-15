# Live verification — v0.20.0 (Agentic UI driver)

> Hand-run against the real Google Flow editor on profile `denon82`. v0.20.0
> ships the pluggable `FlowUiDriver` strategy and **Agentic-cohort image
> generation**. The agentic cohort is server-assigned and cannot be forced from
> the client, so the deterministic trigger `GFLOW_CLI_FORCE_AGENT_UI=1` (clicks
> the in-input "Agent" toggle) is used to exercise the agentic path. Image
> generation is credit-free.

## Environment

| | |
|---|---|
| Branch | `feature/agentic-ui-driver` → `develop` (PR [#189](https://github.com/ffroliva/gflow-cli/pull/189)) |
| Version | `0.19.0` working tree (pre-v0.20.0 bump) |
| Profile | `denon82` (real-browser Chrome) |
| Profile dir | `C:\Users\ffrol\AppData\Local\ffroliva\gflow-cli\profile_denon82` |
| Date | 2026-06-14 |
| Transport | `ui_automation` |
| Engine | `playwright` |

## How to reproduce

```pwsh
$env:PYTHONUTF8=1
# Classic cohort (auto-detected):
uv run gflow image t2i "a red apple on a rustic wooden table" --profile denon82 --aspect 16:9 --json
uv run gflow video t2v "a golden sunset over calm ocean waves" --profile denon82 --aspect 16:9 --duration 4 --json

# Agentic cohort, forced deterministically (clicks the Agent toggle):
$env:GFLOW_CLI_FORCE_AGENT_UI="1"
uv run gflow image t2i "a single ripe banana on a white plate" --profile denon82 --aspect 16:9 --json
uv run gflow image t2i "three colorful macarons on a marble surface" --profile denon82 --count 3 --json

# Deterministic harness (verifies forced→agentic→scrape→download, asserts file count):
.\scripts\e2e\agentic_image_e2e.ps1 -Profile denon82 -Count 3
```

## Evidence ledger (5-layer)

Each run: **file written** + **magic bytes (JPEG/MP4)** + **dimensions/size** +
**structlog invariants** + **user-confirmable artifact (media UUID + path)**.

| # | Path | Cohort | Result | Media UUID(s) | Bytes | Key structlog invariants |
|---|---|---|---|---|---|---|
| 1 | image t2i (classic) | classic | exit 0, 1 JPG | `f3e529ca…` | 647 KB | `ui_driver.bound mode=classic`; `crop_16_9` matched; `batchGenerateImages` 200 captured |
| 2 | video t2v (classic) | classic | exit 0, 1 MP4 | `433a92d5…` | 6.33 MB | `video_mode_entered`; `poll_terminal STATUS_SUCCESSFUL`; `video_saved` |
| 3 | image t2i (forced agentic, count 1) | agentic | exit 0, 1 JPG | `3722b716…` | 420 KB | `agent_mode_forced activated=true`; `ui_driver.bound mode=agentic`; full-res `getMediaUrlRedirect` URL |
| 4 | image t2i (forced agentic, count 3) | agentic | exit 0, **3 distinct** JPGs | `0bfb4127…`, `2f5c5f9e…`, `cd8f4781…` | 596 / 590 / 629 KB | `mode=agentic`; **dedup**: multiple `<img>` nodes → 3 distinct media UUIDs |

## What this proves

- **Classic path unaffected** by the driver refactor (rows 1–2): image + video
  generate and download end-to-end through the new `ClassicFlowUiDriver`.
- **Agentic image generation works end-to-end** (rows 3–4): the Agent toggle
  flips the composer to agentic, the agentic driver encodes settings into the
  prompt, types into the Slate composer, **scrapes** the generated assets from
  the DOM (page-level network capture is dead in this cohort — Web-Worker
  delegated), **deduplicates by media UUID**, and downloads via the same-origin
  `labs.google/.../getMediaUrlRedirect` redirect (allow-listed for download).
- **Synthetic `GeneratedImage` fields** (`seed=0`, `workflow_id=""`,
  `dimensions=(0,0)`) flow through the save/report path without error.

## Not verified this cycle (recorded honestly)

- **A natural (non-forced) agentic load end-to-end:** the cohort flapped to
  classic during the windows tested; the forced path exercises identical driver
  code, but a naturally-served agentic load was not captured generating.
- **A positive content-policy refusal in the agentic cohort:** detection is
  scoped to alert/dialog regions pending a captured sample (a chat-message-only
  refusal currently misses → timeout). See `docs/AGENT_UI_E2E.md`.
- **Agentic video:** out of scope — raises `FlowAgentUiError` (exit 25) pending a
  video scraping capture.

See [docs/AGENT_UI_E2E.md](AGENT_UI_E2E.md) for the repeatable procedure and the
three render-race / detection bugs this live testing surfaced and fixed.
