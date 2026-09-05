---
name: flow-locale-leak-icon-ligatures
description: Flow renders dialog labels in the Chrome PROFILE language; --lang=en-US Chromium arg cannot override it. Selectors must use Material Symbols icon ligatures.
---

Flow's editor UI renders dialog labels in the Chrome **profile** language preference (set in `chrome://settings/languages`), NOT the Google **account** language and NOT the `--lang=en-US` Chromium launch arg. Any selector that matches localized text breaks on non-EN profiles. The fail-loud `RuntimeError` in `_attach_references` (PR #60) correctly distinguishes these — keep that hint when adding similar guards.

The durable selector pattern is `button:has(i.google-symbols:text('<icon_ligature>'))`. Known Material Symbols ligatures used in Flow's UI (locale-stable):

- `add_2` — project "+" button AND editor Add Media button (disambiguate via `aria-haspopup="dialog"` + `aria-controls^="radix-"` for the editor variant)
- `upload` — "Upload media" item in the media-attach popover (use **`:text-is('upload')` exact match** — `:text` would also match `drive_folder_upload` of the Uploads tab)
- `crop_16_9` / `crop_9_16` — mode-switch trigger (current aspect — note: matches whichever aspect was last selected, so the cascade in `MODE_SWITCH_TRIGGER_SELECTORS` covers both)
- `arrow_forward` — Generate/submit button
- `dashboard`, `image`, `videocam`, `voice_selection`, `accessibility_new`, `drive_folder_upload` — media-dialog tabs (locale-stable but rarely selected; tabs are pre-populated by Flow)

**Why:** incident #56 — `UPLOAD_MEDIA_BUTTON = button:has-text('Upload media')` missed on pt-BR ("Enviar mídia") → click landed nowhere → `expect_file_chooser` waited Playwright's default 30s → 34s silent hang. PR #60 replaced it with the icon ligature.

**How to apply:**
- Any new selector added to `src/gflow_cli/api/transports/ui_automation.py` or `ui_automation_video.py` must prefer icon ligatures over text. If text is unavoidable (no icon present), wrap in a multi-locale cascade like the existing `MODE_SWITCH_TRIGGER_SELECTORS`.
- Iconless buttons (e.g. "Add to Prompt") should be selected structurally — `.filter(has_not=<i.google-symbols>)` inside the open popover — not by localized text.
- Still-open text-based: `FRAME_SLOT_BY_LABEL` (I2V Start/End slots, `src/gflow_cli/api/transports/ui_automation_video.py:138`) — tracked in #63 (spun off from #24, which is now closed as the broader umbrella). Same fix shape as PR #60: investigate DOM for an icon ligature, replace with locale-stable selector + EN-text fallback + fail-loud RuntimeError.
- Still-open text-based (PR #123, avatar generation — under council review, NOT yet merged): `_attach_likeness` uses `button[role='tab']:has-text('Avatar')` and `button:has-text('Add to Prompt')` (`ui_automation_video.py:871,881`). The "Add to Prompt" one directly violates the line-24 rule above (it's the exact example given). Step 1 correctly reuses the locale-stable `ADD_MEDIA_BUTTON` (`add_2` ligature); only steps 2–3 regressed. Fix before merge: structural/aria anchor + EN-text fallback. See [[avatar-likeness-wire-field]].

PR #124 added a purely structural anchor `button:has(span.content)` for Flow's new "Agent" composer pill (see [[flow-agent-composer-mode-panel-removal]]). Caveat learned there: a structural selector is only as safe as its uniqueness — `.first` blind-picks, so pin uniqueness with a captured-DOM fixture rather than asserting it in a comment.

See [[image-video-mode-switch-symmetry]] for related selector symmetry rules, and [[playwright-click-no-downstream-event-signature]] for the debugging heuristic that caught this.
