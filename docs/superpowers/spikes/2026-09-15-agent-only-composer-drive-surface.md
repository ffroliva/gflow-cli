# The agent-only composer (#799) can be driven: ProseMirror in, signed CDN image out

**Date:** 2026-09-15 · **Issue:** #799 · **Cost:** one image (daily quota, 0 credits) + one 4 s video (**7 credits**, authorised by the account owner); navigation and DOM reads $0
**Probe:** [`scripts/dev/spike_agent_only_composer.py`](../../../scripts/dev/spike_agent_only_composer.py) (`--submit-image`, `--submit-video`, `--approve-pending`)
**Captures:** `scripts/dev/_spike_out/spike_agent_only_composer_20260915_182030.json` (image), `…_183228.json` (gate outline), `…_183459.json` (video) + screenshots (gitignored)
**Account:** a Google AI Pro account served `flow.google.com`, locale `en`, **in #799's cohort** —
`gflow image t2i --model nano-pro` on 0.75.0 exits 25 `FlowAgentUiError` on it, and the probe
measured `button.agent-mode-chip` count **0** on the project page.

## Question

KNOWN_ISSUES names "no account in the cohort" as the blocker on #799. With one available:
what does the agent-only composer take as input, where do per-request settings go, and how
does a finished generation surface in the DOM?

## What was observed

### Composer and submit

| Role | Anchor observed | Component chain |
|---|---|---|
| prompt editor | `div.ProseMirror` (contenteditable) | `flow-rich-text-editor` → `flow-base-prompt-box` |
| submit | `button:has(mat-icon:text-is('arrow_forward'))`, class `generate-icon-button` | `flow-generate-icon-button` → `flow-base-prompt-box` |
| in-flight | the submit control is **replaced** by `button.stop-button` (ligature `stop`) | `flow-stop-icon-button` |
| settings | `button.agent-action-button` ligature `tune` | `flow-creative-agent-prompt-box` |
| instructions | `button.agent-action-button` ligature `article_spark` | `flow-creative-agent-prompt-box` |

`page.keyboard.insert_text` into a clicked `div.ProseMirror` landed the text; one click on
the submit button started the turn (the user bubble echoed the directive at t=5 s, and the
submit control became `stop`). That `stop` ⇄ `arrow_forward` swap is a turn-in-flight signal
present only during generation — observed on both sides of the transition.

### Agent settings pane (`tune`)

Opens `flow-settings-view` inside `flow-agent-panel`. Closed without saving via the pane's
own `arrow_back` (`button.header-action` in `flow-agent-panel`); the composer came back
(controls 47 → 21).

| Section | Controls (structure, not labels) |
|---|---|
| Confirm before generating | `mat-radio-group` with two `mat-radio-button`s — **was "Always" on this account** |
| Image generation default | `mat-button-toggle-group` (`flow-toggles`) of `button[role=radio][aria-checked]`: aspects by ligature `crop_16_9`, `crop_landscape`, `crop_square`, `crop_portrait`, `crop_9_16`; a second group of four count toggles (`x1`–`x4`, no ligature); `button.image-model-picker-button` |
| Video generation default | aspect group `crop_16_9`, `crop_9_16`; count group `x1`–`x4`; `button.video-model-picker-button` |
| Save | `button.settings-save-button` |

Defaults on this account when read: image 16:9 · x2 · Nano Banana 2; video 16:9 · x1 · Omni 1.1 Flash.

### The directive overrides the defaults — for image

Submitted `Make me a picture of <prompt> in a 9:16 aspect ratio.` (labs
`AgenticFlowUiDriver._compose_directive` phrasing) against defaults of **16:9 · x2**.
Result: **one** image, **768×1376** (9:16). So both aspect and count in the directive beat
the saved defaults on this run. No confirmation was requested even though the setting read
"Always".

### How the output surfaced (t=70 s)

One new media id, rendered as two `<img>` nodes with the same src:

- `flow-image-tile` → `flow-grid-tile-container` (the project grid)
- `flow-a2ui-image-option` → `flow-a2ui-multiple-choice` → `flow-a2ui-message-renderer` → `flow-chat-bubble` (the reply)

src shape: `https://flow-content.google/image/<uuid>?Expires=…&KeyName=labs-flow-prod-cdn-key&Signature=…`
— a **signed CDN URL whose path segment is the media id**. Not the labs
`media.getMediaUrlRedirect?name=<uuid>` form, so `agentic.py`'s `_MEDIA_UUID_RE` does not
match it. The reply text arrived in the same `flow-chat-bubble` with the per-message action
bar (`thumb_up`/`thumb_down`/`content_copy`/`flag`).

### Video (second run, same day — spent 7 credits, balance 1050 → 1043)

Directive: `Make me a 4 second video of <prompt> in a 9:16 aspect ratio.` against a video
default of **16:9**.

1. **The credit gate fires for video** (the "Always" setting). The turn ends (~20 s, submit
   control back from `stop`) with a reply whose title asks to start "1 video generation,
   costing 7 credits". Structure:
   `flow-permission-message` → `div[role=radiogroup]` → three `div[role=radio].option-row`
   carrying `mat-icon` `check` (Approve), `check` (Always approve), `close` (Reject), in that
   order. **They are not `<button>`s** — a button-only inventory saw nothing, twice.
2. **Answered or abandoned gates stay in the chat with `.read-only`**, and the chat session
   **survived a page reload**: three `flow-permission-message`s were on the page, two read-only.
   A live gate is `div[role=radio].option-row:not(.read-only)`.
3. Clicking the first live `check` row approved it: the submit control went to `stop` for
   ~20 s, and the agent replied that the video was queued.
4. **t = 56 s after approval:** a `flow-video-tile` appeared in the grid, first with only a
   poster `<img src="https://flow-content.google/image/<uuid>…">`, plus a
   `flow-a2ui-video-option` in the chat with the same poster.
5. **t ≈ 86 s:** the tile carried `<video src="https://flow-content.google/video/<uuid>?Expires=…&Signature=…">`,
   **720×1280** — the directive's 9:16 beat the 16:9 default, as with images. Poster and video
   share one `<uuid>`.

So the reply text is NOT a completion signal (it says "queued" before the clip exists), and
the poster precedes the playable `<video>`. Completion = a `flow-video-tile` whose `<video>`
src is `/video/<uuid>` for a uuid not in the pre-submit baseline.

### The wire is not the classic driver's wire (third run, image, 0 credits)

`--submit-image` again, with every `batchexecute` response logged by rpcid and the classic
driver's own decoders (`image_records`, `generation_record`) run on the rpcids it reads.
Capture `…_185325.json`. Image appeared at t≈45 s, turn idle at t≈51 s.

- **`ogiZ0b` never fired.** The classic migrated image path waits for exactly that reply
  (`submit_images_and_observe`), so reusing it would time out on every agent-only run.
- The only new rpcid after submit was **`WuwhI`** (t=31 s) — not in any driver constant.
- `as29s` (a `STATUS_RPCS` member) fired twice and `generation_record` decoded a uuid each
  time — **neither uuid appears anywhere in the page**, while the new image's uuid
  (`flow-content.google/image/<uuid>`) is not in any decoded record. So a status record
  cannot be trusted as *this* generation's completion either.

Reading: completion stays a DOM observation — a `flow-image-tile` / `flow-video-tile` whose
`flow-content.google/{image,video}/<uuid>` is not in the pre-submit baseline — consistent
across all three runs. `WuwhI`'s payload was not captured; decoding it is future work,
not a prerequisite.

### A loaded page's grid tiles carry no media id (fourth and fifth runs, $0)

A driver t2v run (7 credits) **timed out on a clip that existed**: its incident `ui.json`
counted zero `<video>` elements. `scripts/dev/spike_agent_only_video_tile.py` then read the
tiles on a reloaded page:

- `flow-video-tile` media are **opaque `https://flow.google.com/asb/…`** URLs — thumbnail
  `<img class=thumbnail>`, and a `<video aria-label=…>` that mounts **only on hover**. No uuid.
- The hover `<video>`'s `/asb/` src **downloads**: 200, redirected to `*.googlevideo.com`,
  `video/mp4`, 1,242,845 B, `ftyp` present.
- The chat reply's `flow-a2ui-video-option` keeps `flow-content.google/image/<uuid>`, and
  that uuid **is the clip's**: opening the tile loaded `flow-content.google/video/<same uuid>`.
- Opening a tile routes to `/project/<id>/edit/<other-uuid>` — a **different** id from the
  media uuid (not used).

So "a grid tile with a new `flow-content.google/video/<uuid>`" is not a completion signal
on its own.

### Readiness, measured (sixth run, 7 credits)

`scripts/dev/spike_agent_only_video_ready.py`, capture `…_195901.json`, confirm = Never.
Polled every 5 s from submit:

| t | newest `flow-video-tile` | chat `flow-a2ui-video-option` |
|---|---|---|
| 20–27 s | `flow-pending-tile` + `flow-soupy-overlay`, prompt text, no media | present, **no poster** |
| 33 s | `flow-pending-tile.queued`, `div.header-leading.queued` | present, no poster |
| 40–53 s | `flow-pending-tile` with `div.loading-percentage` (13 % → 23 %) | present, no poster |
| 60 s | `flow-pending-tile`, percentage gone | present, no poster |
| **66 s** | pending tile **replaced**: `<video src="flow-content.google/video/<uuid>">`, footer title | poster `flow-content.google/image/<same uuid>` |

- **Readiness = no `flow-pending-tile` left and a new finished `flow-video-tile`.** The
  percentage is not needed.
- The chat option's poster appeared with readiness here, but 30 s **before** it in the first
  drive run — so it identifies the clip, and does not mark readiness.
- The wire: `jwpduf` every ~5 s while pending; an `as29s` at 61 s carried one
  `flow-content.google/video/…` URL (whether it was this uuid was not captured — the uuid
  was learned from the DOM 5 s later). DOM remains the signal.

## What this means for #799

The labs `AgenticFlowUiDriver` design carries over; its anchors do not:

1. **Editor:** `div.ProseMirror`, not Slate's `div[role=textbox][data-slate-editor]`. `insert_text` works.
2. **Submit/settle:** `flow-generate-icon-button` / `flow-stop-icon-button` swap is a better
   completion signal than a bare timer.
3. **Media id:** parse the path segment of `flow-content.google/image/<uuid>`; dedupe by it
   (the grid tile and the chat option are the same asset — the labs 3× inflation, here 2×).
4. **Settings:** the pane uses `aria-checked` radios anchored by `crop_*` ligatures —
   structural, locale-invariant, so aspect is drivable without the prompt too.

## What was NOT measured

- **Which model produced the clip**, and whether a model named in the directive (e.g. Veo
  Lite) is honoured — the default read "Omni 1.1 Flash"; not asked for by name.
- **Duration** of the downloaded clip (4 s requested) — the file was not downloaded.
- **"Never" confirm setting** — whether it removes the gate. Not changed on this account.
- **i2v / r2v** (attaching a start frame or references through `Add ingredients`).
- **Count > 1.** Only a single-image directive was sent. Whether `4 pictures` yields 4 ids — and
  whether the x2 default ever wins — is unmeasured.
- **Content-policy refusal.** No blocked prompt was sent.
- **Repeatability.** N=1. Cohort stability for this account over time is unknown.
- **The labs side.** This account is served flow.google.com; no labs comparison.
