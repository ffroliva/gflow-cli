"""D.2.4 UiAutomationTransport — Playwright persistent-context driver for Flow.

Empirically validated 2026-05-12: mirrors the proven CG Worker pattern
(``scripts/smoke_worker_style.py``). Playwright manages its own internal CDP
port, the strategy reuses a pre-authenticated profile dir, and prompts are
submitted by typing into Flow's editor — the same surface a human developer
uses on a Pro/Ultra plan. ``batchGenerateImages`` responses are captured via
``page.on("response")`` and parsed for image URLs.

Implementation arrives in per-method TDD units; this skeleton pins the
Protocol contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import structlog

from gflow_cli.api.character import CHARACTER_MODELS, CharacterImageRequest
from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports._common import extract_project_id
from gflow_cli.api.transports.drivers.factory import AGENT_TUNE_INDICATOR_SELECTOR
from gflow_cli.api.transports.ui_automation_video import (
    COMPOSER_AGENT_TOGGLE_SELECTOR,
    ENTITY_ATTACH_DRIFT_HINT,
    MODE_SWITCH_TRIGGER_SELECTORS,
    VideoGenerationMixin,
    screenshot_clause,
    selector_drift_detail,
    zip_entity_refs,
)
from gflow_cli.errors import (
    AuthExpiredError,
    BatchPartialError,
    ContentPolicyError,
    GFlowError,
    UiSelectorDriftError,
    WafRejectionError,
    WireFormatError,
)
from gflow_cli.profile_lease import ProfileLease

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from playwright.async_api import Locator, Page, ViewportSize

    from gflow_cli.api.transports.base import TransportSetup

# Lazy-imported at call time so ``import gflow_cli`` doesn't pay the
# Playwright import cost when another transport is selected.
try:  # pragma: no cover — re-bound at module import in production
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover — Playwright is an install dependency
    async_playwright = None  # type: ignore[assignment]

log = structlog.get_logger(__name__)

# Type aliases for JSON-shaped ``cast(...)`` targets. Extracted so the quoted
# cast strings are not duplicated (SonarCloud S1192); a module-level alias keeps
# ruff's TC006 happy since the call sites pass a bare name, not a subscript.
_JsonObj = dict[str, Any]
_AnyList = list[Any]

# Flow editor entrypoint — ``?hl=en`` locks locale for selector stability.
FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"
# URL fragment that distinguishes the project editor from the gallery.
_PROJECT_URL_FRAGMENT = "/project/"

# Image model picker (SOT flow-editor-map.json). Same arrow_drop_down trigger as
# video; options matched by product name.  Cascade discipline: Tier 1 (structural
# / ARIA) before Tier 2 (text).  The product names "Nano Banana 2", "Nano Banana
# Pro", and "Imagen 4" are Google-branded model identifiers that Flow does not
# localise across locales — the has-text() entries are therefore locale-stable.
# Primary locale control is ``locale=locale_env`` in launch_persistent_context
# (Playwright kwarg — persists across all in-session navigations including
# /project/<uuid> deep-links that drop the ?hl= param).  FLOW_URL's ``?hl=en``
# reinforces English on the initial load.  'Nano Banana 2' is not a substring of
# 'Nano Banana Pro', so has-text is unambiguous across the three.
# Tier 1 (structural) slots are reserved for data-* / aria-* anchors once a DOM
# probe via scripts/dev/capture_locale_invariants.py confirms stable attributes.
IMAGE_MODEL_PICKER_TRIGGER = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('arrow_drop_down'))"
)
IMAGE_MODEL_OPTION_SELECTORS: dict[Model, tuple[str, ...]] = {
    Model.NARWHAL: ("[role='menuitem']:has-text('Nano Banana 2')",),
    Model.GEM_PIX_2: ("[role='menuitem']:has-text('Nano Banana Pro')",),
    Model.IMAGEN_3_5: ("[role='menuitem']:has-text('Imagen 4')",),
}

# Image-mode tab inside the mode-switch dropdown.  Selectors are tried in
# order; the leading ``aria-controls`` matches are language-independent
# (Flow's accessibility wiring keeps the IMAGE token across locales),
# the ``has-text`` variants are Portuguese/English fallbacks, and the
# icon-ligature is a last resort.  Mirror of
# :data:`ui_automation_video.VIDEO_TAB_IN_MENU_SELECTORS`.
IMAGE_TAB_IN_MENU_SELECTORS = (
    "[role='menu'] [role='tab'][aria-controls*='IMAGE']",
    "[role='tab'][aria-controls*='IMAGE']",
    "[role='menu'] [role='tab']:has-text('Imagem')",
    "[role='menu'] [role='tab']:has-text('Image')",
    "[role='menu'] [role='tab']:has(i:text('image'))",
)

# Browser viewport — 1920×1080, the most common real desktop resolution (#315).
# Enlarged from 1280×800 to reduce the static-fingerprint signal; bigger stays in
# Flow's desktop layout (smaller would cross the responsive breakpoint and drift the
# selectors). NOTE: the sibling CG Worker assumes the old size — reconcile there
# separately. The REST client's own viewport
# (client.py, 1280×720) is an independent, selector-irrelevant context and is unchanged.
_VIEWPORT = {"width": 1920, "height": 1080}

# Hosts allowed when downloading generated PNGs. Flow's fifeUrl currently
# resolves to lh3.googleusercontent.com; the broader allow-list covers
# Google-owned redirect targets without leaking session cookies elsewhere.
# Suffix-match: "googleusercontent.com" matches "lh3.googleusercontent.com".
_ALLOWED_DOWNLOAD_HOST_SUFFIXES: tuple[str, ...] = (
    "googleusercontent.com",
    "googleapis.com",
    "google.com",
    # Agentic cohort: the tRPC redirect URL (media.getMediaUrlRedirect) is
    # same-origin with labs.google — session cookies authorise the download.
    "labs.google",
)


def _is_allowed_download_host(url: str) -> bool:
    """True if ``url``'s host ends with one of the allowed Google domains.

    Refuses URLs that lack a host or use a non-https scheme — both shapes
    are unexpected for Flow-issued fifeUrls and treating them as suspect
    is safer than treating them as trustworthy.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_DOWNLOAD_HOST_SUFFIXES
    )


async def _capture_debug_screenshot(
    page: Any,
    out_dir: Path | None,
    filename: str,
) -> Path | None:
    """Best-effort viewport screenshot for debug troubleshooting.

    Writes to ``out_dir / filename`` and returns the path, or ``None``
    when ``out_dir`` is not provided. Captures only the current viewport
    (``full_page=False``) to bound the PII surface — even a viewport
    screenshot of a logged-in Flow page includes the user's avatar /
    email indicator in the top-right corner, so a warning is logged so
    the operator knows the file may contain identifying information.

    Failures during screenshot capture are swallowed — debugging aids
    must not become a second source of exceptions during a real failure.
    """
    if out_dir is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_path = out_dir / filename
    try:
        await page.screenshot(path=str(shot_path), full_page=False)
        log.warning(
            "ui_automation.debug_screenshot_may_contain_pii",
            path=str(shot_path),
            note=(
                "viewport may include account avatar / email indicator from "
                "the authenticated Google session"
            ),
        )
    except Exception as e:
        log.debug("ui_automation.screenshot_capture_failed", error=str(e))
        return None  # never report a path that was not written (#283)
    return shot_path


# Prompt input selectors — Slate.js editor is the canonical target on
# Flow's editor page; the contenteditable/textarea fallbacks cover UI
# evolutions.
PROMPT_INPUT_SELECTORS = (
    'div[role="textbox"][data-slate-editor="true"]',
    'div[contenteditable="true"]',
    "textarea",
    '[aria-label*="prompt"]',
)

# Submit button selectors — all entries are locale-stable.
# The ``arrow_forward`` ligature is a Material Symbols icon name (not a UI
# label), so it renders identically regardless of the Chrome profile locale.
# Use :text() inside :has() (not :has-text() which is invalid inside :has()).
SUBMIT_BUTTON_SELECTORS = (
    "button:has(i.google-symbols:text('arrow_forward'))",
    "button:has(i:text('arrow_forward'))",
    "button:has-text('arrow_forward')",
)

# Agent-mode activation. Clicking the in-composer "Agent" toggle
# (:data:`COMPOSER_AGENT_TOGGLE_SELECTOR`) flips the classic composer into the
# agentic chat layout — the ``crop_*`` media trigger disappears and the ``tune``
# settings gear + ``expand_content`` open button appear. Captured live
# 2026-06-14 via ``scripts/e2e/capture_agent_toggle.py`` (cropPresent true→false,
# tune false→true). Reached via ``GFLOW_CLI_UI_MODE=agentic`` (or an inferred
# agentic requirement) to drive the agentic path regardless of the
# server-assigned A/B cohort (which has no client-readable flag).
AGENT_EXPAND_BUTTON_SELECTOR = "button:has(i.google-symbols:text-is('expand_content'))"

# _force_agent_mode: after clicking the Agent toggle, POLL for the ``tune``
# indicator instead of a single fixed sleep — the agentic composer renders a
# beat after the click and a fixed-delay check races that render, so the probe
# bound classic ~50% of the time and instructions silently no-op'd (#267).
_AGENT_ACTIVATE_TIMEOUT_S = 6.0
_AGENT_ACTIVATE_POLL_MS = 300
_AGENT_FORCE_MAX_CLICKS = 2

# Self-contained, locale-independent triptych instruction for body generation.
# Live-verified 2026-06-02: produces a consistent front/side/back body image in ONE
# generation, seeded by the auto-attached face reference. We replace Flow's own
# (localized) pre-filled template with this rather than depending on reading/parsing it.
_BODY_TRIPTYCH_PREAMBLE = (
    "Full-body triptych in three angles: front, side (3/4), and back. "
    "High resolution, uniform studio lighting, consistent anatomical proportions "
    "across all angles, solid white background. "
)

# "+ New project" CTA selectors.  Cascade discipline: structural / icon-first
# (locale-stable) before localised text fallbacks spanning all 14 supported
# locales.  The ``add_2`` Material Symbols ligature is locale-invariant — the
# icon-class tier is tried first so this selector works even when the Chrome
# profile runs in a non-English locale.  The ``[role='button']`` ARIA-role
# variant of the icon anchor covers host elements that are not ``<button>``
# tags.  The anchored ``^\+\s+\S+$`` regex matches "+ <single-word>" buttons
# only — anchoring prevents matching e.g. "+ Filter" or "+ Add member" rows
# that contain extra words.  Text variants are ordered by onboarding-locale
# list (same 14 as ``ONBOARDING_SELECTORS``).
NEW_PROJECT_SELECTORS = (
    # Tier 1 — structural / icon: locale-invariant.
    "button:has(i.google-symbols:text('add_2'))",
    "button:has(i:text('add_2'))",
    "[role='button']:has(i.google-symbols:text('add_2'))",
    r"button:text-matches('^\+\s+\S+$', 'i')",
    # Tier 2 — localised text: 14 locales (EN / PT / ES / FR / DE / IT / NL /
    # JA / ZH / KO / PL / RU / TR / ID).
    "button:has-text('New project')",  # EN
    "button:has-text('Novo projeto')",  # PT
    "button:has-text('Nuevo proyecto')",  # ES
    "button:has-text('Nouveau projet')",  # FR
    "button:has-text('Neues Projekt')",  # DE
    "button:has-text('Nuovo progetto')",  # IT
    "button:has-text('Nieuw project')",  # NL
    "button:has-text('新しいプロジェクト')",  # JA
    "button:has-text('新建项目')",  # ZH (Simplified)
    "button:has-text('새 프로젝트')",  # KO
    "button:has-text('Nowy projekt')",  # PL
    "button:has-text('Новый проект')",  # RU
    "button:has-text('Yeni proje')",  # TR
    "button:has-text('Proyek baru')",  # ID
)

# Onboarding bypass — cookie banners, GDPR consent dialogs, landing-page CTAs.
# Cascade discipline: structural/ARIA anchors (Tier 1) before localised text
# (Tier 2).  Every entry is best-effort: _bypass_onboarding swallows misses.
#
# Tier 1 — structural / ARIA: locale-invariant regardless of the Chrome
# profile's display language.
#   • Google Funding Choices / GDPR consent SDK sets button#L2AGLb and
#     aria-label="Accept all" / aria-label="I agree" in English even when
#     the *button text* is translated — these are programmatic SDK constants,
#     not UI strings.
_ONBOARDING_STRUCTURAL_SELECTORS: tuple[str, ...] = (
    "button#L2AGLb",  # Google consent SDK "Accept all"
    "button[aria-label='Accept all']",  # consent SDK ARIA name (exact)
    "button[aria-label='I agree']",  # consent SDK ARIA name (exact)
)

# Tier 2 — localised text / language-dependent ARIA: extends coverage to
# the 14 locales most likely to be used with Flow.  Not locale-invariant,
# but maximises the fallback surface for users who hit onboarding before
# entering the editor.  Includes two case-insensitive ARIA-partial entries
# (`aria-label*='Accept' i` / `*='Agree' i`) at the head: these match many
# CMP dialogs (OneTrust, Cookiebot) whose aria-label values stay in English
# even on non-EN pages, but English ARIA values are not guaranteed across
# every CMP so they live here rather than in the strict structural tier.
_ONBOARDING_TEXT_SELECTORS: tuple[str, ...] = (
    # English-language ARIA partial catches (OneTrust, Cookiebot, etc. often
    # use English aria-label values even on non-EN pages, but this is not
    # guaranteed, so these belong here rather than in the structural tier).
    "button[aria-label*='Accept' i]",  # broader CMP ARIA catch (en)
    "button[aria-label*='Agree' i]",  # broader CMP ARIA catch (en)
    # EN
    "button:has-text('Accept all')",
    "button:has-text('Agree')",
    "button:has-text('I agree')",
    "button:has-text('Accept')",
    "button:has-text('Create with Flow')",
    "button:has-text('Get Started')",
    # PT (Brasil / Portugal)
    "button:has-text('Aceitar tudo')",
    "button:has-text('Aceitar')",
    "button:has-text('Concordo')",
    "button:has-text('Criar com o Flow')",
    "button:has-text('Começar')",
    # DE
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Zustimmen')",
    "button:has-text('Ich stimme zu')",
    "button:has-text('Loslegen')",
    # ES
    "button:has-text('Aceptar todo')",
    "button:has-text('Aceptar')",
    "button:has-text('Acepto')",
    "button:has-text('Comenzar')",
    # FR
    "button:has-text('Tout accepter')",
    "button:has-text('Accepter')",
    'button:has-text("J\'accepte")',
    "button:has-text('Commencer')",
    # IT
    "button:has-text('Accetta tutto')",
    "button:has-text('Accetto')",
    "button:has-text('Inizia')",
    # NL
    "button:has-text('Alles accepteren')",
    "button:has-text('Akkoord')",
    # JA
    "button:has-text('すべて同意する')",
    "button:has-text('同意する')",
    "button:has-text('はじめる')",
    # ZH (Simplified)
    "button:has-text('全部接受')",
    "button:has-text('接受')",
    "button:has-text('开始使用')",
    # KO
    "button:has-text('모두 동의')",
    "button:has-text('동의')",
    "button:has-text('시작하기')",
    # PL
    "button:has-text('Zaakceptuj wszystko')",
    "button:has-text('Zgadzam się')",
    # RU
    "button:has-text('Принять всё')",
    "button:has-text('Принять')",
    # TR
    "button:has-text('Tümünü kabul et')",
    "button:has-text('Kabul et')",
    # ID (Indonesian)
    "button:has-text('Setujui semua')",
    "button:has-text('Setujui')",
)

# Combined public tuple — structural first, text fallbacks after.
# Import this; use _ONBOARDING_STRUCTURAL_SELECTORS / _ONBOARDING_TEXT_SELECTORS
# only when testing cascade ordering.
ONBOARDING_SELECTORS: tuple[str, ...] = (
    *_ONBOARDING_STRUCTURAL_SELECTORS,
    *_ONBOARDING_TEXT_SELECTORS,
)

# Changelog / "What's new" iframe selectors — these src patterns match the
# gstatic CDN paths Flow uses for its release-note overlays. Two patterns are
# included: the /flow/ prefix form and the bare /changelogs/ form.
CHANGELOG_IFRAME_SELECTORS = (
    "iframe[src*='/flow/changelogs/']",
    "iframe[src*='/changelogs/']",
)

# Close-button selectors tried after a changelog iframe is detected.
# Ordered from most-specific to most-generic so a precise match wins first.
# All are tried before the Escape fallback.
OVERLAY_CLOSE_BUTTON_SELECTORS = (
    "[aria-label='Close']",
    "[aria-label='close']",
    "[aria-label='Dismiss']",
    "[aria-label='dismiss']",
    "[aria-label='Cancel']",
    "button:has(i.google-symbols:text('close'))",
    "button:has(i:text('close'))",
    "[role='dialog'] button:has(i:text('close'))",
    "[role='dialog'] button[aria-label*='close' i]",
    "button[data-dismiss]",
    "button:has-text('See what's new')",
)

# Detectors for the "Welcome to Google Flow" splash screen.
WELCOME_SCREEN_SELECTORS = (
    "div:has-text('Welcome to Google Flow')",
    "button:has-text('See what's new')",
)


# Generation settings trigger — the SAME unified button as the mode-switch
# dropdown: a ``button[aria-haspopup='menu']`` carrying the current ratio
# ``crop_*`` icon. The ``aria-haspopup='menu'`` qualifier is REQUIRED — without
# it any icon-only aspect thumbnail in the just-opened panel can match, causing
# a click on the wrong element and a ``gen_settings_panel_not_found`` skip that
# leaves Flow's own default count in effect (typically 2 concurrent requests
# billed while the CLI downloads only 1). Aliased to
# ``MODE_SWITCH_TRIGGER_SELECTORS`` so the two call sites stay a single source
# of truth (see [[image-video-mode-switch-symmetry]]).
GEN_SETTINGS_BUTTON_SELECTORS = MODE_SWITCH_TRIGGER_SELECTORS

# CLI string → ordered list of candidate tab labels to try in the Flow gen
# settings panel. Most ratios are labelled with their colon-numeric form
# ("16:9"), but the "1:1" tab is sometimes rendered as "Square" or "1×1"
# (multiplication sign U+00D7) — we try a small cascade and the first
# locator that becomes visible wins.
_ASPECT_TAB_CANDIDATES: dict[str, tuple[str, ...]] = {
    "16:9": ("16:9",),
    "9:16": ("9:16",),
    "1:1": ("1:1", "Square", "1×1", "1x1"),
    "4:3": ("4:3",),
    "3:4": ("3:4",),
}

# Supported image-count values for the xN selector.
_SUPPORTED_COUNTS: frozenset[int] = frozenset({1, 2, 3, 4})

# Number of count tabs Flow renders in the settings panel (1 through 4).
_COUNT_TAB_COUNT = 4

# Structured event name emitted at every exit path of `_set_count`.
# Extracted to a module-level constant to satisfy SonarCloud S1192
# (duplicate literal) and keep the spelling consistent across log sites.
_EVT_COUNT_SETTER_COMPLETED = "ui_automation.count_setter_completed"

# Structured event name emitted at every overlay-dismissal exit path.
# Extracted to a module-level constant to satisfy SonarCloud S1192.
_EVT_OVERLAY_DISMISSED = "ui_automation.overlay_dismissed"

# Regex that matches count-tab text exactly: "1x", "x2", "x3", "x4".
# These are the ONLY role="tab" elements whose text fits this pattern —
# Mode tabs ("image\nImagem") and Aspect tabs ("16:9", "crop_square") do not.
# The pattern is locale-invariant: Flow never translates the digit+x label.
_COUNT_TAB_TEXT_RE = re.compile(r"^(1x|x[2-4])$")

# Subdirectory inside out_dir where diagnostic artefacts are written.
# Keeps count_before/after screenshots and DOM dumps out of the user-facing
# output directory so file-count assertions on *.png never pick them up.
_DIAGNOSTICS_SUBDIR = "_diagnostics"


def _count_tabs_locator(page: Page) -> Locator:
    """Return a Playwright Locator that matches ONLY the 4 count tabs.

    Filters ``role="tab"`` elements by text matching :data:`_COUNT_TAB_TEXT_RE`
    (``1x``, ``x2``, ``x3``, ``x4``). This pattern is unique to count tabs —
    Mode tabs and Aspect tabs never match it — so the filter survives all three
    Radix tablists being present in the DOM simultaneously.

    DOM evidence: ``tmp/dom_dump.json`` captured on profile denon82 (Portuguese
    locale, 2026-05-22) shows all three tablists rendered; count tabs are the
    only ones whose ``text`` is ``"1x"`` / ``"x2"`` / ``"x3"`` / ``"x4"``.
    """
    return page.locator('[role="tab"]').filter(has_text=_COUNT_TAB_TEXT_RE)


# Reverse map: domain Aspect enum → CLI string accepted by the settings panel.
_CLI_FROM_ASPECT: dict[Aspect, str] = {
    Aspect.PORTRAIT: "9:16",
    Aspect.LANDSCAPE: "16:9",
    Aspect.SQUARE: "1:1",
    Aspect.LANDSCAPE_FOUR_THREE: "4:3",
    Aspect.PORTRAIT_THREE_FOUR: "3:4",
}


def aspect_cli_from_enum(aspect: Aspect) -> str | None:
    """Map the domain Aspect enum to the CLI string the settings panel expects."""
    return _CLI_FROM_ASPECT.get(aspect)


# Back-compat alias — kept so any remaining internal callers and the existing
# test imports work without a rename sweep.
_aspect_cli_from_enum = aspect_cli_from_enum


def _prompt_hash_stable(text: str) -> str:
    """Truncated sha256 matching image_batch._prompt_hash prefix length.

    Inlined here to avoid src/gflow_cli/api/transports importing image_batch
    (would create a circular dependency). 8-char prefix is sufficient for
    structlog event correlation within a single batch run.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _extract_project_id(url: str) -> str | None:
    """Thin alias for `extract_project_id` from `_common`.

    Kept for back-compat with any existing call sites and tests that import
    the private name directly from this module.
    """
    return extract_project_id(url)


def _collect_images_from_body(body: dict[str, Any], images: list[GeneratedImage]) -> None:
    """Append parseable GeneratedImage entries from one batchGenerateImages body."""
    media_list_raw = body.get("media", [])
    if not isinstance(media_list_raw, list):
        return
    for item_raw in cast(_AnyList, media_list_raw):
        if not isinstance(item_raw, dict):
            continue
        item: dict[str, Any] = cast(_JsonObj, item_raw)
        try:
            images.append(GeneratedImage.from_response_item(item))
        except ValueError as e:
            log.warning("ui_automation.parse_media_item_failed", error=str(e))


def _images_from_responses(
    responses: list[dict[str, Any]],
) -> tuple[list[GeneratedImage], int | None, str]:
    """Process captured batchGenerateImages responses.

    Returns ``(images, first_error_status, first_error_route)``. Raises
    :class:`AuthExpiredError` on 401 and :class:`WafRejectionError` on 403,
    which the caller must surface — these are not first-error candidates.
    """
    images: list[GeneratedImage] = []
    first_error_status: int | None = None
    first_error_route: str = ""

    for response in responses:
        status = response.get("status")
        body: dict[str, Any] = cast(_JsonObj, response.get("body") or {})
        route_str: str = str(response.get("url", ""))

        if status == 401:
            raise AuthExpiredError(
                detail="batchGenerateImages returned HTTP 401 — session expired",
                status=401,
                route=route_str,
            )
        if status == 403:
            log.warning(
                "ui_automation.batch_403_body",
                body_prefix=str(body)[:200],
                route=route_str,
            )
            raise WafRejectionError(
                detail=(
                    "batchGenerateImages HTTP 403 — reCAPTCHA score too low or WAF "
                    "fingerprint mismatch. Re-authenticate and retry."
                ),
                status=403,
                route=route_str,
            )
        if status != 200:
            first_error_status = first_error_status or status
            first_error_route = first_error_route or route_str
            continue

        _collect_images_from_body(body, images)

    return images, first_error_status, first_error_route


_REF_VALUE_MAX_CHARS = 512


def _elide_large_value(value: Any) -> Any:
    """Return *value* verbatim when small, else a length-only redaction marker.

    Guards the request-body logger against dumping large/secret payloads: if Flow
    names an i2i image field `reference*`/`*entity*`, its base64 bytes would match
    the reference-field filter and be logged in full. Small fields like
    `referenceEntities` (a short list of `{entityId}`) pass through; anything
    serializing beyond the cap is elided to a `<elided N chars>` marker.
    """
    try:
        serialized = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return f"<unserializable {type(value).__name__}>"
    if len(serialized) > _REF_VALUE_MAX_CHARS:
        return f"<elided {len(serialized)} chars>"
    return value


def _summarize_batch_request_body(post_data: str | None) -> dict[str, Any]:
    """Compact, byte-safe summary of an outgoing ``batchGenerateImages`` body.

    This is the make-or-break signal for the entity-reference spike: it reveals
    whether Flow's image submit carries a ``referenceEntities`` field after we
    attach a character via the picker — WITHOUT logging the full body (i2i
    bodies embed base64 image bytes that would flood the log and leak content).

    Returns a dict with ``present``, ``mentions_reference_entities`` (cheap
    substring probe that survives non-JSON bodies), and — when the body parses —
    the keys of ``requests[0]`` plus any reference/entity-related fields verbatim
    (those are small id lists, safe to surface).
    """
    if not post_data:
        return {"present": False}
    summary: dict[str, Any] = {
        "present": True,
        "bytes": len(post_data),
        "mentions_reference_entities": "referenceEntit" in post_data,
    }
    try:
        parsed: object = json.loads(post_data)
    except (ValueError, TypeError):
        return summary
    if not isinstance(parsed, dict):
        return summary
    parsed_dict = cast(_JsonObj, parsed)
    reqs = parsed_dict.get("requests")
    if isinstance(reqs, list) and reqs and isinstance(reqs[0], dict):
        first = cast(_JsonObj, reqs[0])
        summary["request0_keys"] = sorted(first.keys())
        ref_bits = {
            k: _elide_large_value(v)
            for k, v in first.items()
            if "reference" in k.lower() or "entity" in k.lower()
        }
        if ref_bits:
            summary["reference_fields"] = ref_bits
    else:
        summary["top_keys"] = sorted(parsed_dict.keys())
    return summary


def _entity_ids_from_one_request(req: Any) -> set[str]:
    """Extract entityId strings from a single ``requests[]`` entry."""
    if not isinstance(req, dict):
        return set()
    ents = cast(_JsonObj, req).get("referenceEntities")
    if not isinstance(ents, list):
        return set()
    out: set[str] = set()
    for ent in cast(_AnyList, ents):
        if isinstance(ent, dict):
            entity_id = cast(_JsonObj, ent).get("entityId")
            if isinstance(entity_id, str):
                out.add(entity_id)
    return out


def _entity_ids_from_request_body(post_data: str | None) -> set[str]:
    """Entity ids carried by an outgoing ``batchGenerateImages`` body.

    Request shape (live-verified — issue #170 report + movie spike):
    ``requests[].referenceEntities[].entityId``. Feeds the #170 submit
    backstop: a UI attach miss must not degrade to a text-only generation
    reported as success.
    """
    if not post_data:
        return set()
    try:
        parsed: object = json.loads(post_data)
    except (ValueError, TypeError):
        return set()
    if not isinstance(parsed, dict):
        return set()
    out: set[str] = set()
    reqs = cast(_JsonObj, parsed).get("requests")
    if not isinstance(reqs, list):
        return out
    for req in cast(_AnyList, reqs):
        out |= _entity_ids_from_one_request(req)
    return out


class UiAutomationTransport(VideoGenerationMixin):
    """D.2.4 — Playwright UI mimicry strategy.

    Drives the Flow editor on a logged-in Pro/Ultra profile through a
    Playwright-managed persistent context. The strategy never exposes an
    external CDP debug port; Playwright's internal port is sufficient and
    keeps the browser environment indistinguishable from a typical
    developer session.

    Lifecycle (Protocol § 4.1)::

        await transport.setup(profile_dir)
        images = await transport.generate_images(project_id=..., request=...)
        await transport.teardown()
    """

    name = "ui_automation"

    def __init__(self) -> None:
        self._pw_cm: Any | None = None
        self._ctx: Any | None = None
        self._page: Page | None = None
        self._setup_done: bool = False
        self._owns_playwright: bool = False
        # Cross-process profile lease (D3). Held ONLY on the standalone-context
        # path (setup with page=None), where this transport owns the persistent
        # context. On the shared-page path the caller (FlowApiClient) owns both
        # the context and the lease, so this stays None.
        self._lease: ProfileLease | None = None
        # Typed output/storage wiring handed in by FlowApiClient through the
        # public apply_setup() seam (SupportsTransportSetup). The private slots
        # below are this transport's own derived state — the client no longer
        # writes them directly.
        self.setup_config: TransportSetup | None = None
        # Optional directory for debug screenshots — derived from the client's
        # `out_dir` constructor arg (#18). When None, the internal
        # _capture_debug_screenshot helper is a no-op.
        self._out_dir: Path | None = None
        # Optional cloud-storage configuration. Video downloads read these slots
        # inside VideoGenerationMixin._download_video.
        self._storage_uri: str | None = None
        self._output_dir: Path | None = None
        # Serialize concurrent generate_images calls — a single Playwright Page
        # cannot be safely shared across parallel asyncio tasks (each call
        # navigates, opens panels, and types into the same DOM). The lock
        # converts the N-parallel fan-out from generate_images_batch into N
        # sequential Page interactions, eliminating all race conditions.
        self._generate_lock: asyncio.Lock = asyncio.Lock()

    def apply_setup(self, config: TransportSetup) -> None:
        """Accept output/storage wiring publicly (SupportsTransportSetup seam).

        Stores the immutable record and derives the private slots the debug-
        screenshot and video-download paths read. ``out_dir`` is only adopted
        when set, preserving the prior "don't clobber with None" plumbing
        behaviour; ``storage_uri``/``output_dir`` mirror the config as-is.
        """
        self.setup_config = config
        if config.out_dir is not None:
            self._out_dir = config.out_dir
        self._storage_uri = config.storage_uri
        self._output_dir = config.output_dir

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self, profile_dir: Path, *, page: Page | None = None) -> None:
        """Acquire a Page on the logged-in Flow editor.

        Idempotent — second call is a no-op.

        When ``page`` is provided (shared-page path), the caller owns the
        Playwright lifecycle; teardown() will not close the context. When
        ``page`` is None, the strategy opens its own persistent context
        against ``profile_dir`` and is responsible for its full lifecycle.

        An initial ``page.goto(FLOW_URL)`` is attempted; a navigation
        failure is logged but not raised — auth/UI recovery happens in
        ``generate_images``.
        """
        if self._setup_done:
            return

        if page is not None:
            # Shared-page path: caller owns Playwright lifecycle.
            self._page = page
            self._owns_playwright = False
            self._setup_done = True
            log.info("ui_automation.setup_shared_page")
            return

        # Engine selection (standalone-context path; the shared-page path above
        # already returned). Default playwright keeps the module symbol; only the
        # opt-in patchright engine routes through the resolver.
        from gflow_cli.api._engine import (
            active_engine,
            close_context_bounded,
            log_engine_selected,
            resolve_async_playwright,
        )
        from gflow_cli.config import BrowserEngine

        engine = active_engine()
        log_engine_selected(engine)
        if engine == BrowserEngine.PATCHRIGHT:
            pw_factory = resolve_async_playwright(engine)
        elif async_playwright is None:  # pragma: no cover — install-time guard
            msg = (
                "Playwright is required for UiAutomationTransport. "
                "Install via `uv sync` (it is a runtime dependency)."
            )
            raise RuntimeError(
                msg,
            )
        else:
            pw_factory = async_playwright

        pw_cm = pw_factory()
        # The two engines (playwright | patchright) expose a structurally identical
        # ``.chromium.launch_persistent_context`` surface; type as Any so this
        # standalone path is engine-agnostic without per-engine stubs.
        pw: Any = await pw_cm.__aenter__()
        ctx = None
        try:
            import os

            from gflow_cli.browser_manager import channel_for_profile

            # Own the profile BEFORE Chrome launches (D3). Contention raises
            # ProfileLockedError here; the except below tears the driver back down.
            self._lease = ProfileLease(profile_dir).acquire()
            locale_env = os.getenv("GFLOW_CLI_LOCALE", "en-US")
            ctx = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                viewport=cast("ViewportSize", _VIEWPORT),
                locale=locale_env,
                channel=channel_for_profile(profile_dir),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--password-store=basic",
                    "--disable-dev-shm-usage",
                ],
            )
            # Hide the automation flag so reCAPTCHA Enterprise doesn't score
            # the session as a bot — navigator.webdriver=true causes low-score
            # tokens and HTTP 403 on batchGenerateImages.
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})",
            )
            self._pw_cm = pw_cm
            self._ctx = ctx
            page = cast("Page", ctx.pages[0] if ctx.pages else await ctx.new_page())
            self._page = page
            try:
                await page.goto(FLOW_URL, wait_until="networkidle", timeout=45_000)
            except Exception as e:
                log.warning("ui_automation.flow_initial_goto_failed", error=str(e))
            self._owns_playwright = True
            self._setup_done = True
            log.info(
                "ui_automation.setup_own_context",
                profile_dir=str(profile_dir),
            )
        except BaseException:
            # Partial-setup leak guard. Catches BaseException (not just
            # Exception) so a CancelledError mid-setup ALSO tears down the
            # launched context (D4) — otherwise a cancelled launch would orphan
            # chrome and leak the lease. The context must be closed BEFORE the
            # driver exits — stopping only the driver leaves the detached
            # system-Chrome alive holding the profile dir (issue #293). Each
            # cleanup step is bounded + shielded so the cleanup itself cannot be
            # interrupted before the lease release; the original error re-raises.
            from gflow_cli.api._engine import (  # noqa: PLC0415
                CONTEXT_TEARDOWN_TIMEOUT_S,
                DRIVER_STOP_TIMEOUT_S,
                run_teardown_step,
            )

            if ctx is not None:
                await run_teardown_step(
                    close_context_bounded(ctx, owner="ui_automation"),
                    timeout=CONTEXT_TEARDOWN_TIMEOUT_S,
                    owner="ui_automation",
                    step="setup_context_close",
                )
            await run_teardown_step(
                pw_cm.__aexit__(None, None, None),
                timeout=DRIVER_STOP_TIMEOUT_S,
                owner="ui_automation",
                step="setup_driver_exit",
            )
            # Release the lease last — after context + driver are down (D3).
            if self._lease is not None:
                self._lease.release()
                self._lease = None
            raise

    # ------------------------------------------------------------------
    # Internal helpers — auth detection (unit 3.3)
    # ------------------------------------------------------------------

    @staticmethod
    async def _check_logged_in(page: Page) -> bool:
        """True if the page shows the authenticated Flow UI.

        Gates (pattern G13):
        - URL is on labs.google AND contains /flow (locale-stable;
          /fx/pt/tools/flow, /fx/es/tools/flow, etc. all match).
        - URL is NOT on accounts.google.com.
        - /project/<uuid> URLs short-circuit to True (editor already open).
        - Otherwise reject if a top-level Sign-in CTA is visible.

        A failure in the locator probe is treated as "no Sign-in button"
        — the URL gate already established Flow context, and a transient
        DOM error shouldn't force a re-auth loop.
        """
        if "accounts.google.com" in page.url:
            return False
        on_flow = "labs.google" in page.url and "/flow" in page.url
        if not on_flow:
            return False
        if _PROJECT_URL_FRAGMENT in page.url:
            return True
        try:
            signin_button = await page.locator(
                "button:has-text('Sign in'), a:has-text('Sign in')",
            ).count()
        except Exception:
            signin_button = 0
        return signin_button == 0

    # ------------------------------------------------------------------
    # Internal helpers — gallery → editor navigation (unit 3.4)
    # ------------------------------------------------------------------

    async def _bypass_onboarding(self, page: Page) -> None:
        """Click through cookie banners and 'Get Started' pages if they appear."""
        for selector in ONBOARDING_SELECTORS:
            try:
                loc = page.locator(selector).first
                if await loc.is_visible(timeout=1000):
                    await loc.click(force=True)
                    log.info("ui_automation.onboarding_bypassed", selector=selector)
                    await page.wait_for_timeout(1000)
            except Exception:
                continue

    @staticmethod
    async def _detect_overlay(page: Page) -> bool:
        """Return True if any changelog iframe or welcome screen is currently visible."""
        for sel in CHANGELOG_IFRAME_SELECTORS + WELCOME_SCREEN_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible(timeout=1500):
                    log.info("ui_automation.overlay_detected", selector=sel)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _try_page_close_button(page: Page) -> bool:
        """Try each OVERLAY_CLOSE_BUTTON_SELECTORS at page level. Returns True on success."""
        for close_sel in OVERLAY_CLOSE_BUTTON_SELECTORS:
            try:
                loc = page.locator(close_sel).first
                if await loc.is_visible(timeout=500):
                    await loc.click(force=True)
                    await page.wait_for_timeout(1000)
                    log.info(
                        _EVT_OVERLAY_DISMISSED,
                        selector=close_sel,
                        method="close_button_page",
                    )
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _try_iframe_close_button(page: Page) -> bool:
        """Try each close-button selector inside each changelog iframe. Returns True on success."""
        for iframe_sel in CHANGELOG_IFRAME_SELECTORS:
            try:
                frame = page.frame_locator(iframe_sel).first
                for close_sel in OVERLAY_CLOSE_BUTTON_SELECTORS:
                    loc = frame.locator(close_sel).first
                    if await loc.is_visible(timeout=500):
                        await loc.click(force=True)
                        await page.wait_for_timeout(1000)
                        log.info(
                            _EVT_OVERLAY_DISMISSED,
                            selector=close_sel,
                            method="close_button_frame",
                        )
                        return True
            except Exception:
                continue
        return False

    async def _dismiss_blocking_overlays(
        self,
        page: Page,
        out_dir: Path | None = None,
    ) -> bool:
        """Dismiss Flow changelog / "What's new" iframes and any blocking overlays.

        Called at stable interaction boundaries (after editor navigation, before
        UI interactions that could be intercepted by a changelog popup).

        Strategy:
        1. Check whether any changelog iframe is currently visible.
        2. If none found, return False immediately (cheap; no log noise).
        3. If found, try each close-button selector in OVERLAY_CLOSE_BUTTON_SELECTORS.
           On first visible match: force-click it, log the selector used, return True.
        4. If no close button is discoverable, press Escape as a fallback and return True.
        5. If Escape raises (extremely rare — keyboard unavailable), capture a debug
           screenshot (if out_dir provided) and return False so the caller can decide
           how to proceed. The structured warning carries enough info to identify the
           blocking element.

        Returns True if a dismissal action was taken, False if the page was
        clear (no overlay) or if dismissal could not be confirmed.
        """
        if not await self._detect_overlay(page):
            return False

        if await self._try_page_close_button(page):
            return True

        if await self._try_iframe_close_button(page):
            return True

        # Escape fallback (regression test case: iframe present, no close button).
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
            log.info(
                _EVT_OVERLAY_DISMISSED,
                selector="<none>",
                method="escape",
            )
            return True
        except Exception as exc:
            shot_path = await _capture_debug_screenshot(
                page,
                out_dir,
                "debug_overlay_dismiss_failed.png",
            )
            log.warning(
                "ui_automation.overlay_dismiss_failed",
                error=str(exc),
                screenshot=str(shot_path),
                note=(
                    "A blocking overlay was detected but could not be dismissed. "
                    "Manual intervention may be needed."
                ),
            )
            return False

    async def _enter_editor(
        self,
        page: Page,
        out_dir: Path | None = None,
        *,
        project_id: str | None = None,
        locale: str = "en-US",
    ) -> None:
        """Create a fresh project OR navigate to an existing one.

        If ``project_id`` is provided, navigates directly to that project's
        editor URL. Otherwise clicks "+ New project" on the gallery and
        waits for ``/project/`` navigation.

        When creating a new project and the URL already contains
        ``/project/`` (Flow's PWA restored the previous project on browser
        launch), this navigates back to the gallery first, then falls
        through to the "+ New project" click — the alternative (returning
        early) would reuse the restored project and accumulate images
        across CLI invocations.
        """
        from gflow_cli.api import routes

        if project_id:
            url = routes.project_editor_url(locale, project_id)
            log.info("ui_automation.entering_existing_project", project_id=project_id, url=url)
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await self._dismiss_blocking_overlays(page, out_dir)
            return

        if _PROJECT_URL_FRAGMENT in page.url:
            # Flow's PWA restores the last-visited project URL on next browser
            # launch (persistent context). Returning early here would reuse the
            # old project, accumulating images across CLI invocations instead of
            # starting fresh. Navigate back to the gallery first, then fall
            # through to the "+ New project" click below.
            # Do NOT use wait_until="networkidle" — PWAs re-render incrementally
            # and networkidle is flaky. The selector wait_for below is the real
            # readiness gate.
            log.info("ui_automation.navigating_to_gallery", restored_url=page.url)
            await page.goto(FLOW_URL, timeout=45_000)
            await self._bypass_onboarding(page)

        await page.wait_for_timeout(3000)
        await self._bypass_onboarding(page)
        for selector in NEW_PROJECT_SELECTORS:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=5000)
                log.info("ui_automation.clicking_new_project", selector=selector)
                await loc.click()
                try:
                    await page.wait_for_url(
                        lambda url: _PROJECT_URL_FRAGMENT in url,
                        timeout=15_000,
                    )
                    log.info("ui_automation.entered_editor", url=page.url)
                    return
                except Exception:
                    log.warning(
                        "ui_automation.new_project_click_did_not_navigate",
                        selector=selector,
                    )
            except Exception:
                continue

        shot_path = await _capture_debug_screenshot(page, out_dir, "debug_new_project.png")
        msg = (
            f"Could not find 'New project' CTA on Flow gallery. "
            f"URL: {page.url}.{screenshot_clause(shot_path)}"
        )
        raise RuntimeError(
            msg,
        )

    @staticmethod
    async def _force_agent_mode(page: Page) -> bool:
        """Force the agentic composer by clicking the in-composer Agent toggle.

        Opt-in helper (reached via ``GFLOW_CLI_UI_MODE=agentic``) used to drive the
        agentic path deterministically — the server-assigned A/B cohort has no
        client-readable flag, but the in-composer "Agent" toggle switches the
        classic composer into the same agentic layout (``crop_*`` → ``tune``).

        Idempotent: a no-op when the composer is already agentic (the ``tune``
        gear is present). Returns ``True`` if the composer is agentic on exit.
        """
        if await page.locator(AGENT_TUNE_INDICATOR_SELECTOR).count() > 0:
            log.info("ui_automation.agent_mode_already_active")
            return True
        # The composer renders a beat after navigation, so wait for the Agent
        # toggle before clicking — an instant probe races the render and misses
        # it (same failure mode as the cohort-detection race).
        toggle = page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR).first
        try:
            await toggle.wait_for(state="visible", timeout=8000)
        except Exception:
            log.warning("ui_automation.agent_toggle_not_found")
            return False
        # Click the toggle, then POLL for the ``tune`` indicator up to
        # ``_AGENT_ACTIVATE_TIMEOUT_S`` (the render lags the click); re-click once
        # if the first attempt doesn't take. Replaces the old single 1.2s wait
        # that raced the render and bound classic ~50% of the time (#267).
        for attempt in range(_AGENT_FORCE_MAX_CLICKS):
            await toggle.click(force=True)
            if await UiAutomationTransport._wait_selector_present(
                page, AGENT_TUNE_INDICATOR_SELECTOR, timeout_s=_AGENT_ACTIVATE_TIMEOUT_S
            ):
                # Best-effort: open the agent side panel via the expand button.
                try:
                    expand = page.locator(AGENT_EXPAND_BUTTON_SELECTOR).first
                    if await expand.count() > 0:
                        await expand.click(force=True)
                        await page.wait_for_timeout(800)
                except Exception as e:
                    log.debug("ui_automation.agent_expand_failed", error=str(e)[:80])
                log.info("ui_automation.agent_mode_forced", activated=True, attempt=attempt)
                return True
            log.debug("ui_automation.agent_mode_force_retry", attempt=attempt)
        log.warning("ui_automation.agent_mode_force_failed")
        return False

    @staticmethod
    async def _wait_selector_present(page: Page, selector: str, *, timeout_s: float) -> bool:
        """Poll until ``selector`` matches ≥1 node, or ``timeout_s`` elapses."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            if await page.locator(selector).count() > 0:
                return True
            if asyncio.get_event_loop().time() >= deadline:
                return False
            await page.wait_for_timeout(_AGENT_ACTIVATE_POLL_MS)

    @staticmethod
    async def _switch_to_image_mode(page: Page, *, out_dir: Path | None = None) -> None:
        """Open the 2-step mode dropdown and switch to Image mode.

        Mirror of :meth:`VideoGenerationMixin._switch_to_video_mode`.  Without
        this, an account whose last-used Flow mode was Video silently routes
        ``image t2i`` / ``image batch`` prompts to the video endpoint — no
        ``batchGenerateImages`` response is observed, and the listener times
        out after 3 minutes (an image typically completes in ~15 s).

        The dropdown is closed afterwards (via :kbd:`Escape`) so the caller's
        :meth:`_configure_generation_settings` can open it fresh.
        """
        # New Flow UI: if the composer is in Agent mode the generation panel is
        # absent — switch back to media mode first so the trigger probe below
        # can find the crop_* dropdown.
        await VideoGenerationMixin._exit_agent_mode(page, out_dir=out_dir)
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "mode_switch_trigger",
            MODE_SWITCH_TRIGGER_SELECTORS,
        )
        if trigger is None:
            raise await VideoGenerationMixin._mode_switch_error(page, out_dir, media="image")
        await trigger.click()
        await page.wait_for_timeout(800)
        image_tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "image_mode_tab",
            IMAGE_TAB_IN_MENU_SELECTORS,
        )
        if image_tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_image_tab.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "image_mode_tab",
                    "Image tab not found in the mode dropdown.",
                    shot,
                )
            )
        await image_tab.click()
        await page.wait_for_timeout(1200)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        log.info("ui_automation.image_mode_entered")

    # ------------------------------------------------------------------
    # Internal helpers — prompt submission (unit 3.5)
    # ------------------------------------------------------------------

    async def _locate_prompt_box(
        self,
        page: Page,
        out_dir: Path | None = None,
    ) -> Any:
        """Locate the visible Slate prompt box via :data:`PROMPT_INPUT_SELECTORS`.

        Returns the first visible locator (``.first`` of the matching
        selector). On no-match, writes a debug screenshot to ``out_dir``
        (if provided) and raises ``RuntimeError``.
        """
        for selector in PROMPT_INPUT_SELECTORS:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=10_000)
                log.info("ui_automation.prompt_input_found", selector=selector)
                return loc
            except Exception:
                continue

        shot_path = await _capture_debug_screenshot(page, out_dir, "debug_prompt_not_found.png")
        msg = f"Prompt input not found in Flow UI. URL: {page.url}.{screenshot_clause(shot_path)}"
        raise RuntimeError(msg)

    async def _click_submit(self, page: Page) -> None:
        """Submit the active prompt via the ``arrow_forward`` button.

        Tries :data:`SUBMIT_BUTTON_SELECTORS` in priority order; falls back
        to pressing Enter if no submit button is visible.
        """
        for sel in SUBMIT_BUTTON_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2_000)
                await btn.click()
                log.info("ui_automation.prompt_submitted", via=sel)
                return
            except Exception:
                continue

        log.info("ui_automation.prompt_submitted", via="enter_key_fallback")
        await page.keyboard.press("Enter")

    async def _send_prompt(
        self,
        page: Page,
        prompt_text: str,
        out_dir: Path | None = None,
    ) -> None:
        """Type ``prompt_text`` into Flow's editor and submit.

        Selectors are tried in priority order; the first visible match
        wins. The text input is cleared first (Slate.js requires real
        keyboard events — ``.fill()`` bypasses onChange handlers).

        A staged R2V character entity is NOT affected by the clear: 'Incluir no
        comando' stages it in a separate references drawer, not as a chip inside
        this prompt box (verified 2026-06-06), so the entity still rides the
        submit.

        Submission is preferred via the Create button; if no submit
        button is visible, Enter is pressed as a fallback.

        On input-not-found, a debug screenshot is written to ``out_dir``
        (if provided) and ``RuntimeError`` is raised.
        """
        # Dismiss any overlays that might have appeared since entering the editor.
        await self._dismiss_blocking_overlays(page, out_dir)

        input_box = await self._locate_prompt_box(page, out_dir)

        await input_box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        # insert_text fires a single beforeinput event that Slate.js handles
        # natively — near-instant vs keyboard.type() which is ~1.5s/char.
        await page.keyboard.insert_text(prompt_text)
        await page.wait_for_timeout(500)

        await self._click_submit(page)

    async def _submit_body_prompt(
        self,
        page: Page,
        body_description: str,
        out_dir: Path | None = None,
    ) -> None:
        """Submit a self-contained triptych body prompt, REPLACING Flow's template.

        After clicking the character slot-add ("+"), Flow's JS pre-fills the new
        prompt box with its own (localized) triptych template and auto-attaches
        the generated face as a reference.  Rather than reading and parsing that
        localized template, this helper ignores whatever the box was pre-filled
        with and replaces the entire box with gflow's OWN self-contained,
        locale-independent triptych instruction
        (:data:`_BODY_TRIPTYCH_PREAMBLE`) followed by the body/clothing
        description.

        Live-verified 2026-06-02: this is MORE robust than the bracket-
        substitution approach — it does not depend on Flow's template appearing,
        the box timing, the bracket existing, or the UI locale.  The face
        reference is still auto-attached by the slot-add ("+") click (Flow's JS),
        independent of the text box.

        ONE generation then yields all three angles (front/side/back) as a
        single triptych image, seeded by the auto-attached face reference.

        Logs ``ui_automation.body_prompt_templated`` with ``template`` and the
        submitted length (NO body text).
        """
        input_box = await self._locate_prompt_box(page, out_dir)

        full_prompt = _BODY_TRIPTYCH_PREAMBLE + body_description

        # Clear Flow's pre-filled (localized) template and replace it wholesale
        # with our self-contained triptych prompt. Slate.js needs real keyboard
        # events — select-all + Delete + insert_text, the same path _send_prompt
        # uses.
        await input_box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await page.keyboard.insert_text(full_prompt)
        await page.wait_for_timeout(500)

        log.info(
            "ui_automation.body_prompt_templated",
            template="self_contained",
            prompt_len=len(full_prompt),
        )
        await self._click_submit(page)

    # ------------------------------------------------------------------
    # Internal helpers — generation settings (aspect ratio + count)
    # ------------------------------------------------------------------

    @staticmethod
    async def _open_gen_settings_panel(page: Page) -> bool:
        """Try selectors in order to open the per-generation settings panel.

        Returns True on success, False if no selector matched (non-fatal —
        caller falls back to Flow's current defaults).
        """
        for sel in GEN_SETTINGS_BUTTON_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=3_000)
                await btn.click()
                await page.wait_for_timeout(600)
                log.info("ui_automation.gen_settings_opened", via=sel)
                return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _read_displayed_count(page: Page) -> int | None:
        """Read the currently-displayed image count from the settings panel.

        Locale-invariant: filters ``[aria-selected="true"]`` tabs by
        :data:`_COUNT_TAB_TEXT_RE` so only count tabs ("1x", "x2", "x3",
        "x4") are considered.  Mode tabs ("image\\nImagem") and Aspect tabs
        ("16:9", etc.) are selected simultaneously in the Radix tablist DOM
        and would poison the old unfiltered ``[aria-selected="true"]`` query.

        Returns the integer count (1–4) extracted from the matched tab's text,
        or ``None`` when no count tab is selected / visible.
        """
        try:
            selected = page.locator('[role="tab"][aria-selected="true"]').filter(
                has_text=_COUNT_TAB_TEXT_RE,
            )
            if await selected.count() == 0:
                return None
            text = (await selected.first.text_content(timeout=500) or "").strip()
            m = re.search(r"\d", text)
            return int(m.group()) if m else None
        except Exception:
            return None

    @staticmethod
    async def _is_settings_panel_open(page: Page) -> bool:
        """True if the generation-settings panel count tabs are currently visible.

        Uses :func:`_count_tabs_locator` — the panel is open when at least one
        count tab (text matching ``1x`` / ``x2`` / ``x3`` / ``x4``) is visible.
        This is locale-invariant and immune to Mode/Aspect tab false-positives.
        """
        try:
            return await _count_tabs_locator(page).first.is_visible()
        except Exception:
            return False

    @staticmethod
    async def _select_image_model(page: Page, model: Model) -> None:
        """Click the image model picker and select `model` (Nano Banana 2 / Nano
        Banana Pro / Imagen 4). Must run with the gen-settings panel open. Without
        this the generation uses Flow's UI-default model and ``--model`` is a
        no-op. Non-fatal on miss (logged at WARNING — the wrong model has a real
        cost/quality impact, so a miss is a genuine signal)."""
        option_sels = IMAGE_MODEL_OPTION_SELECTORS.get(model)
        if not option_sels:
            log.warning("ui_automation.image_model_unknown", model=model.value)
            return
        try:
            trigger = page.locator(IMAGE_MODEL_PICKER_TRIGGER).first
            await trigger.wait_for(state="visible", timeout=4000)
            await trigger.click()
            await page.wait_for_timeout(500)
            for sel in option_sels:
                try:
                    loc = page.locator(sel).first
                    await loc.wait_for(state="visible", timeout=4000)
                    await loc.click()
                    await page.wait_for_timeout(500)
                    log.info("ui_automation.image_model_selected", model=model.value, via=sel)
                    return
                except Exception as sel_err:
                    log.debug(
                        "ui_automation.image_model_selector_miss",
                        sel=sel,
                        error=str(sel_err)[:120],
                    )
                    continue
            raise RuntimeError(f"no visible option matched for model {model.value!r}")
        except Exception as e:
            log.warning(
                "ui_automation.image_model_not_set",
                model=model.value,
                error=str(e)[:80],
                note="Flow default model applies",
            )
            await page.keyboard.press("Escape")  # close a stray model menu

    @staticmethod
    async def _configure_generation_settings(
        page: Page,
        aspect_cli: str | None,
        count: int | None,
        *,
        model: Model | None = None,
        out_dir: Path | None = None,
        prompt_idx: int | None = None,
    ) -> None:
        """Open the per-generation settings panel and apply model, aspect ratio,
        and count.

        When ``out_dir`` and ``prompt_idx`` are both provided, diagnostic
        screenshots are saved as ``count_before_prompt_{idx}.png`` and
        ``count_after_prompt_{idx}.png`` so future count-drift can be
        diagnosed without re-instrumenting the code.

        Skips gracefully if the panel trigger cannot be found (non-fatal —
        generation will proceed with Flow's current default settings).
        """
        if aspect_cli is None and count is None and model is None:
            # Nothing to apply.
            return

        # Phase 3 — before screenshot (diagnostic, best-effort).
        await UiAutomationTransport._capture_diag_screenshot(
            page,
            out_dir,
            prompt_idx,
            "before",
        )

        if not await UiAutomationTransport._open_gen_settings_panel(page):
            log.warning("ui_automation.gen_settings_panel_not_found", skipping=True)
            return

        if model is not None:
            await UiAutomationTransport._select_image_model(page, model)

        if aspect_cli:
            await UiAutomationTransport._apply_aspect_ratio(page, aspect_cli)

        if count is not None:
            await UiAutomationTransport._apply_count(page, count, out_dir, prompt_idx)

        # Phase 3 — after screenshot (diagnostic, best-effort).
        await UiAutomationTransport._capture_diag_screenshot(
            page,
            out_dir,
            prompt_idx,
            "after",
        )

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

    @staticmethod
    async def _capture_diag_screenshot(
        page: Page,
        out_dir: Path | None,
        prompt_idx: int | None,
        suffix: str,
    ) -> None:
        if out_dir is not None and prompt_idx is not None:
            diag_dir = out_dir / _DIAGNOSTICS_SUBDIR
            diag_dir.mkdir(parents=True, exist_ok=True)
            await _capture_debug_screenshot(
                page,
                diag_dir,
                f"count_{suffix}_prompt_{prompt_idx}.png",
            )

    @staticmethod
    async def _apply_aspect_ratio(page: Page, aspect_cli: str) -> None:
        candidates = _ASPECT_TAB_CANDIDATES.get(aspect_cli, (aspect_cli,))
        clicked = False
        last_err: str | None = None
        for tab_text in candidates:
            # `:text-is(...)` is exact-match — preferred for short labels
            # like "1:1" because `:has-text(...)` substring-matches and
            # would clash with longer tabs that include the label.
            try:
                tab = page.locator(f'[role="tab"]:text-is("{tab_text}")').first
                await tab.wait_for(state="visible", timeout=2_000)
                await tab.click()
                clicked = True
                log.info(
                    "ui_automation.aspect_ratio_set",
                    value=aspect_cli,
                    matched_label=tab_text,
                )
                break
            except Exception as e:
                last_err = str(e)
                continue
        if not clicked:
            log.warning(
                "ui_automation.aspect_ratio_set_failed",
                value=aspect_cli,
                candidates_tried=list(candidates),
                error=last_err,
            )

    @staticmethod
    async def _apply_count(
        page: Page,
        count: int,
        out_dir: Path | None,
        prompt_idx: int | None,
    ) -> None:
        if count not in _SUPPORTED_COUNTS:
            log.warning("ui_automation.unsupported_count", value=count)
        else:
            await UiAutomationTransport._set_count(
                page,
                count,
                out_dir=out_dir,
                prompt_idx=prompt_idx,
            )

    @staticmethod
    async def _dump_count_panel_dom(
        page: Page,
        out_dir: Path | None,
        prompt_idx: int | None,
    ) -> None:
        """Diagnostic dump of the count-tab area of the editor to out_dir.

        Writes a JSON file enumerating candidate structural patterns so we can
        derive locale-invariant selectors from real DOM evidence (per issue #24).

        Captures:
          - All elements with role="tab", role="tablist", role="radiogroup",
            role="radio" — count, aria-label, aria-selected, text content.
          - All buttons inside any visible Material panel — text, aria-label,
            leading digit if any, google-symbols icon ligature children.
          - Document title + page URL for context.

        Safe-by-default: no-op if out_dir is None or prompt_idx is None.
        Failures swallowed (this is diagnostic).
        """
        if out_dir is None or prompt_idx is None:
            return
        try:
            snapshot = await page.evaluate("""() => {
                const result = {
                    url: location.href,
                    title: document.title,
                    roles: {},
                    buttons_with_digits: [],
                    google_symbols_ligatures: [],
                };
                for (const role of ['tab', 'tablist', 'radiogroup', 'radio']) {
                    const els = Array.from(document.querySelectorAll('[role="' + role + '"]'));
                    result.roles[role] = els.map(el => ({
                        text: (el.innerText || '').slice(0, 120),
                        aria_label: el.getAttribute('aria-label'),
                        aria_selected: el.getAttribute('aria-selected'),
                        aria_controls: el.getAttribute('aria-controls'),
                        id: el.id || null,
                        classes: el.className.toString().slice(0, 200),
                    }));
                }
                // Buttons whose visible text starts with a digit (count-tab candidates).
                for (const btn of document.querySelectorAll('button')) {
                    const text = (btn.innerText || '').trim();
                    if (/^\\d/.test(text)) {
                        result.buttons_with_digits.push({
                            text: text.slice(0, 120),
                            aria_label: btn.getAttribute('aria-label'),
                            aria_selected: btn.getAttribute('aria-selected'),
                            role: btn.getAttribute('role'),
                            parent_role: btn.parentElement?.getAttribute('role') || null,
                            parent_class: (btn.parentElement?.className
                                ?.toString().slice(0, 200)) || null,
                        });
                    }
                }
                // Google Symbols icons present anywhere — gives us the ligature names Flow uses.
                const _gsQuery = 'i.google-symbols, span.google-symbols';
                for (const el of document.querySelectorAll(_gsQuery)) {
                    const lig = (el.innerText || '').trim();
                    if (lig) result.google_symbols_ligatures.push({
                        ligature: lig,
                        parent_text: (el.parentElement?.innerText || '').trim().slice(0, 80),
                        parent_role: el.parentElement?.getAttribute('role'),
                        parent_aria_label: el.parentElement?.getAttribute('aria-label'),
                    });
                }
                return result;
            }""")
            diag_dir = out_dir / _DIAGNOSTICS_SUBDIR
            diag_dir.mkdir(parents=True, exist_ok=True)
            target = diag_dir / f"count_panel_dom_prompt_{prompt_idx}.json"
            target.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info(
                "ui_automation.count_panel_dom_dumped",
                target=str(target),
                tabs_count=len(snapshot.get("roles", {}).get("tab", [])),
                digit_buttons_count=len(snapshot.get("buttons_with_digits", [])),
                ligatures_count=len(snapshot.get("google_symbols_ligatures", [])),
            )
        except Exception as exc:
            log.warning(
                "ui_automation.count_panel_dom_dump_failed",
                error=str(exc),
                prompt_idx=prompt_idx,
            )

    @staticmethod
    async def _set_count(
        page: Page,
        count: int,
        *,
        out_dir: Path | None = None,
        prompt_idx: int | None = None,
    ) -> None:
        """Click the count tab by position — locale-invariant, read-back verify with retry.

        Algorithm (#24 DOM-evidence-driven rewrite):
        1. Ensure the settings panel is open without toggling it closed
           (stay-mounted batch: panel may already be open from the prior prompt).
        2. Read the currently-displayed count via :func:`_count_tabs_locator`
           filtered by :data:`_COUNT_TAB_TEXT_RE` — immune to Mode/Aspect tabs.
        3. If it already matches ``count``, return early (no click needed).
        4. Call ``_count_tabs_locator(page).nth(count - 1)`` and click —
           positional within the filtered set, no text matching.
        5. Read back the digit and confirm the change.
        6. Retry up to 3 attempts total; raise ``RuntimeError`` on non-convergence.

        When read-back returns ``None`` (unrecognised locale text), the
        position-based click is trusted — it is deterministic regardless.

        Four structlog events are emitted for diagnosability:
        - ``ui_automation.count_setter_entered``
        - ``ui_automation.count_click_attempted``
        - ``ui_automation.count_click_result``
        - ``ui_automation.count_setter_completed``
        """
        panel_open = await UiAutomationTransport._is_settings_panel_open(page)
        initial_displayed = await UiAutomationTransport._read_displayed_count(page)

        log.info(
            "ui_automation.count_setter_entered",
            desired_count=count,
            panel_currently_visible=panel_open,
            initial_displayed_count=initial_displayed,
        )

        # Ensure the panel is open — open it only if it's currently closed
        # (avoid toggling a stay-mounted open panel closed).
        if not panel_open:
            opened = await UiAutomationTransport._open_gen_settings_panel(page)
            if not opened:
                log.warning(
                    "ui_automation.count_setter_panel_open_failed",
                    desired_count=count,
                )
                # Non-fatal: completed event records failure.
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=initial_displayed,
                    success=False,
                    attempts=0,
                )
                return

        # Diagnostic DOM dump — captured after panel is confirmed open, before any
        # tab click attempt. Produces count_panel_dom_prompt_{idx}.json in out_dir
        # so the real DOM structure is visible for selector research (issue #24).
        await UiAutomationTransport._dump_count_panel_dom(page, out_dir, prompt_idx)

        _max_attempts = 3
        # Reuse the initial read — avoids a redundant DOM round-trip.
        displayed: int | None = initial_displayed

        for attempt in range(1, _max_attempts + 1):
            # If current display already matches desired, we're done.
            if displayed == count:
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=displayed,
                    success=True,
                    attempts=attempt - 1,
                )
                return

            # Locate count tabs via text-pattern filter (locale-invariant).
            # _count_tabs_locator returns role="tab" elements whose text matches
            # ^(1x|x[2-4])$ — unique to count tabs across all three Radix tablists.
            panel_visible_before = await UiAutomationTransport._is_settings_panel_open(page)
            clicked = False
            click_error: str | None = None

            tabs_locator = _count_tabs_locator(page)
            # 0-indexed: count=1 → nth(0), count=2 → nth(1), etc.
            target_tab = tabs_locator.nth(count - 1)
            selector_desc = f"nth({count - 1}) of _count_tabs_locator"
            log.info(
                "ui_automation.count_click_attempted",
                target=f"count={count}",
                selector=selector_desc,
                panel_visible=panel_visible_before,
                current_displayed_count=displayed,
            )
            try:
                await target_tab.wait_for(state="visible", timeout=3_000)
                await target_tab.click()
                await page.wait_for_timeout(300)
                clicked = True
            except Exception as e:
                click_error = str(e)

            # Read back to verify (digit-extraction, locale-agnostic).
            displayed = await UiAutomationTransport._read_displayed_count(page)
            log.info(
                "ui_automation.count_click_result",
                target=f"count={count}",
                success=clicked,
                current_displayed_count_after=displayed,
                error=click_error,
            )

            # Success when the click landed AND read-back digit matches, OR
            # when read-back returned None (unrecognised locale text — position
            # click was deterministic so trust it).
            if clicked and (displayed is None or displayed == count):
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=displayed,
                    success=True,
                    attempts=attempt,
                    readback_trusted=(displayed is None),
                )
                return

            # Brief pause before retry to allow React re-render.
            if attempt < _max_attempts:
                await page.wait_for_timeout(500)

        # All attempts exhausted without convergence.
        log.info(
            _EVT_COUNT_SETTER_COMPLETED,
            desired_count=count,
            final_displayed_count=displayed,
            success=False,
            attempts=_max_attempts,
        )
        msg = (
            f"_set_count({count}) failed to update Flow UI; "
            f"still showing {displayed!r} after {_max_attempts} attempts"
        )
        raise RuntimeError(
            msg,
        )

    # ------------------------------------------------------------------
    # Internal helpers — batchGenerateImages capture (unit 3.6)
    # ------------------------------------------------------------------

    @staticmethod
    def _attach_batch_response_listener(
        page: Page,
        *,
        project_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], Callable[[], None]]:
        """Synchronously register a ``page.on('response', ...)`` listener
        that records ``batchGenerateImages`` responses into a shared list.

        When ``project_id`` is provided, only responses whose URL contains
        ``/projects/{project_id}/`` are captured — this prevents stale
        responses from previously-visited projects accumulating in the list.

        Returns ``(captured, detach_fn)``:
        - ``captured`` is the shared list — the caller submits the prompt
          next, then polls / awaits that list via :meth:`_await_captured`.
        - ``detach_fn`` removes the handler from the page when called; it is
          idempotent (safe to call multiple times).

        Registering the listener BEFORE issuing the prompt click eliminates
        the race where the click could fire before an ``asyncio.create_task``-
        scheduled listener attaches.
        """
        captured: list[dict[str, Any]] = []

        async def on_response(response: Any) -> None:
            if "batchGenerateImages" not in response.url:
                return
            # Log EVERY batchGenerateImages response BEFORE the project_id
            # filter so live verification can diagnose listener-miss bugs
            # (e.g., URL contains a different project_id than the editor URL).
            log.info(
                "ui_automation.batch_response_seen",
                url=response.url,
                status=response.status,
                filter_project_id=project_id,
            )
            if project_id and f"/projects/{project_id}/" not in response.url:
                log.warning(
                    "ui_automation.batch_response_dropped_project_id_mismatch",
                    url=response.url,
                    filter_project_id=project_id,
                )
                return
            try:
                body = await response.json()
            except Exception as e:
                log.warning(
                    "ui_automation.batch_response_parse_failed",
                    error=str(e),
                    url=response.url,
                )
                return
            captured.append(
                {
                    "status": response.status,
                    "url": response.url,
                    "body": body,
                    "ts": time.monotonic(),
                },
            )
            log.info(
                "ui_automation.batch_response_captured",
                status=response.status,
                url=response.url,
            )

        page.on("response", on_response)

        _detached = False

        def detach() -> None:
            nonlocal _detached
            if _detached:
                return
            _detached = True
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

        return captured, detach

    @staticmethod
    def _attach_batch_request_logger(
        page: Page,
        *,
        project_id: str | None = None,
        sink: list[dict[str, Any]] | None = None,
    ) -> Callable[[], None]:
        """Register a ``page.on('request', ...)`` that logs a compact summary of
        each outgoing ``batchGenerateImages`` body.

        The summary (see :func:`_summarize_batch_request_body`) surfaces whether
        the submit carries ``referenceEntities`` — the entity-reference spike's
        make-or-break signal — without dumping i2i image bytes. Returns an
        idempotent detach fn, torn down alongside the response listener.

        When *sink* is given, each matching request also appends
        ``{"url", "entity_ids"}`` so the caller can enforce the #170 submit
        backstop (:meth:`_assert_image_entities_attached`) after the run.
        """

        def on_request(request_obj: Any) -> None:
            if "batchGenerateImages" not in request_obj.url:
                return
            try:
                post_data = request_obj.post_data
            except Exception:
                post_data = None
            log.info(
                "ui_automation.batch_request_body",
                url=request_obj.url,
                filter_project_id=project_id,
                summary=_summarize_batch_request_body(post_data),
            )
            if sink is not None:
                sink.append(
                    {
                        "url": request_obj.url,
                        "entity_ids": _entity_ids_from_request_body(post_data),
                    }
                )

        page.on("request", on_request)

        _detached = False

        def detach() -> None:
            nonlocal _detached
            if _detached:
                return
            _detached = True
            try:
                page.remove_listener("request", on_request)
            except Exception:
                pass

        return detach

    @staticmethod
    async def _await_captured(
        captured: list[dict[str, Any]],
        timeout_s: float = 180.0,
        *,
        expected_count: int = 1,
        submit_time: float = 0.0,
        poll_interval_s: float = 0.5,
        straggler_window_s: float = 2.5,
    ) -> list[dict[str, Any]]:
        """Wait for ``expected_count`` batchGenerateImages responses.

        Flow generates N images via N separate API calls (not one call with
        N URLs). We poll until we have enough fresh responses (those whose
        ``ts >= submit_time``) or the timeout expires.

        **Defense A — post-submit-time filter (primary correctness fix):**
        Each captured entry carries a ``ts`` field written by the handler at
        append time (``time.monotonic()``). Only entries with
        ``entry["ts"] >= submit_time`` count toward ``expected_count``.  This
        eliminates the cross-contamination bug where a listener attached
        *before* the submit click inherits stale responses from prior prompts
        that arrived in the window between attach and click.

        When ``submit_time`` is 0.0 (the default, used by
        ``_capture_batch_response`` and legacy callers), all entries pass the
        filter, preserving backwards compatibility.

        **Straggler window:** after the count threshold is first reached the
        method waits an additional ``straggler_window_s`` seconds so that any
        slower same-submission responses (e.g. the last of a 2-image batch)
        can arrive before the list is snapshotted. This mirrors the Worker
        pattern (``_wait_for_n_new_images`` in the compile-growth monorepo).

        Raises ``TimeoutError`` if no fresh responses arrive within
        ``timeout_s``.  Returns the underlying response dicts (entries without
        the ``ts`` wrapper key) for entries with ``ts >= submit_time``.
        """
        deadline = time.monotonic() + timeout_s

        def _fresh() -> list[dict[str, Any]]:
            return [e for e in captured if e.get("ts", 0.0) >= submit_time]

        # Poll until we have enough fresh responses or the deadline passes.
        while time.monotonic() < deadline and len(_fresh()) < expected_count:
            await asyncio.sleep(poll_interval_s)

        fresh = _fresh()
        if not fresh:
            msg = f"No batchGenerateImages response within {timeout_s:.1f}s."
            raise TimeoutError(msg)
        if len(fresh) < expected_count:
            log.warning(
                "ui_automation.fewer_responses_than_expected",
                got=len(fresh),
                expected=expected_count,
            )
        else:
            # Threshold reached — wait for any slow stragglers from this same
            # submission before snapshotting the list.
            await asyncio.sleep(straggler_window_s)
            fresh = _fresh()

        # Return entries stripped of the internal `ts` bookkeeping key so
        # callers (_images_from_responses, tests) receive plain response dicts.
        return [{k: v for k, v in e.items() if k != "ts"} for e in fresh]

    @staticmethod
    async def _capture_batch_response(
        page: Page,
        timeout_s: float = 120.0,
        *,
        poll_interval_s: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Convenience wrapper: attach + await in one call.

        Useful when the caller has no work to interleave between attach
        and wait. ``generate_images`` does NOT use this — it splits the
        two halves so the listener is attached synchronously before
        ``_send_prompt`` issues the click.
        """
        captured, _detach = UiAutomationTransport._attach_batch_response_listener(page)
        return await UiAutomationTransport._await_captured(
            captured,
            timeout_s,
            poll_interval_s=poll_interval_s,
        )

    # ------------------------------------------------------------------
    # Internal helpers — image download (unit 3.8)
    # ------------------------------------------------------------------

    @staticmethod
    async def _download(
        urls: list[str],
        out_dir: Path,
        cookies: dict[str, str],
    ) -> list[Path]:
        """Download each URL into ``out_dir`` using session cookies.

        Saves to ``out_dir / image_NN.png`` (zero-padded index). Individual
        download failures are logged and skipped — the function returns the
        list of paths that DID write successfully.

        URLs whose host is not in :data:`_ALLOWED_DOWNLOAD_HOST_SUFFIXES`
        are skipped before any HTTP request is made — this prevents
        session cookies from being forwarded to a non-Google host through
        a malicious or compromised fifeUrl. Redirects are also disabled
        (``follow_redirects=False``) so an open-redirect on an allowed
        host cannot rebound the request to a third party.
        """
        import httpx  # local import — httpx is a runtime dependency

        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=False,
            cookies=cookies,
        ) as client:
            for i, url in enumerate(urls):
                if not _is_allowed_download_host(url):
                    log.error(
                        "ui_automation.download_host_rejected",
                        url=url,
                        allowed_suffixes=list(_ALLOWED_DOWNLOAD_HOST_SUFFIXES),
                    )
                    continue
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    # Auto-detect extension from Content-Type / magic bytes.
                    ct = resp.headers.get("content-type", "")
                    if "jpeg" in ct or "jpg" in ct or resp.content[:3] == b"\xff\xd8\xff":
                        ext = ".jpg"
                    else:
                        ext = ".png"
                    p = out_dir / f"image_{i:02d}{ext}"
                    p.write_bytes(resp.content)
                    paths.append(p)
                    log.info(
                        "ui_automation.image_saved",
                        path=str(p),
                        bytes=len(resp.content),
                        format=ext,
                    )
                except Exception as e:
                    log.exception(
                        "ui_automation.download_failed",
                        url=url,
                        error=str(e),
                    )
        return paths

    # ------------------------------------------------------------------
    # Protocol — generate_images (unit 3.9)
    # ------------------------------------------------------------------

    async def generate_images(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        """Submit ``request.prompt`` through Flow's editor and return the
        generated images as DTOs.

        If ``project_id`` is provided, navigates to that project. Otherwise
        creates a new one.

        Raises ``RuntimeError`` if setup() has not been called, the
        ``batchGenerateImages`` response is non-200, or the response is
        200 but contains no image URLs.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before generate_images()"
            raise RuntimeError(
                msg,
            )
        async with self._generate_lock:
            return await self._generate_images_locked(request, project_id=project_id)

    async def _generate_images_locked(
        self,
        request: GenerateImageRequest,
        *,
        project_id: str | None = None,
    ) -> list[GeneratedImage]:
        """Serialized body of generate_images — called under self._generate_lock."""
        from gflow_cli.api.transports.drivers.factory import (  # noqa: PLC0415
            get_ui_driver,
        )

        page: Page = self._page  # type: ignore[assignment]  # guard in caller
        out_dir = self._out_dir

        await self._enter_editor(page, out_dir, project_id=project_id)
        # Dismiss any Flow changelog / "What's new" overlay that may be on top
        # of the editor before we click into settings / submit (#26).
        await self._dismiss_blocking_overlays(page, out_dir)

        # Determine the arm this command REQUIRES: explicit --ui-mode / env, or
        # inferred — agent instructions (-i) are an agentic-only surface, so they
        # force agentic. get_ui_driver switches to the required arm as a
        # PREREQUISITE, VERIFIES via a DOM re-probe, and fails fast
        # (UiModeUnavailableError, exit 28) if the arm is unreachable — so -i can
        # never silently no-op on a classic bind, and no credits are spent. The
        # cohort flaps per page load, so this runs every generation (no caching).
        from gflow_cli.config import infer_required_ui_mode, resolve_ui_mode

        base_mode = request.ui_mode if request.ui_mode is not None else resolve_ui_mode(None)
        required_mode = infer_required_ui_mode(
            base_mode, has_instructions=bool(request.instructions)
        )
        # Inject ``self`` into the classic driver at construction (via the
        # factory) — never mutate ``_transport`` onto the driver after the fact.
        ui_driver = await get_ui_driver(page, ui_mode=required_mode, transport=self)

        # Select Image mode explicitly. If the account was last in Video mode,
        # an unguarded submission goes to the video endpoint and the image
        # listener never observes ``batchGenerateImages``.
        await ui_driver.switch_to_image_mode(page, out_dir=out_dir)

        # Resolve the project_id from the URL now that we're in the editor.
        nav_project_id = _extract_project_id(page.url)

        # Configure generation settings (aspect ratio + count) BEFORE attaching
        # the response listener so settings clicks don't interfere with capture.
        await ui_driver.configure_image_settings(
            page,
            request,
            out_dir=out_dir,
        )

        # I2I: bind local reference images through the editor's media dialog —
        # the same add_2 dialog as video R2V, via the inherited _attach_references.
        # The REST uploadImage path 401s, and passive capture needs the refs IN
        # the UI (not just a wire body), so we attach + let Flow's JS include them.
        if request.ref_paths:
            # prefer_existing: dedup a repeated local ref by selecting the
            # already-uploaded library asset (named by its filename) instead of
            # re-uploading it (#314). Image i2i only; R2V keeps upload-every-time.
            await self._attach_references(
                page, list(request.ref_paths), out_dir=out_dir, prefer_existing=True
            )

        # Pre-generated image UUID refs: attach by selecting the EXISTING Flow
        # asset in the reference picker (no duplicate upload — founder principle),
        # falling back to uploading the local file only when it can't be located.
        if request.refs:
            await self._attach_image_uuid_refs(
                page,
                [(r.name, r.display_name, r.local_path) for r in request.refs],
                out_dir=out_dir,
            )

        # Entity references: attach locked CHARACTER entities via the Personagens
        # picker (inherited from the video transport). The entity must live in the
        # project we generate in (pass --project / project_id). Flow's JS then
        # includes them on the submit; the request logger below records whether the
        # outgoing batchGenerateImages carries `referenceEntities` (spike signal).
        if request.reference_entities:
            await self._attach_character_entities(
                page,
                zip_entity_refs(request.reference_entities, request.reference_entity_names),
                out_dir=out_dir,
            )

        # Wrap generation submission in the reference entities interceptor context.
        # This programmatically filters outgoing batchGenerateImages requests so that
        # only the explicitly requested reference_entities are permitted to go to the server,
        # preventing stale or poisoned entities from smuggling themselves into unrelated runs.
        expected_ents = set(request.reference_entities)
        async with self._intercept_reference_entities(page, expected_ents):
            # Agentic path: DOM scraping (page-level network capture is dead in this
            # cohort — requests are Web-Worker-delegated, so 0 entries are captured).
            # The request + expected count are handed to the driver directly.
            from gflow_cli.api.transports.drivers.agentic import (  # noqa: PLC0415
                AgenticFlowUiDriver,
            )

            if isinstance(ui_driver, AgenticFlowUiDriver):
                return await ui_driver.submit_images(page, request, request.count, out_dir=out_dir)

            # Classic path: network-capture via response listener (unchanged).
            # Attach the response listener SYNCHRONOUSLY before any prompt
            # action. asyncio.create_task is unsafe here: it defers the listener
            # registration until the new task gets event-loop scheduling, which
            # could happen AFTER _send_prompt's click on a busy loop. Splitting
            # attach/await eliminates that race. Project-ID filter prevents stale
            # responses from previously-visited projects accumulating in the list.
            captured, detach = self._attach_batch_response_listener(page, project_id=nav_project_id)
            # Also log the OUTGOING request body summary AND collect the entity ids
            # it carries — the #170 submit backstop reads the sink after the run.
            request_bodies: list[dict[str, Any]] = []
            req_log_detach = self._attach_batch_request_logger(
                page, project_id=nav_project_id, sink=request_bodies
            )
            # Record submit_time BEFORE the click so the post-submit-time filter
            # in _await_captured can distinguish this prompt's responses from any
            # stale entries that arrived between listener attach and the click.
            submit_time = time.monotonic()
            responses: list[dict[str, Any]] = []
            try:
                await ui_driver.send_prompt(page, request.prompt, out_dir=out_dir)
                responses = await self._await_captured(
                    captured,
                    expected_count=request.count,
                    submit_time=submit_time,
                )
            finally:
                detach()
                req_log_detach()

        # Collect images from ALL captured responses (Flow makes one API call
        # per image when count > 1).
        images, first_error_status, first_error_route = _images_from_responses(responses)

        if first_error_status is not None and not images:
            raise WireFormatError(
                detail=f"batchGenerateImages returned HTTP {first_error_status}",
                status=first_error_status,
                route=first_error_route,
            )

        if not images:
            raise ContentPolicyError(
                detail="batchGenerateImages returned 200 but no parseable media items",
                route=first_error_route or "",
            )
        # Submit backstop (issue #170): the run is only a success if every
        # requested character entity actually rode the wire. A missed UI attach
        # (e.g. the include selector clicked the wrong element) would otherwise
        # return a plain text-only generation as if it used the character.
        if request.reference_entities:
            self._assert_image_entities_attached(
                request_bodies, expected=list(request.reference_entities)
            )
        return images

    @staticmethod
    def _assert_image_entities_attached(
        request_bodies: list[dict[str, Any]],
        *,
        expected: list[str],
    ) -> None:
        """Defense-in-depth mirror of the video path's _assert_entities_attached.

        *request_bodies* is the sink filled by :meth:`_attach_batch_request_logger`
        (one entry per captured outgoing ``batchGenerateImages`` body). Every
        *expected* entity id must appear in at least one captured submit;
        otherwise raise :class:`WireFormatError` — never report a text-only
        generation as an entity-referenced success.
        """
        seen: set[str] = set()
        for body in request_bodies:
            ids = body.get("entity_ids")
            if isinstance(ids, set):
                seen |= cast("set[str]", ids)
        missing = [e for e in expected if e not in seen]
        if missing:
            raise WireFormatError(
                detail=(
                    f"captured batchGenerateImages submit is missing "
                    f"referenceEntities {missing} — the staged character "
                    f"never rode the wire"
                ),
                route="flowMedia:batchGenerateImages",
                remediation_hint=ENTITY_ATTACH_DRIFT_HINT,
                discovery={"entity_attach_context": "image"},
            )
        log.info("ui_automation.image_entities_attached", entity_ids=sorted(seen))

    # ------------------------------------------------------------------
    # Public batch API — generate_images_batch (stay-mounted, v3-3)
    # ------------------------------------------------------------------

    async def generate_images_batch(
        self,
        *,
        prompts: list[GenerateImageRequest],
        jitter_range: tuple[float, float],
        continue_on_error: bool = False,
    ) -> list[BatchSubmissionResult]:
        """Submit all prompts into one Flow project and return per-prompt results.

        Opens the editor once, configures+submits each prompt with jitter
        between submissions, awaits and parses responses in submission order.
        The editor stays mounted for the full batch lifetime — this is the
        bug fix for --same-project=1 no-op (each call to generate_images
        previously created a new project and discarded the caller's project_id).

        With ``continue_on_error=False`` (default): the first per-prompt
        failure stops further submissions, remaining listeners are detached,
        and ``BatchPartialError`` is raised carrying any already-completed
        ``BatchSubmissionResult`` records so the orchestrator can salvage
        paid-for images before re-raising.

        With ``continue_on_error=True``: all prompts are submitted regardless
        of per-prompt failures; failed prompts produce results with
        ``status="fail"`` and a non-None ``error`` field.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before generate_images_batch()"
            raise RuntimeError(
                msg,
            )
        async with self._generate_lock:
            return await self._generate_images_batch_locked(
                prompts=prompts,
                jitter_range=jitter_range,
                continue_on_error=continue_on_error,
            )

    async def _run_one_prompt_in_batch(
        self,
        *,
        page: Page,
        idx: int,
        req: GenerateImageRequest,
        project_id: str,
        out_dir: Path | None,
        ui_driver: Any,
    ) -> tuple[BatchSubmissionResult, GFlowError | None]:
        """Single prompt's lifecycle inside a batch: configure → attach
        listener → submit → await → detach → parse.

        Returns ``(result, fatal_error)``.  ``fatal_error`` is ``None`` on
        success or whenever the failure was non-fatal (the caller can
        continue).  When non-``None`` it carries the :class:`GFlowError`
        the caller should propagate via :class:`BatchPartialError`.  Detach
        is guaranteed exactly once on every code path.
        """
        prompt_hash = _prompt_hash_stable(req.prompt)

        def _fail(exc: BaseException) -> tuple[BatchSubmissionResult, GFlowError]:
            g_exc = (
                exc
                if isinstance(exc, GFlowError)
                else GFlowError(detail=str(exc), route="generate_images_batch")
            )
            return (
                BatchSubmissionResult(
                    status="fail",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=(),
                    error=g_exc,
                ),
                g_exc,
            )

        # Step 1 — configure settings (aspect + count) for this prompt.
        try:
            await ui_driver.configure_image_settings(
                page,
                req,
                out_dir=out_dir,
                prompt_idx=idx,
            )
        except Exception as exc:
            return _fail(exc)

        # Agentic path: DOM scraping — no page-level listener (Web-Worker-delegated).
        from gflow_cli.api.transports.drivers.agentic import (  # noqa: PLC0415
            AgenticFlowUiDriver,
        )

        if isinstance(ui_driver, AgenticFlowUiDriver):
            try:
                images = await ui_driver.submit_images(page, req, req.count, out_dir=out_dir)
            except Exception as exc:
                return _fail(exc)
            return (
                BatchSubmissionResult(
                    status="ok",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=tuple(images),
                    error=None,
                ),
                None,
            )

        # Classic path: network-capture via response listener (unchanged).
        # Step 2 — attach a fresh listener JUST for this prompt.
        # Attaching after configure ensures settings-panel clicks never land
        # in the listener window.  Detach happens immediately after
        # _await_captured returns so no two listeners are ever live at once.
        captured, detach = self._attach_batch_response_listener(page, project_id=project_id)
        # Record submit_time BEFORE the click — defense-in-depth: the
        # post-submit-time filter in _await_captured rejects any stale
        # entries that slipped into the freshly-attached listener before
        # the click fired.
        submit_time = time.monotonic()

        # Step 3 — submit the prompt.
        try:
            await ui_driver.send_prompt(page, req.prompt, out_dir=out_dir)
        except Exception as exc:
            detach()
            return _fail(exc)

        # Step 4 — await THIS prompt's responses, then detach immediately.
        try:
            responses = await self._await_captured(
                captured,
                expected_count=req.count,
                submit_time=submit_time,
            )
        except Exception as exc:
            detach()
            return _fail(exc)
        detach()

        # Step 5 — parse responses.
        if len(responses) < req.count:
            err = GFlowError(
                detail=f"_await_captured timed out: got {len(responses)}/{req.count}",
                route="generate_images_batch",
            )
            return (
                BatchSubmissionResult(
                    status="fail",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=(),
                    error=err,
                ),
                err,
            )

        images, first_error_status, _ = _images_from_responses(responses)
        if not images:
            err = GFlowError(
                detail=f"no parseable images (first_error_status={first_error_status})",
                route="generate_images_batch",
            )
            return (
                BatchSubmissionResult(
                    status="fail",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=(),
                    error=err,
                ),
                err,
            )

        return (
            BatchSubmissionResult(
                status="ok",
                project_id=project_id,
                prompt_idx=idx,
                prompt_hash=prompt_hash,
                images=tuple(images),
                error=None,
            ),
            None,
        )

    async def _generate_images_batch_locked(
        self,
        *,
        prompts: list[GenerateImageRequest],
        jitter_range: tuple[float, float],
        continue_on_error: bool,
    ) -> list[BatchSubmissionResult]:
        """Serialized body of generate_images_batch — called under self._generate_lock.

        Strictly serial submission (Worker pattern): each prompt's full
        lifecycle (configure → attach → submit → await → detach → parse)
        completes before the next prompt's listener is attached.  Only one
        listener is active at a time, making cross-contamination structurally
        impossible even when Flow's response payload carries no per-submission
        identifier.

        The editor stays mounted for the full batch (same-project invariant
        intact) — only the submit/await cycle is serial.
        """
        from gflow_cli.api.transports.drivers.factory import (  # noqa: PLC0415
            get_ui_driver,
        )

        page: Any = self._page  # type: ignore[assignment]
        out_dir = self._out_dir

        # ---- Batch-setup phase (once per batch) ----
        await self._enter_editor(page, out_dir)
        project_id = _extract_project_id(page.url)
        if project_id is None:
            msg = (
                f"Could not extract project_id from editor URL after _enter_editor. URL: {page.url}"
            )
            raise RuntimeError(
                msg,
            )

        try:
            await self._dismiss_blocking_overlays(page, out_dir)
        except Exception:
            # Orphaned-project warning: _enter_editor succeeded (server-side project
            # was created) but a later setup step failed. Log so the user can find
            # their orphaned project on the Flow UI.
            log.warning(
                "ui_automation.orphaned_project_warning",
                project_id=project_id,
                page_url=page.url,
                failed_step="_dismiss_blocking_overlays",
            )
            raise

        # Probe the DOM for the active UI cohort AFTER _enter_editor.  The
        # cohort flaps per page load; bind once per batch (the editor stays
        # mounted so the cohort is stable for this batch's lifetime).
        # Classic driver requires a transport reference for send_prompt.
        # No instruction inference here: the image-batch manifest carries no
        # per-item -i instructions (single-prompt only), so the required arm is
        # just the resolved --ui-mode / env. Route through infer_required_ui_mode
        # if batch ever gains instruction support.
        from gflow_cli.config import resolve_ui_mode

        # Inject ``self`` into the classic driver at construction (via the
        # factory) — never mutate ``_transport`` onto the driver after the fact.
        ui_driver = await get_ui_driver(page, ui_mode=resolve_ui_mode(None), transport=self)

        try:
            await ui_driver.switch_to_image_mode(page, out_dir=out_dir)
        except Exception:
            log.warning(
                "ui_automation.orphaned_project_warning",
                project_id=project_id,
                page_url=page.url,
                failed_step="_switch_to_image_mode",
            )
            raise

        # ---- Serial per-prompt cycle: each prompt's lifecycle (configure →
        # attach → submit → await → detach → parse) is encapsulated in
        # ``_run_one_prompt_in_batch``.  This outer loop only manages
        # iteration, result collection, fail-fast control, and inter-prompt
        # jitter.
        results: list[BatchSubmissionResult] = []
        submit_error: GFlowError | None = None

        for idx, req in enumerate(prompts):
            result, fatal_err = await self._run_one_prompt_in_batch(
                page=page,
                idx=idx,
                req=req,
                project_id=project_id,
                out_dir=out_dir,
                ui_driver=ui_driver,
            )
            results.append(result)

            # Fail-fast: break before the next submission so we do not spend
            # credits on prompts the caller will not see in the success path.
            if result.status == "fail" and not continue_on_error and fatal_err is not None:
                submit_error = fatal_err
                break

            # Jitter between iterations (anti-bot cadence) — not after the last.
            if idx < len(prompts) - 1:
                await asyncio.sleep(random.uniform(*jitter_range))

        # Fail-fast: surface partial-results salvage so orchestrator can download
        # already-paid-for images before re-raising.
        if submit_error is not None and not continue_on_error:
            raise BatchPartialError(
                detail=f"batch failed at prompt index {len(results)}: {submit_error!s}",
                route="generate_images_batch",
                partial_results=tuple(r for r in results if r.status == "ok"),
                cause=submit_error,
            )

        return results

    # ------------------------------------------------------------------
    # Character editor — navigation + passive-capture entry (T4)
    # ------------------------------------------------------------------

    # Selector for the character editor's Slate prompt textbox — used as the
    # "editor mounted" readiness anchor.  This IS PROMPT_INPUT_SELECTORS[0];
    # duplicated here as a named constant so the character-editor path is
    # self-documenting without importing the tuple.
    _CHARACTER_EDITOR_READY_SELECTOR = 'div[role="textbox"][data-slate-editor="true"]'

    # Slot-add button for character image slots 1+ (body / accessories).
    #
    # Live DOM evidence (2026-06-02 spike, character editor with slot 0 filled)
    # disproved the old ``button:has(...).nth(1)`` heuristic.  Two ``add_2``
    # ligatures exist in the editor DOM and they are STRUCTURALLY DISTINCT:
    #
    #   1. Slot-add (the target):  ``<div role="button"
    #      aria-label="Adicionar imagem do personagem"><i ...>add_2</i></div>``
    #      — a ``[role=button]`` div whose ONLY accessible content is the
    #      ``add_2`` icon (icon-only; no visible/sibling text-label span).
    #   2. The decoy:  ``<button type="button" aria-haspopup="dialog">
    #      <i ...>add_2</i><span style="...clip...">…</span></button>`` — a real
    #      ``<button>`` carrying a sibling ``<span>`` text label.
    #
    # The old ``button:has(i.google-symbols:text('add_2'))`` selector did not
    # even MATCH the slot-add (it is a ``div[role=button]``, not ``<button>``);
    # ``.nth(1)`` of that locator landed on the wrong control entirely.
    #
    # Robust, language-agnostic discriminator: the slot-add is the ``add_2``
    # icon hosted in a ``[role=button]`` whose stripped ``inner_text`` is EXACTLY
    # the ligature ``"add_2"`` (icon-only).  The decoy's hidden label makes its
    # inner_text longer, so it is excluded.  The localised aria-label
    # ("Adicionar imagem do personagem") is NEVER the primary anchor — see
    # [[flow-locale-leak-icon-ligatures]] — it may only serve as a positional
    # hint.  ``text-is`` (exact match) is used so partial-ligature collisions
    # (e.g. ``add_2_box``) cannot match.
    _CHARACTER_SLOT_ADD_SELECTOR = "[role='button']:has(i.google-symbols:text-is('add_2'))"
    # The exact ligature an icon-only slot-add candidate's inner_text reduces to.
    _CHARACTER_SLOT_ADD_LIGATURE = "add_2"

    # Character-editor model picker.  The editor shows a model chip ("🍌 Nano
    # Banana 2") with an ``arrow_drop_down`` ligature; clicking it opens a menu
    # whose options are the product names.  Product names ("Nano Banana 2" /
    # "Nano Banana Pro") are NOT localized, so text matching is acceptable here.
    #
    # ⚠️ NOT yet spiked from the live DOM — this is a reasonable best-effort
    # selector cascade, live-confirmed later.  A failed pick is NON-FATAL:
    # generation proceeds with Flow's default model.
    _CHARACTER_MODEL_PICKER_TRIGGER_SELECTORS = (
        "button:has(i.google-symbols:text-is('arrow_drop_down'))",
        "[role='button']:has(i.google-symbols:text-is('arrow_drop_down'))",
    )

    async def _select_character_model(
        self,
        page: Any,
        model_alias: str,
        out_dir: Any = None,
    ) -> None:
        """Best-effort select the character model via the editor's model picker.

        ``model_alias`` is the friendly CLI alias (``"nano2"`` / ``"nanopro"``);
        it is mapped through :data:`CHARACTER_MODELS` to the UI display name.

        If the requested model is the DEFAULT (``nano2`` / "Nano Banana 2") the
        picker is left untouched — it is already selected, so an extra click is
        wasteful and risks closing nothing useful.

        Otherwise the dropdown is opened (the element bearing the
        ``arrow_drop_down`` ligature near the model chip) and the option whose
        visible text contains the display name is clicked.

        NON-FATAL: if the alias is unknown, or the dropdown / option cannot be
        found, a warning is logged and generation proceeds with Flow's default
        model.  The picker DOM is not yet spiked — see the selector constants.
        """
        display_name = CHARACTER_MODELS.get(model_alias.lower())
        if display_name is None:
            log.warning(
                "ui_automation.character_model_picker_not_found",
                model=model_alias,
                reason="unknown_alias",
            )
            return

        # nano2 / "Nano Banana 2" is the editor default — already selected.
        default_display = CHARACTER_MODELS["nano2"]
        if display_name == default_display:
            log.info(
                "ui_automation.character_model_selected",
                model=display_name,
                via="default_no_click",
            )
            return

        try:
            opened = False
            for trig_sel in self._CHARACTER_MODEL_PICKER_TRIGGER_SELECTORS:
                try:
                    trigger = page.locator(trig_sel).first
                    await trigger.wait_for(state="visible", timeout=4000)
                    await trigger.click()
                    await page.wait_for_timeout(400)
                    opened = True
                    break
                except Exception:
                    continue
            if not opened:
                raise RuntimeError("model-picker trigger not found")

            option_sel = f":has-text('{display_name}')"
            option = page.locator(option_sel).first
            await option.wait_for(state="visible", timeout=4000)
            await option.click()
            await page.wait_for_timeout(400)
            log.info(
                "ui_automation.character_model_selected",
                model=display_name,
                via=option_sel,
            )
        except Exception as e:
            log.warning(
                "ui_automation.character_model_picker_not_found",
                model=model_alias,
                display_name=display_name,
                error=str(e)[:120],
                note="Flow default model applies",
            )
            await _capture_debug_screenshot(page, out_dir, "debug_character_model_picker.png")
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

    async def _enter_character_editor(
        self,
        page: Any,
        *,
        project_id: str,
        entity_id: str,
        locale: str,
    ) -> None:
        """Navigate to the Flow character editor for an existing entity.

        Does NOT create a new project — navigates directly to the existing
        project's character editor URL via ``page.goto``.

        Readiness gate: waits for the prompt textbox
        (``div[role=textbox][data-slate-editor='true']``) to become visible,
        confirming the Slate editor has mounted.  Overlays are dismissed
        after navigation so they don't block subsequent interactions.
        """
        from gflow_cli.api import routes

        url = routes.character_editor_url(locale, project_id, entity_id)
        log.info(
            "ui_automation.entering_character_editor",
            url=url,
            project_id=project_id,
            entity_id=entity_id,
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await self._dismiss_blocking_overlays(page, self._out_dir)

        # Wait for the Slate editor to mount — the prompt textbox is the
        # reliable "editor ready" anchor for the character editor surface.
        try:
            await page.locator(self._CHARACTER_EDITOR_READY_SELECTOR).first.wait_for(
                state="visible",
                timeout=20_000,
            )
        except Exception as exc:
            shot = await _capture_debug_screenshot(
                page,
                self._out_dir,
                "debug_character_editor_not_ready.png",
            )
            msg = (
                f"Character editor not ready: prompt textbox not visible "
                f"within 20 s. URL: {page.url}.{screenshot_clause(shot)}"
            )
            raise RuntimeError(msg) from exc

        log.info("ui_automation.character_editor_ready", url=page.url)

    @staticmethod
    def _workflows_from_responses(
        responses: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Extract workflow metadata from captured batchGenerateImages responses.

        Returns a flat list of workflow dicts, each containing at minimum:
        ``name``, ``metadata`` (with ``primaryMediaId``), ``projectId``, and
        ``parentEntityId``.  The ``parentEntityId`` field binds the generation
        result to the character entity.

        Only workflow entries present in 200-status responses are returned;
        error responses are silently skipped (``_images_from_responses`` already
        raises on 401/403 before this is called).
        """
        workflows: list[dict[str, Any]] = []
        for response in responses:
            if response.get("status") != 200:
                continue
            body: dict[str, Any] = cast(_JsonObj, response.get("body") or {})
            raw_workflows = body.get("workflows", [])
            if not isinstance(raw_workflows, list):
                continue
            for wf_raw in cast(_AnyList, raw_workflows):
                if not isinstance(wf_raw, dict):
                    continue
                wf: dict[str, Any] = cast(_JsonObj, wf_raw)
                # Only surface workflows that carry the character-binding field.
                if "parentEntityId" not in wf:
                    log.debug(
                        "ui_automation.workflow_missing_parent_entity_id",
                        workflow_name=wf.get("name"),
                    )
                workflows.append(wf)
        return workflows

    async def generate_character_images(
        self,
        *,
        project_id: str,
        entity_id: str,
        request: CharacterImageRequest,
        image_reference_index: int,
        locale: str,
    ) -> tuple[list[GeneratedImage], list[dict[str, Any]]]:
        """Navigate to the character editor and generate images via passive capture.

        Reuses the same generate lock, listener, and parse helpers as the
        image-generation path — the difference is that we navigate to an
        EXISTING project's character editor (not a new project), and we
        return workflow metadata alongside the images so the caller can bind
        results to the character entity via ``parentEntityId``.

        ``image_reference_index``:
          - 0 → face slot (no slot-add interaction needed; the slot is
            already active in the character editor).
          - >= 1 → body / accessory slot; a click on the ``add_2`` slot-add
            button is required before submitting the prompt.

        Returns ``(images, workflows)`` where each ``workflows`` entry carries
        at minimum ``name``, ``metadata.primaryMediaId``, ``projectId``, and
        ``parentEntityId``.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before generate_character_images()"
            raise RuntimeError(msg)

        async with self._generate_lock:
            return await self._generate_character_images_locked(
                project_id=project_id,
                entity_id=entity_id,
                request=request,
                image_reference_index=image_reference_index,
                locale=locale,
            )

    async def _generate_character_images_locked(
        self,
        *,
        project_id: str,
        entity_id: str,
        request: CharacterImageRequest,
        image_reference_index: int,
        locale: str,
    ) -> tuple[list[GeneratedImage], list[dict[str, Any]]]:
        """Serialized body of generate_character_images — called under _generate_lock."""
        page: Any = self._page  # type: ignore[assignment]  # guard in caller
        out_dir = self._out_dir

        await self._enter_character_editor(
            page,
            project_id=project_id,
            entity_id=entity_id,
            locale=locale,
        )
        await self._dismiss_blocking_overlays(page, out_dir)

        # Characters have NO aspect-ratio control and NO per-generation settings
        # panel (the live editor renders neither — the old
        # _configure_generation_settings call only ever logged
        # ``gen_settings_panel_not_found`` and skipped).  Model selection uses
        # the editor's OWN model dropdown instead.  ``request.model`` is the
        # friendly CLI alias ("nano2" / "nanopro").  Best-effort, non-fatal:
        # a failed pick proceeds with Flow's default model.  Character
        # generation is always exactly one image per slot.
        await self._select_character_model(page, request.model, out_dir)

        # Slot-add: face (index 0) needs no interaction; body/accessories do.
        # Clicking slot-add auto-attaches the generated face as a reference (Flow's
        # JS) and pre-fills the new prompt box with Flow's localized triptych
        # template. The body path REPLACES that box with gflow's own
        # self-contained triptych prompt (locale-safe), independent of the
        # auto-attached face reference.
        is_body_slot = image_reference_index >= 1
        if is_body_slot:
            await self._click_character_slot_add(page, out_dir)

        # Attach listener BEFORE submit to eliminate the race condition.
        captured, detach = self._attach_batch_response_listener(page, project_id=project_id)
        submit_time = time.monotonic()
        responses: list[dict[str, Any]] = []
        try:
            if is_body_slot:
                await self._submit_body_prompt(page, request.prompt, out_dir)
            else:
                await self._send_prompt(page, request.prompt, out_dir)
            responses = await self._await_captured(
                captured,
                expected_count=1,
                submit_time=submit_time,
            )
        finally:
            detach()

        images, first_error_status, first_error_route = _images_from_responses(responses)

        if first_error_status is not None and not images:
            raise WireFormatError(
                detail=f"batchGenerateImages returned HTTP {first_error_status}",
                status=first_error_status,
                route=first_error_route,
            )

        if not images:
            from gflow_cli.errors import ContentPolicyError

            raise ContentPolicyError(
                detail="batchGenerateImages returned 200 but no parseable media items",
                route=first_error_route or "",
            )

        workflows = self._workflows_from_responses(responses)
        return images, workflows

    async def _click_character_slot_add(
        self,
        page: Any,
        out_dir: Any = None,
    ) -> None:
        """Click the slot-add (``add_2``) button for character image slots >= 1.

        Live DOM evidence (2026-06-02 spike) showed the character editor renders
        two ``add_2`` ligatures that are STRUCTURALLY distinct (see the
        ``_CHARACTER_SLOT_ADD_SELECTOR`` constant for the full DOM): the slot-add
        target is a ``[role=button]`` whose accessible content is icon-ONLY,
        while the decoy is a ``<button>`` that also carries a hidden text-label
        ``<span>``.  The previous ``.nth(1)`` heuristic was wrong — it indexed
        into a ``<button>``-only locator that never even matched the slot-add
        div.

        Selection logic (language-agnostic):

        1. Locate every ``[role=button]`` that hosts an ``add_2`` icon.
        2. Keep only the icon-ONLY candidates — those whose stripped
           ``inner_text`` is exactly the ``add_2`` ligature.  The decoy's hidden
           label lengthens its inner_text, excluding it.
        3. If exactly one icon-only candidate remains, click it.  If several
           remain, prefer the first (the slot-add renders adjacent to the
           character image slots, ahead of any other icon-only ``add_2``).

        The localised ``aria-label`` is intentionally NOT part of the predicate
        ([[flow-locale-leak-icon-ligatures]]); the icon-only test is the anchor.

        Non-fatal: a miss is logged at WARNING so generation can proceed
        (the prompt will apply to whichever slot is currently active).
        """
        try:
            loc = page.locator(self._CHARACTER_SLOT_ADD_SELECTOR)
            await loc.first.wait_for(state="visible", timeout=5_000)
            total = await loc.count()
            chosen = None
            chosen_index = -1
            for i in range(total):
                cand = loc.nth(i)
                try:
                    raw = await cand.inner_text()
                except Exception:
                    continue
                # Icon-only ⇔ the accessible text reduces to just the ligature.
                # The decoy carries a hidden label span → longer inner_text.
                if raw.strip() == self._CHARACTER_SLOT_ADD_LIGATURE:
                    chosen = cand
                    chosen_index = i
                    break
            if chosen is None:
                raise RuntimeError(f"no icon-only add_2 [role=button] among {total} candidate(s)")
            await chosen.click()
            await page.wait_for_timeout(400)
            log.info(
                "ui_automation.character_slot_add_clicked",
                image_reference_index=1,
                selector=self._CHARACTER_SLOT_ADD_SELECTOR,
                candidates=total,
                chosen_index=chosen_index,
            )
        except Exception as exc:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_character_slot_add.png")
            log.warning(
                "ui_automation.character_slot_add_failed",
                error=str(exc)[:120],
                screenshot=str(shot),
                note=(
                    "slot-add button not found; prompt will be submitted "
                    "to the currently-active slot"
                ),
            )

    # ------------------------------------------------------------------
    # Protocol — refresh_auth (unit 3.10) + teardown (unit 3.11)
    # ------------------------------------------------------------------

    async def refresh_auth(self) -> None:
        """No-op for the UI strategy.

        Flow's own JavaScript re-mints reCAPTCHA tokens and refreshes
        auth state inside the Page on every prompt submission. There is
        no separate token cache to refresh from this strategy's side.
        Kept on the Protocol surface for consistency with the HTTP
        strategies (S1/S2/S3) where refresh_auth has real work to do.
        """
        await asyncio.sleep(0)  # yield to event loop — Protocol-required async signature
        log.debug("ui_automation.refresh_auth_noop")

    async def teardown(self) -> None:
        """Close the Playwright context if this strategy owns it.

        Idempotent — safe to call multiple times. When ``_owns_playwright``
        is False (shared-page setup) the caller retains lifecycle
        ownership; this method releases nothing and just resets state.
        """
        if not self._setup_done:
            return
        from gflow_cli.api._engine import (  # noqa: PLC0415
            CONTEXT_TEARDOWN_TIMEOUT_S,
            DRIVER_STOP_TIMEOUT_S,
            close_context_bounded,
            run_teardown_step,
        )

        # Cancellation-complete teardown (D4): each step is bounded + shielded so
        # a CancelledError landing mid-close cannot skip the driver exit or the
        # lease release in `finally`; the original cancellation re-raises last.
        # Teardown order: (4) close context -> exit driver; (6) release lease.
        cancelled: BaseException | None = None
        try:
            if self._owns_playwright and self._pw_cm is not None:
                if self._ctx is not None:
                    # Bounded close + force-close fallback (issue #293) — same
                    # helper as FlowApiClient's teardown; this standalone path
                    # had the identical unbounded-close-and-swallow gap.
                    cancelled = (
                        await run_teardown_step(
                            close_context_bounded(self._ctx, owner="ui_automation"),
                            timeout=CONTEXT_TEARDOWN_TIMEOUT_S,
                            owner="ui_automation",
                            step="context_close",
                        )
                        or cancelled
                    )
                cancelled = (
                    await run_teardown_step(
                        self._pw_cm.__aexit__(None, None, None),
                        timeout=DRIVER_STOP_TIMEOUT_S,
                        owner="ui_automation",
                        step="driver_exit",
                    )
                    or cancelled
                )
        finally:
            # Release the profile lease last — after the context is closed and
            # the driver stopped (D3). No-op on the shared-page path (lease is
            # None). Field resets survive even a cancelled teardown.
            if self._lease is not None:
                self._lease.release()
                self._lease = None
            self._pw_cm = None
            self._ctx = None
            self._page = None
            self._setup_done = False
            self._owns_playwright = False
        if cancelled is not None:
            raise cancelled
