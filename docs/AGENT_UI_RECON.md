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

## Open follow-ups

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
