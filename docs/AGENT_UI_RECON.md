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

- **Wire/HAR**: capture the agentic generation endpoint + payload/response (does
  it differ from `batchGenerateImages`? does `referenceEntities` ride — the #174
  question?). Needs a HAR around one live agentic image generation.
- **httpOnly cohort cookie**: confirm/deny from HAR request `Cookie` headers.
- **Model families**: `narwhal_display` (image) and `abra` (video) families
  surfaced in `FLOW_MAIN_PROMPT_BOX_STATE` — cross-check against gflow's model map.
