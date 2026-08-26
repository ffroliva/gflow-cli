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

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api import routes
from gflow_cli.api._retry import parse_retry_after
from gflow_cli.api.transports._common import (
    close_menu,
    count_visible,
    extract_project_id,
    generation_error,
    offered_menu_labels,
)
from gflow_cli.api.transports.drivers.factory import AGENTIC_INDICATOR_SELECTORS
from gflow_cli.api.video import (
    I2V_DEFAULT_MODEL,
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
    VideoStarted,
    VideoStartedCallback,
    VideoStatus,
    media_name_from_generate_response,
    operation_name_from_generate_response,
    parse_video_status,
)
from gflow_cli.errors import (
    AuthExpiredError,
    FlowAgentUiError,
    FlowAppError,
    MediaUploadRejectedError,
    ModelModeIncompatibilityError,
    RateLimitError,
    ReferenceNotFoundError,
    TransportTimeoutError,
    UiSelectorDriftError,
    VideoModelSelectionError,
    WafRejectionError,
    WireFormatError,
)
from gflow_cli.file_integrity import matches_recorded_file
from gflow_cli.storage import AnyPath, storage_path, write_asset_async

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

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
# Status-poll route — Flow's SPA polls this itself while a generation runs.
VIDEO_STATUS_ROUTE = "batchCheckAsyncVideoGenerationStatus"

# Mode switching is a 2-step dropdown (spec §6, §10.5). The trigger is the
# unified generation-settings button — the only button[aria-haspopup='menu']
# carrying an aspect-ratio crop_* icon; clicking it opens a role='menu' with
# the Imagem/Vídeo role='tablist' (the tabs are not in the DOM until it opens).
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
    VideoModel.OMNI_FLASH: "[role='menuitem']:has-text('Omni Flash')",
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
# Tier 2 (FALLBACK, EN-only): `FRAME_SLOT_BY_LABEL` matches by visible English
# text. Kept for defense-in-depth in case Flow drops the `type` attribute on
# the slots; the fail-loud `RuntimeError` in `_attach_frame` covers the case
# where both tiers miss on a non-EN profile.
#
# Caller labels are always the hardcoded constants 'Start' / 'End', so no
# CSS-escaping is needed.
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
FRAME_SLOT_BY_LABEL = "div[aria-haspopup='dialog']:has-text('{label}')"
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
# FALLBACK / OPERATOR NOTE: if Google ever restructures this dialog so even these
# anchors break, `_upload_via_open_dialog` raises a clear error + screenshot
# instead of hanging. The operator workaround is to set the CHROME PROFILE
# language to English -- the Google ACCOUNT language alone is NOT enough.
UPLOAD_MEDIA_BUTTON = (
    "[role='dialog'][data-state='open'] button:has(i.google-symbols:text-is('upload'))"
)
# Tier-2 fallback: the original localized-text selector (#50). Only matches an
# ENGLISH-rendering profile — kept as a graceful fallback for the narrow case
# where Google changes the `upload` icon ligature but the English label survives.
# This is exactly why the failure message tells the operator to set the Chrome
# profile to English: it makes this fallback tier viable.
UPLOAD_MEDIA_BUTTON_TEXT = "[role='dialog'][data-state='open'] button:has-text('Upload media')"
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
# 2026-06-11): the button carries NO ligature icon, so known localized captions
# lead; the structural fallback is the LONE iconless button inside the open
# picker dialog (every other dialog button has a ligature: tabs, `play_arrow`
# preview, `arrow_drop_down` sort) — same situation as ADD_TO_PROMPT_DIALOG.
# Tiers are probed sequentially (see _resolve_include_action), never flattened
# into one comma list: comma lists resolve in DOM order, not tier priority.
PICKER_INCLUDE_BUTTON: tuple[str, str] = (
    "button:has-text('Incluir no comando'), button:has-text('Добавить в запрос'),"
    " button:has-text('Add to prompt')",
    "[role='dialog'][data-state='open'] button:not(:has(i.google-symbols))",
)
_INCLUDE_BUTTON_TIER_NAMES = ("text", "structural")
# Context-menu include action shown on RIGHT-CLICK of a Personagens entity
# tile. This is what stages a `referenceEntity` (the inline Tudo button instead
# stages a `referenceImage` of the thumbnail). Verified 2026-06-06.
# Issue #170 + recon 2026-06-11 (pt-BR denon82, matching the ru report): the
# menu is `div[role='menu'][data-state='open']` with menuitem ligatures
# add / content_cut / content_copy / delete — `add` is unique within the menu,
# so the icon tier is locale-free. The text tier keeps known captions, menu-
# scoped so a user-named tile (e.g. a character called 'Add to prompt') can
# never match.
PICKER_CONTEXT_INCLUDE: tuple[str, str] = (
    "[role='menu'][data-state='open'] [role='menuitem']:has(i.google-symbols:text-is('add'))",
    "[role='menu'] [role='menuitem']:has-text('Incluir no comando'),"
    " [role='menu'] [role='menuitem']:has-text('Добавить в запрос'),"
    " [role='menu'] [role='menuitem']:has-text('Add to prompt')",
)
_CONTEXT_INCLUDE_TIER_NAMES = ("icon", "text")
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
    if out_dir is None:
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


class VideoGenerationMixin:
    """Video-generation methods mixed into `UiAutomationTransport`.

    The mixin depends on host state and helpers that `UiAutomationTransport`
    supplies; they are declared below as a TYPE-ONLY contract so
    `pyright --strict` resolves `self._page` / `self._enter_editor` etc. The
    bare annotations and `if TYPE_CHECKING` stubs create no runtime members —
    the real values come from `UiAutomationTransport.__init__` and its methods.
    This replaces a separate `_VideoHost` Protocol: pyright rejects an explicit
    `self: _VideoHost` annotation on a mixin method because `VideoGenerationMixin`
    itself does not satisfy that Protocol.
    """

    # --- host contract: supplied by UiAutomationTransport (type-only) ---
    _page: Page | None
    _setup_done: bool
    _generate_lock: asyncio.Lock
    _out_dir: Path | None

    if TYPE_CHECKING:

        async def _enter_editor(
            self,
            page: Page,
            out_dir: Path | None = None,
            *,
            project_id: str | None = None,
            project_name: str | None = None,
        ) -> None: ...
        async def _send_prompt(
            self,
            page: Page,
            prompt_text: str,
            out_dir: Path | None = None,
        ) -> None: ...
        async def _dismiss_blocking_overlays(
            self,
            page: Page,
            out_dir: Path | None = None,
        ) -> bool: ...

    @contextlib.asynccontextmanager
    async def _intercept_reference_entities(
        self,
        page: Page,
        expected_entities: set[str],
    ) -> AsyncGenerator[None, None]:
        """Register a route handler to strip unrequested referenceEntities from outgoing requests.

        This prevents 'poisoned' character entities from smuggling themselves into unrelated
        image/video generation runs.
        """
        import json
        from unittest.mock import AsyncMock, Mock

        if isinstance(page, Mock):
            if not isinstance(getattr(page, "route", None), AsyncMock):
                page.route = AsyncMock()
            if not isinstance(getattr(page, "unroute", None), AsyncMock):
                page.unroute = AsyncMock()

        async def intercept_generation_request(route: Any) -> None:
            req_obj = route.request
            try:
                post_data = req_obj.post_data
                if not post_data:
                    await route.continue_()
                    return

                body = cast(dict[str, Any], json.loads(post_data))
                modified = False

                if "requests" in body and isinstance(body["requests"], list):
                    requests_list = cast(list[dict[str, Any]], body["requests"])
                    for item in requests_list:
                        if "referenceEntities" in item:
                            refs = item["referenceEntities"]
                            if isinstance(refs, list):
                                refs_list = cast(list[dict[str, Any]], refs)
                                filtered_refs: list[dict[str, Any]] = []
                                for r in refs_list:
                                    if "entityId" in r:
                                        ent_id = r["entityId"]
                                        if isinstance(ent_id, str) and ent_id in expected_entities:
                                            filtered_refs.append(r)

                                if len(filtered_refs) != len(refs_list):
                                    item["referenceEntities"] = filtered_refs
                                    modified = True

                                if not filtered_refs:
                                    item.pop("referenceEntities", None)
                                    modified = True

                if modified:
                    log.info(
                        "ui_automation.batch_request_modified",
                        url=req_obj.url,
                        reason="stripped unrequested referenceEntities",
                        expected_entities=list(expected_entities),
                    )
                    await route.continue_(post_data=json.dumps(body))
                else:
                    await route.continue_()
            except Exception as exc:
                log.warning(
                    "ui_automation.batch_request_modify_failed",
                    url=req_obj.url,
                    error=str(exc),
                )
                await route.continue_()

        # Register rules for both image and video endpoints
        await page.route("**/batchGenerateImages", intercept_generation_request)
        await page.route("**/batchAsyncGenerateVideo*", intercept_generation_request)
        try:
            yield
        finally:
            try:
                await page.unroute("**/batchGenerateImages")
                await page.unroute("**/batchAsyncGenerateVideo*")
            except Exception:
                pass

    @staticmethod
    def _attach_video_response_listener(page: Page) -> tuple[_JsonObjList, Any]:
        """Register a `page.on('response')` listener for the three
        batchAsyncGenerateVideo* routes (spec §2.1). Returns `(captured, handler)`
        — the caller awaits `captured` after submitting the prompt and MUST
        `page.remove_listener('response', handler)` in a `finally` (the Page is
        pooled and persistent; an un-removed handler leaks across calls).
        Registered synchronously before `_send_prompt` so a fast response is
        never missed.

        The captured `body` is kept for parsing only — it carries
        `remainingCredits` and media UUIDs and MUST NOT be logged.
        """
        captured: _JsonObjList = []

        async def on_response(response: Any) -> None:
            if not any(route in response.url for route in VIDEO_GENERATE_ROUTES):
                return
            try:
                body = await response.json()
            except Exception as e:
                log.warning("ui_automation_video.generate_parse_failed", error=str(e))
                return
            captured.append({"status": response.status, "url": response.url, "body": body})
            # Proof that the attached images actually made it into the request
            # (the user saw the UI start generating before the upload spinner
            # cleared). Parse the REQUEST post_data and log only counts +
            # mediaId prefixes (UUIDs, not secrets) — never the token/credits.
            inputs = _summarize_request_image_inputs(response.request)
            captured[-1]["image_inputs"] = inputs
            log.info(
                "ui_automation_video.generate_captured",
                status=response.status,
                url=response.url,
                image_inputs=inputs,
            )

        page.on("response", on_response)
        return captured, on_response

    @staticmethod
    def _attach_status_response_listener(page: Page) -> tuple[_JsonObjList, Any]:
        """Register a `page.on('response')` listener for the status route. Flow's
        SPA polls `batchCheckAsyncVideoGenerationStatus` itself while a
        generation runs; this captures that traffic. Returns `(captured, handler)`
        — the caller MUST `page.remove_listener('response', handler)` in a
        `finally`. Attached BEFORE `_send_prompt` so no early status response is
        missed (spec §5.5)."""
        captured: _JsonObjList = []

        async def on_response(response: Any) -> None:
            if VIDEO_STATUS_ROUTE not in response.url:
                return
            try:
                body = await response.json()
            except Exception as e:
                log.warning("ui_automation_video.status_parse_failed", error=str(e))
                return
            captured.append({"status": response.status, "url": response.url, "body": body})

        page.on("response", on_response)
        return captured, on_response

    @staticmethod
    def _scan_for_terminal_status(
        captured_status: _JsonObjList,
        media_name: str,
    ) -> tuple[VideoStatus | None, str | None]:
        """Scan all captured status responses for a terminal VideoStatus.

        Returns ``(terminal_status, last_seen_status_string)``. Raises
        ``AuthExpiredError`` on HTTP 401. Skips responses for other media.
        """
        terminal: VideoStatus | None = None
        last_status: str | None = None
        for response in captured_status:
            if response.get("status") == 401:
                raise AuthExpiredError(
                    detail=(
                        "batchCheckAsyncVideoGenerationStatus returned HTTP 401"
                        " — session expired mid-poll"
                    ),
                    status=401,
                    route="video:status",
                )
            try:
                status = parse_video_status(response.get("body") or {}, media_id=media_name)
            except ValueError:
                continue  # this response is for other media — skip
            last_status = status.status
            if status.is_terminal:
                terminal = status
        return terminal, last_status

    @staticmethod
    async def _nudge_tab_if_stalled(
        page: Page,
        captured_status: _JsonObjList,
        seen_count: int,
        last_progress: float,
        nudged: bool,
        stall_nudge_s: float,
        media_name: str,
    ) -> tuple[int, float, bool]:
        """Update stall-detection bookkeeping and nudge the tab once on stall.

        Returns the updated ``(seen_count, last_progress, nudged)`` tuple.
        """
        if len(captured_status) != seen_count:
            return len(captured_status), time.monotonic(), nudged
        if not nudged and time.monotonic() - last_progress > stall_nudge_s:
            # Distinguish "Flow never polled the status route at all" from
            # "Flow stalled mid-run".
            event = (
                "ui_automation_video.poll_no_status_traffic"
                if seen_count == 0
                else "ui_automation_video.poll_stall_nudge"
            )
            log.warning(event, media_name=media_name, status_responses_seen=seen_count)
            try:
                await page.bring_to_front()
            except Exception as e:
                log.debug("ui_automation_video.bring_to_front_failed", error=str(e))
            nudged = True
        return seen_count, last_progress, nudged

    @staticmethod
    async def _poll_video_status(
        page: Page,
        captured_status: _JsonObjList,
        media_name: str,
        *,
        timeout_s: float = 600.0,
        poll_interval_s: float = 2.0,
        stall_nudge_s: float = 120.0,
    ) -> VideoStatus:
        """Read terminal status from Flow's own captured status traffic.

        `captured_status` is the list filled by `_attach_status_response_listener`.
        Each tick scans the WHOLE list for a terminal status of `media_name`
        (Flow appends chronologically; a terminal status is the last it emits) —
        no early `break`, so a terminal entry is never skipped. Returns the
        `VideoStatus` once terminal; the caller maps a FAILED status to a typed
        error (spec §7).

        If Flow stops polling (a backgrounded tab can throttle its timers) the
        captured list stops growing; after `stall_nudge_s` with no new capture
        this brings the page to the foreground ONCE and keeps waiting (spec
        §5.5). Raises `TimeoutError` only at the hard `timeout_s` deadline.
        """
        deadline = time.monotonic() + timeout_s
        last_status: str | None = None
        seen_count = len(captured_status)
        last_progress = time.monotonic()
        nudged = False
        while time.monotonic() < deadline:
            terminal, last_status = VideoGenerationMixin._scan_for_terminal_status(
                captured_status, media_name
            )
            if terminal is not None:
                log.info(
                    "ui_automation_video.poll_terminal",
                    media_name=media_name,
                    status=terminal.status,
                )
                return terminal
            seen_count, last_progress, nudged = await VideoGenerationMixin._nudge_tab_if_stalled(
                page, captured_status, seen_count, last_progress, nudged, stall_nudge_s, media_name
            )
            await asyncio.sleep(poll_interval_s)
        cause = (
            "Flow never polled the status route"
            if seen_count == 0
            else "Flow stopped polling before a terminal status"
        )
        msg = (
            f"no terminal status for {media_name!r} within {timeout_s:.0f}s — "
            f"{seen_count} status response(s) seen, last status: {last_status}. {cause}."
        )
        raise TimeoutError(
            msg,
        )

    async def _download_video(
        self,
        media_id: str,
        out_dir: Path | None,
        page: Any,
    ) -> AnyPath:
        """Download a generated video to local disk or cloud storage.

        Calls ``media.getMediaUrlRedirect?name=<media_id>`` which 302s to a
        signed GCS URL; Playwright follows the redirect automatically.

        When the transport's ``_storage_uri`` is set the video is uploaded to
        the configured cloud backend; otherwise it is written to ``out_dir``.
        """
        url = routes.media_download_url(media_id)
        resp = await page.request.get(url, max_redirects=5, timeout=180_000)
        if resp.status >= 400:
            raise WireFormatError(
                detail=(
                    f"video download returned HTTP {resp.status} for {media_id!r} "
                    f"via media.getMediaUrlRedirect"
                ),
                status=resp.status,
                route="media.getMediaUrlRedirect",
            )
        body = await resp.body()
        storage_uri: str | None = getattr(self, "_storage_uri", None)
        if storage_uri:
            from datetime import date

            from gflow_cli import paths as _paths

            key = f"videos/{date.today().isoformat()}/{_paths.validate_job_id(media_id)}.mp4"
            # output_dir fallback only used for key computation when cloud is active
            output_dir = getattr(self, "_output_dir", None) or Path("tmp")
            target: AnyPath = storage_path(storage_uri, output_dir, key)
        else:
            effective_dir = out_dir or self._out_dir or Path("tmp")
            target = effective_dir / f"{media_id}.mp4"
        await write_asset_async(target, body)
        log.info(
            "ui_automation_video.video_saved",
            path=str(target),
            bytes=len(body),
            media_id=media_id,
        )
        return target

    @staticmethod
    async def _probe_selector_cascade(
        page: Page,
        label: str,
        candidates: tuple[str, ...],
        *,
        timeout_ms: int = 4000,
    ) -> Locator | None:
        """Try each selector in order; return the first visible match or None.
        Logs every attempt so a failed probe is diagnosable from the structured
        log alone."""
        for selector in candidates:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                log.info("ui_automation_video.selector_matched", probe=label, selector=selector)
                return loc
            except Exception:
                log.debug("ui_automation_video.selector_miss", probe=label, selector=selector)
        log.warning("ui_automation_video.selector_probe_failed", probe=label)
        return None

    @staticmethod
    async def _media_panel_present(page: Page) -> bool:
        """True if the media-generation panel is mounted.

        Keyed on the locale-stable ``crop_*`` settings trigger
        (:data:`MODE_SWITCH_TRIGGER_SELECTORS`) — the same anchor the mode
        switches probe. Its presence is the signal that the composer is in media
        (Image/Video) mode rather than Agent mode, which removes the panel.
        """
        for sel in MODE_SWITCH_TRIGGER_SELECTORS:
            if await page.locator(sel).count() > 0:
                return True
        return False

    @staticmethod
    async def _dismiss_agent_affordances(page: Page, *, allow_reload: bool = False) -> bool:
        """Bring the composer back to classic media mode; return True if it acted.

        Delegates to the robust :func:`mode_control.ensure_media_mode`, which is
        **state-aware**: it reads the Agent toggle's ``aria-pressed`` (the
        locale-invariant source of truth) and clicks it OFF only when actually
        on, and closes the expanded chat sidebar (X) first — replacing the older
        blind single pill-click that gave up on a still-open composer. Does not
        raise; the caller (:meth:`_exit_agent_mode`) re-checks the media panel
        and escalates via :meth:`_check_forced_agentic_ui` only if it never
        returned.

        ``allow_reload`` passes through to ``ensure_media_mode``'s pinned-arm
        reload rescue — only the pre-bind ``get_ui_driver`` path may set it (a
        reload re-rolls the cohort arm; see the mode_control docstring).
        """
        # Local import keeps mode_control a leaf and avoids any import cycle.
        from gflow_cli.api.transports import mode_control

        return await mode_control.ensure_media_mode(page, allow_reload=allow_reload)

    @staticmethod
    async def _check_forced_agentic_ui(page: Page, out_dir: Path | None) -> None:
        """Raise ``FlowAgentUiError`` if any forced Agentic UI indicator is present."""
        for sel in AGENTIC_UI_INDICATORS:
            if await page.locator(sel).count() > 0:
                log.warning(
                    "ui_automation_video.forced_agentic_ui_detected",
                    indicator=sel,
                    note="Forced Agentic UI detected; classic media panel is not recoverable.",
                )
                shot_path = await _capture_debug_screenshot(
                    page, out_dir, "debug_forced_agent_ui.png"
                )
                raise FlowAgentUiError(
                    detail=(
                        f"Agentic UI detected via indicator {sel!r}. "
                        f"Viewport screenshot: {shot_path}"
                    )
                )

    @staticmethod
    async def _detect_non_classic_cohort(page: Page) -> str | None:
        """Return the first :data:`NON_CLASSIC_COHORT_INDICATORS` selector present,
        else ``None``. Called at the mode-switch RAISE site — after ``crop_*`` is
        confirmed absent AND the ~24s crop cascade has already elapsed, so the page
        is fully rendered and one scan is decisive: the known agentic/media-library
        cohort (#174/#183 → a clean :class:`FlowAgentUiError`) vs genuine drift."""
        for sel in NON_CLASSIC_COHORT_INDICATORS:
            try:
                if await page.locator(sel).count() > 0:
                    return sel
            except Exception:  # noqa: BLE001  # NOSONAR — best-effort probe
                continue
        return None

    @staticmethod
    async def _is_flow_app_crash(page: Page) -> bool:
        """True if Flow's web app rendered its client-side-exception error boundary
        (a transient Flow crash) instead of the editor — keyed on the (English,
        Next.js-hardcoded) error-page title. Best-effort; never raises."""
        try:
            title = (await page.title()) or ""
        except Exception:  # noqa: BLE001  # NOSONAR — best-effort probe
            return False
        return "application error" in title.lower()

    @staticmethod
    async def _mode_switch_error(
        page: Page, out_dir: Path | None, *, media: str
    ) -> FlowAppError | FlowAgentUiError | UiSelectorDriftError:
        """Build (do NOT raise — the caller raises) the right error for a
        ``mode_switch_trigger`` miss on the ``image``/``video`` path: dump DOM
        diagnostics, then classify — a transient :class:`FlowAppError` if Flow's app
        crashed, a clean retryable :class:`FlowAgentUiError` for the known
        agentic/media-library A/B cohort (#174/#183), else :class:`UiSelectorDriftError`
        for genuine drift. Returning the exception (vs raising here) keeps the
        None-path terminating *visibly* at the two call sites. Shared by
        ``_switch_to_image_mode`` and ``_switch_to_video_mode``."""
        diag = await capture_ui_diagnostics(page, out_dir, "diag_mode_switch_miss")
        diag_clause = f" Diagnostics: {diag}" if diag is not None else ""
        if await VideoGenerationMixin._is_flow_app_crash(page):
            return FlowAppError(
                detail=(
                    "Flow's web app crashed (client-side exception) before the editor "
                    f"rendered, so there is no {media} generation control to drive."
                    f"{diag_clause}"
                )
            )
        cohort = await VideoGenerationMixin._detect_non_classic_cohort(page)
        if cohort is not None:
            return FlowAgentUiError(
                detail=(
                    "Flow opened this project in its new media-library / agentic "
                    f"composer (server-side A/B cohort, issues #174/#183; matched "
                    f"{cohort!r}) — there is no classic aspect/mode control for gflow to "
                    f"drive {media} generation here. This cohort flaps; retrying in a few "
                    f"minutes often lands the classic UI.{diag_clause}"
                )
            )
        return UiSelectorDriftError(
            selector_drift_detail(
                "mode_switch_trigger",
                "no matching element found on the Flow editor. No known Flow "
                "cohort matched either — the editor may be a new Flow UI layout "
                "this gflow-cli version does not recognize yet (issue #493).",
                None,
            )
            + diag_clause
        )

    @staticmethod
    async def _exit_agent_mode(
        page: Page, *, out_dir: Path | None = None, allow_reload: bool = False
    ) -> bool:
        """Ensure the composer is in media (Image/Video) mode, not Agent mode.

        Flow's "Agent" mode hides the media-generation panel — the ``crop_*``
        settings trigger that :meth:`_switch_to_image_mode`,
        :meth:`_switch_to_video_mode`, and ``_configure_generation_settings``
        probe disappears — so generation fails with "mode-switch dropdown trigger
        not found". Agent mode shows up in TWO shapes, and which one appears on a
        given project open is non-deterministic (Flow A/B):

        1. **In-composer pill** (:data:`COMPOSER_AGENT_TOGGLE_SELECTOR`) — an
           ``Agent`` toggle next to the prompt; clicking it returns to media mode.
        2. **Chat side-panel** (:data:`AGENT_CHAT_PANEL_CLOSE_SELECTOR`) — a docked
           "Untitled session" chat on the right; while it is up the pill is not in
           the DOM at all, so it must be dismissed (its X) first, after which the
           pill reappears (usually still active) and step 1 applies.

        This drives a small fixed-iteration loop: while the media panel is absent,
        dismiss whichever Agent affordance is present (chat panel first, then
        pill) and re-check. The loop is keyed on the OUTCOME (``crop_*`` is back),
        never on assuming which control exists — so it covers pill-only,
        panel-only, panel-then-pill, and neither (older UI) without special-casing.

        Returns ``True`` only when it actually brought the media panel back,
        ``False`` otherwise (already in media mode, nothing to act on, or the
        clicks did not re-mount the panel). Best-effort, locale-invariant
        (Material Symbols ligatures + structural anchors, no UI text, no ARIA),
        and raises FlowAgentUiError if a forced Agentic UI cohort is encountered.
        """
        try:
            # Common case: the media panel is already mounted → media mode,
            # nothing to do. Cheap no-op on every normal generation.
            if await VideoGenerationMixin._media_panel_present(page):
                return False

            acted = await VideoGenerationMixin._dismiss_agent_affordances(
                page, allow_reload=allow_reload
            )

            if await VideoGenerationMixin._media_panel_present(page):
                if acted:
                    log.info("ui_automation_video.exited_agent_mode")
                return True

            # If the media panel is not present after the exit attempts, check if
            # any of the unique Agentic UI indicators are present.
            await VideoGenerationMixin._check_forced_agentic_ui(page, out_dir)

            if acted:
                # We clicked something but the panel never came back — don't claim
                # a false "exited"; warn and let the caller's own trigger probe
                # fail loudly (with a screenshot) so the real cause surfaces.
                log.warning(
                    "ui_automation_video.exit_agent_mode_no_panel",
                    note="dismissed Agent affordance(s) but the media panel did not re-mount",
                )
            return False
        except FlowAgentUiError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("ui_automation_video.agent_toggle_probe_failed", error=str(e)[:80])
            return False

    @staticmethod
    async def _switch_to_video_mode(page: Page, *, out_dir: Path | None) -> None:
        """Open the 2-step mode dropdown and switch to Video mode. The menu
        stays open afterward so the caller can also set aspect + count."""
        # New Flow UI: if the composer is in Agent mode the generation panel is
        # absent — return to media mode first so the trigger probe can find the
        # crop_* dropdown.
        await VideoGenerationMixin._exit_agent_mode(page, out_dir=out_dir)
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "mode_switch_trigger",
            MODE_SWITCH_TRIGGER_SELECTORS,
        )
        if trigger is None:
            raise await VideoGenerationMixin._mode_switch_error(page, out_dir, media="video")
        await trigger.click()
        await page.wait_for_timeout(800)
        video_tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "video_mode_tab",
            VIDEO_TAB_IN_MENU_SELECTORS,
        )
        if video_tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_video_tab.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "video_mode_tab",
                    "Video tab not found in the mode dropdown.",
                    shot,
                )
            )
        await video_tab.click()
        await page.wait_for_timeout(1200)
        log.info("ui_automation_video.video_mode_entered")

    @staticmethod
    async def _wait_video_editor_ready(page: Page) -> None:
        """Wait for the editor SPA to mount before probing video controls. The
        /project/ URL nav fires before the UI renders — the Phase 0 spike found
        probes taken right after it see only the page shell. The prompt textbox
        is the ready anchor. Non-fatal on timeout (the cascade probes still
        have their own per-selector waits)."""
        try:
            await page.locator(_EDITOR_READY_ANCHOR).first.wait_for(state="visible", timeout=20_000)
            await page.wait_for_timeout(1000)
            log.info("ui_automation_video.editor_ready")
        except Exception as e:
            log.warning("ui_automation_video.editor_ready_timeout", error=str(e))

    @staticmethod
    async def _set_output_count(page: Page, n: int, *, out_dir: Path | None = None) -> None:
        """Set the output count to `n` (1-4). Flow defaults to x2 (two videos =
        double credits — spec §10.5). Disambiguated by label text, NOT
        id-suffix — '-trigger-4' collides with the DURATION 4s tab (exact
        text match keeps '4s' from ever colliding with 'x4'). BOTH affix
        orders are probed for every digit ('x1' current / '1x' legacy — the
        issue #404 rename class), so the next affix flip degrades to the
        fallback selector instead of a miss.

        Fatal on miss: count is a credit MULTIPLIER (every
        ``GenerateVideoRequest`` carries a definite 1-4 value), and a miss
        hands the run to Flow's sticky default — typically x2, silently
        DOUBLING spend for count=1 and under-delivering for count=3/4. This
        runs before frame attach and submit, so refusing spends nothing —
        the same pre-spend contract as the duration probe (#288) and the
        required i2v model select (#125)."""
        labels = (f"x{n}", f"{n}x")
        selectors = tuple(f"[role='tab']:text-is('{label}')" for label in labels) + tuple(
            f"[role='tab']:has-text('{label}')" for label in labels
        )
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "count_tab",
            selectors,
        )
        if tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_count_tab.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "count_tab",
                    f"the output-count tab for count={n} (label 'x{n}' or '{n}x') was "
                    f"not found on the Flow editor; refusing to proceed — Flow's "
                    f"sticky default (typically x2) would silently change how many "
                    f"clips this run generates and bills for. No credits were spent.",
                    shot,
                )
            )
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.output_count_set", count=n)

    @staticmethod
    async def _select_video_model(
        page: Page,
        model: VideoModel,
        *,
        out_dir: Path | None,
    ) -> None:
        """Open the model picker and select *model*, or FAIL before spending.

        Every miss is FATAL (changed 2026-08-26). This previously refused only
        when ``required=True`` (i2v with frames, issue #125); a plain t2v miss
        logged "Flow default model applies" and returned, so the run generated on
        whatever model Flow last had selected and CHARGED FOR THAT TIER. Video is
        the credit-bearing arm — veo-quality costs 100 against veo-lite's 10.

        Refusing is unambiguously correct here, unlike the image panel-miss,
        which stayed a warning. ``configure_video_settings`` calls this **only**
        when ``effective_model is not None``, and ``--model`` defaults to
        ``None`` on every video command, so reaching this function means a model
        was EXPLICITLY requested. There is no "default or asked-for?" ambiguity.

        AMBIGUOUS is a failure, not a ``.first`` guess: ``.first`` resolves by
        DOM order, so an ambiguous selector silently picks whichever Flow renders
        first and changes behaviour with no code change on our side.
        ``has-text`` is a SUBSTRING match and ``Veo 3.1 - Lite`` is a prefix of
        ``Veo 3.1 - Lite [Lower Priority]``, so this is a live hazard, not a
        theoretical one.

        Reliability (issue #125): the trigger click occasionally does not open
        the menu. We click the trigger and probe up to two times, pressing
        Escape between attempts to reset the dropdown state.

        Runs before frame attach and submit, so a refusal spends nothing.
        """
        option_sel = VIDEO_MODEL_OPTION_SELECTORS.get(model)
        if option_sel is None:
            # Unreachable for the registered models — the governance test pins
            # that every VideoModel has a selector — but keeps the contract if
            # one is added without one.
            raise VideoModelSelectionError(
                detail=f"no model-picker selector registered for {model.value!r}",
                route="model_option",
            )
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "model_picker_trigger",
            (MODEL_PICKER_TRIGGER,),
        )
        if trigger is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_model_picker.png")
            raise VideoModelSelectionError(
                detail=(
                    f"model picker trigger not found; cannot select {model.value!r}. "
                    f"Refusing to proceed — generating on whatever model Flow last "
                    f"had selected would spend credits on a tier that was not "
                    f"requested (refs #125). No credits were spent."
                    f"{screenshot_clause(shot)}"
                ),
                route="model_picker_trigger",
            )

        offered: list[str] = []
        for attempt in (1, 2):
            await trigger.click()
            await page.wait_for_timeout(600)
            offered = await offered_menu_labels(page)
            matches, first_visible = await count_visible(page, option_sel)
            if matches > 1:
                await close_menu(page)
                raise VideoModelSelectionError(
                    detail=(
                        f"selector for {model.value!r} is AMBIGUOUS — {matches} entries "
                        f"match {option_sel!r}. Selecting .first would pick by DOM order "
                        f"and could charge a different credit tier than requested. "
                        f"Flow offered: {offered}. No credits were spent."
                    ),
                    route="model_option",
                )
            if matches == 1:
                await first_visible.click()
                await page.wait_for_timeout(800)
                log.info("ui_automation_video.model_selected", model=model.value, via=option_sel)
                return
            # The menu may not have opened (trigger click raced) or rendered the
            # option late. Escape closes only the dropdown (the settings popover
            # underneath stays open), then we retry the trigger click once.
            log.debug(
                "ui_automation_video.model_option_retry",
                model=model.value,
                attempt=attempt,
                offered=offered,
            )
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)

        log.warning(
            "ui_automation_video.model_option_not_found", model=model.value, offered=offered
        )
        # Screenshot BEFORE closing: the whole point of this capture is what the
        # menu was rendering, and `close_menu` dismisses it.
        shot = await _capture_debug_screenshot(page, out_dir, "debug_model_option_not_found.png")
        await close_menu(page)
        raise VideoModelSelectionError(
            detail=(
                f"video model {model.value!r} is not selectable — no picker entry "
                f"matched after 2 attempts. Flow offered: {offered}. Refusing to "
                f"generate on a different model than requested: the wrong tier "
                f"costs different credits, caps duration differently, and only "
                f"Veo 3.1 supports an end frame (refs #125). No credits were spent."
                f"{screenshot_clause(shot)}"
            ),
            route="model_option",
        )

    @staticmethod
    async def _select_video_duration(page: Page, seconds: int, *, out_dir: Path | None) -> None:
        """Click the duration tab for `seconds` (4/6/8, or 10 for omni_flash).
        Disambiguated by visible tab/button text ('4s'..'10s'), NOT id-suffix
        (collides with count). Must run AFTER model select — the 10s tab only
        exists once omni_flash is chosen. Fatal on miss (issue #288): this
        only runs for an explicit --duration, and duration is a contract
        parameter — a silent fall-through to Flow's default corrupts
        downstream timeline math."""
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "duration_tab",
            (
                f"[role='tab']:text-is('{seconds}s')",
                f"[role='tab']:has-text('{seconds}s')",
                f"[role='button']:text-is('{seconds}s')",
                f"[role='button']:has-text('{seconds}s')",
                f"button:text-is('{seconds}s')",
                f"button:has-text('{seconds}s')",
                f"[role='option']:text-is('{seconds}s')",
                f"[role='option']:has-text('{seconds}s')",
                f"[role='menuitem']:text-is('{seconds}s')",
                f"[role='menuitem']:has-text('{seconds}s')",
            ),
        )

        if tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_duration_tab.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "duration_tab",
                    f"the {seconds}s duration tab was not found on the Flow editor; "
                    f"refusing to proceed — Flow's default duration (typically 8s) "
                    f"would silently replace the requested value (issue #288). Omit "
                    f"--duration to accept Flow's default.",
                    shot,
                )
            )
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.duration_set", seconds=seconds)

    @staticmethod
    async def _switch_video_sub_mode(page: Page, sub: str, *, out_dir: Path | None) -> None:
        """Switch the video sub-mode tab: 'frames' (I2V) or 'references' (R2V).
        Must run while the settings panel is open (after _switch_to_video_mode)."""
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            f"video_submode_{sub}",
            VIDEO_SUBMODE_SELECTORS[sub],
        )
        if tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, f"debug_no_submode_{sub}.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    f"video_submode_{sub}",
                    f"video sub-mode tab {sub!r} not found on the Flow editor.",
                    shot,
                )
            )
        await tab.click()
        await page.wait_for_timeout(900)
        log.info("ui_automation_video.video_submode_entered", sub=sub)

    @staticmethod
    async def _attach_frame(
        page: Page,
        slot_index: int,
        label: str,
        image: Path,
        *,
        out_dir: Path | None,
        timeout_s: float = 120.0,
    ) -> None:
        """Fill the I2V first/last frame slot (`slot_index` 0=first, 1=last) with
        `image` through Flow's media dialog (the ONLY way that binds the image to
        the slot — set_input_files on the generic input only adds it to the
        library, see the UPLOAD_IMAGE_ROUTE note). Sequence: click slot ->
        'Upload media' (file chooser) -> wait uploadImage -> commit. Must run with
        the settings panel CLOSED (the slots live in the main editor). `label` is
        for logging only. Path existence is validated here (the boundary)."""
        if not image.exists():
            msg = f"frame image not found: {image}"
            raise FileNotFoundError(msg)

        # wait_ms is short (1500) because _wait_video_editor_ready already
        # guaranteed the editor SPA is mounted; the frame panel resolves in
        # <10 ms on a pre-rendered page, so a future swap_horiz rename surfaces
        # as a fast, clear error instead of an 8-second dead wait per call.
        slot = await VideoGenerationMixin._resolve_frame_slot(
            page, slot_index, label, out_dir=out_dir, wait_ms=1500
        )
        await slot.click()
        await page.wait_for_timeout(1000)  # media dialog opens
        await VideoGenerationMixin._upload_via_open_dialog(
            page,
            image,
            log_label=label,
            out_dir=out_dir,
            timeout_s=timeout_s,
        )
        log.info("ui_automation_video.frame_attached", slot=label)

    @staticmethod
    async def _pick_option_and_include(
        page: Page,
        name: str,
        *,
        surface: str,
        detail: str,
        out_dir: Path | None,
        dialog_timeout_s: float,
    ) -> None:
        """Shared picker flow: type ``name`` into the open resource picker,
        select the matching result tile, fire the locale-safe include action,
        and verify the picker dialog closed. Used by both the I2V frame and R2V
        reference remote-attach paths (the picker is identical once open)."""
        search_input = page.locator(PICKER_SEARCH_INPUT)
        # Human-like typing jitter to dodge WAF bot heuristics — not a security
        # context, so a plain PRNG is fine.
        await search_input.press_sequentially(name, delay=random.randint(10, 50))  # NOSONAR
        await page.wait_for_timeout(600)

        # Text match — apostrophes in the display name would break a
        # `:has-text('{name}')` CSS selector.
        tile = VideoGenerationMixin._remote_option_tile(page, name).first
        await tile.wait_for(state="visible", timeout=8000)
        await tile.click()
        await page.wait_for_timeout(300)

        # #529 live recon (pt profile): clicking the result tile now attaches
        # directly and closes the picker — the include button exists only in
        # the hover-preview pane. Treat a closed dialog as success and fall
        # through to the legacy include-button flow only while it stays open.
        dialog = page.locator(DIALOG_ANY).last
        try:
            await dialog.wait_for(state="hidden", timeout=3000)
        except Exception:  # noqa: BLE001 - any wait failure = dialog still open
            pass
        else:
            return

        # Include via the tiered, locale-safe resolver (a hardcoded English
        # "Add to Prompt" here previously hung non-English accounts — #170/#56).
        include = await VideoGenerationMixin._resolve_include_action(
            page,
            PICKER_INCLUDE_BUTTON,
            _INCLUDE_BUTTON_TIER_NAMES,
            surface=surface,
            detail=detail,
            out_dir=out_dir,
            screenshot_name=f"{surface}_missing.png",
        )
        await include.click(timeout=3000)
        await page.wait_for_timeout(600)

        # Confirm the picker closed — otherwise the include never registered and
        # logging success would be a silent false positive.
        dialog = page.locator(DIALOG_ANY).last
        try:
            await dialog.wait_for(state="hidden", timeout=dialog_timeout_s * 1000)
        except Exception as e:
            raise TransportTimeoutError(
                f"{detail} picker dialog did not close after {dialog_timeout_s}s "
                "(the include action may not have registered)",
            ) from e

    @staticmethod
    def _remote_option_tile(page: Page, name: str) -> Locator:
        """Locate a picker result tile by its display name.

        Matches the option's text via ``has_text`` (a Playwright arg, never a
        CSS string): ``name`` is a stored ``display_name`` or the original
        generation prompt, both of which commonly contain an apostrophe or
        quote that would break a single-quoted ``:has-text()`` CSS selector
        (PR #237 review).
        """
        # Anchored match: a substring match would let 'cabin' select
        # 'cabin at night' and .first attach the wrong image (PR #245 review).
        # #529 live recon: the picker dialog exposes NO accessible tree (the
        # option's computed name is empty), so role+name matching can never
        # find the tile — match the option's text instead. The text carries
        # the picker's localized media-type badge after the display name
        # ('…map\nImagem' on a pt profile); tolerate only a trailing
        # capitalized badge word, which 'at night' is not.
        pattern = re.compile(rf"^{re.escape(name)}(\s?[^\Wa-z0-9_]\S*)?$")
        return page.locator("[role='option']", has_text=pattern)

    @staticmethod
    async def _resolve_frame_slot(
        page: Page,
        slot_index: int,
        label: str,
        *,
        out_dir: Path | None,
        wait_ms: int,
    ) -> Locator:
        """Locate an I2V frame slot, structural-first with a text-label fallback.

        Shared by the local-upload and remote-ref frame-attach paths. The frame
        slots are the dialog-divs inside the swap_horiz container, indexed by
        position (0=start, 1=end); FRAME_SLOT_BY_LABEL (has-text 'Start'/'End',
        English-only) is the fallback when the structural count is insufficient.
        Once an image binds, its slot leaves the structural pattern, so the next
        unfilled slot is `.first` of the remaining matches regardless of index.
        Returns the (unclicked) slot locator; raises RuntimeError with a debug
        screenshot if none resolves.
        """
        structs = page.locator(FRAME_SLOTS_STRUCT)
        try:
            await structs.first.wait_for(state="visible", timeout=wait_ms)
        except Exception as e:
            shot = await _capture_debug_screenshot(
                page, out_dir, f"debug_no_{label.lower()}_slot.png"
            )
            msg = f"frame slot {label!r} not found on the Flow editor.{screenshot_clause(shot)}"
            raise RuntimeError(msg) from e

        struct_count = await structs.count()
        if struct_count > slot_index:
            return structs.nth(slot_index)
        if struct_count > 0:
            return structs.first
        slot = page.locator(FRAME_SLOT_BY_LABEL.format(label=label)).first
        try:
            await slot.wait_for(state="visible", timeout=3000)
        except Exception as e:
            msg = (
                f"frame slot index {slot_index} ({label!r}) not present "
                f"(found {struct_count} structural slot(s), "
                f"text-label fallback also missed)"
            )
            raise RuntimeError(msg) from e
        return slot

    @staticmethod
    async def _attach_remote_frame(
        page: Page,
        slot_index: int,
        label: str,
        name: str,
        *,
        out_dir: Path | None,
        timeout_s: float = 120.0,
    ) -> None:
        """Attach a remote image (by display name) into an I2V frame slot."""
        slot = await VideoGenerationMixin._resolve_frame_slot(
            page, slot_index, label, out_dir=out_dir, wait_ms=12000
        )
        await slot.click()
        await page.wait_for_timeout(1000)  # media dialog opens

        await VideoGenerationMixin._pick_option_and_include(
            page,
            name,
            surface="remote_frame_include",
            detail=f"{label} remote frame",
            out_dir=out_dir,
            dialog_timeout_s=timeout_s,
        )
        log.info("ui_automation_video.remote_frame_attached", slot=label, display_name=name)

    @staticmethod
    async def _attach_frame_by_media_id(
        page: Page,
        slot_index: int,
        label: str,
        media_id: str,
        display_name: str,
        *,
        out_dir: Path | None,
        project_name: str | None = None,
        local_path: Path | None = None,
        local_sha256: str = "",
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Fill the I2V first/last frame slot with an already-existing in-project
        asset without a duplicate upload. The catalog-resolved ``display_name``
        filters the browser picker; ``media_id`` verifies the exact result tile.
        An empty legacy/programmatic name permits only the exact rendered
        viewport tile. If name lookup misses, a catalog-recorded local copy can
        upload the exact asset bytes instead of scanning or scrolling."""
        slot = await VideoGenerationMixin._resolve_frame_slot(
            page, slot_index, label, out_dir=out_dir, wait_ms=12000
        )
        await slot.click()
        await page.wait_for_timeout(1000)  # media dialog opens

        # #287 (live-confirmed): the picker's library view is per-project —
        # align it to the target project BEFORE any lookup. `project_name` is
        # the user's --project-name override for the menu's name-based match.
        await VideoGenerationMixin._sync_picker_project(
            page, out_dir=out_dir, project_name=project_name
        )

        selected = await VideoGenerationMixin._select_existing_asset(
            page, media_id, display_name, out_dir=out_dir, name_resolver=name_resolver
        )
        if not selected:
            if local_path is not None:
                if not matches_recorded_file(local_path, sha256=local_sha256):
                    raise TransportTimeoutError(
                        f"{label} frame asset {media_id!r} local fallback changed "
                        "since it was recorded; refusing to upload different bytes."
                    )
                log.info(
                    "ui_automation_video.frame_ref_upload_fallback",
                    slot=label,
                    media_id=media_id,
                    resolved_by="upload",
                )
                await VideoGenerationMixin._upload_via_open_dialog(
                    page,
                    local_path,
                    log_label=f"{label}_frame_ref",
                    out_dir=out_dir,
                )
                return
            # The frame-slot dialog is the one surface this picker reuse is
            # unproven on (#237's name-search never surfaced generated media
            # here) — capture the dialog state so a live miss is diagnosable.
            shot = await _capture_debug_screenshot(
                page, out_dir, f"debug_frame_ref_miss_{label.lower()}.png"
            )
            msg = (
                f"{label} frame asset {media_id!r} could not be located in the "
                "media picker by its catalog display name — is it in the target "
                "project (missing or wrong --project), and was it recorded by "
                "this profile?"
            )
            if shot is not None:
                msg = f"{msg} Screenshot: {shot}"
            raise TransportTimeoutError(msg)
        log.info(
            "ui_automation_video.frame_ref_attached",
            slot=label,
            media_id=media_id,
            resolved_by="display_name",
        )

    @staticmethod
    async def _upload_via_open_dialog(
        page: Page,
        image: Path,
        *,
        log_label: str,
        out_dir: Path | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        """With a media dialog ALREADY open, upload `image` and commit it into
        the active slot/carousel: 'Upload media' (file chooser) -> wait the
        uploadImage XHR 200 (bytes stored) -> 'Add to Prompt' -> wait the dialog
        to CLOSE (commit registered) before returning, so the caller never
        submits before the image binds. Shared by I2V slots and R2V references."""
        uploaded: list[int] = []  # uploadImage response statuses, in arrival order

        def on_response(response: Any) -> None:
            if UPLOAD_IMAGE_ROUTE in response.url:
                uploaded.append(response.status)
                log.info(
                    "ui_automation_video.image_uploaded",
                    target=log_label,
                    status=response.status,
                )

        page.on("response", on_response)
        try:
            # Tier 1 = locale-agnostic icon selector (primary, every locale).
            # Tier 2 = the original English-text selector (#50) — catches an
            # icon-ligature change on an English profile. If BOTH miss, fail loud
            # with a screenshot. Short per-tier timeouts so two misses can't add
            # up to the silent ~34s hang that #56 was about.
            chooser = None
            last_err: Exception | None = None
            for sel in (UPLOAD_MEDIA_BUTTON, UPLOAD_MEDIA_BUTTON_TEXT):
                try:
                    async with page.expect_file_chooser(timeout=8000) as fc_info:
                        await page.locator(sel).first.click(timeout=4000)
                    chooser = await fc_info.value
                    break
                except Exception as e:
                    last_err = e
            if chooser is None:
                shot = await _capture_debug_screenshot(
                    page,
                    out_dir,
                    f"debug_upload_no_chooser_{log_label}.png",
                )
                msg = (
                    f"Neither the icon nor the text 'Upload media' selector opened a file "
                    f"chooser for {log_label!r} — Google likely changed the media dialog "
                    f"(issue #56). Workaround: set the CHROME BROWSER "
                    f"PROFILE's language to English (chrome://settings/languages). NOTE: "
                    f"this is the Chrome PROFILE language, NOT the Google ACCOUNT language "
                    f"— changing only the Google account language does NOT work, because "
                    f"Flow follows the Chrome profile locale (and the --lang=en-US launch "
                    f"arg cannot override an already-configured profile)."
                    f"{screenshot_clause(shot)}"
                )
                raise RuntimeError(
                    msg,
                ) from last_err
            await chooser.set_files(str(image))
            deadline = time.monotonic() + timeout_s
            while not uploaded and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
            if not uploaded:
                log.warning("ui_automation_video.upload_incomplete", target=log_label)
            else:
                # Fail loud on a rejected upload instead of committing an empty
                # slot that silently falls back to T2V (#125). Typed (#287) so
                # callers get exit 27 + a re-encode hint, not "Unexpected error."
                rejection = _upload_rejection_message(uploaded[-1], log_label)
                if rejection is not None:
                    raise MediaUploadRejectedError(detail=rejection, route=UPLOAD_IMAGE_ROUTE)
        finally:
            page.remove_listener("response", on_response)

        # 'Add to Prompt' = the only iconless button in the open dialog (its text
        # is localized; see the UPLOAD_MEDIA_BUTTON note). Select it structurally.
        add_btn = (
            page.locator(ADD_TO_PROMPT_DIALOG)
            .last.locator("button")
            .filter(has_not=page.locator("i.google-symbols"))
            .last
        )
        if await add_btn.count():
            await add_btn.click()
        try:
            await page.locator(DIALOG_ANY).last.wait_for(state="hidden", timeout=15_000)
        except Exception:
            log.warning("ui_automation_video.dialog_close_timeout", target=log_label)
        await page.wait_for_timeout(1500)

    @staticmethod
    async def _attach_references(
        page: Page,
        images: list[Path],
        *,
        out_dir: Path | None,
        timeout_s: float = 120.0,
        prefer_existing: bool = False,
    ) -> None:
        """R2V: attach up to MAX_REFERENCE_IMAGES reference images. References
        have no Start/End slots — each is added via the 'Add Media' button, which
        opens the same media dialog. Must run with the settings panel closed.

        When ``prefer_existing`` is set (image i2i), each ref is first looked up
        in the open picker by its filename before uploading: Flow names an
        uploaded file by its exact filename, so a repeated local ref is DEDUPED —
        selected in place — instead of re-uploaded, avoiding the duplicate
        library entries of #314. Upload stays the fallback when no library match
        exists (fresh file, or a picker cohort without a search box). Off by
        default so the R2V video path keeps its upload-every-time behaviour."""
        missing = [str(p) for p in images if not p.exists()]
        if missing:
            msg = f"reference image(s) not found: {missing}"
            raise FileNotFoundError(msg)
        attached = 0
        for i, img in enumerate(images):
            add_media = page.locator(ADD_MEDIA_BUTTON).first
            try:
                await add_media.wait_for(state="visible", timeout=8000)
            except Exception as e:
                if i == 0:
                    shot = await _capture_debug_screenshot(page, out_dir, "debug_no_add_media.png")
                    clause = screenshot_clause(shot)
                    msg = f"'Add Media' button not found for first reference.{clause}"
                    raise RuntimeError(
                        msg,
                    ) from e
                # Flow removes the Add-Media button once the per-model reference
                # cap is hit (omni_flash=7, veo_3_1_*=3). The DTO enforces this
                # when the model is known; for an unknown (None) model we only
                # learn the cap here. Proceed with what's attached + warn loudly.
                log.warning(
                    "ui_automation_video.reference_cap_reached",
                    attached=attached,
                    requested=len(images),
                    note="Flow hid 'Add Media' — per-model reference cap reached",
                )
                break
            await add_media.click()
            await page.wait_for_timeout(1000)
            if prefer_existing and await VideoGenerationMixin._try_select_existing_by_filename(
                page, img.name, out_dir=out_dir
            ):
                attached += 1
                log.info("ui_automation_video.reference_attached", index=i, deduped=True)
                continue
            await VideoGenerationMixin._upload_via_open_dialog(
                page,
                img,
                log_label=f"ref{i}",
                out_dir=out_dir,
                timeout_s=timeout_s,
            )
            attached += 1
            log.info("ui_automation_video.reference_attached", index=i)

    @staticmethod
    async def _try_select_existing_by_filename(
        page: Page, filename: str, *, out_dir: Path | None
    ) -> bool:
        """In the OPEN reference picker, try to select an existing library asset
        whose display name equals ``filename`` — dedup instead of re-upload (#314).

        Flow names an uploaded file by its exact filename (live-verified: an
        upload of ``zzdedupprobe.png`` surfaces as a picker option named
        ``zzdedupprobe.png``), and the picker search filters on that name. So a
        repeated local ref can be attached by selecting the existing tile rather
        than uploading a duplicate — without persisting or trusting any media
        UUID across runs.

        Contract: returns ``True`` once a matching asset is attached; ``False``
        when the search + a virtualised-grid scroll surface no match, having
        cleared the filter so the caller's upload path starts from a clean grid.
        A match that is *found but then fails to attach* (the include never
        registers) raises ``TransportTimeoutError`` — a real UI failure is
        surfaced rather than silently uploading a duplicate, matching the
        media-UUID path. Keyed on the exact filename: a name Flow sanitises on
        upload (stored display name then differs) simply misses and falls back to
        upload; verified for ASCII-safe names.
        """
        # The picker library has its OWN active project (#287) — align it to the
        # editor's project so the search sees this project's media, where the
        # duplicate uploads accumulate.
        await VideoGenerationMixin._sync_picker_project(page, out_dir=out_dir)

        search = page.locator(PICKER_SEARCH_INPUT).first
        if not await search.count():
            return False  # no search box (older / #174 full-page cohort) → upload
        await search.fill("")
        # Human-like typing jitter to dodge WAF heuristics — not security.
        await search.press_sequentially(filename, delay=random.randint(10, 50))  # NOSONAR
        await page.wait_for_timeout(600)

        # Match the tile by its image ALT (= the exact filename, locale-invariant).
        # The option's accessible NAME also carries a localised media-type suffix
        # ("Image"/"Imagem"), so an exact name match would miss; the img alt is
        # exactly the filename. get_by_alt_text avoids CSS-quoting a filename.
        tile = (
            page.get_by_role("option").filter(has=page.get_by_alt_text(filename, exact=True)).first
        )
        # The library grid is virtualised (react-virtuoso) — a real match can sit
        # off-screen after a broad search, absent from the DOM until scrolled into
        # range. Scroll (progress-bounded, #287) before concluding there is no
        # existing asset, so a still-existing dup isn't re-uploaded.
        if not await tile.count() and not (
            await VideoGenerationMixin._scroll_picker_grid_until_rendered(page, tile)
        ):
            await search.fill("")  # clear the filter for the upload fallback
            return False

        await VideoGenerationMixin._attach_selected_tile(
            page,
            tile,
            out_dir=out_dir,
            detail=f"filename ref {filename!r}",
            surface="filename_ref_include",
            screenshot_name="filename_ref_include_missing.png",
            dialog_timeout_s=REMOTE_PICKER_CLOSE_TIMEOUT_S,
        )
        log.info("ui_automation_video.reference_deduped_by_filename", filename=filename)
        return True

    @staticmethod
    async def _attach_remote_references(
        page: Page,
        ref_names: list[str],
        *,
        out_dir: Path | None,
    ) -> None:
        """Attach remote images by searching their display_name in the All tab.

        The picker indexes Flow's own short auto-caption, NOT the generation
        prompt, so a name that never matches used to surface as a bare
        Playwright ``TimeoutError`` after 8 s — no indication of what went
        wrong. The raw picker/catalog comparison is recorded in
        ``docs/superpowers/spikes/2026-08-15-picker-tile-alt-text.md``. A miss is
        now a typed, self-documenting error that names the term searched and
        lists what the picker actually offered.
        """
        # playwright is a TYPE_CHECKING-only import at module level; the
        # timeout class is needed at runtime to type the picker miss.
        from playwright.async_api import (  # noqa: PLC0415
            TimeoutError as PlaywrightTimeoutError,
        )

        for name in ref_names:
            add = page.locator(ADD_MEDIA_BUTTON).first
            await add.wait_for(state="visible", timeout=8000)
            await add.click()
            await page.wait_for_timeout(800)

            try:
                await VideoGenerationMixin._pick_option_and_include(
                    page,
                    name,
                    surface="remote_reference_include",
                    detail=f"remote reference {name!r}",
                    out_dir=out_dir,
                    dialog_timeout_s=REMOTE_PICKER_CLOSE_TIMEOUT_S,
                )
            except PlaywrightTimeoutError as exc:
                offered = await VideoGenerationMixin._picker_option_names(page)
                shot = await _capture_debug_screenshot(
                    page, out_dir, "debug_remote_reference_not_found.png"
                )
                raise ReferenceNotFoundError(
                    f"no media named {name!r} in this project's picker. Flow indexes a "
                    f"short auto-caption, not the generation prompt, so passing a prompt "
                    f"as a reference name will not match. "
                    + (
                        f"The picker offered: {offered}."
                        if offered
                        else "The picker listed no selectable media."
                    )
                    + " Pass the asset's media UUID instead, or use --ref with a local file."
                    + screenshot_clause(shot)
                ) from exc
            log.info("ui_automation_video.remote_reference_attached", display_name=name)

    @staticmethod
    async def _picker_option_names(page: Page, limit: int = 12) -> list[str]:
        """Names the open picker is actually offering — for a not-found error.

        Best-effort: a diagnostic must never mask the failure it describes.
        """
        try:
            return await page.evaluate(
                """(n) => [...document.querySelectorAll("[role='option']")]
                       .map(o => (o.getAttribute('aria-label') || o.textContent || '')
                                   .replace(/\\s+/g, ' ').trim())
                       .filter(Boolean).slice(0, n)""",
                limit,
            )
        except Exception:  # noqa: BLE001 - diagnostic only
            return []

    @staticmethod
    def _existing_asset_tile(page: Page, media_id: str) -> Locator:
        """Locate a picker tile for an already-existing asset by its media UUID.

        The tile's thumbnail URL is ``media.getMediaUrlRedirect?name=<uuid>``, so
        matching ``img[src*=<uuid>]`` selects the exact asset with no dependence
        on a display name (robust to name collisions) and no search term.
        """
        return page.locator(f"[role='option']:has(img[src*='{media_id}'])").first

    @staticmethod
    async def _tile_is_fully_visible(tile: Locator) -> bool:
        """Use the browser's intersection engine to prevent implicit click scrolling."""
        try:
            return bool(
                await tile.evaluate(
                    """(el) => new Promise(resolve => {
                      const observer = new IntersectionObserver(([entry]) => {
                        observer.disconnect();
                        resolve(entry.intersectionRatio === 1);
                      }, {threshold: 1});
                      observer.observe(el);
                    })"""
                )
            )
        except Exception:  # noqa: BLE001 - stale/vanished tile is not selectable
            return False

    @staticmethod
    async def _select_existing_asset(
        page: Page,
        media_id: str,
        display_name: str,
        *,
        out_dir: Path | None,
        dialog_timeout_s: float = REMOTE_PICKER_CLOSE_TIMEOUT_S,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> bool:
        """In the OPEN reference picker, select the already-existing Flow asset
        identified by ``media_id`` (preferred over uploading a duplicate).

        Flow's browser picker is name-addressed: search by the catalog-recorded
        ``display_name``, then assert the exact UUID in the surfaced tile's
        thumbnail URL. This handles duplicate names without selecting the wrong
        asset. It deliberately does not scroll the unfiltered virtualised grid
        or type UUID fragments into the name search. A missing/stale name or
        unavailable search input falls through to the
        caller's verified local-file fallback or typed error.

        #546 rename self-healing: on a search MISS, ``name_resolver`` (a sync
        callable, UUID -> current Flow display name or ``None``) is consulted
        once; a non-empty DIFFERENT name triggers exactly one retry search
        (``_search_picker_for_tile`` clears the box before typing). Cached
        name = optimization; listing = truth; UUID = identity. A raising
        resolver is swallowed with a warning and the fallback chain proceeds.

        The resolver is invoked via ``asyncio.to_thread`` so it may block on
        I/O: the CLI bridge blocks its worker thread on a listing fetch it
        schedules back onto THIS loop (``run_coroutine_threadsafe``), which
        only completes because the loop is parked here awaiting the worker.
        """
        if not display_name:
            return False
        tile = VideoGenerationMixin._existing_asset_tile(page, media_id)
        found = await VideoGenerationMixin._search_picker_for_tile(page, tile, display_name)
        if found is False and name_resolver is not None:
            fresh: str | None = None
            try:
                # to_thread: the resolver is a SYNC callable that may block on
                # I/O (the CLI bridge parks its worker thread on a listing
                # fetch scheduled back onto this loop via
                # run_coroutine_threadsafe — see cli_image.wire_refresh_resolver).
                # Calling it inline would deadlock that bridge.
                fresh = await asyncio.to_thread(name_resolver, media_id)
            except Exception:  # noqa: BLE001 - resolver must never kill the generation
                # Log only the media_id — the exception message may carry
                # listing payload fragments (redaction).
                log.warning("ui_automation_video.name_resolver_failed", media_id=media_id)
            if fresh and fresh != display_name:
                # Bounded second pass with the resolver-fresh name — NOT a loop.
                found = await VideoGenerationMixin._search_picker_for_tile(page, tile, fresh)
        if not found:
            # #287 diagnosis telemetry: capture WHAT the picker was showing
            # when the lookup gave up — which attribute carries the media
            # identity, and which project the library view was on, are the
            # key unknowns on a live miss.
            # cast + isinstance: a unit-test fake's page.url may not be a str.
            page_url = cast("object", page.url)
            project_id = extract_project_id(page_url) if isinstance(page_url, str) else None
            # Capture only the filtered result set. When search is unavailable,
            # avoid collecting unrelated assets from the unfiltered picker.
            shot = (
                await _capture_debug_screenshot(
                    page, out_dir, f"debug_picker_miss_{media_id[:8]}.png"
                )
                if found is False
                else None
            )
            dump = (
                await _capture_picker_dom_dump(page, out_dir, media_id, project_id)
                if found is False
                else None
            )
            log.warning(
                "ui_automation_video.existing_asset_not_found",
                media_id=media_id,
                project_id=project_id,
                screenshot=str(shot) if shot is not None else None,
                dom_dump=str(dump) if dump is not None else None,
            )
            if found is False:
                clear_search = page.locator(PICKER_SEARCH_INPUT).first
                await clear_search.fill("")
                await page.wait_for_timeout(400)
            return False

        await VideoGenerationMixin._attach_selected_tile(
            page,
            tile,
            out_dir=out_dir,
            detail=f"image ref {media_id}",
            surface="image_ref_include",
            screenshot_name="image_ref_include_missing.png",
            dialog_timeout_s=dialog_timeout_s,
        )
        return True

    @staticmethod
    async def _attach_selected_tile(
        page: Page,
        tile: Locator,
        *,
        out_dir: Path | None,
        detail: str,
        surface: str,
        screenshot_name: str,
        dialog_timeout_s: float,
    ) -> None:
        """Attach an already-located picker ``tile`` and confirm the dialog closed.

        The image reference picker attaches on tile-click and auto-closes the
        dialog (one step); the video r2v picker instead needs an explicit
        "Add to Prompt" include after selecting. Handle both: click, and if the
        dialog did not auto-close, resolve the locale-safe include button and
        click it, then verify the dialog closed. Shared by the media-UUID
        (:meth:`_select_existing_asset`) and filename
        (:meth:`_try_select_existing_by_filename`) selection paths. Raises
        ``TransportTimeoutError`` if the dialog never closes after the include —
        a tile matched but the attach did not register."""
        await tile.click()
        await page.wait_for_timeout(400)
        dialog = page.locator(DIALOG_ANY).last
        try:
            await dialog.wait_for(state="hidden", timeout=2500)
            return
        except Exception:  # noqa: BLE001 - still open -> needs explicit include
            pass

        include = await VideoGenerationMixin._resolve_include_action(
            page,
            PICKER_INCLUDE_BUTTON,
            _INCLUDE_BUTTON_TIER_NAMES,
            surface=surface,
            detail=detail,
            out_dir=out_dir,
            screenshot_name=screenshot_name,
        )
        await include.click(timeout=3000)
        await page.wait_for_timeout(600)
        try:
            await dialog.wait_for(state="hidden", timeout=dialog_timeout_s * 1000)
        except Exception as e:
            raise TransportTimeoutError(
                f"{detail} picker dialog did not close after {dialog_timeout_s}s "
                "(the include action may not have registered)",
            ) from e

    @staticmethod
    async def _attach_image_uuid_refs(
        page: Page,
        refs: list[tuple[str, str, str, str]],
        *,
        out_dir: Path | None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Attach pre-generated image UUID references for I2I.

        Each ref is ``(media_id, display_name, local_path, local_sha256)``. Prefers selecting
        the already-existing Flow asset in the picker (no duplicate upload —
        the founder principle); uploads ``local_path`` only as a fallback when the
        asset can't be located in place. Raises when neither is possible.
        """
        for media_id, display_name, local_path, local_sha256 in refs:
            add = page.locator(ADD_MEDIA_BUTTON).first
            await add.wait_for(state="visible", timeout=8000)
            await add.click()
            await page.wait_for_timeout(800)

            # #287 (live-confirmed): the picker's library view is per-project
            # — align it to the target project BEFORE this ref's lookup
            # (re-checked per dialog open; the switch itself no-ops when
            # already aligned or when the selector is absent).
            await VideoGenerationMixin._sync_picker_project(page, out_dir=out_dir)

            selected = await VideoGenerationMixin._select_existing_asset(
                page, media_id, display_name, out_dir=out_dir, name_resolver=name_resolver
            )
            if selected:
                log.info(
                    "ui_automation_video.image_ref_selected_existing",
                    media_id=media_id,
                    resolved_by="display_name",
                )
                continue

            if local_path:
                path = Path(local_path)
                if not matches_recorded_file(path, sha256=local_sha256):
                    raise TransportTimeoutError(
                        f"image ref {media_id!r} local fallback changed since it was "
                        "recorded; refusing to upload different bytes."
                    )
                log.info(
                    "ui_automation_video.image_ref_upload_fallback",
                    media_id=media_id,
                    resolved_by="upload",
                )
                await VideoGenerationMixin._upload_via_open_dialog(
                    page, path, log_label="image_ref", out_dir=out_dir
                )
                continue

            raise TransportTimeoutError(
                f"image ref {media_id!r} could not be selected in the picker and "
                "has no local file to upload — re-generate it or pass a local path.",
            )

    @staticmethod
    async def _sync_picker_project(
        page: Page, *, out_dir: Path | None = None, project_name: str | None = None
    ) -> None:
        """#287 CONFIRMED (live round 2): the media picker's library view has
        its OWN active project — ``--project`` only navigates the EDITOR — so
        the picker can open on a different project, making the target asset
        unreachable no matter how deep the grid is scrolled. Derives the
        target project from the editor URL (``--project`` navigation put it
        there), resolves its display NAME (the picker menu lists projects by
        NAME, not id — round 4 dump), and aligns the picker's project
        selector. ``project_name`` is the user-supplied override
        (``--project-name`` / ``GFLOW_CLI_PROJECT_NAME``) and takes precedence
        over page-derived resolution. No-op when the URL carries no project id
        or the picker has no project selector (older cohort)."""
        # cast + isinstance: a unit-test fake's page.url may not be a str.
        page_url = cast("object", page.url)
        project_id = extract_project_id(page_url) if isinstance(page_url, str) else None
        if not project_id:
            log.info(
                "ui_automation_video.picker_project_sync_skipped",
                reason="no_project_in_url",
            )
            return
        if project_name:
            log.info(
                "ui_automation_video.picker_project_name_override",
                project_id=project_id,
                name=project_name,
            )
            resolved_name: str | None = project_name
        else:
            resolved_name = await VideoGenerationMixin._resolve_project_name(page, project_id)
        await VideoGenerationMixin._ensure_picker_project(
            page, project_id, project_name=resolved_name, out_dir=out_dir
        )

    @staticmethod
    def _strip_flow_branding(title: str) -> str:
        """Strip Flow branding from a tab title, tolerantly (#287 round 5):
        try the suffix/prefix separator variants; fall back to the raw title.
        The raw title is logged on every resolution, so the real live pattern
        is learnable and this list can be tightened from evidence."""
        cleaned = title.strip()
        # 'Google Flow' first (longer, more specific) — the round-5 live run
        # observed 'Google Flow - <project name>'.
        for brand in ("Google Flow", "Flow"):
            for sep in (" - ", " – ", " — ", " | "):
                suffix = f"{sep}{brand}"
                if cleaned.endswith(suffix):
                    return cleaned[: -len(suffix)].strip()
                prefix = f"{brand}{sep}"
                if cleaned.startswith(prefix):
                    return cleaned[len(prefix) :].strip()
        return cleaned

    @staticmethod
    async def _resolve_project_name(page: Page, project_id: str) -> str | None:
        """Resolve the target project's display NAME (#287 rounds 2/5): the
        picker's project menu lists projects by NAME (unnamed projects show
        only creation timestamps), and the CLI only knows the UUID. Tier 0:
        the editor tab title (document.title — the editor was navigated to
        /project/<id> before the picker opened), stripped of Flow branding
        and rejected when branding-only. Tier 1: an element whose href
        references the project id. Tier 2: a project-title-classed element.
        The ``--project-name`` override in `_sync_picker_project` beats all
        of this. The local catalog's ``projects.title`` was considered but
        the transport layer has no catalog access, and the live page also
        reflects renames and non-gflow projects. Best-effort: ``None`` when
        nothing usable is found."""
        raw: object = None
        try:
            raw = await page.evaluate(_PROJECT_NAME_FROM_PAGE_JS, project_id)
        except Exception:  # noqa: BLE001 - name resolution is best-effort
            raw = None
        raw_title = ""
        candidates: list[tuple[str, str]] = []
        if isinstance(raw, dict):
            data = cast("dict[str, object]", raw)
            raw_title = str(data.get("title") or "").strip()
            title_name = VideoGenerationMixin._strip_flow_branding(raw_title)
            if title_name and title_name.lower() not in _FLOW_BRANDING_TITLES:
                candidates.append(("title", title_name))
            for source_key, source in (("href_text", "href"), ("class_text", "class")):
                text = str(data.get(source_key) or "").strip()
                if text:
                    candidates.append((source, text))
        if candidates:
            source, name = candidates[0]
            log.info(
                "ui_automation_video.picker_project_name_resolved",
                project_id=project_id,
                name=name,
                source=source,
                raw_title=raw_title,
            )
            return name
        log.info(
            "ui_automation_video.picker_project_name_unresolved",
            project_id=project_id,
            raw_title=raw_title,
        )
        return None

    @staticmethod
    async def _wait_project_menu_open(page: Page) -> bool:
        """Whether the (portal-rendered) project menu is open — Radix stamps
        `data-state='open'` on the visible [role='menu'] (#287 round 2)."""
        menu = page.locator(PICKER_PROJECT_MENU_OPEN).last
        try:
            await menu.wait_for(state="visible", timeout=2000)
        except Exception:  # noqa: BLE001 - not-open is an expected branch
            return False
        return True

    @staticmethod
    async def _wait_project_menu_populated(page: Page) -> int:
        """Poll the OPEN portal menu for element children (#287 round 3: the
        open-state flips before the project list populates — matching or
        dumping too early sees an empty portal and is a guaranteed miss).
        Bounded at PICKER_PROJECT_MENU_POLLS x PICKER_PROJECT_MENU_POLL_MS
        (~3s); stops as soon as any elements render. Returns the final
        element count (0 = never populated / probe failed)."""
        elements = 0
        for _ in range(PICKER_PROJECT_MENU_POLLS):
            raw: object = None
            try:
                raw = await page.evaluate(_PICKER_PROJECT_MENU_CHILD_COUNT_JS)
            except Exception:  # noqa: BLE001 - poll probe is best-effort
                raw = None
            elements = int(raw) if isinstance(raw, (int, float)) else 0
            if elements > 0:
                break
            await page.wait_for_timeout(PICKER_PROJECT_MENU_POLL_MS)
        return elements

    @staticmethod
    async def _match_project_option(
        page: Page, match_args: dict[str, str | None]
    ) -> tuple[bool, str | None, int]:
        """One pass of the option-match JS over the open portal. Returns
        ``(clicked, matched_by, candidate_count)`` — best-effort, never
        raises (a failed probe is a miss)."""
        try:
            outcome_raw: object = await page.evaluate(_PICKER_PROJECT_OPTION_MATCH_JS, match_args)
        except Exception:  # noqa: BLE001 - dropdown scan is best-effort
            return False, None, 0
        if not isinstance(outcome_raw, dict):
            return False, None, 0
        outcome = cast("dict[str, object]", outcome_raw)
        matched_by_value = outcome.get("matched_by")
        candidates_value = outcome.get("candidates")
        return (
            bool(outcome.get("clicked")),
            str(matched_by_value) if matched_by_value else None,
            int(candidates_value) if isinstance(candidates_value, (int, float)) else 0,
        )

    @staticmethod
    async def _scroll_project_menu_and_match(
        page: Page, match_args: dict[str, str | None]
    ) -> tuple[bool, str | None, int]:
        """#287 round 5: the project menu is the full recency-ordered project
        list (80 items observed) — the target's entry can sit below the
        visible fold or outside a virtualised window. Scroll the open portal
        with the progress-bounded pattern: scroll one step, re-match, keep
        going while the rendered item set still changes, stall-terminate
        after ``PICKER_GRID_SCROLL_STALL_LIMIT`` no-progress scrolls, hard
        ceiling at ``PICKER_PROJECT_MENU_SCROLL_MAX``. Same return shape as
        `_match_project_option`."""
        stalls = 0
        previous: frozenset[str] | None = None
        attempts = 0
        reason = "ceiling"
        clicked = False
        matched_by: str | None = None
        candidates = 0
        while attempts < PICKER_PROJECT_MENU_SCROLL_MAX:
            raw: object = None
            try:
                raw = await page.evaluate(
                    _PICKER_PROJECT_MENU_SCROLL_JS, PICKER_PROJECT_MENU_SCROLL_DELTA_PX
                )
            except Exception:  # noqa: BLE001 - scroll probe is best-effort
                raw = None
            attempts += 1
            fingerprint: frozenset[str] | None = None
            if isinstance(raw, list) and raw:
                fingerprint = frozenset(str(item) for item in cast("list[object]", raw))
            await page.wait_for_timeout(PICKER_PROJECT_MENU_SCROLL_SETTLE_MS)
            clicked, matched_by, candidates = await VideoGenerationMixin._match_project_option(
                page, match_args
            )
            log.info(
                "ui_automation_video.picker_project_menu_scroll_probe",
                attempt=attempts,
                items=len(fingerprint) if fingerprint is not None else None,
                new_items=(
                    len(fingerprint - previous)
                    if fingerprint is not None and previous is not None
                    else None
                ),
            )
            if clicked:
                reason = "found"
                break
            if fingerprint is None:
                reason = "no_menu"
                break
            if fingerprint == previous:
                stalls += 1
                if stalls >= PICKER_GRID_SCROLL_STALL_LIMIT:
                    reason = "stall"
                    break
            else:
                stalls = 0
                previous = fingerprint
        log.info(
            "ui_automation_video.picker_project_menu_scroll_done",
            reason=reason,
            attempts=attempts,
            found=clicked,
        )
        return clicked, matched_by, candidates

    @staticmethod
    async def _ensure_picker_project(
        page: Page,
        project_id: str,
        *,
        project_name: str | None = None,
        out_dir: Path | None = None,
    ) -> bool | None:
        """Align the OPEN picker's library view to ``project_id`` (#287).

        Live round 2 confirmed the mechanics: the trigger is a Radix
        `ProjectDropdownSubTrigger` rendering the ACTIVE project's name; the
        submenu options render project NAMES in a portal. Sequence: probe the
        trigger cascade; consider the target active when the trigger matches
        by id or resolved name; otherwise open the submenu (click, then hover,
        then focus+ArrowRight — Radix SubTriggers may ignore plain click),
        verify `[role='menu'][data-state='open']`, poll for the portal to
        POPULATE (round 3: the open-state flips before the list renders), and
        click the innermost candidate matching by href-with-id, id-in-markup,
        then normalized name (round 3: the portal had ZERO classic menu-item
        ARIA roles, so generic clickables are swept). Returns ``None`` when
        the picker has no project selector (older cohort — pure no-op),
        ``True`` when the target project is (already) active, ``False`` when a
        selector exists but the target could not be selected — a miss dumps
        the open portal's raw bounded innerHTML (+ child count and tag
        histogram) to the out-dir, presses Escape (never leave an open
        overlay), and lets callers proceed: the asset lookup stays the
        authority."""
        trigger = None
        matched_selector: str | None = None
        for selector in PICKER_PROJECT_SELECTOR_TRIGGERS:
            candidate = page.locator(selector).first
            if await candidate.count():
                trigger = candidate
                matched_selector = selector
                break
        if trigger is None:
            log.info(
                "ui_automation_video.picker_project_selector_absent",
                project_id=project_id,
            )
            return None
        match_args = {"projectId": project_id, "projectName": project_name}
        already_active = False
        try:
            already_active = bool(
                await trigger.evaluate(_PICKER_PROJECT_TRIGGER_ACTIVE_JS, match_args)
            )
        except Exception:  # noqa: BLE001 - probe is best-effort; fall through to a switch
            already_active = False
        if already_active:
            log.info(
                "ui_automation_video.picker_project_already_active",
                project_id=project_id,
                selector=matched_selector,
            )
            return True

        # Open the Radix submenu: click, then hover, then keyboard — each
        # verified against the portal-rendered open-state menu (#287 round 2:
        # SubTriggers open on hover/ArrowRight; plain click may be a no-op).
        opened = False
        open_method = "none"
        await trigger.click()
        if await VideoGenerationMixin._wait_project_menu_open(page):
            opened, open_method = True, "click"
        if not opened:
            try:
                await trigger.hover(timeout=1000)
            except Exception:  # noqa: BLE001 - hover unsupported -> keyboard tier
                pass
            if await VideoGenerationMixin._wait_project_menu_open(page):
                opened, open_method = True, "hover"
        if not opened:
            try:
                await trigger.focus(timeout=1000)
                await page.keyboard.press("ArrowRight")
            except Exception:  # noqa: BLE001 - focus unsupported -> match attempt anyway
                pass
            if await VideoGenerationMixin._wait_project_menu_open(page):
                opened, open_method = True, "keyboard"
        log.info(
            "ui_automation_video.picker_project_menu_opened",
            project_id=project_id,
            opened=opened,
            method=open_method,
        )
        # #287 round 3: the portal populates AFTER the open-state flips —
        # matching an empty portal was a guaranteed miss. Poll (bounded)
        # before matching or dumping.
        menu_elements = await VideoGenerationMixin._wait_project_menu_populated(page)
        log.info(
            "ui_automation_video.picker_project_menu_populated",
            project_id=project_id,
            elements=menu_elements,
        )

        clicked, matched_by, candidates = await VideoGenerationMixin._match_project_option(
            page, match_args
        )
        if not clicked:
            # #287 round 5: the target's entry may be below the menu's fold —
            # scroll the open portal (progress-bounded) and re-match.
            (
                clicked,
                matched_by,
                candidates,
            ) = await VideoGenerationMixin._scroll_project_menu_and_match(page, match_args)
        if not clicked:
            # Raw open-portal dump (#287 round 4): role-filtered item lists
            # blinded us twice — capture bounded raw markup instead.
            portal: object = None
            try:
                portal = await page.evaluate(_PICKER_PROJECT_MENU_DUMP_JS)
            except Exception:  # noqa: BLE001 - dump probe is best-effort
                portal = None
            menu_dump = _write_project_menu_dump(
                out_dir,
                project_id,
                {
                    "project_id": project_id,
                    "project_name": project_name,
                    "menu_opened": opened,
                    "menu_elements": menu_elements,
                    "candidates": candidates,
                    "portal": portal,
                },
            )
            log.warning(
                "ui_automation_video.picker_project_switch_miss",
                project_id=project_id,
                project_name=project_name,
                selector=matched_selector,
                menu_opened=opened,
                menu_elements=menu_elements,
                candidates=candidates,
                menu_dump=str(menu_dump) if menu_dump is not None else None,
                note="no portal candidate matched the target project by href, id, or name",
            )
            # Never leave an open overlay on the pooled Page.
            await page.keyboard.press("Escape")
            return False
        await page.wait_for_timeout(800)
        log.info(
            "ui_automation_video.picker_project_switched",
            project_id=project_id,
            selector=matched_selector,
            matched_by=matched_by,
        )
        return True

    @staticmethod
    async def _search_picker_for_tile(page: Page, tile: Locator, term: str) -> bool | None:
        """Type ``term`` into the picker search box and report whether it
        surfaced ``tile``. Clears any previous term first so repeated searches
        don't concatenate (``press_sequentially`` appends). Returns ``None``
        (without touching the page) when this picker variant has no search
        input at all (#174's full-page media-library drift) — search must
        never become a hard dependency."""
        search = page.locator(PICKER_SEARCH_INPUT).first
        try:
            await search.wait_for(state="visible", timeout=4000)
        except Exception:  # noqa: BLE001 - absent search is a supported picker cohort
            log.info(
                "ui_automation_video.picker_search_unavailable",
                term_length=len(term),
            )
            return None
        await search.fill("")
        # Human-like typing jitter to dodge WAF heuristics — not security.
        await search.press_sequentially(term, delay=random.randint(10, 50))  # NOSONAR
        await page.wait_for_timeout(800)
        rendered = await VideoGenerationMixin._picker_grid_fingerprint(page)
        found = True
        try:
            await tile.wait_for(state="visible", timeout=6000)
            found = await VideoGenerationMixin._tile_is_fully_visible(tile)
        except Exception:  # noqa: BLE001 - not surfaced by this term
            found = False
        log.info(
            "ui_automation_video.picker_search_tier",
            term_length=len(term),
            found=found,
            rendered_tiles=len(rendered) if rendered is not None else None,
        )
        return found

    @staticmethod
    async def _scroll_picker_grid(
        page: Page, delta_px: int = PICKER_GRID_SCROLL_DELTA_PX
    ) -> dict[str, object] | None:
        """Scroll the open resource picker down one step. The Tudo grid is
        virtualised, so off-screen tiles are absent from the DOM until
        scrolled into view. #287 round 6: react-virtuoso scrolls its own
        container, so the primary path drives the dialog's ACTUAL scrollable
        node via JS and returns evidence (tag/class + scrollTop before/after
        — a no-op scroll where scrollTop never moves is then visible in
        telemetry). Falls back to the blind hover+wheel when the JS probe
        fails, returning ``None`` (no evidence)."""
        info_raw: object = None
        try:
            info_raw = await page.evaluate(_PICKER_GRID_SCROLL_JS, delta_px)
        except Exception:  # noqa: BLE001 - JS probe is best-effort
            info_raw = None
        if isinstance(info_raw, dict):
            await page.wait_for_timeout(350)
            return cast("dict[str, object]", info_raw)
        dialog = page.locator(DIALOG_ANY).last
        try:
            await dialog.hover(timeout=2000)
        except Exception:  # noqa: BLE001 - hover is best-effort; wheel still scrolls
            pass
        await page.mouse.wheel(0, delta_px)
        await page.wait_for_timeout(350)
        return None

    @staticmethod
    async def _picker_grid_fingerprint(page: Page) -> frozenset[str] | None:
        """Identifiers of the tiles currently rendered in the open picker, or
        ``None`` when there is no evidence (the probe failed, or zero tiles
        matched — e.g. a DOM drift), so the scroll loop falls back to the
        legacy fixed budget instead of misreading 'no evidence' as 'end of
        grid'."""
        try:
            tile_ids = await page.evaluate(_PICKER_GRID_TILE_IDS_JS)
        except Exception:  # noqa: BLE001 - probe is best-effort evidence only
            return None
        if not isinstance(tile_ids, list) or not tile_ids:
            return None
        return frozenset(str(tile_id) for tile_id in cast("list[object]", tile_ids))

    @staticmethod
    async def _scroll_picker_grid_until_rendered(page: Page, tile: Locator) -> bool:
        """Scroll the virtualised picker grid until ``tile`` is in the DOM.

        #287: bounded by evidence of progress, not a fixed attempt count —
        keeps scrolling while the set of rendered tile identifiers still
        CHANGES between scrolls (the grid is still advancing) and stops after
        ``PICKER_GRID_SCROLL_STALL_LIMIT`` consecutive scrolls with no new
        tiles (end of grid), so the reachable depth is proportional to the
        grid size. When the DOM probe yields no evidence the legacy fixed
        ``PICKER_GRID_SCROLL_ATTEMPTS`` budget applies, and
        ``PICKER_GRID_SCROLL_MAX_ATTEMPTS`` is a hard safety ceiling either
        way. Returns whether the tile ended up in the DOM — including a tile
        rendered by the very last scroll (#283 off-by-one: the count runs
        BEFORE each scroll, so it is re-checked once after the loop)."""
        attempts = 0
        stalls = 0
        previous: frozenset[str] | None = None
        reason = "ceiling"
        while attempts < PICKER_GRID_SCROLL_MAX_ATTEMPTS:
            if await tile.count():
                log.info(
                    "ui_automation_video.picker_scroll_done",
                    reason="found",
                    attempts=attempts,
                    found=True,
                )
                return True
            scroll_info = await VideoGenerationMixin._scroll_picker_grid(page)
            attempts += 1
            rendered = await VideoGenerationMixin._picker_grid_fingerprint(page)
            log.info(
                "ui_automation_video.picker_scroll_probe",
                attempt=attempts,
                rendered_tiles=len(rendered) if rendered is not None else None,
                new_tiles=(
                    len(rendered - previous)
                    if rendered is not None and previous is not None
                    else None
                ),
                # #287 round 6 audit: WHICH node scrolled, and did it move at
                # all — a wrong-node no-op (scrollTop frozen) is now visible.
                scrolled_tag=scroll_info.get("tag") if scroll_info is not None else None,
                scrolled_class=scroll_info.get("cls") if scroll_info is not None else None,
                scroll_top_before=scroll_info.get("before") if scroll_info is not None else None,
                scroll_top_after=scroll_info.get("after") if scroll_info is not None else None,
            )
            if rendered is None:
                if attempts >= PICKER_GRID_SCROLL_ATTEMPTS:
                    reason = "legacy_budget"
                    break
            elif rendered == previous:
                stalls += 1
                if stalls >= PICKER_GRID_SCROLL_STALL_LIMIT:
                    reason = "stall"
                    break
            else:
                stalls = 0
                previous = rendered
        found = bool(await tile.count())
        log.info(
            "ui_automation_video.picker_scroll_done",
            reason=reason,
            attempts=attempts,
            found=found,
        )
        return found

    @staticmethod
    async def _find_picker_entity_tile(page: Page, entity_id: str) -> Locator:
        """Locate the Personagens-tab tile for a character entity. Each tile is
        keyed by the entity id as `data-tile-id="fe_id_<entityId>"` (exact — no
        display-name ambiguity). Scroll the grid until it renders (#287:
        progress-bounded, so a crowded character grid is fully reachable),
        then return the locator (the caller still waits for visibility)."""
        tile = page.locator(f"[data-tile-id='fe_id_{entity_id}']").first
        await VideoGenerationMixin._scroll_picker_grid_until_rendered(page, tile)
        return tile

    @staticmethod
    async def _resolve_include_action(
        page: Page,
        tiers: tuple[str, ...],
        tier_names: tuple[str, ...],
        *,
        surface: str,
        detail: str,
        out_dir: Path | None,
        screenshot_name: str,
    ) -> Locator:
        """Probe the include-action selector tiers IN ORDER; return the first
        visible match.

        Issue #170: a single localized has-text selector broke every
        non-Portuguese account. Tiers are probed sequentially (not flattened
        into one comma list — comma lists resolve in DOM order, not tier
        priority) and the matched tier is logged so a dead locale-free tier
        silently carried by the text fallback stays observable.

        On exhaustion: capture a screenshot, press Escape twice (context menu
        + picker dialog — a Page must never return to the pool with an open
        overlay), and raise a typed, locale-neutral
        :class:`TransportTimeoutError` so the CLI surfaces the remediation
        hint with exit 9 instead of the privacy-hashed 'Unexpected error.'.
        """
        last_exc: Exception | None = None
        for tier, selector in zip(tier_names, tiers, strict=True):
            loc = page.locator(selector).first
            try:
                await loc.wait_for(state="visible", timeout=4000)
            except Exception as e:  # noqa: BLE001 — try the next tier
                last_exc = e
                continue
            log.info(
                "ui_automation_video.include_selector_tier",
                surface=surface,
                tier=tier,
            )
            return loc
        shot = await _capture_debug_screenshot(page, out_dir, screenshot_name)
        await page.keyboard.press("Escape")
        await page.keyboard.press("Escape")
        raise TransportTimeoutError(
            f"{detail} include action did not appear (no selector tier "
            f"matched on surface {surface!r}).{screenshot_clause(shot)}",
            remediation_hint=(
                "Flow's resource picker may have changed, or your account's "
                "UI language is not yet covered by the selector fallbacks. "
                "Report the visible menu/button captions (plus the screenshot) "
                "at https://github.com/ffroliva/gflow-cli/issues."
            ),
        ) from last_exc

    @staticmethod
    async def _attach_character_entities(
        page: Page,
        entities: list[tuple[str, str]],
        *,
        out_dir: Path | None,
    ) -> None:
        """R2V: attach each character as a `referenceEntity` via the resource
        picker's Personagens tab.

        Mechanism (verified credit-free via route-abort payload capture,
        2026-06-06): open 'Add Media' -> Personagens tab -> RIGHT-CLICK the entity
        tile -> the context-menu include action (`add`-ligature menu item; the
        caption is localized, e.g. 'Incluir no comando' on pt-BR). This stages
        `referenceEntities:[{entityId}]` on the submit payload. A LEFT-click on a
        Tudo-tab tile + the inline include button instead stages a plain
        `referenceImage` (the character thumbnail) — which the submit backstop
        (`_assert_entities_attached`) correctly rejects. A plain left-click on the
        Personagens tile navigates into the character editor (it is an
        `<a href=.../character/...>`), hence the right-click.

        `entities` is a list of `(entity_id, display_name)` pairs. Tiles are
        addressed by entity id (`data-tile-id="fe_id_<entityId>"`), so selection
        is unambiguous even when several characters share a display name.
        """
        for entity_id, name in entities:
            add = page.locator(ADD_MEDIA_BUTTON).first
            await add.wait_for(state="visible", timeout=8000)
            await add.click()
            await page.wait_for_timeout(800)
            ptab = page.locator(PICKER_PERSONAGENS_TAB).first
            await ptab.wait_for(state="visible", timeout=8000)
            await ptab.click(force=True)
            await page.wait_for_timeout(700)
            tile = await VideoGenerationMixin._find_picker_entity_tile(page, entity_id)
            await tile.wait_for(state="visible", timeout=8000)
            await tile.scroll_into_view_if_needed(timeout=8000)
            await tile.click(button="right")
            await page.wait_for_timeout(400)
            include = await VideoGenerationMixin._resolve_include_action(
                page,
                PICKER_CONTEXT_INCLUDE,
                _CONTEXT_INCLUDE_TIER_NAMES,
                surface="context_menu",
                detail=f"character {name!r} ({entity_id})",
                out_dir=out_dir,
                screenshot_name="debug_entity_ctx_menu.png",
            )
            await include.click()
            await page.wait_for_timeout(600)
            log.info(
                "ui_automation_video.character_entity_attached",
                name=name,
                entity_id=entity_id,
            )

    @staticmethod
    async def _attach_reference_audio(
        page: Page,
        voice_id: str,
        *,
        out_dir: Path | None,
    ) -> None:
        """R2V: attach a voice resource via the Vozes picker -> include button."""
        add = page.locator(ADD_MEDIA_BUTTON).first
        await add.wait_for(state="visible", timeout=8000)
        await add.click()
        await page.wait_for_timeout(800)
        await page.locator(PICKER_VOZES_TAB).first.click()
        await page.wait_for_timeout(400)
        await page.locator(PICKER_SEARCH_INPUT).first.fill(voice_id)
        await page.wait_for_timeout(600)
        # Role+name match (apostrophe-safe, mirroring _remote_option_tile) — a
        # voice_id with a quote would break a single-quoted :has-text() selector.
        tile = (
            page.get_by_role("option", name=voice_id)
            .or_(page.get_by_role("button", name=voice_id))
            .first
        )
        await tile.click()
        await page.wait_for_timeout(300)
        include = await VideoGenerationMixin._resolve_include_action(
            page,
            PICKER_INCLUDE_BUTTON,
            _INCLUDE_BUTTON_TIER_NAMES,
            surface="vozes_button",
            detail=f"voice {voice_id!r}",
            out_dir=out_dir,
            screenshot_name="debug_voice_include.png",
        )
        await include.click()
        await page.wait_for_timeout(600)
        log.info("ui_automation_video.reference_audio_attached", voice=voice_id)

    @staticmethod
    def _collect_entity_ids_from_response_shape(body: dict[str, Any]) -> list[str]:
        """Extract entityIds from the live response shape.

        Path: ``media[].mediaMetadata.requestData.videoGenerationRequestData
                   .videoGenerationEntityInputs[].entityId``
        """
        ids: list[str] = []
        for media in cast(_JsonObjList, body.get("media") or []):
            meta = cast(_JsonObj, media.get("mediaMetadata") or {})
            req_data = cast(_JsonObj, meta.get("requestData") or {})
            vgrd = cast(_JsonObj, req_data.get("videoGenerationRequestData") or {})
            for e in cast(_JsonObjList, vgrd.get("videoGenerationEntityInputs") or []):
                entity_id = cast("str | None", e.get("entityId"))
                if entity_id:
                    ids.append(entity_id)
        return ids

    @staticmethod
    def _collect_entity_ids_from_request_shape(body: dict[str, Any]) -> list[str]:
        """Extract entityIds from the request-body shape (fallback).

        Path: ``requests[].referenceEntities[].entityId``
        """
        ids: list[str] = []
        for r in cast(_JsonObjList, body.get("requests") or []):
            for e in cast(_JsonObjList, r.get("referenceEntities") or []):
                entity_id = cast("str | None", e.get("entityId"))
                if entity_id:
                    ids.append(entity_id)
        return ids

    @staticmethod
    def _assert_entities_attached(generate_resp: dict[str, Any], *, expected: list[str]) -> None:
        """Defense-in-depth: confirm the character entities actually rode the wire.

        A UI attach miss would degrade to a text/image-only clip reported as a
        success. Raise loudly instead.

        The captured SUBMIT *response* echoes the accepted entities at:

            media[].mediaMetadata.requestData.videoGenerationRequestData
                  .videoGenerationEntityInputs[].entityId

        NOT ``requests[].referenceEntities`` — that is the *request* shape; the
        response re-keys it (verified against a live capture, 2026-06-06; an
        earlier version checked the request path against the response body and
        false-rejected every successful entity generation). The request-shape
        path is still accepted so the check also works against a request body.
        """
        if not expected:
            return
        body = cast(_JsonObj, generate_resp.get("body") or {})
        got = VideoGenerationMixin._collect_entity_ids_from_response_shape(
            body
        ) + VideoGenerationMixin._collect_entity_ids_from_request_shape(body)
        missing = [e for e in expected if e not in got]
        if missing:
            raise WireFormatError(
                detail=(
                    f"character entities not echoed in submit response (expected "
                    f"{expected}, got {got}); entity attach failed - refusing to "
                    f"report success"
                ),
                route="video:batchAsyncGenerateVideoReferenceImages",
                remediation_hint=ENTITY_ATTACH_DRIFT_HINT,
                discovery={"entity_attach_context": "video"},
            )

    @staticmethod
    async def _select_video_aspect(page: Page, aspect: Aspect) -> None:
        """Click the aspect-ratio tab for `aspect` in the open mode dropdown.
        Non-fatal on miss — generation proceeds with Flow's default ratio."""
        candidates = VIDEO_ASPECT_TAB_SELECTORS.get(aspect)
        if candidates is None:
            log.warning("ui_automation_video.aspect_unsupported", aspect=aspect.value)
            return
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "video_aspect_tab",
            candidates,
        )
        if tab is None:
            log.warning("ui_automation_video.aspect_not_set", aspect=aspect.value)
            return
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.aspect_set", aspect=aspect.value)

    @staticmethod
    async def _await_generate_response(
        captured: _JsonObjList,
        *,
        timeout_s: float = 180.0,
        poll_interval_s: float = 0.5,
    ) -> dict[str, Any]:
        """Wait for the first captured batchAsyncGenerateVideo* response."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not captured:
            await asyncio.sleep(poll_interval_s)
        if not captured:
            msg = (
                f"no batchAsyncGenerateVideo* response within {timeout_s:.0f}s — "
                "did the submit fire? did reCAPTCHA fail silently?"
            )
            raise TimeoutError(
                msg,
            )
        return captured[0]

    async def generate_video(
        self,
        *,
        request: GenerateVideoRequest,
        project_id: str | None = None,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> VideoResult:
        """Generate ONE video by driving the Flow editor UI (T2V / I2V / R2V).

        If ``project_id`` is provided, navigates to that project. Otherwise
        creates a new one.

        Returns a `VideoResult` carrying both the terminal `VideoStatus` and the
        on-disk `local_path` (``None`` when ``download=False`` or the generation
        failed — callers should check ``result.status.succeeded`` first). Raises
        `RuntimeError` (no setup / editor control missing), `ValueError` (SQUARE
        aspect), `FileNotFoundError` (I2V/R2V image path missing),
        `AuthExpiredError` (401), `WafRejectionError` (403), `WireFormatError`
        (other non-200 / no media), or `TimeoutError`.

        ``on_started`` is called with a :class:`VideoStarted` as soon as the
        media_id is known (before polling completes) so the recorder can insert
        a STARTED row even if the long poll later fails.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before generate_video()"
            raise RuntimeError(
                msg,
            )
        if request.aspect is Aspect.SQUARE:
            msg = (
                "video generation does not support the SQUARE aspect; "
                "use PORTRAIT (9:16) or LANDSCAPE (16:9)"
            )
            raise ValueError(
                msg,
            )
        async with self._generate_lock:
            return await self._generate_video_locked(
                request,
                project_id=project_id,
                out_dir=out_dir,
                poll_timeout_s=poll_timeout_s,
                download=download,
                on_started=on_started,
                name_resolver=name_resolver,
            )

    @staticmethod
    def _parse_generate_response(
        generate_resp: dict[str, Any],
    ) -> tuple[str, str | None]:
        """Validate HTTP status, extract media_name and flow_operation_id.

        Raises AuthExpiredError, WafRejectionError, or WireFormatError on bad
        status codes or missing media id. Returns (media_name, flow_operation_id).
        """
        http_status = generate_resp.get("status")
        url = str(generate_resp.get("url", ""))
        # errors.py documents `route` as a sanitized route NAME, not a URL.
        route = next((r for r in VIDEO_GENERATE_ROUTES if r in url), "video:generate")
        if http_status == 401:
            raise AuthExpiredError(
                detail="batchAsyncGenerateVideo* returned HTTP 401 — session expired",
                status=401,
                route=route,
            )
        if http_status == 403:
            raise WafRejectionError(
                detail="batchAsyncGenerateVideo* returned HTTP 403 — WAF / reCAPTCHA rejection",
                status=403,
                route=route,
            )
        if http_status == 429:
            # #379 gave the image path a 429 branch; video never got one, so a
            # quota hit surfaced as "unexpected response shape" (#528).
            retry_after = parse_retry_after(generate_resp)
            raise RateLimitError(
                detail=(
                    "batchAsyncGenerateVideo* returned HTTP 429 — rate limit hit."
                    + (f" Retry after {retry_after:.0f}s." if retry_after is not None else "")
                ),
                status=429,
                route=route,
                retry_after=retry_after,
            )
        if http_status != 200:
            # #528: same misclassification as the image path — a 400 here is a
            # content-policy refusal, not a malformed request.
            raise generation_error(
                status=http_status if isinstance(http_status, int) else -1,
                route=route,
                body=generate_resp.get("body") or {},
            )
        # A video 200 ALWAYS carries media[0] (the asset slot — capture 02);
        # content rejection surfaces later as a FAILED *status*, not empty media.
        # So a missing media[0] here is a genuine wire anomaly — WireFormatError.
        body: dict[str, Any] = cast(_JsonObj, generate_resp.get("body") or {})
        try:
            media_name = media_name_from_generate_response(body)
        except ValueError as e:
            # discovery carries only route + top-level KEY NAMES (not values).
            raise WireFormatError(
                detail=f"video generate response carries no media id: {e}",
                route=route,
                discovery={"route": route, "top_level_keys": sorted(body)},
            ) from e
        # Stored SEPARATELY from media_name even when they currently match —
        # spec explicitly keeps them distinct for future divergence.
        flow_operation_id: str | None = operation_name_from_generate_response(body)
        return media_name, flow_operation_id

    @staticmethod
    async def _run_stage(
        coro: Any,
        *,
        stage: str,
        page: Page,
        out_dir: Path | None,
        timeout_s: float,
    ) -> Any:
        """Await *coro* under a named wall-clock deadline.

        Every UI probe in this transport carries its own per-selector timeout,
        so a stage that blows a whole-stage budget is not "slow Flow" — it is a
        Playwright call that stopped honouring its deadline, so the per-probe
        timer never fires. Left alone that presents as an unbounded SILENT
        hang: browser alive, no error, no further log line, no indication of
        which stage owns the wait.

        Known cause (2026-08-03): an **unpinned playwright** — an install that
        resolved 1.62.0 against a project locked to 1.59.0 wedged every
        ``video i2v`` run immediately after the frame upload. ``uv tool
        install <path>`` ignores ``uv.lock``, so a local/tool install silently
        picks the newest version the pyproject range allows; the range is now
        upper-bounded. The version check lives in the error message rather
        than in a preflight assert because a future driver regression may
        present the same way on an in-range version — the watchdog is the
        general net, the pin is the specific fix.

        On expiry this raises :class:`TransportTimeoutError` naming the stage,
        after a best-effort screenshot taken under its own short deadline (the
        page is by definition not answering, so an unbounded capture here would
        re-hang the code path meant to end the hang).
        """
        log.info("ui_automation_video.stage_started", stage=stage, timeout_s=timeout_s)
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(coro, timeout=timeout_s)
        except TimeoutError as e:
            shot: Path | None = None
            with contextlib.suppress(Exception):
                shot = await asyncio.wait_for(
                    _capture_debug_screenshot(page, out_dir, f"debug_stage_stalled_{stage}.png"),
                    timeout=STAGE_TIMEOUT_SHOT_S,
                )
            pw_version = _playwright_version()
            log.error(
                "ui_automation_video.stage_stalled",
                stage=stage,
                timeout_s=timeout_s,
                playwright_version=pw_version,
                screenshot=str(shot) if shot else None,
            )
            msg = (
                f"the {stage!r} stage did not complete within {timeout_s:.0f}s. "
                f"Every probe inside it is individually bounded, so this means "
                f"the browser stopped responding to automation — the run was "
                f"aborted instead of hanging silently. Nothing was submitted, "
                f"so no credit was spent. FIRST THING TO CHECK: your installed "
                f"playwright is {pw_version}; gflow is tested against "
                f"{SUPPORTED_PLAYWRIGHT_RANGE}. An out-of-range playwright is "
                f"the known cause of this exact stall (a 1.62.0 install against "
                f"a 1.59.0-locked project wedged every i2v run right after the "
                f"frame upload). `uv tool install <path>` IGNORES uv.lock, so "
                f"reinstall pinned: "
                f"`uv tool install --force --with playwright=={PINNED_PLAYWRIGHT} .`"
                f"{screenshot_clause(shot)}"
            )
            raise TransportTimeoutError(detail=msg) from e
        log.info(
            "ui_automation_video.stage_completed",
            stage=stage,
            elapsed_s=round(time.monotonic() - started, 2),
        )
        return result

    @staticmethod
    async def _fire_on_started(
        on_started: VideoStartedCallback,
        started: VideoStarted,
    ) -> None:
        """Invoke the on_started callback, awaiting it if it returns a coroutine."""
        import inspect

        result_or_coro = on_started(started)
        if inspect.isawaitable(result_or_coro):
            await result_or_coro

    @staticmethod
    def _resolve_i2v_model(
        request: GenerateVideoRequest,
        is_i2v_with_frames: bool,
    ) -> VideoModel | None:
        """Validate and resolve the effective model for an I2V request.

        Raises ``ModelModeIncompatibilityError`` when the model/frame
        combination is unsupported: no current model rejects a START frame
        (omni-flash re-verified on the wire 2026-08-03, refs #125), but an END
        frame requires :meth:`VideoModel.supports_i2v_end_frame` (omni-flash:
        Flow lists first+last as "coming soon" — no wire proof). Returns the
        effective model to use — defaults to ``I2V_DEFAULT_MODEL`` when
        ``is_i2v_with_frames`` and no model is set.
        """
        has_end_ref = (
            request.end_image is not None
            or request.end_image_ref_id is not None
            or request.end_image_ref_name is not None
        )
        if (
            is_i2v_with_frames
            and request.model is not None
            and has_end_ref
            and not request.model.supports_i2v_end_frame()
        ):
            log.error(
                "ui_automation_video.model_mode_rejected",
                model=request.model.value,
                mode=request.mode.name,
                has_start_image=request.start_image is not None,
                has_end_image=has_end_ref,
                issue_ref="#125",
            )
            raise ModelModeIncompatibilityError(
                detail=(
                    f"{request.model.value!r} does not support this i2v frame "
                    f"combination: an END frame (first+last interpolation) "
                    f"requires a Veo 3.1 model — Flow lists it as 'coming "
                    f"soon' for omni-flash and gflow has no wire-level proof "
                    f"of that route (refs #125). Drop the end frame, or use a "
                    f"Veo 3.1 model (e.g. veo-lite)."
                ),
            )
        effective_model = request.model
        if is_i2v_with_frames and effective_model is None:
            effective_model = I2V_DEFAULT_MODEL
            log.info(
                "ui_automation_video.i2v_model_defaulted",
                model=effective_model.value,
                issue_ref="#125",
            )
        return effective_model

    @staticmethod
    async def _attach_i2v_frames(
        page: Page,
        request: GenerateVideoRequest,
        *,
        out_dir: Path | None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Attach the Start (and optional End) I2V frame: local path, in-project
        asset UUID (#287), or remote display-name ref."""
        if request.start_image is not None:
            await VideoGenerationMixin._attach_frame(
                page, 0, "Start", request.start_image, out_dir=out_dir
            )
        elif request.start_image_ref_id is not None:
            await VideoGenerationMixin._attach_frame_by_media_id(
                page,
                0,
                "Start",
                request.start_image_ref_id,
                request.start_image_ref_display_name,
                out_dir=out_dir,
                project_name=request.project_name,
                local_path=request.start_image_ref_local_path,
                local_sha256=request.start_image_ref_local_sha256,
                name_resolver=name_resolver,
            )
        elif request.start_image_ref_name is not None:
            await VideoGenerationMixin._attach_remote_frame(
                page, 0, "Start", request.start_image_ref_name, out_dir=out_dir
            )
        if request.end_image is not None:
            await VideoGenerationMixin._attach_frame(
                page, 1, "End", request.end_image, out_dir=out_dir
            )
        elif request.end_image_ref_id is not None:
            await VideoGenerationMixin._attach_frame_by_media_id(
                page,
                1,
                "End",
                request.end_image_ref_id,
                request.end_image_ref_display_name,
                out_dir=out_dir,
                project_name=request.project_name,
                local_path=request.end_image_ref_local_path,
                local_sha256=request.end_image_ref_local_sha256,
                name_resolver=name_resolver,
            )
        elif request.end_image_ref_name is not None:
            await VideoGenerationMixin._attach_remote_frame(
                page, 1, "End", request.end_image_ref_name, out_dir=out_dir
            )

    @staticmethod
    async def _attach_r2v_references(
        page: Page, request: GenerateVideoRequest, *, out_dir: Path | None
    ) -> None:
        """Attach R2V character entities, local reference images, remote refs, and audio."""
        if request.reference_entities:
            await VideoGenerationMixin._attach_character_entities(
                page,
                zip_entity_refs(request.reference_entities, request.reference_entity_names),
                out_dir=out_dir,
            )
        if request.reference_images:
            await VideoGenerationMixin._attach_references(
                page, list(request.reference_images), out_dir=out_dir
            )
        if request.ref_names:
            await VideoGenerationMixin._attach_remote_references(
                page, list(request.ref_names), out_dir=out_dir
            )
        if request.reference_audio:
            await VideoGenerationMixin._attach_reference_audio(
                page, request.reference_audio, out_dir=out_dir
            )

    @staticmethod
    async def _attach_media_inputs(
        page: Page,
        request: GenerateVideoRequest,
        *,
        out_dir: Path | None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Attach I2V frames or R2V references/entities to the editor before submit."""
        if request.mode is Mode.I2V:
            await VideoGenerationMixin._attach_i2v_frames(
                page, request, out_dir=out_dir, name_resolver=name_resolver
            )
        elif request.mode is Mode.R2V:
            await VideoGenerationMixin._attach_r2v_references(page, request, out_dir=out_dir)

    async def _submit_and_poll(
        self,
        page: Page,
        request: GenerateVideoRequest,
        is_i2v_with_frames: bool,
        effective_model: VideoModel | None,
        project_id: str | None,
        out_dir: Path | None,
        poll_timeout_s: float,
        download: bool,
        on_started: VideoStartedCallback | None,
        ui_driver: Any,
    ) -> VideoResult:
        """Submit the prompt, await and validate the generate response, then poll."""
        generate_captured, generate_handler = VideoGenerationMixin._attach_video_response_listener(
            page
        )
        status_captured, status_handler = VideoGenerationMixin._attach_status_response_listener(
            page
        )
        generate_resp: dict[str, Any] = {}
        expected_ents = set(request.reference_entities)
        try:
            async with self._intercept_reference_entities(page, expected_ents):
                # Watchdog: this is the window that hung silently after
                # `frame_attached` — overlay dismissal + prompt-box lookup +
                # typing + submit, all of which are individually bounded.
                await VideoGenerationMixin._run_stage(
                    ui_driver.send_prompt(page, request.prompt, out_dir=out_dir),
                    stage="send_prompt",
                    page=page,
                    out_dir=out_dir,
                    timeout_s=SUBMIT_STAGE_TIMEOUT_S,
                )
                generate_resp = await VideoGenerationMixin._await_generate_response(
                    generate_captured
                )

            # Layer-2 backstop (issue #125): for i2v, the request MUST have
            # routed to a Start/StartAndEndImage endpoint.
            if is_i2v_with_frames:
                captured_url = str(generate_resp.get("url") or "")
                if _T2V_GENERATE_ROUTE in captured_url:
                    log.error(
                        "ui_automation_video.i2v_routed_to_t2v",
                        url=captured_url,
                        model=(effective_model.value if effective_model else None),
                        issue_ref="#125",
                    )
                    raise WireFormatError(
                        detail=(
                            "i2v request routed to the T2V endpoint "
                            f"({_T2V_GENERATE_ROUTE}); Flow dropped the start/end "
                            "frames and produced a text-only video (issue #125). "
                            "The credit was spent but the output is not an "
                            "interpolation — refusing to report success."
                        ),
                        route=_T2V_GENERATE_ROUTE,
                    )

            if request.reference_entities:
                VideoGenerationMixin._assert_entities_attached(
                    generate_resp, expected=list(request.reference_entities)
                )

            media_name, flow_operation_id = VideoGenerationMixin._parse_generate_response(
                generate_resp
            )

            if on_started is not None:
                started = VideoStarted(
                    media_id=media_name,
                    project_id=project_id,
                    flow_operation_id=flow_operation_id,
                )
                await VideoGenerationMixin._fire_on_started(on_started, started)

            status = await VideoGenerationMixin._poll_video_status(
                page,
                status_captured,
                media_name,
                timeout_s=poll_timeout_s,
            )
            local_path = (
                await self._download_video(status.media_id, out_dir, page)
                if download and status.succeeded
                else None
            )
            return VideoResult(
                status=status,
                local_path=local_path,
                project_id=project_id,
                flow_operation_id=flow_operation_id,
            )
        finally:
            # The Page is pooled and persistent — remove both listeners so they
            # never leak across calls.
            page.remove_listener("response", generate_handler)
            page.remove_listener("response", status_handler)

    async def _generate_video_locked(
        self,
        request: GenerateVideoRequest,
        *,
        project_id: str | None = None,
        out_dir: Path | None,
        poll_timeout_s: float,
        download: bool,
        on_started: VideoStartedCallback | None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> VideoResult:
        """Serialized body of `generate_video` — runs under `self._generate_lock`
        (shared with `generate_images`: one Page, one DOM)."""
        is_i2v_with_frames = request.mode is Mode.I2V and any(
            (
                request.start_image,
                request.start_image_ref_name,
                request.start_image_ref_id,
                request.end_image,
                request.end_image_ref_name,
                request.end_image_ref_id,
            )
        )
        # Defense-in-depth model/mode guard (issue #125). Pure DTO check — runs
        # BEFORE any browser interaction so a bad combination fails instantly
        # with no DOM state mutated and no credit risk.
        effective_model = VideoGenerationMixin._resolve_i2v_model(request, is_i2v_with_frames)

        page: Page = self._page  # type: ignore[assignment]  # guarded in generate_video

        await self._enter_editor(page, out_dir, project_id=project_id)
        await VideoGenerationMixin._wait_video_editor_ready(page)
        # Dismiss any Flow changelog / "What's new" overlay that may be on top
        # of the editor before we click into mode-switch / settings / submit (#26).
        await self._dismiss_blocking_overlays(page, out_dir)

        # #299: the video path binds through the mode policy like images do —
        # get_ui_driver switches to the required arm, VERIFIES via a DOM
        # re-probe, and fails fast pre-submit (UiModeUnavailableError, exit 28)
        # when the arm is unreachable, so an agentic cohort flip costs $0
        # instead of a mid-flow selector-drift failure. The video pipeline only
        # has a classic driver, so every request clamps to classic-required:
        # ``auto`` ≡ classic until an agentic video driver exists, and an
        # env-sourced ``agentic`` (set for image workflows) degrades to classic
        # with a warning — only the explicit ``--ui-mode agentic`` flag
        # rejects, at the CLI edge (exit 2). The bind runs AFTER the editor
        # mounts + overlays clear: the policy probes the live DOM.
        from gflow_cli.api.transports.drivers.factory import (  # noqa: PLC0415
            get_ui_driver,
        )
        from gflow_cli.config import UiMode, resolve_ui_mode  # noqa: PLC0415

        base_mode = request.ui_mode if request.ui_mode is not None else resolve_ui_mode(None)
        if base_mode is UiMode.AGENTIC:
            log.warning(
                "ui_automation_video.ui_mode_agentic_clamped",
                requested=base_mode.value,
                bound="classic",
                reason="no agentic video driver exists; classic required",
            )
        ui_driver = await get_ui_driver(page, ui_mode=UiMode.CLASSIC, transport=self)
        # The CLASSIC recovery inside the bind may have RELOADED the page (the
        # sanctioned rescue re-rolls the cohort) — a fresh load can re-mount
        # the #26 overlay and drop editor hydration, so re-run both guards
        # before the driver starts clicking. Cheap no-ops when nothing
        # reloaded.
        await VideoGenerationMixin._wait_video_editor_ready(page)
        await self._dismiss_blocking_overlays(page, out_dir)
        await ui_driver.switch_to_video_mode(page, out_dir=out_dir)

        # Capture project_id from the editor URL as soon as we have it —
        # needed for VideoStarted provenance and recorded before the generate request.
        # Falls back to the caller-supplied id when the URL carries none.
        project_id = extract_project_id(page.url) or project_id

        # All settings-panel selections happen while the panel is open: model
        # (gates the 10s duration), sub-mode tab, aspect, count, duration.
        # For i2v, model selection is REQUIRED (required=True): a silent miss
        # would run i2v on whatever model Flow last had selected, breaking the
        # request contract (cost tier, duration cap, end-frame capability —
        # refs #125), so _select_video_model raises here — before any frame
        # attach or submit, spending no credit.
        await ui_driver.configure_video_settings(page, request, out_dir=out_dir)

        # Attach images AFTER the panel is closed — the slots / 'Add Media' button
        # live in the main editor. This is what makes Flow fire StartImage /
        # StartAndEndImage / ReferenceImages instead of the plain Text route.
        await VideoGenerationMixin._attach_media_inputs(
            page, request, out_dir=out_dir, name_resolver=name_resolver
        )

        # Submit prompt, await generate response, validate, and poll for status.
        return await self._submit_and_poll(
            page,
            request,
            is_i2v_with_frames,
            effective_model,
            project_id,
            out_dir,
            poll_timeout_s,
            download,
            on_started,
            ui_driver,
        )
