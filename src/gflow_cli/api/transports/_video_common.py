"""Video-generation methods for UiAutomationTransport.

Mixed into `UiAutomationTransport` via `VideoGenerationMixin` — kept in its own
module because `ui_automation.py` is already over the 800-line cap.

Video generation mirrors `generate_images`: the transport drives the Flow
editor UI and Flow's own JavaScript builds the request, sends it, and mints
reCAPTCHA on submit — the transport never POSTs a generate body. The status
endpoint returns HTTP 401 to `page.request.post`, so polling captures Flow's
own `batchCheckAsyncVideoGenerationStatus` responses instead of issuing the
POST (spec §5.5).
"""

# The private helpers here are used by the `_video_*` parts, not in this module (#1249).
# pyright: reportUnusedFunction=false

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.transports.drivers.factory import AGENTIC_INDICATOR_SELECTORS
from gflow_cli.api.video import (
    Aspect,
    VideoModel,
)

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

# Type aliases for JSON-shaped ``cast(...)`` targets. Extracted so the quoted
# cast strings are not duplicated (SonarCloud S1192); module-level aliases keep
# ruff's TC006 happy since call sites pass a bare name, not a subscript.
_JsonObj = dict[str, Any]
_JsonObjList = list[dict[str, Any]]
# The three mode-specific generate routes (spec §2.1). The listener filters on
# these substrings only — video generate URLs carry no /projects/{id}/ path
# segment, so a project-id URL filter is impossible (deviation from §5.4).
VIDEO_GENERATE_ROUTES = (
    "batchAsyncGenerateVideoText",
    "batchAsyncGenerateVideoStartImage",
    "batchAsyncGenerateVideoStartAndEndImage",
    "batchAsyncGenerateVideoReferenceImages",
)
# The pure text-to-video route. An i2v request that lands here had its frame
# refs silently dropped (issue #125) — used by the Layer-2 post-submit backstop.
_T2V_GENERATE_ROUTE = "batchAsyncGenerateVideoText"
# The first+last interpolation route. A request carrying an end frame that lands
# anywhere else had that frame dropped at submit (#626) — same backstop.
_START_AND_END_GENERATE_ROUTE = "batchAsyncGenerateVideoStartAndEndImage"

# Substring, not a path glob: the real endpoints carry a namespaced final segment
# (`.../flowMedia:batchGenerateImages`), which `**/batchGenerateImages` can never
# match. The VIDEO endpoint is namespaced too (`.../video:batchAsyncGenerateVideoText`),
# so `**/batchAsyncGenerateVideo*` was equally dead — #615 names only the image one.
# See `_intercept_reference_entities` for why it is registered on the context.
_GENERATION_ROUTE_RE = re.compile(r"batchGenerateImages|batchAsyncGenerateVideo")
# Status-poll route — Flow's SPA polls this itself while a generation runs.
VIDEO_STATUS_ROUTE = "batchCheckAsyncVideoGenerationStatus"

# Mode switching is a 2-step dropdown (spec §6, §10.5). The trigger is the
# unified generation-settings button — the only button[aria-haspopup='menu']
# carrying an aspect-ratio crop_* icon; clicking it opens a role='menu' with
# the Imagem/Vídeo role='tablist' (the tabs are not in the DOM until it opens).
# Default per-selector wait in `_probe_selector_cascade`, read at call time (not
# bound as a default argument) so the hermetic e2e can shorten it.
_PROBE_TIMEOUT_MS = 4000

MODE_SWITCH_TRIGGER_SELECTORS = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_16_9'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_9_16'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_square'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_portrait'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_landscape'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_original'))",
)
# SOT (flow-editor-map.json): the VIDEO mode tab id ends with '-trigger-VIDEO'
# (radix prefix is dynamic — match the suffix). aria-controls ends with
# '-content-VIDEO'. Both are EXACT (ends-with), so they do NOT match the
# sub-mode tabs '-trigger-VIDEO_FRAMES' / '-trigger-VIDEO_REFERENCES'. Icon +
# id-suffix are locale-independent; the localized-text fallbacks come last.
VIDEO_TAB_IN_MENU_SELECTORS = (
    "[role='tab'][id$='-trigger-VIDEO']",
    "[role='tab'][aria-controls$='-content-VIDEO']",
    "[role='menu'] [role='tab']:has(i:text('play_circle'))",
    "[role='menu'] [role='tab']:has-text('Vídeo')",
    "[role='menu'] [role='tab']:has-text('Video')",
)

# Composer "Agent" mode toggle. Flow's newer editor puts a pill toggle next to
# the prompt box: a ``<button>`` whose only label is a ``<span class="content">``.
# When Agent mode is on, the whole media-generation panel is REMOVED from the
# DOM — the aspect/settings button (the locale-stable ``crop_*`` icon trigger
# keyed on by MODE_SWITCH_TRIGGER_SELECTORS / GEN_SETTINGS_BUTTON_SELECTORS), the
# Image/Video tablist, and the count/model controls all disappear, so
# ``_switch_to_image_mode`` / ``_switch_to_video_mode`` raise "mode-switch
# dropdown trigger not found". Clicking the pill returns to media mode.
#
# This selector is deliberately STRUCTURAL — no localized text and no ARIA:
#  * No localized text: the pill's "Agent" label is translated in some Flow
#    locales, so matching it by visible text would regress non-English users
#    (issue #24: locale-agnostic selectors — a recurring source of PR pushback in
#    this module). The only ``:text(...)`` here is ``arrow_forward``, a Material
#    Symbols icon ligature, which is locale-invariant — the same technique the
#    module already uses for ``crop_*`` / SUBMIT_BUTTON_SELECTORS anchors.
#  * No ARIA: aria-* anchors have also been pushed back on in past reviews, and
#    one is not needed here — Agent mode is detected from the *absence* of the
#    ``crop_*`` media trigger, so the toggle only has to be located, not have its
#    state read.
#
# SCOPED to the generation composer (PR #124 review must-fix): the pill is
# matched only inside the element that holds BOTH the Slate prompt box AND the
# ``arrow_forward`` submit button. Page-wide there is exactly one
# ``button:has(span.content)`` today (live-verified count == 1), but ``.first``
# on the bare global selector would silently grab the wrong element if a future
# Flow build added another ``span.content`` button (header/sidebar) ordered
# before the pill. Scoping to the prompt+submit composer keeps the match correct
# regardless of unrelated additions elsewhere. The composer's own ancestor chain
# carries no stable id/role/data-* attribute (all styled-component hashes), so
# the prompt box and submit icon ARE the stable structural anchors. Uniqueness is
# pinned by a structural unit test (decoy outside the composer) and asserted live
# (count == 1) in the e2e.
COMPOSER_AGENT_TOGGLE_SELECTOR = (
    "div:has(div[role='textbox'][data-slate-editor='true'])"
    ":has(button:has(i:text('arrow_forward'))) button:has(span.content)"
)

# Agent CHAT side-panel close (X). Flow's even-newer editor sometimes promotes
# Agent mode from the in-composer pill (above) to a full chat panel docked on the
# right ("Untitled session", "What would you like to do?") — it appears on some
# project opens and not others. While that panel is up, the in-composer pill is
# NOT in the DOM at all, so the pill selector matches nothing; the panel must be
# dismissed first (its X), after which the pill reappears (usually still active)
# and the normal pill path takes over. The panel header carries a New-session
# button (``edit_square`` icon) next to its close (``close`` icon); we anchor on
# that pairing so we hit the panel's X and not some other ``close`` icon on the
# page. ``:text-is`` is EXACT — ``:text('close')`` would also match the sidebar's
# ``left_panel_close`` ligature. Locale-invariant (Material Symbols ligatures,
# not UI text) and aria-free, same discipline as the pill selector above.
AGENT_CHAT_PANEL_CLOSE_SELECTOR = (
    "div:has(button:has(i.google-symbols:text-is('edit_square'))) "
    "button:has(i.google-symbols:text-is('close'))"
)

# Selectors unique to the new Agentic UI cohort. If crop settings are absent
# and any of these are present, we are in the forced Agentic UI cohort. The
# ligature probes are canonical in ``drivers/factory.py`` (detection source of
# truth); this tuple extends them with the composer pill + chat-panel close,
# which only the exit-loop cares about.
AGENTIC_UI_INDICATORS = (
    *AGENTIC_INDICATOR_SELECTORS,
    COMPOSER_AGENT_TOGGLE_SELECTOR,
    AGENT_CHAT_PANEL_CLOSE_SELECTOR,
)

# Locale-invariant markers of Flow's #174 full-page media-library cohort — the
# left-nav library ("All Media / Characters / Scenes / Tools / Trash") a project
# can open into INSTEAD of the generation composer. It has no ``crop_*`` aspect
# trigger, so ``_switch_to_image_mode``/``_switch_to_video_mode`` cannot drive it.
# Keyed on the collapse-sidebar control + the "All Media" grid nav (ligatures, not
# UI text, to stay locale-independent). Confirmed live on ffroliva 2026-07-22.
LIBRARY_UI_INDICATORS = (
    "i.google-symbols:text-is('left_panel_close')",
    "i.google-symbols:text-is('dashboard')",
)

# Union of markers that mean "the classic media composer is not reachable" —
# either the forced agentic chat UI or the full-page media library. Consulted
# ONLY after the ``crop_*`` trigger is confirmed absent AND agent-mode recovery
# has already failed, so any match here is a decisive "stuck in a non-classic
# cohort" signal (as opposed to a recoverable Agent pill/panel earlier in the flow).
NON_CLASSIC_COHORT_INDICATORS = (*AGENTIC_UI_INDICATORS, *LIBRARY_UI_INDICATORS)

# Output-count + duration tabs are selected by aria-label text in
# `_set_output_count` / `_select_video_duration` — NOT by id-suffix: the count
# tab '-trigger-4' and the duration tab '-trigger-4' (4s) share a suffix, so an
# id match is ambiguous (this was the prior '[id*=-trigger-1]' bug that also
# caught '-trigger-10'). Labels 'x1'/'x2'.. (legacy '1x' — issue #404 rename)
# and '4s'/'6s'.. are unambiguous and locale-independent.

# Aspect tabs inside the open menu. SOT (flow-editor-map.json): video aspect
# tab ids end with '-trigger-PORTRAIT' (9:16, icon crop_9_16) and
# '-trigger-LANDSCAPE' (16:9, icon crop_16_9). The prior selector matched
# aria-controls*='9_16', but the real aria-controls is '-content-PORTRAIT' /
# '-content-LANDSCAPE' (NO '9_16'/'16_9' substring) — it never matched and
# always fell through to text. id-suffix + icon are locale-independent and
# exact (ends-with '-trigger-PORTRAIT' does not match the image-only
# '-trigger-PORTRAIT_3_4'). A miss is non-fatal (Flow's default applies).
VIDEO_ASPECT_TAB_SELECTORS: dict[Aspect, tuple[str, ...]] = {
    Aspect.PORTRAIT: (
        "[role='tab'][id$='-trigger-PORTRAIT']",
        "[role='menu'] [role='tab']:has(i.google-symbols:text-is('crop_9_16'))",
        "[role='tab']:has-text('9:16')",
    ),
    Aspect.LANDSCAPE: (
        "[role='tab'][id$='-trigger-LANDSCAPE']",
        "[role='menu'] [role='tab']:has(i.google-symbols:text-is('crop_16_9'))",
        "[role='tab']:has-text('16:9')",
    ),
}

# Model picker (SOT flow-editor-map.json). The trigger is the only
# button[aria-haspopup='menu'] carrying an 'arrow_drop_down' icon; its label is
# the currently-selected model. Options are role='menuitem' matched by product
# name (NOT localized). 'Veo 3.1 - Lite' is a prefix of 'Veo 3.1 - Lite [Lower
# Priority]', so it needs an EXACT text match (the menuitem text is the model
# name prefixed by the 'volume_up' icon ligature); the others match by has-text.
MODEL_PICKER_TRIGGER = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('arrow_drop_down'))"
)
VIDEO_MODEL_OPTION_SELECTORS: dict[VideoModel, str] = {
    # Two ANDed `has-text` substrings, NOT one contiguous 'Omni Flash': Flow
    # renamed the entry to 'Omni 1.1 Flash', and a version number injected in the
    # MIDDLE of the label breaks a contiguous substring match. The old selector
    # matched zero entries, so `_select_video_model` refused every explicit
    # `--model omni-flash` run with VideoModelSelectionError (exit 18). Both
    # clauses must hold on the SAME menuitem, which matches 'Omni Flash',
    # 'Omni 1.1 Flash', and whatever version Flow bumps to next, while staying
    # unique against the four 'Veo 3.1 - *' entries. If Flow ever offers two
    # concurrent Omni tiers, this goes AMBIGUOUS and the transport refuses
    # before spending credits — which is the correct failure, not a silent
    # `.first` guess. The trailing `:not` mirrors the VEO_3_1_LITE sibling
    # below: Flow already ships a '[Lower Priority]' variant of a tier, so an
    # 'Omni 1.1 Flash [Lower Priority]' entry would make the two ANDed clauses
    # match two menuitems and put omni-flash back at exit 18 — the same outage,
    # from the same class of drift.
    VideoModel.OMNI_FLASH: (
        "[role='menuitem']:has-text('Omni'):has-text('Flash')"
        ":not(:has-text('1.2')):not(:has-text('[Lower Priority]'))"
    ),
    VideoModel.OMNI_1_2_FLASH: (
        "[role='menuitem']:has-text('Omni 1.2 Flash'):not(:has-text('[Lower Priority]'))"
    ),
    # The Veo entries deliberately KEEP the contiguous '3.1'. Do NOT "fix" them
    # the way OMNI_FLASH was fixed above: the discriminator is whether gflow's
    # own identifier pins the version. `omni_flash` carries none, so '1.1' in
    # Flow's label is noise the anchor must span. `VEO_3_1_FAST` makes 3.1 part
    # of the model's identity, so if Flow swaps this tier for a 'Veo 3.2' a loud
    # MISS is the CORRECT outcome — widening to 'Veo' + 'Fast' would silently
    # bind `--model veo-fast` to a different tier at a different credit price.
    VideoModel.VEO_3_1_FAST: "[role='menuitem']:has-text('Veo 3.1 - Fast')",
    VideoModel.VEO_3_1_QUALITY: "[role='menuitem']:has-text('Veo 3.1 - Quality')",
    # Substring `:has-text` (NOT `:text-is`) so it matches regardless of the
    # leading Material Symbols icon ligature in the menu item's accessible text
    # (e.g. "volume_upVeo 3.1 - Lite"). The exact-match `:text-is(...)` form that
    # hardcoded the icon prefix was the issue #125 model-select reliability bug:
    # it silently missed -> Flow kept omni-flash -> i2v routed to T2V. `:not`
    # excludes the 'Veo 3.1 - Lite [Lower Priority]' sibling (has-text is a
    # substring/prefix match).
    VideoModel.VEO_3_1_LITE: (
        "[role='menuitem']:has-text('Veo 3.1 - Lite'):not(:has-text('[Lower Priority]'))"
    ),
    VideoModel.VEO_3_1_LITE_LOWER_PRIORITY: "[role='menuitem']:has-text('[Lower Priority]')",
}

# Image-attach for I2V (SOT flow-editor-map.json + live verification).
# CRITICAL: set_input_files() on the generic hidden input only adds the image to
# the LIBRARY — it does NOT associate it with the start/end frame slot, so Flow
# then fires the plain `batchAsyncGenerateVideoText` route (image ignored). The
# frame slot MUST be filled through its own dialog: click the slot
# (div[aria-haspopup='dialog'] labelled Start/End) -> 'Upload media' (opens a
# file chooser) -> wait uploadImage -> 'Add to Prompt' to commit it into the
# slot. Only then does the DOM Generate click fire StartImage/StartAndEndImage.
UPLOAD_IMAGE_ROUTE = "uploadImage"
# Frame slots are `<div type="button" aria-haspopup="dialog">` — Flow uses a
# div-with-button-semantics custom component for the Start/End slots in I2V mode.
# Their label text is localized (EN 'Start'/'End', PT-BR 'Inicial'/'Final',
# DE 'Anfang'/'Ende', JA '開始'/'終了', etc.).
#
# Tier 1 (PRIMARY, locale-free): `FRAME_SLOTS_STRUCT` matches the exact pair via
# the `type='button'` + `aria-haspopup='dialog'` composite — a unique pattern in
# Flow's editor (regular elements don't carry a `type` attr on divs). Order is
# DOM order: `.nth(0)` = Start, `.nth(1)` = End.
#
# It is the only tier (#1273): the English-label fallback that used to follow it
# could only match an English profile, and the visibility wait before it already
# fails when the `type` attribute is gone. A miss raises a loud `RuntimeError`.
#
# Earlier PR #70 used a structural anchor
#   `div:has(> button:has(i.google-symbols:text-is('swap_horiz')))`
# as the parent of the dialog slots. That anchor was broken on real Flow DOMs because
# (1) the slots are `<div type="button">` not children of any `div > button`
# wrapper, and (2) the `swap_horiz` icon uses class `material-icons` (NOT
# `google-symbols`). PR #70's structural tier therefore matched ZERO elements
# on every profile, silently falling through to the text-tier which only
# matched on EN. This was discovered 2026-05-26 via DOM probe on pt-BR — see
# scripts/dev/capture_i2v_frame_slots_dom.py.
FRAME_SLOTS_STRUCT = "div[type='button'][aria-haspopup='dialog']"
# Media-dialog action buttons. These MUST be locale-agnostic: Flow renders the
# dialog in the CHROME PROFILE's language (NOT the Google account language, and
# the `--lang=en-US` launch arg does NOT override an existing profile's stored
# language), so a text match like has-text('Upload media') silently misses on a
# pt-BR / th / ... profile -> the file chooser never opens -> 34s hang (#56).
# Anchor on the Material Symbols icon ligature (locale-free) instead:
#   - 'Upload media' carries the `upload` icon. Use :text-is('upload') (EXACT) so
#     it doesn't also grab the 'Uploads' tab (icon `drive_folder_upload`).
#   - 'Add to Prompt' has NO icon, so it can't be matched by ligature; it's the
#     only iconless button in the open dialog -> selected structurally at the
#     call site via .filter(has_not=<icon>).
# Both are scoped to the open Radix popover ([role='dialog'][data-state='open']).
# If Google ever restructures this dialog so even these anchors break,
# `_upload_via_open_dialog` raises a clear error + screenshot instead of hanging.
UPLOAD_MEDIA_BUTTON = (
    "[role='dialog'][data-state='open'] button:has(i.google-symbols:text-is('upload'))"
)
# 'Add to Prompt' has no stable string anchor; it is resolved via the tiered,
# locale-safe PICKER_INCLUDE_BUTTON / _resolve_include_action pattern (a
# hardcoded English string here previously hung non-English accounts — #170/#56).
ADD_TO_PROMPT_DIALOG = "[role='dialog'][data-state='open']"
# Any open dialog (state-agnostic), used to confirm a picker closed after an
# include action.
DIALOG_ANY = "[role='dialog']"
# How long to wait for the resource picker to close after an include. Matched to
# the I2V remote-frame budget (was an unexplained 8s for R2V, which spuriously
# aborted slow-but-successful attaches on large/virtualised grids — #245 review).
REMOTE_PICKER_CLOSE_TIMEOUT_S = 120.0
# Wall-clock ceiling for the prompt-submission stage: overlay dismissal +
# prompt-box lookup + typing + submit click. Every probe inside is already
# individually bounded (overlay detect <=6x1.5s, prompt box 4x10s, submit
# 3x2s), so a healthy stage finishes in seconds and even a fully-drifted one
# raises inside ~50s. A longer wait means a Playwright call stopped honouring
# its own per-probe deadline — observed as a SILENT hang right after
# `frame_attached`, browser alive, no error, no further log line. Failing
# fast and naming the stage is what turns that into a diagnosable bug.
SUBMIT_STAGE_TIMEOUT_S = 90.0
# Screenshot capture after a wedged stage gets its own short deadline: the
# page is by definition not answering, so an unbounded best-effort capture
# would re-hang the very code path meant to end the hang.
STAGE_TIMEOUT_SHOT_S = 15.0
# Playwright version gflow is tested against — kept in step with the
# `playwright` constraint in pyproject.toml (asserted by
# tests/test_playwright_pin.py). Surfaced in the stall error because an
# out-of-range playwright is the known cause of that stall.
PINNED_PLAYWRIGHT = "1.61.0"
SUPPORTED_PLAYWRIGHT_RANGE = ">=1.61.0,<1.62.0"


def _playwright_version() -> str:
    """Installed playwright version, or ``'unknown'`` — never raises.

    Read at failure time (not import time) so a broken/absent distribution
    cannot turn a diagnostic into a second exception.
    """
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return version("playwright")
    except Exception:
        return "unknown"


# R2V references mode has NO Start/End slots — references are added via the
# only button[aria-haspopup='dialog'] in the editor: a 'Create' button carrying
# the 'add_2' icon (its visible text 'Create' / 'Add Media' is unreliable — a
# has-text('Add Media') match grabbed a nav-header button instead). The icon +
# dialog-popup combo is locale-free and unambiguous in the editor. Repeat up to
# MAX_REFERENCE_IMAGES — the button persists to add the next reference.
ADD_MEDIA_BUTTON = "button[aria-haspopup='dialog']:has(i.google-symbols:text-is('add_2'))"
# Resource picker (spike-verified 2026-06-06, locale-agnostic via ligatures/id).
PICKER_SEARCH_INPUT = "#add-menu-input"
PICKER_PERSONAGENS_TAB = (
    "[role='tab']:has(i.google-symbols:text-is('accessibility_new')),"
    " button:has(i.google-symbols:text-is('accessibility_new'))"
)
PICKER_VOZES_TAB = (
    "[role='tab']:has(i.google-symbols:text-is('voice_selection')),"
    " button:has(i.google-symbols:text-is('voice_selection'))"
)
# Picker include button (Vozes flow). Issue #170: the original single pt-BR
# has-text selector broke every non-Portuguese account — Flow renders in the
# ACCOUNT language and `?hl=en` cannot override it. Recon (denon82 pt-BR,
# 2026-06-11): the button carries NO ligature icon; it is the LONE iconless
# button inside the open picker dialog (every other dialog button has a
# ligature: tabs, `play_arrow` preview, `arrow_drop_down` sort) — same
# situation as ADD_TO_PROMPT_DIALOG. Structural only (#1273): the caption tier
# is gone, and _resolve_include_action refuses a tier that matches more than
# one element, so a second iconless button fails loudly instead of clicking.
PICKER_INCLUDE_BUTTON: tuple[str, ...] = (
    "[role='dialog'][data-state='open'] button:not(:has(i.google-symbols))",
)
_INCLUDE_BUTTON_TIER_NAMES = ("structural",)
# Context-menu include action shown on RIGHT-CLICK of a Personagens entity
# tile. This is what stages a `referenceEntity` (the inline Tudo button instead
# stages a `referenceImage` of the thumbnail). Verified 2026-06-06.
# Issue #170 + recon 2026-06-11 (pt-BR denon82, matching the ru report): the
# menu is `div[role='menu'][data-state='open']` with menuitem ligatures
# add / content_cut / content_copy / delete — `add` is unique within the menu,
# so the icon tier is locale-free and the only one (#1273).
PICKER_CONTEXT_INCLUDE: tuple[str, ...] = (
    "[role='menu'][data-state='open'] [role='menuitem']:has(i.google-symbols:text-is('add'))",
)
_CONTEXT_INCLUDE_TIER_NAMES = ("icon",)
# Issue #174: Flow is A/B-rolling a full-page media-library UI where the
# include action lands (a chip appears) but the staged entity never reaches
# the submit. Both entity-attach backstops (video + image) point affected
# users at the tracking issue instead of the generic file-a-bug hint.
ENTITY_ATTACH_DRIFT_HINT = (
    "If clicking 'Add Media' on this account opens a full-page media library "
    "instead of a picker dialog, this is Flow's new library UI rollout, where "
    "the include action no longer stages entities — follow "
    "https://github.com/ffroliva/gflow-cli/issues/174 for status and progress. "
    "Otherwise, file a bug at https://github.com/ffroliva/gflow-cli/issues "
    "(do NOT include captured tokens or signed URLs)."
)
# The picker grid is virtualised (react-virtuoso): off-screen tiles are not in
# the DOM. When the target tile is not initially rendered, scroll the grid in
# steps until it appears. #287 (live repro): a fixed scroll budget capped the
# reachable depth at ATTEMPTS * DELTA px, so an asset deep in a crowded
# project (100+ media) was unreachable and the picker gave up on an asset
# that WAS in the project. The scroll loop is therefore bounded by evidence
# of progress — keep scrolling while the set of rendered tile identifiers
# still CHANGES between scrolls, stop after STALL_LIMIT consecutive scrolls
# with no new tiles (end of grid) — with MAX_ATTEMPTS as a hard safety
# ceiling against a pathological grid that never stops changing. The fixed
# ATTEMPTS budget remains as the fallback bound when the DOM probe yields no
# progress evidence at all.
PICKER_GRID_SCROLL_ATTEMPTS = 12
PICKER_GRID_SCROLL_DELTA_PX = 500
PICKER_GRID_SCROLL_MAX_ATTEMPTS = 200
PICKER_GRID_SCROLL_STALL_LIMIT = 3
# One JS pass over the open picker: the identifiers of every currently
# rendered tile — media thumbnails keyed by their src UUID, entity tiles by
# data-tile-id. Used as the progress fingerprint for the scroll loop.
_PICKER_GRID_TILE_IDS_JS = (
    "() => Array.from("
    "document.querySelectorAll(\"[role='option'] img, [data-tile-id]\")"
    ").map((el) => el.getAttribute('data-tile-id') || el.getAttribute('src') || '')"
)
# #287 round 6 audit: react-virtuoso scrolls its OWN container (usually a
# [data-virtuoso-scroller] node), not the dialog — a mouse wheel over the
# wrong node is a silent no-op that looks like "end of grid". Scroll the
# actual scrollable element via JS and return evidence (which node moved,
# scrollTop before/after) so a no-op scroll is visible in telemetry. The
# hover+wheel fallback remains for when this probe fails.
_PICKER_GRID_SCROLL_JS = (
    "(delta) => {"
    " const dialogs = document.querySelectorAll(\"[role='dialog']\");"
    " const root = dialogs.length ? dialogs[dialogs.length - 1] : document.body;"
    " const nodes = [root].concat(Array.from(root.querySelectorAll('*')));"
    " const scroller = nodes.find((el) => el.hasAttribute('data-virtuoso-scroller'))"
    "  || nodes.find((el) => el.scrollHeight > el.clientHeight + 1)"
    "  || root;"
    " const before = scroller.scrollTop;"
    " scroller.scrollTop = before + delta;"
    " return {"
    "  tag: scroller.tagName.toLowerCase(),"
    "  cls: (scroller.getAttribute('class') || '').slice(0, 120),"
    "  before: before,"
    "  after: scroller.scrollTop,"
    " };"
    "}"
)
# #287 CONFIRMED (live round 2): the media picker's library view has its OWN
# active project — `--project` only navigates the EDITOR — so the picker can
# open on a different project (observed live: an old test project, with the
# target asset unreachable at any scroll depth). The trigger is a Radix
# `ProjectDropdownSubTrigger` (aria-haspopup='menu', submenu semantics,
# portal-rendered options); a sibling `SortDropdownSubTrigger` also matches a
# generic menu-haspopup probe, so the stable class comes FIRST and the generic
# menu tier explicitly excludes the sort trigger. Probed IN ORDER inside the
# open dialog, first match wins, no-op when none match (older cohort).
PICKER_PROJECT_SELECTOR_TRIGGERS = (
    "[role='dialog'] [class*='ProjectDropdownSubTrigger']",
    "[role='dialog'] [role='combobox']",
    "[role='dialog'] button[aria-haspopup='listbox']",
    "[role='dialog'] button[aria-haspopup='menu']:not([class*='SortDropdownSubTrigger'])",
)
# Radix renders the opened (sub)menu in a PORTAL outside the dialog, stamped
# data-state='open' — the open-verification anchor for the switch sequence.
PICKER_PROJECT_MENU_OPEN = "[role='menu'][data-state='open']"
# #287 round 3: the open-state flips BEFORE the project list populates —
# matching (or dumping) too early sees an empty portal. After the menu
# reports open, poll for element children before matching: up to POLLS steps
# of POLL_MS each (~3s), stopping as soon as the portal has any elements.
PICKER_PROJECT_MENU_POLLS = 10
PICKER_PROJECT_MENU_POLL_MS = 300
# #287 round 5: the project menu is the full recency-ordered project list (80
# items observed) — the target's entry can sit below the visible fold or
# outside a virtualised window. When the in-view match misses, the open
# portal is scrolled with the progress-bounded pattern (re-match after each
# scroll, stall-terminate on no new items, hard ceiling).
PICKER_PROJECT_MENU_SCROLL_MAX = 30
PICKER_PROJECT_MENU_SCROLL_DELTA_PX = 400
PICKER_PROJECT_MENU_SCROLL_SETTLE_MS = 250
# Element count inside the open portal menu(s) — the population poll probe.
_PICKER_PROJECT_MENU_CHILD_COUNT_JS = (
    "() => Array.from(document.querySelectorAll(\"[role='menu'][data-state='open']\"))"
    ".reduce((n, m) => n + m.querySelectorAll('*').length, 0)"
)
# Scroll the open portal menu one step (finds the first scrollable node) and
# return the rendered item texts — the progress fingerprint for the loop.
_PICKER_PROJECT_MENU_SCROLL_JS = (
    "(delta) => {"
    " const menus = Array.from(document.querySelectorAll(\"[role='menu'][data-state='open']\"));"
    " if (!menus.length) { return null; }"
    " const root = menus[menus.length - 1];"
    " const nodes = [root].concat(Array.from(root.querySelectorAll('*')));"
    " const scrollable = nodes.find((el) => el.scrollHeight > el.clientHeight + 1) || root;"
    " scrollable.scrollTop = scrollable.scrollTop + delta;"
    " const items = Array.from(root.querySelectorAll("
    "\"a, button, li, [role='menuitem'], [role='menuitemradio'], [role='option']\"));"
    " return items.map((el) => (el.textContent || '').trim()).filter(Boolean);"
    "}"
)
# Titles that are ONLY Flow branding — never usable as a project-name
# candidate ('flow' as a contains-match would hit e.g. 'gflow-cli i2i').
_FLOW_BRANDING_TITLES = frozenset(
    {"flow", "google flow", "flow by google", "google labs flow", "labs.google", "google labs"}
)
# Raw open-portal dump for a switch miss (#287 round 4): the round-2/3 dumps
# were role-filtered and came back empty — raw bounded innerHTML plus a child
# count and tag histogram can't be blinded by role assumptions.
_PICKER_PROJECT_MENU_DUMP_JS = (
    "() => {"
    " const menus = Array.from(document.querySelectorAll(\"[role='menu'][data-state='open']\"));"
    " if (!menus.length) { return null; }"
    " const root = menus[menus.length - 1];"
    " const els = Array.from(root.querySelectorAll('*'));"
    " const hist = {};"
    " els.forEach((el) => {"
    "  const t = el.tagName.toLowerCase();"
    "  hist[t] = (hist[t] || 0) + 1;"
    " });"
    " return { child_elements: els.length, tag_histogram: hist,"
    "  inner_html: root.innerHTML.slice(0, 4000) };"
    "}"
)
# Active-project probe on the CLOSED trigger: the trigger renders the active
# project's NAME (live round 2: 'gflow-cli t2i'), so match by id-in-markup OR
# by the resolved target name (normalized text, never a locale-dependent
# label).
# Round 6: the trigger's textContent carries the active project's name plus
# icon-ligature noise, so exact equality missed a CORRECT project (round-5
# waste: ~30 menu probes hunting for the project we were already in).
# Contains is safe here: the trigger only ever shows the ACTIVE project.
_PICKER_PROJECT_TRIGGER_ACTIVE_JS = (
    "(el, args) => {"
    " if (el.outerHTML.includes(args.projectId)) { return true; }"
    " if (!args.projectName) { return false; }"
    " const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();"
    " return norm(el.textContent) === norm(args.projectName)"
    "  || norm(el.textContent).includes(norm(args.projectName));"
    "}"
)
# With the project (sub)menu OPEN: click the candidate matching the target
# project. #287 round 3: the portal contained ZERO classic menu-item ARIA
# roles, so the sweep covers generic clickables. Tiers: an anchor whose href
# carries the project id (jackpot — no name needed), then the id anywhere in
# markup, then the resolved project NAME (normalized text; locale-free).
# Container-safe: among matches the INNERMOST element is clicked, never a
# wrapping list container. Returns the candidate count for telemetry.
_PICKER_PROJECT_OPTION_MATCH_JS = (
    "(args) => {"
    " const menus = Array.from(document.querySelectorAll(\"[role='menu'][data-state='open']\"));"
    " const scope = menus.length ? menus : [document];"
    " const candidates = scope.flatMap((m) => Array.from(m.querySelectorAll("
    "\"a, button, li, div[role], [role='menuitem'], [role='menuitemradio'],"
    " [role='menuitemcheckbox'], [role='option']\""
    ")));"
    " const innermost = (els) =>"
    "  els.find((el) => !els.some((o) => o !== el && el.contains(o))) || els[0] || null;"
    " let hit = innermost(candidates.filter((el) =>"
    "  (el.getAttribute('href') || '').includes(args.projectId)));"
    " let matchedBy = hit ? 'href' : null;"
    " if (!hit) {"
    "  hit = innermost(candidates.filter((el) => el.outerHTML.includes(args.projectId)));"
    "  matchedBy = hit ? 'id' : null;"
    " }"
    " if (!hit && args.projectName) {"
    "  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();"
    "  const wanted = norm(args.projectName);"
    "  hit = innermost(candidates.filter((el) => norm(el.textContent) === wanted))"
    "   || innermost(candidates.filter((el) => norm(el.textContent).includes(wanted)));"
    "  matchedBy = hit ? 'name' : null;"
    " }"
    " if (!hit) { return { clicked: false, matched_by: null, candidates: candidates.length }; }"
    " hit.click();"
    " return { clicked: true, matched_by: matchedBy, candidates: candidates.length };"
    "}"
)
# Raw name signals from the live editor page: the picker menu renders names,
# the CLI only knows the UUID. Tier 0 (#287 round 5): document.title — we
# navigated to /project/<id> before the picker opened, so the tab title is
# the strongest signal (Python strips the Flow branding and logs the raw
# title so the real pattern is learnable from live runs). Then: an element
# whose href references the project id; a project-title-classed element.
_PROJECT_NAME_FROM_PAGE_JS = (
    "(projectId) => {"
    ' const byHref = document.querySelector(`[href*="${projectId}"]`);'
    " const byClass = document.querySelector("
    "\"[class*='projectTitle'], [class*='ProjectTitle'], [class*='project-title']\");"
    " return {"
    "  title: (document.title || '').trim(),"
    "  href_text: byHref && byHref.textContent ? byHref.textContent.trim() : '',"
    "  class_text: byClass && byClass.textContent ? byClass.textContent.trim() : '',"
    " };"
    "}"
)
# Bounded picker DOM dump for a not-found asset (#287 diagnosis): tile count,
# the first 3 tiles' outerHTML (truncated — enough to see which attribute
# carries the media identity in this cohort), the dialog's aria/role/data
# attributes, the project-selector candidates' outerHTML, and whether the
# target project id appears anywhere in the dialog at all.
_PICKER_DOM_DUMP_JS = (
    "(projectId) => {"
    " const dialogs = document.querySelectorAll(\"[role='dialog']\");"
    " const root = dialogs.length ? dialogs[dialogs.length - 1] : document.body;"
    " const tiles = Array.from(root.querySelectorAll(\"[role='option']\"));"
    " const selectors = Array.from(root.querySelectorAll("
    "\"[role='combobox'], button[aria-haspopup], [role='listbox']\"));"
    " return {"
    " tile_count: tiles.length,"
    " tiles: tiles.slice(0, 3).map((el) => el.outerHTML.slice(0, 500)),"
    " container_attrs: root.getAttributeNames()"
    ".filter((n) => n === 'role' || n === 'id' || n.startsWith('aria-') || n.startsWith('data-'))"
    ".map((n) => n + '=' + root.getAttribute(n)),"
    " project_selector_candidates: selectors.slice(0, 5).map((el) => el.outerHTML.slice(0, 500)),"
    " target_project_in_dialog: projectId ? root.innerHTML.includes(projectId) : null,"
    " };"
    "}"
)
VIDEO_SUBMODE_SELECTORS: dict[str, tuple[str, ...]] = {
    # I2V — "frames" (start + optional end frame). Icon: crop_free.
    "frames": (
        "[role='tab'][id$='-trigger-VIDEO_FRAMES']",
        "[role='menu'] [role='tab']:has(i.google-symbols:text('crop_free'))",
    ),
    # R2V — "references"/ingredients/Elementos. Icon: chrome_extension.
    "references": (
        "[role='tab'][id$='-trigger-VIDEO_REFERENCES']",
        "[role='menu'] [role='tab']:has(i.google-symbols:text('chrome_extension'))",
    ),
}


def zip_entity_refs(
    entity_ids: tuple[str, ...],
    entity_names: tuple[str, ...],
) -> list[tuple[str, str]]:
    """Pair character entity ids with display names for the Personagens picker.

    Tiles are addressed by id (``data-tile-id="fe_id_<id>"``); the name is only a
    human label for logs/error screenshots. When fewer names than ids are given,
    the id stands in as its own name so the pairing never drops an entity. Shared
    by the image (`ui_automation`) and video (R2V) entity-attach paths.
    """
    names = list(entity_names)
    return [(eid, names[i] if i < len(names) else eid) for i, eid in enumerate(entity_ids)]


# The editor SPA's ready anchor — the Slate prompt textbox. The /project/ URL
# nav fires before the UI mounts; this is the readiness gate (used by
# _wait_video_editor_ready and asserted in its test).
_EDITOR_READY_ANCHOR = "div[role='textbox'][data-slate-editor='true'], div[contenteditable='true']"


async def _capture_debug_screenshot(page: Any, out_dir: Path | None, filename: str) -> Path | None:
    """Best-effort viewport screenshot for debugging. Duplicated from
    `ui_automation.py` to keep this module free of a circular import."""
    from gflow_cli.config import get_settings  # noqa: PLC0415 - import cycle

    # A page capture outside the incident bundle: opting out of incident capture
    # opts out of this too (#1062).
    if out_dir is None or not get_settings().incident_capture:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_path = out_dir / filename
    try:
        await page.screenshot(path=str(shot_path), full_page=False)
        log.warning(
            "ui_automation_video.debug_screenshot_may_contain_pii",
            path=str(shot_path),
            note="viewport may include the account avatar / email from the Google session",
        )
    except Exception as e:
        log.debug("ui_automation_video.screenshot_capture_failed", error=str(e))
        return None  # never report a path that was not written (#283)
    return shot_path


def screenshot_clause(shot: Path | None) -> str:
    """' Screenshot: <path>' when one was captured, else '' — a capture
    failure must not put a phantom path (or 'None') in the error text."""
    return f" Screenshot: {shot}" if shot is not None else ""


# DOM signature dumped on a UI-drift failure. Keyed on Material-Symbols ligatures
# (locale-invariant) via the ONE structural DOM engine shared with the incident
# recorder (incident-diagnostics design §6.3) — raw url/title/body text never
# reach the artifact; only allowlisted structural fields survive validation.


async def capture_ui_diagnostics(page: Any, out_dir: Path | None, name: str) -> Path | None:
    """UI-drift debug engine (legacy opt-in wrapper): dump the composer's
    STRUCTURAL DOM signature to ``<name>.json``. Consolidated onto
    ``diagnostics.STRUCTURAL_DOM_JS`` + ``validate_structural_dom`` — the same
    engine the automatic incident bundle uses; the old raw
    url/title/bodyTextPreview fields are gone (S12), and this wrapper writes
    **no screenshot**: ``out_dir`` is the user's plain output directory on
    every ordinary run, so a full-page shot here would land unmarked, outside
    the bundle's ``sensitive/`` review boundary and outside retention — the
    incident bundle owns the (full-page) screenshot evidence.
    Best-effort — returns the JSON path or ``None``; never raises."""
    from gflow_cli.diagnostics import STRUCTURAL_DOM_JS, CommandHasher, validate_structural_dom

    if out_dir is None:
        return None
    try:
        diag = validate_structural_dom(await page.evaluate(STRUCTURAL_DOM_JS), CommandHasher())
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"{name}.json"
        json_path.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.debug("ui_automation_video.ui_diagnostics_failed", error=str(e)[:120])
        return None
    log.warning(
        "ui_automation_video.ui_diagnostics_captured",
        path=str(json_path),
        note="structural ligature inventory; the incident bundle carries the screenshot",
    )
    return json_path


async def _capture_picker_dom_dump(
    page: Any, out_dir: Path | None, media_id: str, project_id: str | None
) -> Path | None:
    """Bounded DOM dump of the open picker for a not-found asset (#287
    diagnosis): what the picker was actually rendering when the lookup gave
    up — see `_PICKER_DOM_DUMP_JS` for the captured fields. Same contract as
    `_capture_debug_screenshot` (0.32.1): returns ``None`` on any capture
    failure so callers never report a file that was not written."""
    if out_dir is None:
        return None
    try:
        picker_state = await page.evaluate(_PICKER_DOM_DUMP_JS, project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        dump_path = out_dir / f"debug_picker_dom_{media_id[:8]}.json"
        dump_path.write_text(
            json.dumps(
                {"media_id": media_id, "project_id": project_id, "picker": picker_state},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001 - diagnosis capture must never mask the miss
        log.debug("ui_automation_video.picker_dom_dump_failed", error=str(e))
        return None
    return dump_path


def _write_project_menu_dump(
    out_dir: Path | None, project_id: str, payload: dict[str, Any]
) -> Path | None:
    """Persist the OPEN project menu's items on a switch miss (#287 round 2:
    the closed trigger's markup wasn't enough evidence). Same contract as
    `_capture_debug_screenshot` (0.32.1): returns ``None`` on any write
    failure so callers never report a file that was not written."""
    if out_dir is None:
        return None
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        dump_path = out_dir / f"debug_picker_project_menu_{project_id[:8]}.json"
        dump_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001 - diagnosis capture must never mask the miss
        log.debug("ui_automation_video.project_menu_dump_failed", error=str(e))
        return None
    return dump_path


def selector_drift_detail(probe: str, what: str, shot: Path | None) -> str:
    """Build a :class:`UiSelectorDriftError` detail string for a probe miss.

    Shared by the image and video transports so the message shape stays
    symmetric, and so the ``Screenshot:`` clause is omitted (rather than
    rendering a literal ``None``) when no debug screenshot was captured —
    the caller had no ``out_dir`` to write into.
    """
    detail = f"probe={probe}: {what}"
    if shot is not None:
        detail = f"{detail} Screenshot: {shot}"
    return detail


def _summarize_request_image_inputs(request: Any) -> dict[str, Any]:
    """Privacy-safe summary of the image inputs in a generate request body:
    presence of startImage/endImage + referenceImages count, each as an 8-char
    mediaId PREFIX (UUIDs are asset ids, not secrets). Proves the attached
    images are bound into the request. Never returns the reCAPTCHA token or
    credit balance. Non-fatal — returns ``{"parsed": False}`` on any error."""
    try:
        raw = request.post_data
        if not raw:
            return {"parsed": False}
        data = cast(_JsonObj, json.loads(raw))
        reqs = cast(_JsonObjList, data.get("requests") or [])
        first: dict[str, Any] = reqs[0] if reqs else {}

        def _mid(obj: Any) -> str | None:
            if not isinstance(obj, dict):
                return None
            mid = cast(_JsonObj, obj).get("mediaId")
            return mid[:8] if isinstance(mid, str) else None

        refs = cast(_JsonObjList, first.get("referenceImages") or [])
        return {
            "parsed": True,
            "startImage": _mid(first.get("startImage")),
            "endImage": _mid(first.get("endImage")),
            "referenceCount": len(refs),
            "referenceIds": [_mid(r) for r in refs],
        }
    except Exception as e:
        return {"parsed": False, "error": str(e)[:60]}


def _upload_rejection_message(status: int | None, label: str) -> str | None:
    """Return an error message when an ``uploadImage`` response status means the
    frame upload was rejected (>= 400), else ``None``.

    A 4xx/5xx here means Flow refused the bytes (e.g. the file is not a valid
    image — see the video-content guard in ``download_image``). The upload
    listener otherwise only matched the route by URL and ignored the status, so
    a rejection was treated as success: the code committed an empty slot and the
    generation silently fell back to T2V (#125). Fail loud instead.
    """
    if status is not None and status >= 400:
        return (
            f"frame image upload for {label!r} was rejected by Flow (HTTP "
            f"{status}) — the file may not be a valid image. Flow would drop the "
            "frame and fall back to a text-only video (#125), so aborting."
        )
    return None
