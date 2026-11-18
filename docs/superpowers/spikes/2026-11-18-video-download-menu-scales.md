# Spike: which scales the video download menu lists, per model (#1338)

**Date:** 2026-11-18 · **Host served:** flow.google.com · **Locale:** en-GB · **Cost:** $0
(navigation and DOM reads; no item clicked)

## Question

`gflow video upscale --scale 1080p` exits 23 ("menu item for 1080p was not found") on a
Veo 3.1 Lite clip and an Omni 1.2 Flash clip, while the 270p GIF export of the same clip
works. Did the `1080p` token change, did the item move, or does Flow not offer it?

## Probe

[`scripts/dev/spike_upscale_menu_anchors.py`](../../../scripts/dev/spike_upscale_menu_anchors.py)
(`--video PROJECT:MEDIA`, no `--export-1080p`), which opens the clip's edit route, clicks the
download button and dumps every item in the overlay: tag, attributes, icons, scale tokens
and, added for this spike, the item text. One profile, four clips of four models.

## Observation

Every item is still a bare `button[role=menuitem][mat-menu-item]` with no per-option
attribute or icon, as measured on 2026-10-07. No item has a submenu (`aria-expanded=false`,
no `aria-haspopup`).

| Model | Items listed (en) |
|---|---|
| Veo 3.1 Fast | `270p Animated GIF`, `720p Original size`, `1080p Upscaled`, `4K Upscaled` (+ `Upgrade`) |
| Veo 3.1 Quality | `270p Animated GIF`, `720p Original size` |
| Veo 3.1 Lite | `270p Animated GIF`, `720p Original size` |
| Omni 1.2 Flash | `270p Animated GIF`, `360p Original size`, `720p Upscaled` |

## Conclusion

The `1080p` token and the item structure are unchanged where the item exists (Veo 3.1 Fast).
Flow decides per clip which scales it offers, and lists no 1080p for the Lite, Quality and
Omni 1.2 Flash clips measured. The missing item is therefore not selector drift.

`find_download_menu_item` now raises `UpscaleUnavailableError` (exit 22) when the menu's
other items carry scale tokens but not the requested one, naming the listed scales, and keeps
`UiSelectorDriftError` (exit 23) for a menu in which no scale token can be read.

One profile and one clip per model: whether the offer depends on the model alone, the
account tier, or the clip's age is not established by this spike.
