"""Common selectors, regexes and helpers for UiAutomationTransport mixins.

Extracted from `ui_automation.py` (#1249 wave 2) to bring the file under the
2,000-line ceiling while keeping every selector and helper available to all mixins.
"""

# The private helpers here are used across the `_ui_*` parts (#1249).
# pyright: reportUnusedFunction=false

from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import structlog

from gflow_cli.api._retry import parse_retry_after
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect, Model
from gflow_cli.api.transports._common import extract_project_id
from gflow_cli.api.transports.ui_automation_video import MODE_SWITCH_TRIGGER_SELECTORS
from gflow_cli.errors import (
    AuthExpiredError,
    RateLimitError,
    WafRejectionError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Locator, Page

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
# Class-only carrier anchor (#730): `.google-symbols` matches the labs `<i>` AND the
# migrated `<mat-icon>`, which both carry the class — verified against a real CSS engine in
# tests/api/transports/test_ligature_carrier.py. The tag-qualified form matched ZERO on the
# migrated host, and this constant has neither a carrier twin nor a text fallback, so the
# miss was total and silent: the picker is best-effort, so generation simply proceeded on
# whatever tier the editor happened to open at.
IMAGE_MODEL_PICKER_TRIGGER = (
    "button[aria-haspopup='menu']:has(.google-symbols:text-is('arrow_drop_down'))"
)
# Verified live 2026-08-26 (menu read WHILE OPEN, profile denon82):
#   ['Nano Banana Pro', 'Nano Banana 2', 'Nano Banana 2 Lite']
# `has-text` is a SUBSTRING match, so 'Nano Banana 2' also matches
# 'Nano Banana 2 Lite' — text-is pins the exact label instead. The previous
# comment claimed the three names were mutually unambiguous; that was true when
# written and silently expired when Flow added the Lite tier.
# `Imagen 4` is NO LONGER OFFERED. Its selector is kept so the governance test
# reports an honest MISS (tests/flow_selectors/test_model_governance.py) rather
# than the model quietly disappearing from the registry.
IMAGE_MODEL_OPTION_SELECTORS: dict[Model, tuple[str, ...]] = {
    # `text-is` was tried first and REMOVED 2026-08-26: it never matched live.
    # The recorded inventory stores whitespace-NORMALISED labels, but `text-is`
    # matches raw innerText, which carries a newline between the emoji and the
    # label. Keeping a selector that only matches in the fixture would make the
    # governance test bless something that cannot work against Flow.
    Model.NARWHAL: ("[role='menuitem']:has-text('Nano Banana 2'):not(:has-text('Lite'))",),
    Model.GEM_PIX_2: ("[role='menuitem']:has-text('Nano Banana Pro')",),
    Model.HARBOR_SEAL: ("[role='menuitem']:has-text('Nano Banana 2 Lite')",),
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
    # Migrated flow.google.com host: results are signed CDN URLs on this host
    # (Expires + KeyName=labs-flow-prod-cdn-key + Signature), measured 2026-09-05.
    "flow-content.google",
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
# Class-only carrier anchor (#730). `.google-symbols` covers the labs `<i>` and the migrated
# `<mat-icon>` alike — both carry the class, verified against a real CSS engine in
# tests/api/transports/test_ligature_carrier.py.
#
# This cascade was WORKING BY LUCK on the migrated host. Entries 0 and 1 were tag-qualified
# and matched nothing there; the submit only ever landed because entry 2 matches the
# `<mat-icon>`'s *text*. Observed live 2026-09-07:
# `prompt_submitted via="button:has-text('arrow_forward')"`. Two misses at ~2s each were
# paid on every submit before the fallback rescued it.
#
# The old `button:has(i:text(...))` entry is deleted, not kept: with entry 0 class-only it is
# strictly dominated — anything a bare `<i>` carrying that text could match, the class-only
# form matches too, and an `<i>` WITHOUT the class is not an icon carrier.
# `:text-is` (exact), NOT `:text` (substring). The old entry was
# `i.google-symbols:text('arrow_forward')`, whose substring match also accepts
# `arrow_forward_ios` — a real Material Symbol. The `<i>` qualifier happened to contain that
# on labs; dropping it for the class-only carrier made the over-match reachable, and the
# two-carrier fixture caught it as `matched 3 of 2` before it ever ran live. Same trap as
# `:text('upload')` accepting `drive_folder_upload` ([[flow-locale-leak-icon-ligatures]]).
SUBMIT_BUTTON_SELECTORS = (
    "button:has(.google-symbols:text-is('arrow_forward'))",
    "button:has-text('arrow_forward')",
)

# Prompt-format button in the character editor ("Format" in EN) — rewrites the
# typed prompt into Flow's character prompt-engineering shape.
#
# Live DOM, two frontends, two captures by scripts/dev/spike_character_prompt_format.py:
#
#   labs      2026-07-27:  <button disabled>
#                            <i class="... google-symbols ...">personal_recommendations</i>
#                            <span>Format</span></button>
#   migrated  2026-09-07:  <flow-format-prompt-button>
#                            <button ... aria-label="Formatar">
#                              <mat-icon class="... google-symbols ...">
#                                personal_recommendations</mat-icon>
#                              <span>Formatar</span></button></flow-format-prompt-button>
#
# Same ligature, different carrier tag (`<i>` vs `<mat-icon>`) — and the label is
# localised on both, so it is never an anchor. Hence: custom element first, then the
# ligature under either carrier. Full evidence, including why the EN `span:text-is`
# fallback was deleted rather than translated, in
# docs/superpowers/spikes/2026-09-07-character-format-button-anchor.md.
#
# The button ships `disabled` while the prompt box is empty (both hosts) — see
# :meth:`UiAutomationTransport.format_character_prompt` for why that matters.
#
# `:text()` not `:has-text()` (invalid inside `:has()`); `text-is` exact match so a
# longer ligature cannot partial-match.
# Observed-rewrite gate for the Format button (#727). Flow rewrites SERVER-SIDE:
# measured 2026-09-07 on the migrated host, the reshaped text reached the composer
# at ~5.4s. The budget is deliberately generous rather than fitted to that single
# sample -- the costs are asymmetric. Too long is bounded latency on an opt-in flag
# whose expiry falls back to exactly the old behaviour; too short silently
# reintroduces the bug, invisibly, which is what shipped. `elapsed_ms` is logged on
# every success so a real distribution accrues in production instead of being
# guessed here from n=1.
_FORMAT_REWRITE_TIMEOUT_S = 30.0
# Jittered, like every other wait in this module: a fixed cadence is a
# deterministic signature in front of Google's anti-bot stack.
_FORMAT_REWRITE_POLL_MS = 250
# Slate/ProseMirror re-render and normalise whitespace, so a bare `!=` fires on
# churn. Normalisation moves the length by a handful of characters; the observed
# rewrite moved it by 632 (19 -> 651). 16 sits far above the former and far below
# the latter.
_FORMAT_REWRITE_MIN_DELTA = 16

PROMPT_FORMAT_SELECTORS: tuple[str, ...] = (
    "flow-format-prompt-button button",
    "button:has(mat-icon:text-is('personal_recommendations'))",
    "button:has(i.google-symbols:text-is('personal_recommendations'))",
    "button:has(i:text-is('personal_recommendations'))",
)

# Self-contained, locale-independent triptych instruction for body generation.
# Live-verified 2026-06-02: produces a consistent front/side/back body image in ONE
# generation, seeded by the auto-attached face reference. We replace Flow's own
# (localized) pre-filled template with this rather than depending on reading/parsing it.
_BODY_TRIPTYCH_PREAMBLE = (
    "Full-body triptych in three angles: front, side (3/4), and back. "
    "High resolution, uniform studio lighting, consistent anatomical proportions "
    "across all angles, solid white background. "
)

# Body-mode settle gate — two-button character editor ("Portrait" / "Create
# Body", observed live 2026-07-25/26 on 0.43.0). When the switch has not
# settled, typing can land in the PORTRAIT composer and Flow autosaves the
# corruption via PATCH /v1/flowWorkflows/{id}. Current Flow reuses one Slate
# box; the auto-attached face reference proves it belongs to body mode. Legacy
# cohorts mount a second Slate box, detected by the count rising. Both signals
# are structural, never localized button text.
_BODY_SLOT_MOUNT_TIMEOUT_S = 10.0
_BODY_SLOT_MOUNT_POLL_MS = 250

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
#
# LABS-ONLY BY MEASUREMENT, not by intent. The migrated `flow.google.com` frontend renders
# the ligature `add` and renders `add_2` NOWHERE — composer add=1/add_2=0, editor
# add=2/add_2=0, per-surface controls passing (2026-09-07,
# docs/superpowers/spikes/2026-09-07-ligature-carrier-and-name-drift.md). This is a ligature
# NAME drift, not the carrier split #730 fixed, so adding a `mat-icon` twin would not help.
#
# It is harmless today only because no migrated path reaches here: `migrated_can_serve`
# refuses without a `project_id`, `ensure_editor` navigates straight to the project URL, and
# `character create` requires `--project`. Those guards are what a future port relaxes — so
# `_enter_editor` now names the host rather than blaming this cascade when it is reached.
# Per-selector visibility wait in the "+ New project" sweep. A full miss costs
# len(NEW_PROJECT_SELECTORS) x this; a module constant so the hermetic e2e can shorten it.
_NEW_PROJECT_PROBE_TIMEOUT_MS = 5000

NEW_PROJECT_SELECTORS = (
    # Tier 1 — structural / icon: locale-invariant.
    # The migrated `flow.google.com` gallery renders the CTA with the ligature **`add`**,
    # under a `<mat-icon>`, and renders `add_2` nowhere on that surface (measured
    # 2026-09-07, denon82, control 47 ligature nodes). Class-only so one entry covers both
    # carriers. Listed FIRST because without it every Tier-1 entry below misses there and
    # the CTA survives only on the English text fallback — which is the anti-pattern this
    # tier exists to avoid, and which fails outright on a non-EN migrated profile.
    "button:has(.google-symbols:text-is('add'))",
    "[role='button']:has(.google-symbols:text-is('add'))",
    "button:has(.google-symbols:text-is('add_2'))",
    "[role='button']:has(.google-symbols:text-is('add_2'))",
    # `button:text-matches('^\+\s+\S+$', 'i')` used to sit here. It is not a valid
    # Playwright selector and RAISES on every evaluation — measured twice against the live
    # gallery, both runs `ERR Error`. `except Exception: continue` in the sweep swallowed
    # that, so it has never matched anything on any host while costing a round trip per
    # attempt. Deleted rather than repaired: the `add` anchor above is what the "+ <word>"
    # regex was reaching for, and it is structural rather than a text shape.
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

# Top banner / announcement selectors (#369).
#
# DO NOT add bare `[role='dialog']` or `[role='alert']` here. Both shipped
# briefly and broke `gflow character create` (#395): Flow's own working
# surfaces carry those roles, so `_detect_overlay` matched the app itself and
# `_dismiss_blocking_overlays` pressed Escape on the character composer. The
# generation then went out WITHOUT `entityContext`, and Flow filed the portrait
# as a plain project image with no `parentEntityId` — a silent, credit-spending
# failure. Proven live 2026-07-28: with those two selectors removed, the very
# same command bound the character on the first try (`entity_patched`, real
# `thumbnail_media_id`); with them present it failed every run.
#
# Top banner / announcement selectors (#369, #403).
#
# DO NOT add bare `[role='dialog']` or `[role='alert']` here. Both shipped
# briefly and broke `gflow character create` (#395): Flow's own working
# surfaces carry those roles, so `_detect_overlay` matched the app itself and
# `_dismiss_blocking_overlays` pressed Escape on the character composer.
#
# Overlay detection is strictly language-agnostic, anchoring on DOM roles,
# structural element tags, and href attributes rather than localized text.
TOP_BANNER_SELECTORS: tuple[str, ...] = (
    "[role='banner']",
    "a[href*='changelog']",
    "a[href*='changelogs']",
)

# Close-button selectors tried after a changelog iframe or banner overlay is detected.
# Ordered from most-specific to most-generic so a precise match wins first.
# All are tried before the Escape fallback.
OVERLAY_CLOSE_BUTTON_SELECTORS: tuple[str, ...] = (
    "[role='dialog']:has(a[href*='changelog']) button",
    "button:has(i.google-symbols:text('clear'))",
    "button:has(i:text('clear'))",
    "button:has(i.google-symbols:text('close'))",
    "button:has(i:text('close'))",
    "[role='dialog'] button:has(i:text('close'))",
    "button[data-dismiss]",
)

# #593 split of the cascade above. The first entry requires a changelog link inside
# the dialog, so it cannot match one of Flow's own surfaces and is safe to try on any
# page. The rest are generic enough to match app chrome and only run once the page is
# known to be blocked.
_CHANGELOG_SCOPED_CLOSE: tuple[str, ...] = OVERLAY_CLOSE_BUTTON_SELECTORS[:1]
_GENERIC_CLOSE: tuple[str, ...] = OVERLAY_CLOSE_BUTTON_SELECTORS[1:]

# Detectors for the splash screen or welcome overlay (pure structural anchors).
WELCOME_SCREEN_SELECTORS: tuple[str, ...] = (
    "[role='dialog']:has(a[href*='flow'])",
    "[role='dialog']:has(a[href*='changelog'])",
)

# Selector-free block probe (#593). `pointer-events: none` on the body is what an
# overlay does to the app behind it; it is not tied to any announcement's markup,
# copy, or locale. Used three ways: as the gate that keeps the destructive Escape
# fallback off working surfaces (#395), as the post-dismissal verification, and as
# the post-mortem detail on a timeout.
_BLOCK_PROBE_JS = """
() => ({
  pointerEvents: getComputedStyle(document.body).pointerEvents,
  dialogs: document.querySelectorAll("[role='dialog'],[role='alertdialog'],dialog").length,
})
"""


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

# Regex that matches count-tab text exactly, across BOTH label cohorts:
# legacy "1x"/"x2"/"x3"/"x4" and the renamed "x1"/"x2"/"x3"/"x4" observed
# live 2026-07-31 (issue #404 — Flow unified the labels to xN, which made the
# old `^(1x|x[2-4])$` filter silently drop the count-1 tab).
# These are the ONLY role="tab" elements whose text fits this pattern —
# Mode tabs ("image\nImagem") and Aspect tabs ("16:9", "crop_square") do not.
# The pattern is locale-invariant: Flow never translates the digit+x label.
_COUNT_TAB_TEXT_RE = re.compile(r"^(1x|x[1-4])$")

# Subdirectory inside out_dir where diagnostic artefacts are written.
# Keeps count_before/after screenshots and DOM dumps out of the user-facing
# output directory so file-count assertions on *.png never pick them up.
_DIAGNOSTICS_SUBDIR = "_diagnostics"


def _count_tabs_locator(page: Page) -> Locator:
    """Return a Playwright Locator that matches ONLY the 4 count tabs.

    Filters ``role="tab"`` elements by text matching :data:`_COUNT_TAB_TEXT_RE`
    (legacy ``1x``/``x2``/``x3``/``x4`` and renamed ``x1``/``x2``/``x3``/``x4``,
    issue #404). The pattern is unique to count tabs — Mode tabs and Aspect
    tabs never match it — so the filter survives all three Radix tablists
    being present in the DOM simultaneously.

    DOM evidence: ``tmp/dom_dump.json`` (profile denon82, pt-BR, 2026-05-22)
    for the legacy cohort; ``scripts/dev/spike_issue404_count_tabs_recon.py``
    output (profile ffroliva, 2026-07-31) for the renamed cohort.
    """
    return page.locator('[role="tab"]').filter(has_text=_COUNT_TAB_TEXT_RE)


def _count_tab_locator_for(page: Page, count: int) -> Locator:
    """Locator for THE count tab carrying digit ``count``.

    Keyed on the digit in the label rather than position, so it survives the
    label-cohort rename (legacy ``1x`` → current ``x1``, issue #404) and any
    reordering/shrinking of the filtered tab set.
    """
    return page.locator('[role="tab"]').filter(has_text=re.compile(rf"^({count}x|x{count})$"))


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
            # Parse a one-item full response so the sibling ``workflows[]``
            # metadata is joined by workflow id.  Parsing the media item alone
            # drops Flow's ``displayName`` — the browser picker's search key.
            parsed = GeneratedImage.from_response_dict(
                {"media": [item], "workflows": body.get("workflows", [])}
            )
            images.extend(parsed)
        except ValueError as e:
            log.warning("ui_automation.parse_media_item_failed", error=str(e))


def _images_from_responses(
    responses: list[dict[str, Any]],
) -> tuple[list[GeneratedImage], int | None, str, dict[str, Any]]:
    """Process captured batchGenerateImages responses.

    Returns ``(images, first_error_status, first_error_route, first_error_body)``.
    Raises :class:`AuthExpiredError` on 401, :class:`WafRejectionError` on 403,
    and :class:`RateLimitError` on 429, which the caller must surface — these are
    not first-error candidates.

    The error **body** rides out with the status (issue #528): the caller has to
    tell a content-policy 400 apart from a genuinely unexpected response shape,
    and dropping the body here left it no way to.
    """
    images: list[GeneratedImage] = []
    first_error_status: int | None = None
    first_error_route: str = ""
    first_error_body: dict[str, Any] = {}

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
        if status == 429:
            retry_after = parse_retry_after(response)
            log.warning(
                "ui_automation.batch_429_body",
                body_prefix=str(body)[:200],
                route=route_str,
                retry_after=retry_after,
            )
            raise RateLimitError(
                detail=(
                    "batchGenerateImages HTTP 429 — rate limit hit."
                    + (f" Retry after {retry_after:.0f}s." if retry_after is not None else "")
                ),
                status=429,
                route=route_str,
                retry_after=retry_after,
            )
        if status != 200:
            if status == 400:
                # We still do not know Flow's real 400 shape on this route — every
                # #528 incident bundle showed a bare `{"status": 400}`. Log it like
                # the 403 branch above so the next occurrence settles it.
                log.warning(
                    "ui_automation.batch_400_body",
                    body_prefix=str(body)[:200],
                    route=route_str,
                )
            if first_error_status is None:
                first_error_status = status
                first_error_route = route_str
                first_error_body = body
            continue

        _collect_images_from_body(body, images)

    return images, first_error_status, first_error_route, first_error_body


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


def _reference_field_count(post_data: str | None) -> int:
    """How many reference/entity-ish fields ride on ``requests[0]`` (issue #528).

    A COUNT, never the key names: this feeds the incident bundle, which retains
    counts and booleans only (§5.3, S02/S29). The deciding signal for a policy
    400 is how many face-bearing references were attached, not what they were
    called.
    """
    if not post_data:
        return 0
    try:
        parsed: object = json.loads(post_data)
    except (ValueError, TypeError):
        return 0
    if not isinstance(parsed, dict):
        return 0
    reqs = cast(_JsonObj, parsed).get("requests")
    if not (isinstance(reqs, list) and reqs and isinstance(reqs[0], dict)):
        return 0
    first = cast(_JsonObj, reqs[0])
    return sum(1 for k in first if "reference" in k.lower() or "entity" in k.lower())


def _jitter_ms(base_ms: int, variance: float = 0.25) -> int:
    """Calculate a randomized delay around `base_ms` with `±variance` spread.

    Adds timing entropy to browser interaction delays to break deterministic Playwright
    automation fingerprints. Returns 0 if base_ms <= 0.
    """
    if base_ms <= 0:
        return 0
    delta = int(round(base_ms * max(0.0, min(1.0, variance))))
    return max(1, secrets.SystemRandom().randint(base_ms - delta, base_ms + delta))  # NOSONAR


async def _try_iframe_close_button(page: Page) -> bool:
    """Try each close-button selector inside each changelog iframe. Returns True on success."""
    for iframe_sel in CHANGELOG_IFRAME_SELECTORS:
        try:
            frame = page.frame_locator(iframe_sel).first
            for close_sel in OVERLAY_CLOSE_BUTTON_SELECTORS:
                loc = frame.locator(close_sel).first
                if await loc.is_visible(timeout=500):
                    await loc.click(force=True)
                    await page.wait_for_timeout(_jitter_ms(1000))
                    log.info(
                        _EVT_OVERLAY_DISMISSED,
                        selector=close_sel,
                        method="close_button_frame",
                    )
                    return True
        except Exception:
            continue
    return False
