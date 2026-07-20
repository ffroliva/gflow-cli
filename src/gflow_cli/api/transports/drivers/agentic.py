"""AgenticFlowUiDriver — the agentic (conversational) Flow composer layout.

Grounded by live capture 2026-06-14 (docs/AGENT_UI_RECON.md §§ "DOM scraping
validation", "Settings via prompt, not the tune popover"):

- Assets render as ``<img src="https://labs.google/fx/api/trpc/
  media.getMediaUrlRedirect?name=<uuid>[&mediaUrlType=…]">`` nodes.
- ONE asset produces MULTIPLE ``<img>`` nodes (full-res + thumbnail variants);
  scraping must deduplicate by ``name=<uuid>``, not by raw node count.
- Page-level network capture captures 0 entries (Web-Worker delegation); DOM
  scraping is the only viable capture path in the agentic cohort.
- The ``flag`` ligature is a normal per-message chat affordance (matched 11×
  on a successful generation) — it MUST NOT be treated as a policy signal.
- Settings are encoded in the prompt directive AS THE PRIMARY mechanism; the
  ``tune`` Radix-popover is driven as a best-effort FALLBACK for image count
  only (issue #313 — a stale sticky default there can silently override the
  natural-language directive). See ``_enforce_image_count_via_settings_panel``.

**Circular-import discipline:** this module must never import ``ui_automation``
or ``ui_automation_video`` at module load time. All such imports happen inside
the method body (function-level late import).
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.errors import (
    ContentPolicyError,
    FlowAgentUiError,
    MediaAttributionError,
    TransportTimeoutError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.image import AgentInstruction, GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest

log = structlog.get_logger(__name__)

# Stable tRPC redirect URL for a full-res asset (session-cookie authorised).
# Omit ``&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL`` to get the full-resolution
# version. The ``name=<uuid>`` query param is the stable backend media id.
_MEDIA_REDIRECT_BASE = "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={uuid}"

# Regex to extract the media UUID from a getMediaUrlRedirect src.
# Matches: …/media.getMediaUrlRedirect?name=<uuid>[&…]
_MEDIA_UUID_RE = re.compile(r"[?&]name=([0-9a-fA-F-]+)")

# Slate.js composer selector — confirmed present in the agentic cohort.
# The ``data-slate-editor="true"`` attribute distinguishes the primary input
# from any secondary contenteditable nodes that may exist in the page.
_SLATE_COMPOSER_SELECTOR = 'div[role="textbox"][data-slate-editor="true"]'
# structlog route tag for the await-images generation wait (used in 3 error paths).
_ROUTE_AWAIT_IMAGES = "agentic:await_images"

# Agent settings panel (issue #313, reworked 2026-07-16 after code review) —
# driven as a best-effort FALLBACK only, because prompt-encoding is the
# primary mechanism and this popover is the most drift-prone surface on
# Flow's UI (docs/AGENT_UI_RECON.md § "Settings via prompt, not the tune
# popover"). A sticky prior value in this panel can silently override the
# natural-language directive's requested count — that mismatch is issue
# #313's root cause.
#
# Reuses classic mode's count-tab primitives (_count_tabs_locator,
# _COUNT_TAB_TEXT_RE, UiAutomationTransport._is_settings_panel_open) via late
# import — live-verified 2026-07-16 that Flow's Agent settings panel uses the
# SAME role="tab"/aria-selected Radix pattern as classic mode's count
# popover: 8 matching [role="tab"] elements appear when the panel is open (4
# for "Image generation default", 4 for "Video generation default"), with
# the image section's 4 tabs always rendering first in DOM order. The tune
# open-trigger, the explicit Save requirement (classic's popover applies
# live, this panel does not), and the never-raise contract (classic's
# _set_count raises on failure since it has no fallback; this method DOES
# have a fallback — the natural-language directive — so it must degrade
# instead) are the genuine differences that prevent calling _set_count
# directly.

# The page has TWO ``arrow_back`` icon buttons when the panel is open: the
# main toolbar's "Voltar"/back-to-gallery button (live-verified 2026-07-16 —
# present at all times, NOT removed while the panel is open) and the panel's
# OWN header back arrow. Both happen to satisfy "walk up N ancestors and find
# a count-tablist" (Flow's DOM nests broadly enough that even the toolbar
# button reaches a shared ancestor within a few levels) — so picking the
# FIRST candidate that satisfies the walk (as an earlier version of this code
# did) can silently grab the WRONG one and navigate away from the project
# entirely. The fix: check EVERY arrow_back candidate and take the one with
# the SHALLOWEST (closest) matching ancestor — the panel's own back arrow is
# always structurally adjacent to its tablist (depth 1, live-verified);
# the toolbar's only reaches a shared ancestor much further up (depth 4+).
_FIND_PANEL_ROOT_JS_PRELUDE = """
  const backBtns = [...document.querySelectorAll('button')].filter((b) => {
    const i = b.querySelector('i.google-symbols');
    return i && (i.textContent || '').trim() === 'arrow_back';
  });
  let panelRoot = null;
  let backBtnEl = null;
  let bestDepth = Infinity;
  for (const backBtn of backBtns) {
    let node = backBtn.parentElement;
    for (let depth = 0; depth < 8 && node; depth++) {
      const hasCountTablist = [...node.querySelectorAll("[role='tab']")].some((t) =>
        /^(1x|x[2-4])$/.test((t.textContent || '').trim())
      );
      if (hasCountTablist) {
        if (depth < bestDepth) { panelRoot = node; backBtnEl = backBtn; bestDepth = depth; }
        break;
      }
      node = node.parentElement;
    }
  }
"""

# The panel's "Save" button has no icon ligature, no data-* attribute, no
# type="submit", and its text ("Salvar"/"Save"/etc.) is locale-dependent —
# per memory flow-locale-leak-icon-ligatures this cannot be text-matched.
# Locate it structurally instead: within the correctly-identified panel root
# (see _FIND_PANEL_ROOT_JS_PRELUDE), take the last visible <button> — that
# button is Save in every live-verified capture (2026-07-16). Tags the match
# with a data attribute so Playwright can locate it without re-running the
# walk in a second round-trip.
_FIND_SAVE_BUTTON_JS = (
    """
() => {
"""
    + _FIND_PANEL_ROOT_JS_PRELUDE
    + """
  if (!panelRoot) return false;
  const visible = [...panelRoot.querySelectorAll('button')].filter((b) => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  const save = visible[visible.length - 1];
  if (!save) return false;
  save.setAttribute('data-gflow-save-target', '1');
  return true;
}
"""
)

# The panel's own header back arrow — the verified way to abandon/close the
# panel WITHOUT saving (user-confirmed live: "the back arrow returns to the
# main area"). Uses the same panel-root disambiguation as Save (see
# _FIND_PANEL_ROOT_JS_PRELUDE) rather than an unscoped selector, which was
# live-verified to sometimes click the main toolbar's back-to-gallery button
# instead and navigate away from the project entirely — the root cause of
# send_prompt's composer never becoming visible again on that path.
_FIND_PANEL_BACK_BUTTON_JS = (
    """
() => {
"""
    + _FIND_PANEL_ROOT_JS_PRELUDE
    + """
  if (!backBtnEl) return false;
  backBtnEl.setAttribute('data-gflow-panel-back-target', '1');
  return true;
}
"""
)

# DOM polling defaults — match the classic transport's _await_captured cadence.
_POLL_INTERVAL_S = 0.5
_AWAIT_TIMEOUT_S = 180.0

# Auth session endpoint — same URL used by FlowApiClient._fetch_access_token.
_SESSION_API_URL = "https://labs.google/fx/api/auth/session"
_LABS_ORIGIN = "https://labs.google"

# Aspect-ratio human-readable labels for the prompt directive.
# Maps Aspect enum values to the natural-language clause injected into the
# prompt (e.g. "in 16:9 aspect ratio"). ``None`` omits the clause (default).
_ASPECT_PROMPT_LABEL: dict[str, str] = {
    "IMAGE_ASPECT_RATIO_PORTRAIT": "9:16",
    "IMAGE_ASPECT_RATIO_LANDSCAPE": "16:9",
    "IMAGE_ASPECT_RATIO_SQUARE": "1:1",
    "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE": "4:3",
    "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR": "3:4",
}

# Content-policy text signals (case-insensitive substring match).
# CONSERVATIVE: only raise on an *explicit* textual block; let the poll
# continue on ambiguous or unknown text. A deliberate content-policy-refusal
# capture is still outstanding (docs/AGENT_UI_RECON.md § "Open follow-ups");
# the exact selector below is therefore provisional.
_POLICY_TEXT_SIGNALS: tuple[str, ...] = (
    "content policy",
    "can't create",
    "violat",
    "not able to generate",
)

# Content-policy detection is scoped to explicit alert / dialog / live regions.
# Scanning the whole page body false-positives on STATIC CHROME — e.g. a
# "Content policy" footer/menu link present on every load (observed live
# 2026-06-14: a benign prompt raised a spurious block within ~6 s). A real block
# surfaces in an alert/dialog; a chat-message-only refusal would be missed, which
# is the acceptable trade (a miss → timeout beats a false positive that breaks
# every benign generation). Pending a captured positive sample
# (docs/AGENT_UI_RECON.md § "Open follow-ups").
_POLICY_REGION_SELECTOR = '[role="alert"], [aria-live="assertive"], [role="dialog"]'


def _extract_uuids(srcs: list[str]) -> set[str]:
    """Return the set of distinct media UUIDs from a list of img src strings."""
    uuids: set[str] = set()
    for src in srcs:
        m = _MEDIA_UUID_RE.search(src)
        if m:
            uuids.add(m.group(1))
    return uuids


async def _scrape_img_srcs(page: Page) -> list[str]:
    """Return the src attribute of every <img> on the page.

    Uses ``eval_on_selector_all`` (Playwright synchronous JS evaluation over
    all matching nodes) to capture the full src list in a single round-trip.
    Returns an empty list on any evaluation error (best-effort; the caller's
    poll loop retries).
    """
    try:
        result: Any = await page.eval_on_selector_all(
            "img",
            "nodes => nodes.map(n => n.src || '')",
        )
        if isinstance(result, list):
            items = cast("list[Any]", result)
            return [s for s in items if isinstance(s, str) and s]
        return []
    except Exception as exc:  # noqa: BLE001
        log.debug("agentic_driver.scrape_img_srcs_failed", error=str(exc))
        return []


async def _check_content_policy(page: Page) -> bool:
    """True if an alert/dialog region shows an explicit content-policy block.

    CONSERVATIVE: scans only ``[role=alert]`` / ``[aria-live=assertive]`` /
    ``[role=dialog]`` text for an explicit policy phrase — NOT the whole page
    body (static chrome like a "Content policy" footer link matches every load
    and produced a false positive on a benign prompt live, 2026-06-14). The
    ``flag`` ligature and bare body text are deliberately excluded. Pending a
    captured positive sample to widen this safely.
    """
    try:
        texts: Any = await page.eval_on_selector_all(
            _POLICY_REGION_SELECTOR,
            "nodes => nodes.map(n => (n.innerText || '').toLowerCase())",
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("agentic_driver.policy_probe_failed", error=str(exc))
        return False
    if not isinstance(texts, list):
        return False
    items = cast("list[Any]", texts)
    joined = " ".join(t for t in items if isinstance(t, str))
    return any(sig in joined for sig in _POLICY_TEXT_SIGNALS)


class AgenticFlowUiDriver:
    """Driver for the agentic chat UI (prompt-encoded settings, DOM-scraped responses).

    The driver holds **no per-request state**: ``submit_images`` takes the
    :class:`GenerateImageRequest` and the expected image count directly and
    threads the request-derived count / aspect / model into ``send_prompt``
    (which composes the Slate directive and submits via ``insert_text`` — the
    Slate-friendly path; ``fill()`` is ignored by Slate's contenteditable
    editor) and ``await_images``. This matters because the transport reuses ONE
    driver across every prompt in a batch, so any stashed ``_pending_*`` field
    would be shared mutable state between prompts.

    ``await_images`` polls the DOM for new ``<img>`` nodes, deduplicates by
    ``name=<uuid>`` from the tRPC redirect URL, and builds ``GeneratedImage``
    objects whose wire-only fields (``seed``, ``workflow_id``, etc.) are set to
    scrape-synthesised sentinel values (documented below).
    """

    name = "agentic"

    # ------------------------------------------------------------------
    # FlowUiDriver protocol — mode switching
    # ------------------------------------------------------------------

    async def switch_to_image_mode(  # NOSONAR
        self,
        page: Page,  # NOSONAR
        *,
        out_dir: Path | None = None,  # NOSONAR
    ) -> None:
        """No-op: the agentic UI has no explicit mode toggle.

        The conversational agent infers image vs. video from the prompt
        directive.  We record an internal hint so ``send_prompt`` can compose
        correctly and log a debug event so the call-site trace shows the
        driver was entered.
        """
        log.debug("agentic_driver.switch_to_image_mode.noop")

    async def switch_to_video_mode(self, page: Page, *, out_dir: Path | None = None) -> None:
        """Agentic video is not yet supported (no evidence-backed scraping path).

        The video scraping path is unvalidated — a deliberate video-generation
        capture in the agentic cohort is still outstanding. Raise a typed error
        so the caller surfaces a clear, actionable message rather than timing out.
        """
        raise FlowAgentUiError(
            detail=(
                "Agentic video is not yet supported: DOM scraping for video "
                "assets in the agentic cohort has not been validated by a live "
                "capture. Use a Classic UI profile or wait for a future update."
            ),
        )

    # ------------------------------------------------------------------
    # FlowUiDriver protocol — settings configuration
    # ------------------------------------------------------------------

    async def configure_image_settings(  # NOSONAR
        self,
        page: Page,  # NOSONAR
        request: GenerateImageRequest,
        *,
        out_dir: Path | None = None,  # NOSONAR
        prompt_idx: int | None = None,
    ) -> None:
        """Reconcile agent instruction cards and best-effort enforce the count
        via the Agent settings panel.

        Stores **nothing** on the driver — the count / aspect / model reach the
        directive through ``submit_images`` at submit time. Does NOT drive the
        ``tune`` popover for aspect/model — only count, as a fallback for issue
        #313 (a stale sticky default there can override the natural-language
        directive). See ``_enforce_image_count_via_settings_panel``.
        """
        log.debug(
            "agentic_driver.configure_image_settings",
            count=request.count,
            aspect=_ASPECT_PROMPT_LABEL.get(str(request.aspect)),
            model=str(request.model),
            prompt_idx=prompt_idx,
        )

        if request.instructions is not None:
            await self._reconcile_instructions(page, request.instructions)

        await self._enforce_image_count_via_settings_panel(page, request.count)

    async def _enforce_image_count_via_settings_panel(self, page: Page, count: int) -> None:
        """Best-effort: set Agent mode's sticky "Image generation default"
        count to match ``count`` via the ``tune`` Settings panel.

        Fallback mechanism for issue #313 — see the module-level selector
        comments for why this reuses classic mode's count-tab primitives and
        why it cannot simply call classic's ``_set_count`` unmodified.

        **Never raises, and always leaves the panel closed / composer
        visible before returning** — on every path (success, short-circuit,
        or failure), so ``send_prompt``'s composer click is never blocked by
        a panel this method left open. Any selector miss (older UI cohort
        with no ``tune`` panel at all, a future Flow redesign, a transient
        render delay) degrades to the natural-language directive alone —
        the pre-existing behavior — rather than a hard failure.
        """
        # Late imports — agentic.py must not import ui_automation or
        # drivers.factory at module load time (circular: factory imports
        # this module to build AgenticFlowUiDriver).
        from gflow_cli.api.transports.drivers.factory import (  # noqa: PLC0415
            AGENT_TUNE_INDICATOR_SELECTOR,
        )
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415
            UiAutomationTransport,
            _count_tabs_locator,  # type: ignore[reportPrivateUsage]
        )

        try:
            already_open = await UiAutomationTransport._is_settings_panel_open(  # type: ignore[reportPrivateUsage]
                page
            )
            if not already_open:
                tune_btn = page.locator(f"button:has({AGENT_TUNE_INDICATOR_SELECTOR})").first
                if await tune_btn.count() == 0:
                    log.debug("agentic_driver.settings_panel.tune_not_found")
                    return
                await tune_btn.click(timeout=5_000)
                # Allow the panel to render — matches classic mode's
                # _open_gen_settings_panel wait, needed for the same reason
                # (React re-render lag; Locator.count() does not auto-wait).
                await page.wait_for_timeout(600)

            tabs = _count_tabs_locator(page)
            total = await tabs.count()
            if total < count:
                log.warning(
                    "agentic_driver.settings_panel.count_button_not_found",
                    count=count,
                    tabs_found=total,
                )
                await self._close_agent_settings_panel(page)
                return

            # Image section's tabs render first in DOM order (live-verified
            # 2026-07-16) — nth(count - 1) targets the image count tab, not
            # the video section's tabs that follow it.
            target_tab = tabs.nth(count - 1)

            if await target_tab.get_attribute("aria-selected") == "true":
                # Already correct — the common case. No click, no Save
                # needed; just restore the composer.
                log.debug("agentic_driver.settings_panel.count_already_correct", count=count)
                await self._close_agent_settings_panel(page)
                return

            _max_attempts = 3
            converged = False
            for attempt in range(1, _max_attempts + 1):
                await target_tab.click(timeout=5_000)
                await page.wait_for_timeout(300)
                if await target_tab.get_attribute("aria-selected") == "true":
                    converged = True
                    break
                if attempt < _max_attempts:
                    # Brief pause before retry to allow React re-render —
                    # same rationale as classic mode's _set_count.
                    await page.wait_for_timeout(500)

            if not converged:
                log.warning(
                    "agentic_driver.settings_panel.count_not_converged",
                    count=count,
                    attempts=_max_attempts,
                )
                await self._close_agent_settings_panel(page)
                return

            found_save = await page.evaluate(_FIND_SAVE_BUTTON_JS)
            if not found_save:
                log.warning("agentic_driver.settings_panel.save_button_not_found")
                await self._close_agent_settings_panel(page)
                return

            # Save auto-closes the panel back to the composer (live-verified
            # 2026-07-16) — no further close step needed on this path.
            await page.locator("[data-gflow-save-target='1']").first.click(timeout=5_000)
            await page.wait_for_timeout(300)
            log.debug("agentic_driver.settings_panel.count_enforced", count=count)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "agentic_driver.settings_panel.enforce_count_failed",
                error=str(e)[:120],
            )
            await self._close_agent_settings_panel(page)

    @staticmethod
    async def _close_agent_settings_panel(page: Page) -> None:
        """Best-effort: click the panel's OWN back arrow to abandon it
        WITHOUT saving, restoring the composer. Never raises — this is
        itself a failure-path cleanup step, so any error here is logged and
        swallowed rather than propagated.

        Uses the panel-root-scoped lookup (:data:`_FIND_PANEL_BACK_BUTTON_JS`)
        rather than an unscoped arrow_back selector — live-verified
        2026-07-16 that the page has a SECOND, unrelated arrow_back button
        (the main toolbar's back-to-gallery control) that an unscoped
        `.first` can grab instead, navigating away from the project entirely.

        Safe to call even if the panel is already closed (the JS walk simply
        finds no matching panel root and returns ``false``).
        """
        try:
            found = await page.evaluate(_FIND_PANEL_BACK_BUTTON_JS)
            if not found:
                return
            back_btn = page.locator("[data-gflow-panel-back-target='1']").first
            await back_btn.click(timeout=3_000)
            await page.wait_for_timeout(300)
        except Exception as e:  # noqa: BLE001
            log.debug("agentic_driver.settings_panel.close_failed", error=str(e)[:120])

    async def _reconcile_instructions(
        self,
        page: Page,
        requested: tuple[AgentInstruction, ...],
    ) -> None:
        """Sync the agent instruction cards via the REST API (PATCH agentInfo).

        Replaces the prior DOM-loop approach: directly PATCHes
        ``/v1/projects/{projectId}/agentInfo?updateMask=project_brief.cards``
        with the full desired card set, eliminating Playwright DOM reconciliation
        entirely.  Auth reuses the same Bearer token path as FlowApiClient.

        Content-type is ``text/plain;charset=UTF-8`` (the aisandbox default).
        The previous ``application/json+protobuf`` value made Flow reject the
        JSON-object body with HTTP 400 ("JSPB Fava message don't accept
        top-level braces") — and the status was unchecked, so instruction sync
        failed **silently** (instructions spike, 2026-07-08). We now log the
        status and warn on any non-2xx so a broken sync is visible.
        """
        import json
        import re

        from gflow_cli.api.image import build_agent_brief_cards

        project_id_match = re.search(r"/project/([^/?#\s]+)", str(page.url))
        if project_id_match is None:
            log.warning(
                "agentic_driver.reconcile_instructions.no_project_id",
                url=page.url,
            )
            return

        project_id = project_id_match.group(1)

        # Fetch the SPA access token via the /fx/api/auth/session endpoint —
        # the same path used by FlowApiClient._fetch_access_token.
        session_resp = await page.request.get(_SESSION_API_URL)
        try:
            session_data = await session_resp.json()
        except Exception:  # noqa: BLE001
            session_data = {}
        if isinstance(session_data, dict):
            session_dict = cast("dict[str, object]", session_data)
            token_val = session_dict.get("access_token")
            access_token = str(token_val) if isinstance(token_val, str) else ""
        else:
            access_token = ""

        headers = {
            "authorization": f"Bearer {access_token}",
            "origin": _LABS_ORIGIN,
            "content-type": "text/plain;charset=UTF-8",
        }

        # Serialize via the shared builder so the wire shape + per-card title
        # stay identical to FlowApiClient.patch_agent_info (no drift).
        cards = build_agent_brief_cards(requested, project_id=project_id)

        # ``projectBrief.enabled`` is the brief-level MASTER switch: on a fresh
        # project it defaults off, and while off the agent ignores every card
        # regardless of per-card ``enabled`` or prompt phrasing (instructions
        # spike e2e, 2026-07-08 — cards synced but output stayed photorealistic
        # until the master flag was set). Turn it on whenever we sync cards so
        # the enabled ones actually reach the agent's reasoning step.
        body: dict[str, Any] = {"projectBrief": {"enabled": True, "cards": cards}}
        url = (
            f"https://aisandbox-pa.googleapis.com/v1/projects/{project_id}"
            f"/agentInfo?updateMask=project_brief.enabled,project_brief.cards"
        )
        log.debug(
            "agentic_driver.reconcile_instructions.patch",
            project_id=project_id,
            card_count=len(cards),
        )
        resp = await page.request.patch(url, data=json.dumps(body), headers=headers)
        status = getattr(resp, "status", None)
        if isinstance(status, int) and status >= 400:  # noqa: PLR2004
            # Never fail the generation over a brief sync (instructions are
            # supplementary) — but make the failure loud so it isn't silent.
            log.warning(
                "agentic_driver.reconcile_instructions.patch_failed",
                project_id=project_id,
                status=status,
            )

    async def configure_video_settings(
        self,
        page: Page,
        request: GenerateVideoRequest,
        *,
        out_dir: Path | None = None,
    ) -> None:
        """Agentic video is not yet supported — see ``switch_to_video_mode``."""
        raise FlowAgentUiError(
            detail=(
                "Agentic video settings are not yet supported: DOM scraping for "
                "video assets in the agentic cohort has not been validated. "
                "Use a Classic UI profile or wait for a future update."
            ),
        )

    # ------------------------------------------------------------------
    # FlowUiDriver protocol — prompt submission
    # ------------------------------------------------------------------

    @staticmethod
    def _compose_directive(count: int, aspect: str | None, prompt_text: str) -> str:
        """Build the conversational request string for the Slate composer.

        Template: ``Make me a picture of {prompt}[ in a {aspect} aspect ratio].``
        (``pictures`` when ``count > 1``.) When no aspect is stored, that clause
        is omitted.

        **Why conversational, not ``Generate N images: …``** — the instructions
        spike (2026-07-08) showed that an imperative ``"Generate one image: X"``
        directive is passed to the image tool **verbatim**, so the project-brief
        instruction cards are never applied. A natural-language request
        ("Make me a picture of X") instead engages the agent's reasoning step,
        which rewrites the tool prompt to fold in every *enabled* card (live:
        an enabled "crayon drawing" card produced a crayon image only via this
        phrasing). Flow's own help frames the Agent as driving generation
        "through natural conversation". This phrasing is what makes
        ``gflow ... -i "…"`` instructions actually take effect. See
        docs/AGENT_UI_RECON.md and the plan's ``spike-findings.md``; the live e2e
        (tests/e2e/test_live_agentic_instructions.py) asserts a card styles output.

        Kept as a staticmethod so unit tests can call it directly without
        constructing a full driver instance.
        """
        subject = "a picture" if count == 1 else f"{count} pictures"
        aspect_clause = f" in a {aspect} aspect ratio" if aspect else ""
        return f"Make me {subject} of {prompt_text}{aspect_clause}."

    async def send_prompt(
        self,
        page: Page,
        prompt_text: str,
        *,
        out_dir: Path | None = None,  # NOSONAR
        count: int = 1,
        aspect: str | None = None,
    ) -> None:
        """Compose the directive from ``prompt_text`` + ``count`` / ``aspect``
        and submit it into the Slate composer.

        ``count`` / ``aspect`` are passed in per call (by ``submit_images``) —
        never read from driver state — so the batch-reused driver carries no
        cross-prompt bleed.

        Slate's contenteditable editor ignores Playwright's ``fill()`` method
        because ``fill()`` dispatches an ``input`` event with ``isTrusted=false``
        that Slate filters out.  This method uses ``keyboard.insert_text`` — the
        same Slate-friendly path the classic transport's ``_send_prompt`` uses
        (a single ``beforeinput`` event Slate handles natively).

        Submit: the ``arrow_forward`` Material Symbol Create button (same
        ``SUBMIT_BUTTON_SELECTORS`` the classic path uses). Falls back to
        pressing ``Enter`` if no button is found within a short timeout.
        """
        # Late import — agentic.py must not import ui_automation at module load.
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415
            SUBMIT_BUTTON_SELECTORS,
        )

        directive = self._compose_directive(count, aspect, prompt_text)

        # Locate the Slate composer.  Playwright raises if it times out.
        composer = page.locator(_SLATE_COMPOSER_SELECTOR).first
        await composer.wait_for(state="visible", timeout=15_000)
        await composer.click()

        # Type via a real beforeinput event — Slate ignores fill(); insert_text
        # is the proven Slate path (mirrors classic _send_prompt).
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Delete")
        await page.keyboard.insert_text(directive)

        log.debug(
            "agentic_driver.send_prompt.typed",
            directive_preview=directive[:80],
        )

        # Submit via the arrow_forward Create button.
        submitted = False
        for sel in SUBMIT_BUTTON_SELECTORS:
            btn = page.locator(sel).first
            try:
                if await btn.count() > 0:
                    await btn.click(timeout=5_000)
                    submitted = True
                    break
            except Exception:  # noqa: BLE001
                continue

        if not submitted:
            # Enter fallback — works when the button is temporarily hidden.
            await page.keyboard.press("Enter")
            log.debug("agentic_driver.send_prompt.enter_fallback")

    # ------------------------------------------------------------------
    # Agentic image generation — submit + DOM-scrape (cohort-specific)
    # ------------------------------------------------------------------

    async def submit_images(
        self,
        page: Page,
        request: GenerateImageRequest,
        expected_count: int,
        *,
        out_dir: Path | None = None,
    ) -> list[GeneratedImage]:
        """Submit ``request`` and return the scraped images.

        The single entry point the transport calls for the agentic cohort. It
        takes the request and expected count **directly** (there is no
        ``configure``-then-``send`` handshake carrying state on the driver):
        derives the human-readable aspect label once, composes + submits the
        directive via :meth:`send_prompt`, then DOM-scrapes via
        :meth:`await_images` — threading the request's model / aspect straight
        into the built ``GeneratedImage`` DTOs.
        """
        aspect = _ASPECT_PROMPT_LABEL.get(str(request.aspect))
        await self.send_prompt(
            page, request.prompt, out_dir=out_dir, count=request.count, aspect=aspect
        )
        return await self.await_images(
            page,
            expected_count,
            out_dir=out_dir,
            model=str(request.model),
            aspect=aspect,
        )

    # ------------------------------------------------------------------
    # DOM scraping — await_images
    # ------------------------------------------------------------------

    async def await_images(
        self,
        page: Page,
        expected_count: int,
        *,
        out_dir: Path | None = None,  # NOSONAR
        model: str = "NARWHAL",
        aspect: str | None = None,
    ) -> list[GeneratedImage]:
        """Poll the DOM until ``expected_count`` distinct new media UUIDs appear.

        **Deduplication:** one generated asset surfaces as multiple ``<img>``
        nodes (full-res + thumbnail variants, each also in bare and
        ``&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL`` forms). The 2026-06-14
        live capture observed 9 new ``<img>`` nodes for only 3 distinct
        assets. Counting by raw node count over-counts ~3×; this method
        counts **distinct ``name=<uuid>`` values** extracted from the src
        attribute of every ``<img>`` on the page.

        **Timeout:** 180 s (matching the classic transport's ``_await_captured``
        default). On partial completion the error detail includes the
        produced-vs-requested mismatch count.

        **Baseline settle (issue #281):** a single baseline scrape can miss
        pre-existing project tiles that lazily render a moment later — those
        would then be miscounted as "new" media and silently attributed to
        this generation (the 2026-07-10 production incident: a pre-existing
        logo was downloaded and reported as a fresh portrait). The baseline is
        therefore the UNION of two scrapes separated by one
        ``_POLL_INTERVAL_S`` sleep, so a lazy tile rendering between the two
        passes still counts as pre-existing, not new. This adds one poll
        interval of wall time up front; the total poll timeout budget
        (``_AWAIT_TIMEOUT_S``) is unchanged.

        **Stable break (issue #283):** reaching ``expected_count`` on a single
        scrape is not trusted — a lazily-rendered pre-existing tile can
        transiently hit the exact count. The loop breaks only when the same
        new-UUID set holds across two consecutive scrapes (~one extra 0.5s
        poll). Deliberate fall-through: a set that first reaches the exact
        count on the final poll before the deadline is returned unconfirmed —
        better a last-second success than a spurious timeout.

        **Ambiguity fail-fast (issue #281):** if more new UUIDs appear than
        were requested, there is no reliable way to tell which ones belong to
        this generation. Rather than arbitrarily slice the unordered set (the
        prior, buggy behaviour), this raises ``MediaAttributionError`` naming
        every candidate UUID and the expected count. The caller should re-run
        the generation — ideally in a dedicated project with fewer
        pre-existing assets, which avoids the ambiguity entirely.

        **Content-policy fail-fast (conservative):** scans for explicit text
        (``"content policy"`` / ``"can't create"`` / ``"violat"`` / ``"not able
        to generate"``) and the ``warning``/``error``/``block`` Material Symbol
        ligatures. The ``flag`` ligature is excluded (normal chat affordance).
        Because no positive block-refusal sample was captured, we prefer a false
        *miss* (timeout) over a false *positive* (spurious ContentPolicyError).
        """
        # Baseline settle (issue #281): union two scrapes separated by one
        # poll interval so a lazily-rendered pre-existing tile is captured as
        # baseline, not miscounted as "new" media.
        first_baseline_srcs = await _scrape_img_srcs(page)
        await asyncio.sleep(_POLL_INTERVAL_S)
        second_baseline_srcs = await _scrape_img_srcs(page)
        baseline_uuids = _extract_uuids(first_baseline_srcs) | _extract_uuids(second_baseline_srcs)

        deadline = asyncio.get_event_loop().time() + _AWAIT_TIMEOUT_S
        new_uuids: set[str] = set()
        prev_new_uuids: set[str] | None = None

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)

            # Content-policy fail-fast (conservative — see module docstring).
            if await _check_content_policy(page):
                raise ContentPolicyError(
                    detail=(
                        "Agentic UI reported a content-policy block "
                        "(explicit text or block symbol detected). "
                        "Prompt may violate Flow's content policy."
                    ),
                    route=_ROUTE_AWAIT_IMAGES,
                )

            current_srcs = await _scrape_img_srcs(page)
            current_uuids = _extract_uuids(current_srcs)
            new_uuids = current_uuids - baseline_uuids

            if len(new_uuids) > expected_count:
                break  # ambiguity — fail fast below (#281)
            # Stable-break (#283): exact-count on a single scrape can still be
            # a lazy-render race; require the SAME set on two consecutive
            # scrapes (~one extra poll interval) before trusting it.
            if len(new_uuids) == expected_count and new_uuids == prev_new_uuids:
                break
            prev_new_uuids = set(new_uuids)

        if len(new_uuids) < expected_count:
            if new_uuids:
                detail = (
                    f"Agentic DOM scraping timed out after {_AWAIT_TIMEOUT_S:.0f}s: "
                    f"produced {len(new_uuids)}/{expected_count} distinct media UUIDs"
                )
            else:
                detail = (
                    f"Agentic DOM scraping timed out after {_AWAIT_TIMEOUT_S:.0f}s: "
                    f"0/{expected_count} distinct media UUIDs appeared"
                )
            raise TransportTimeoutError(detail=detail, route=_ROUTE_AWAIT_IMAGES)

        if len(new_uuids) > expected_count:
            candidates = sorted(new_uuids)
            raise MediaAttributionError(
                detail=(
                    f"Cannot attribute the generation among {len(candidates)} candidate "
                    f"media UUIDs (expected {expected_count}): {candidates}."
                ),
                route=_ROUTE_AWAIT_IMAGES,
            )

        return _build_generated_images(
            uuids=new_uuids,
            expected_count=expected_count,
            pending_model=model,
            pending_aspect=aspect,
        )


def _build_generated_images(
    *,
    uuids: set[str],
    expected_count: int,
    pending_model: str,
    pending_aspect: str | None,
) -> list[GeneratedImage]:
    """Construct ``GeneratedImage`` objects from scraped media UUIDs.

    Wire-only fields (``seed``, ``workflow_id``, ``model_name_type``,
    ``aspect_ratio``, ``media_generation_id``) are NOT available via DOM
    scraping — they live in the Web-Worker-delegated streamChat SSE stream
    which Playwright's page-level instrumentation cannot observe. The values
    below are scrape-synthesised sentinels documented here so downstream
    consumers can detect the agentic provenance:

    - ``seed=0``              — no seed in the DOM; Flow's default
    - ``workflow_id=""``      — not exposed on the page
    - ``model_name_type``     — mapped from the request's model field
    - ``aspect_ratio``        — mapped from the request's aspect field, or
                                 ``"IMAGE_ASPECT_RATIO_PORTRAIT"`` (default)
    - ``media_generation_id`` — ``None`` (not in the DOM)
    - ``dimensions``          — ``(0, 0)`` placeholder (naturalWidth/Height
                                 are unreliable for tRPC redirect URLs until
                                 the image has fully decoded; scraping deferred)

    ``uuids`` MUST contain exactly ``expected_count`` entries — the caller
    (``await_images``) raises ``TransportTimeoutError`` for fewer and
    ``MediaAttributionError`` for more, so this is only ever reached with an
    unambiguous, exact match (issue #281: a set is unordered and previously
    was silently truncated to ``expected_count``, which could attribute the
    wrong media to the request).
    """
    if len(uuids) != expected_count:
        msg = (
            f"_build_generated_images invariant violated: got {len(uuids)} uuids, "
            f"expected exactly {expected_count}. Callers must raise "
            "TransportTimeoutError (too few) or MediaAttributionError (too many) "
            "before reaching this helper."
        )
        raise AssertionError(msg)

    # Late import — avoid module-load-time import of dto.
    from gflow_cli.api.dto import GeneratedImage  # noqa: PLC0415

    aspect_wire = "IMAGE_ASPECT_RATIO_PORTRAIT"
    if pending_aspect:
        # Reverse-map the human label back to the wire enum value.
        _label_to_wire = {v: k for k, v in _ASPECT_PROMPT_LABEL.items()}
        aspect_wire = _label_to_wire.get(pending_aspect, aspect_wire)

    images: list[GeneratedImage] = []
    for uuid in uuids:
        fife_url = _MEDIA_REDIRECT_BASE.format(uuid=uuid)
        images.append(
            GeneratedImage(
                media_name=uuid,
                # Scrape-synthesised — not in the DOM (see docstring).
                workflow_id="",
                seed=0,
                prompt="",  # prompt not echoed in the DOM
                model_name_type=pending_model,
                aspect_ratio=aspect_wire,
                fife_url=fife_url,
                dimensions=(0, 0),
                media_generation_id=None,
            )
        )
    return images
