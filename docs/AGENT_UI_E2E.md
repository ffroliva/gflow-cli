# Agentic UI — End-to-End Testing Runbook

> How to **deterministically** drive and validate `gflow`'s **Agentic UI** image
> path against live Google Flow. Companion to
> [AGENT_UI_RECON.md](AGENT_UI_RECON.md) (the reverse-engineering) and
> `docs/superpowers/plans/2026-06-14-agentic-ui-detection/` (the design).

## The problem, and the way in

Google Flow serves the composer via a **server-assigned A/B cohort** that flaps
per page load and has **no client-readable flag** — so the agentic UI cannot be
*requested*. A naive "retry until the dice land agentic" e2e is unreliable (we
observed 7 classic loads in a row).

**The deterministic trigger:** the *classic* composer exposes an in-input
**"Agent" toggle**. Clicking it switches the composer into the **same** agentic
layout — confirmed live (`scripts/e2e/capture_agent_toggle.py`, 2026-06-14):

| Signal | Before toggle | After toggle |
|---|---|---|
| `crop_*` media trigger | present | **gone** |
| `tune` settings gear | absent | **present** |
| Slate composer | present | present |

So `tune` appears and `crop_*` disappears → `detect_ui_mode` then binds the
agentic driver. An **`expand_content`** button opens the full agent side panel.

## Repeatable procedure (deterministic)

Set **`GFLOW_CLI_FORCE_AGENT_UI=1`** and the transport clicks the Agent toggle
after entering the editor, forcing the agentic path on any load. The harness
runs that and verifies the whole chain:

```powershell
# From repo root. Image generation is free — safe to run repeatedly.
.\scripts\e2e\agentic_image_e2e.ps1 -Profile denon82 -Count 1
.\scripts\e2e\agentic_image_e2e.ps1 -Profile denon82 -Count 3   # also exercises dedup
```

It PASSes only when all four hold (else it prints which stage failed):

1. `agent_mode_forced activated=true` — the toggle flipped the composer to agentic;
2. `ui_driver.bound mode=agentic` — the agentic driver was bound;
3. exit 0;
4. exactly `-Count` `.jpg` files written (scrape → dedup-by-UUID → download).

Run it directly without the harness, too:

```powershell
$env:GFLOW_CLI_FORCE_AGENT_UI="1"
uv run gflow image t2i "a ripe banana on a white plate" --profile denon82 --json
```

> `GFLOW_CLI_FORCE_AGENT_UI` is a **testing/diagnostic opt-in**, not a user
> feature: it forces the agentic path even when the server served classic.
> Leave it unset for normal use (the cohort is then auto-detected per
> generation).

## Validation result (2026-06-14, profile denon82)

Forced-agentic runs validated the three assumptions that mocked tests cannot:

- **`--count 1`** → exit 0, one **420 KB JPG**. Scrape found the asset, built the
  full-res redirect URL, the `labs.google` download resolved with session
  cookies, and the synthetic `GeneratedImage` fields (`workflow_id=""`, `seed=0`,
  `dimensions=(0,0)`) flowed through the save path without error.
- **`--count 3`** → exit 0, **3 distinct JPGs** for 3 distinct media UUIDs —
  confirming dedup: the agentic UI's multiple `<img>` nodes per asset collapse to
  the correct count.

Classic remains unaffected: live `gflow image t2i` and `gflow video t2v` both
succeed when the cohort/loads serve classic.

## Knowledge extracted (this cost real e2e runs — keep it)

Three render-timing / detection bugs were found **only** by live e2e (mocked
tests passed throughout); each is now fixed with a regression test:

1. **Cohort detection raced the render.** `get_ui_driver` probed instantly after
   navigation and missed the agentic `tune` indicator (it appeared ~1.25 s
   later), defaulting to classic → spurious `FlowAgentUiError` (exit 25). Fix:
   `detect_ui_mode` polls (8 s window). Test: `test_detect_agentic_after_delayed_render`.
2. **The force-toggle raced the render too.** `_force_agent_mode` probed for the
   Agent toggle ~0.8 s after entering the editor, before the composer rendered →
   `agent_toggle_not_found`, silently staying classic. Fix: `wait_for` the toggle
   (8 s) before clicking.
3. **Content-policy detection false-positived on static chrome.** Scanning the
   whole `document.body` for "content policy" matched a benign page's footer/menu
   link → every agentic generation died with a spurious `ContentPolicyError`
   (exit 5) in ~6 s. Fix: scope the scan to `[role=alert]`/`[role=dialog]`/
   `[aria-live=assertive]` regions only (a real block surfaces there; a
   chat-message-only refusal is missed, which is the safe trade — a miss →
   timeout beats a false positive that breaks every generation). Tests:
   `test_await_images_raises_content_policy_on_explicit_text`,
   `test_await_images_ignores_body_chrome_policy_text`.

General lesson: **any agentic-composer probe must wait for the render** — instant
DOM checks race Flow's deferred composer mount.

## Still owed

- A **captured positive content-policy refusal** in the agentic cohort, to widen
  detection safely beyond alert/dialog regions (chat-message refusals are
  currently missed → timeout). Trigger a disallowed prompt under
  `GFLOW_CLI_FORCE_AGENT_UI=1` and capture how the refusal surfaces.
- **Agentic video** — only the image path is validated; video stays on the
  classic→`FlowAgentUiError` path pending a video scraping capture.
