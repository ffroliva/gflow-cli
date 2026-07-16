# Agentic Flow UI — Recon Findings

> Reverse-engineered 2026-06-14 via `gflow-agent-browser-spike` (CDP-attached
> real Chrome, `agent-browser@0.27.0`) against the live Flow editor on two
> Chrome profiles. Account-specific values (emails, project UUIDs) are redacted.
> Relates to issues #183 (image `t2i` exit 23) and #174 (library-UI entity drop).

## TL;DR

- Flow runs a **server-side A/B cohort** that swaps the project composer between
  the **classic** media UI and a new **agentic** UI (chat-style "Agent").
- It is **volatile**: the *same* Chrome profile rendered the classic composer at
  one point and the agentic composer later on the *same* project — confirming the
  #174 flapping (denon82 reverting within 12h).
- **gflow's own dedicated automation profiles DO land in the agentic cohort**
  (not only the user's primary profile), so #183/#174 are reproducible through
  gflow — the feature must handle it.
- The cohort is **NOT discoverable from client-side state**: localStorage keys and
  JS-visible cookies are byte-identical across both UIs, and the prime-suspect key
  `FLOW_MAIN_PROMPT_BOX_STATE` holds only generation settings, not a mode flag.
  → **Detection must be runtime DOM-based**; there is no pre-navigation cookie/flag
  to read or flip.
- `navigator.webdriver` is **`false`** under CDP-attached real Chrome.

## How it was captured

`gflow-agent-browser-spike` launches real Chrome with `--remote-debugging-port`
on a chosen profile and drives `agent-browser` over CDP. JS is passed as
**base64-wrapped `eval`** (`eval(atob('…'))`) — multi-line eval args get mangled
through `npx`, so base64 is the reliable path (this also applies to
`capture-agent-ui.ps1`, which must minify/encode its eval expressions). Capturing
the user's **primary** profile required relaunching it with the debug flag (only
applied on a fresh Chrome start, so all windows must be closed first), which
triggered a one-time Google 2-Step Verification.

## The two UIs — DOM signature

| Signal | Classic | Agentic |
|---|---|---|
| `crop_*` inline aspect/mode trigger | **present** (`crop_9_16` etc.) | **absent** |
| Agent pill (`/\bAgent\b/` + `div[role='textbox']`) | no | **yes** |
| Chat ligatures `edit_square`, `thumb_up`, `thumb_down`, `flag` | no | **yes** |
| Agentic ligatures `article_spark`, `apps_spark_2`, `tune` | no | **yes** |
| "What do you want to create?" placeholder | no | **yes** |
| Aspect / model / upscale controls | inline `crop_*` dropdown | moved into **"Agent settings"** (`tune`) |

Agentic ligature inventory (captured): `accessibility_new, add, add_2,
apps_spark_2, arrow_back, arrow_forward, arrow_forward_ios, article_spark, close,
content_copy, dashboard, delete, edit_square, filter_list, flag, help, image,
left_panel_close, menu, more_vert, movie, search, settings_2, thumb_down,
thumb_up, tune, warning` — and **no `crop_*`**.

## Gating mechanism (the cohort key)

Captured on agentic profiles (both the user's primary and a gflow dedicated
profile), the client-visible state is **identical**:

- **localStorage keys**: `FLOW_MAIN_PROMPT_BOX_STATE`, `FLOW_QUICK_SEARCH_MODE`,
  `_grecaptcha`, `glue.CookieNotificationBar`, `nextauth.message`.
- **JS-visible cookies**: `EMAIL`, `_ga`, `_ga_X2GNH8R5NS` (any cohort cookie
  would be `httpOnly`, i.e. invisible to JS — recoverable only from HAR request
  headers, not yet captured).
- **`FLOW_MAIN_PROMPT_BOX_STATE`** value (agentic):
  `{"aspectRatio":"PORTRAIT","selectedImageModelFamily":"narwhal_display","selectedVideoModelFamily":"abra","imageOrVideoMode":"IMAGE","outputsPerPrompt":1,"selectedVideoDuration":4}`
  — generation settings only, **no cohort/mode flag**.

Because (a) the key set is identical across classic and agentic, (b) the one
plausible key holds only settings, and (c) the same profile flaps between states,
the cohort is concluded to be **server-assigned per page load**, not a readable
client signal. Pre-navigation detection (read a cookie before driving) is **not
viable**; runtime DOM classification is the only reliable detector.

## Why the existing handling fails (#183)

`_exit_agent_mode` (`ui_automation_video.py`) assumes the classic composer is
recoverable and keys on `_media_panel_present` (the `crop_*` trigger). In the
agentic cohort there is **no `crop_*`** at all (aspect moved into Agent settings),
so the probe never recovers, the selector cascade exhausts, and the caller raises
`UiSelectorDriftError` (exit 23). The recovery has nothing to recover *to*.

## Recommendation for the feature plan

1. **Detection = runtime DOM classification.** Agentic if *no `crop_*` mode
   trigger* **and** (*Agent pill present* **or** *chat-panel ligatures present*).
   This is what `classify_composer` / a refreshed `_exit_agent_mode` should key
   on. Run it every generation — the cohort flaps, so no caching.
2. **No pre-flight cookie/flag gate** — there is no client-readable cohort signal.
3. **Response (decide in the feature plan, evidence now supports either):**
   - **Detect + fail cleanly** with a typed `FlowAgentUiError` (own exit code) +
     screenshot — low risk, immediately diagnosable. *Recommended first step.*
   - **Drive the agentic surface** — type into the chat composer, set aspect/model
     via the Agent-settings (`tune`) panel, handle the confirm-before-generating
     gate. Larger; intermittently needed because the cohort flaps.

## DOM scraping validation (2026-06-14, live capture)

> Captured via `scripts/capture-media-scrape.ps1` (CDP-attached real Chrome,
> profile `default`, agentic cohort confirmed: `crop=false, agentPill=true,
> chatPanel=true`). One image generation, before/after media-DOM snapshot.
> Artifact: `media-scrape-agentic-20260614-202739.json`.

This resolves the open question blocking `AgenticFlowUiDriver.await_images`: **how do
generated assets surface in the agentic DOM, and is count-delta scraping viable?**

- **Assets render as plain remote `<img>` nodes.** After the generation: `blob=0`,
  `data=0`, `canvas=0`, `background-image=0`. The full-res asset is a normal
  `<img src="https://…">`. Count-delta DOM scraping **is viable** — network
  interception is not (see HAR note below).
- **The src is a same-origin tRPC redirect carrying a stable media id:**
  ```
  https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=<uuid>[&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL]
  ```
  It 302-redirects to the CDN blob; same-origin (`labs.google`) means the session
  cookies authorize the download. The `name=<uuid>` is the **stable backend media id**
  — scraped assets correlate to media identity (usable for the batch ledger / dedup).
- **Dedupe by `name=<uuid>`, NOT by `<img>` node count.** One generated asset surfaces
  as **multiple** `<img>` nodes (canvas full-res + filmstrip thumbnail + chat preview,
  each in bare and `&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL` variants). The single
  capture produced **9 new `<img>` nodes for only 3 distinct assets** (~3× inflation).
  `await_images` must extract the `name` query param and **count distinct UUIDs** — a
  raw `initial_count + expected_count` node check would massively over-count.
- **Page-level HAR captured 0 entries.** Confirms the Web-Worker delegation: the
  `streamChat` + media requests bypass page-level network capture entirely. **DOM
  scraping is the only viable capture path** in the agentic cohort.
- **`flag` is NOT a content-policy signal.** The warn-symbol probe matched the `flag`
  ligature 11× on a *successful* generation — `flag`/`thumb_up`/`thumb_down` are the
  normal per-message chat affordances. Fail-fast detection must key on `warning` /
  `error` / `block` or specific dialog/stream text, **never `flag`**. *(A deliberate
  content-policy-refusal capture is still outstanding — no positive block sample yet.)*

## Settings via prompt, not the `tune` popover (agentic acts MCP-like)

The agentic composer behaves like a conversational agent: generation parameters that the
classic UI exposes as discrete controls — **output count** (1 / 4 images), **video
duration** (4/6/8/10 s), and likely **aspect ratio** and **model** — can be expressed in
**natural language inside the prompt** (e.g. `Generate 4 images of <prompt> in 16:9`), and
the agent resolves the selection itself.

Implications for `AgenticFlowUiDriver`:
- **`configure_settings` drives count as a fallback (2026-07-16, issue #313).**
  Prompt-encoding alone proved unreliable: Agent mode's `tune` panel has a
  STICKY "Image generation default" count that silently overrode the
  natural-language directive when stale. `AgenticFlowUiDriver` now sets that
  control to match the request (best-effort, never raises — falls through to
  prompt-only on any selector miss) in addition to the natural-language
  phrasing. Aspect/model are NOT automated via this panel — count only, to
  keep the newly-added surface area minimal. See
  `flow-agent-settings-panel-sticky-defaults` project memory for the full
  selector write-up.
- **The agent *interprets* the request → verify, don't trust.** It may produce a
  different count or ignore a duration. This is precisely why scrape-and-dedup-by-UUID
  is load-bearing: request N → poll until N distinct new media UUIDs → if the produced
  count differs, raise a typed mismatch rather than silently returning the wrong set.
- **Compose carefully** so settings directives aren't read as subject text (e.g. a
  `Generate {n} image(s){aspect}: {prompt}` template).

## Open follow-ups

- **Content-policy block sample (outstanding):** capture a deliberate-refusal generation
  in the agentic cohort to learn how a block surfaces (chat message vs. dialog vs.
  symbol) — needed to design `await_images` fail-fast. The `flag` symbol is ruled out.

## The Wire & API Endpoint (Resolved via Issue #183 Reporter)

The network request logs from the agentic UI have been resolved. The new UX bypasses the direct media generation endpoints (such as `batchGenerateImages`) and routes all generation requests through Google's conversational flow creation agent.

- **Endpoint**: `POST https://aisandbox-pa.googleapis.com/v1/flowCreationAgent:streamChat?alt=sse`
- **Transport**: Server-Sent Events (SSE) stream, preceded by a CORS OPTIONS preflight.
- **Request Payload Structure**:
  ```json
  {
    "agentSessionId": "<uuid>",
    "agentClientContext": {
      "projectId": "projects/<projectUuid>",
      "clientSessionId": ";<epochMillis>",
      "recaptchaContext": {
        "token": "<recaptcha_token>",
        "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB"
      },
      "turnNumber": 2
    },
    "userMessage": {
      "userPrompt": {
        "parts": [
          {
            "text": "a red apple"
          }
        ]
      }
    }
  }
  ```

### Why page-level CDP/Playwright instrumentation recorded 0 entries
During local capture sessions, page-level request event listeners repeatedly recorded `0` API request entries. This occurred because the agentic UI delegates all `streamChat` network requests to a background **Web/Service Worker** target (`type: worker`). Worker-initiated requests completely bypass page-level network event registration in Playwright/CDP and require worker-level target attachments to capture.

### Impact on issue #174 (referenceEntities)
In the agentic UI, the prompt box is a conversational Slate.js editor. Because generation is driven by the conversation agent stream rather than direct REST inputs, staging library entities (e.g. characters) behaves differently. For the initial implementation phase, driving or injecting into `flowCreationAgent:streamChat` is out of scope. The recommended direction is **Runtime DOM detection + Fail Cleanly** with `FlowAgentUiError` (exit code 23) to prevent automation lockups while the cohort is active.
