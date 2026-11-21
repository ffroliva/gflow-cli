# Spike: the agent-only driver's settings, gate and reply on Flow's 2026-11-20 build

**Date:** 2026-11-21 · **Host served:** flow.google.com · **Locale:** en-GB · **Cost:** four
4-second clips and one 3-second 360p clip at Omni 1.2 Flash, and three images (no credits);
the runs that timed out charged nothing; everything else was layout clicks and settings
writes, all restored

Probe: [`scripts/dev/spike_agent_settings_new_build.py`](../../../scripts/dev/spike_agent_settings_new_build.py).
One profile, one project. Issues: #1369, #1387. Follows
[2026-11-20-agent-only-driver-on-the-new-build](2026-11-20-agent-only-driver-on-the-new-build.md).

## Question

The 2026-11-20 spike found the driver stops at its first step on the new build: the `tune`
settings button it opens is gone, replaced by a `settings_2` gear. No account available
here is served the agent-only composer, but an account with the Agent switch, held in agent
mode, renders the same agent surface. On that surface, does the ported driver:

1. open Agent settings, apply a run's defaults and restore them, with the agent panel
   closed (the compact prompt box) and open (the panel header);
2. leave the account's settings as it found them, read back from a fresh browser session;
3. pass the credit gate, read the reply, name and download the clip, both when the panel is
   open at the submit and when it is closed (#1388 closes the panel before the agent-only
   verdict, so a genuinely agent-only page reaches the driver closed);
4. keep each request's own aspect across two back-to-back requests (#1369's carry-over)?

## What the new build renders

Measured live before writing the port:

- **Opener.** One `settings_2` gear is visible in each panel state: the compact prompt box's
  `button.agent-action-button` while the panel is closed, a panel-header button while it is
  open. A second `settings_2` gear elsewhere on the page (the media grid) is not an Agent
  settings opener, so the driver requires exactly one visible match.
- **First level, `flow-unified-settings-view`.** The approval toggle group (position 0
  "Always", position 1 "Never") and one row per section,
  `button.image-defaults-row` / `button.video-defaults-row`, whose
  `.defaults-row-caption` reads e.g. `Omni 1.2 Flash, 16:9, 1 output`.
- **Second level, `flow-generation-defaults-view`.** A model radio group
  (`flow-radio-group.model-radio-group label.radio-option`, the selected one carrying
  `.radio-option-selected`; image labels carry a `🍌 ` prefix), then toggle groups: aspect
  (`crop_*` ligatures) and count (x1-x4). Each toggle is `button[role=radio][aria-checked]`.
  The header's `arrow_back` returns one level.

## Saves are queued, not immediate

Every click in Agent settings sends one batchexecute `Kcr7Ub` POST carrying the whole
section, not the one field:

```text
["projects/<id>",[null,null,[null,["veo_3_1_lite",2,1]]],[["default_generation_settings.video_defaults"]]]
```

(`veo_3_1_lite` / `abra` are model keys; the next two numbers are the aspect code and the
count.) The UI updates at once, but the saves go out one at a time, each sent when the one
before it is answered, about 0.5 s apart:

| t (s) | event |
|---|---|
| 12.90 | three clicks start (aspect, count, model) |
| 12.91 | save 1 sent |
| 13.17 | last click done; the UI shows all three values |
| 13.45 | save 1 answered, save 2 sent |
| 14.01 | save 2 answered, save 3 sent |
| 14.53 | save 3 answered |

A driver that leaves the page as soon as the UI shows the new values loses the saves still
queued. Before the fix, an apply followed by a restore left, in a fresh session, the
apply's model, aspect and approval value; only the count had been restored. The next run on
that account then started from the wrong defaults (see the first two generation attempts
below).

The port now watches `Kcr7Ub` requests during apply and restore and returns only when every
save sent has been answered and none has been sent for 1 s. A failed or HTTP-error save, or
a queue that does not drain within 20 s, is `UiSelectorDriftError`.

## Observation

Driver code on the branch, one account in agent mode. Every run starts with the same
settings checks:

| Step | Result |
|---|---|
| fresh session, rows read | `confirm=Always`, image `Nano Banana 2.1, 16:9, 2 outputs`, video `Omni 1.2 Flash, 16:9, 1 output` (the values the previous run restored) |
| panel closed: video apply (Veo 3.1 - Lite, 9:16, x2, approval Never) | rows read back the applied values; restore passed |
| panel open: image apply (Nano Banana Pro, 3:4, x3, approval Never) | rows read back the applied values; restore passed |
| rows after both | identical to the start |

Then two back-to-back clips, 16:9 then 9:16, 4 s each:

| Run | Panel at the first submit | Clip 1 (16:9) | Clip 2 (9:16) |
|---|---|---|---|
| A | open | gate approved, named by the project listing, downloaded in 56 s; 1280x720 | the same in 60 s; 720x1280 |
| B, before the chat fix | closed | no gate approved and no clip queued within 600 s (`approvals=0`, `pending seen: False`) | the same |
| C, with the chat fix | closed | `panel_opened`, then gate approved 12 s after the submit; downloaded in 64 s; 1280x720 | the same in 55 s; 720x1280 |

Then, in a separate run with the chat fix, two image turns, the first from a closed panel
(`--generate-images --generate-panel closed`; images cost no credits). In that run the
second turn started with the panel the first turn had opened; the script now sets the
requested panel state before each image turn, which this run did not measure:

| Turn | Result |
|---|---|
| 16:9, x2, Nano Banana 2.1 | `panel_opened`, 2 images in 37 s, both 1376x768 |
| 9:16, x1, Nano Banana Pro, right after | 1 image in 36 s, 768x1376 |

In every run the rows after the clips (and after the images) were identical to the start, and
the account went back to the classic composer at the end. After run C no unanswered gate was left in the chat, so
whether the agent raised a gate during run B is not known; what is known is that the driver
saw none and nothing was charged.

Finally `gflow video t2v` on the branch, with the account left in agent mode and the panel
open: `agent_panel_closed`, `agent_mode_exited`, `editor_ready`, and the classic composer
generated a 3 s clip, exit 0.

Two earlier runs (before the save watch) asked for a 4 s clip while the account's video
default had been left at Veo 3.1 - Lite. The agent answered with a question instead of
generating, and the driver waited out its 600 s poll budget. No clip was produced.

## Reading

- The settings port works on the new build in both panel states, and the account's
  settings survive into a new session.
- Gate, reply, naming and download work through the driver on this surface once the panel is
  open, for video and for images. With the panel closed they do not (run B), so the driver now opens it before the
  submit (run C). The closed-panel case recorded on #1387 was real and is covered.
- #1369's carry-over did not reproduce: in runs A and C each clip came out in its own aspect.
- When the account's defaults cannot satisfy a request, the agent asks rather than
  generates, and the driver does not read that as an answer. That is a separate gap from
  #1387 and is left to its own issue.

## Not measured

- An account served the agent-only composer with no Agent switch at all: none is available
  here. The port follows the same page structure, but that account was not driven.
- The 2026-11-19 build, where #1369 was first seen.
