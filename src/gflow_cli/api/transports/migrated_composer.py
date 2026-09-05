"""Drive Flow's migrated ``flow.google.com`` editor (Angular Material) — t2v first.

Google is moving accounts from ``labs.google/fx/tools/flow`` onto
``flow.google.com`` (issue #639). The migrated app is the same product on a
different widget toolkit: ligatures live in ``<mat-icon>`` instead of ``<i>``,
the settings popover is a ``cdk-overlay`` pane of ``[role=radiogroup]`` /
``[role=radio]`` buttons instead of ``role=menu`` tabs, the model picker is a
``[role=menu]`` of ``[role=menuitem]``s, and the composer is a ``contenteditable``
(the ``textarea`` next to it is not clickable). On the wire it is ``batchexecute``,
not aisandbox REST: submit is rpcid ``YhhmEf``, the app then polls ``jwpduf`` every
5 s by itself and fetches the result with ``as29s`` — so this driver **observes**
the page's own traffic and adds none. Recon with measurements:
``docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md``.

Every anchor here is structural or a Material Symbols ligature; the only text
matched is a numeric token (``8s``, ``x2``) or a product name (``Veo 3.1 - Lite``).
``aria-label`` values are translated on this host and are never used.

Selector trap recorded by the spike: Playwright's CSS ``:text-matches('\\s…')``
goes through CSS string escaping, which turns ``\\s`` into ``s`` — labels are
matched with a Python-side ``filter(has_text=re.compile(...))`` instead.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import structlog

from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    generation_record,
    parse_frames,
)
from gflow_cli.api.video import Aspect, VideoModel, VideoStarted
from gflow_cli.errors import ConfigurationError, UiSelectorDriftError, WireFormatError

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStartedCallback

log = structlog.get_logger(__name__)

MIGRATED_PROJECT_URL = "https://flow.google.com/project/{project_id}"
READY_ANCHOR = ".settings-trigger-button"
OVERLAY = ".cdk-overlay-pane"
BACKDROP = ".cdk-overlay-backdrop"
RADIOGROUP = "[role='radiogroup']"
RADIO = "[role='radio']"
MENU_ITEM = "[role='menuitem']"
COMPOSER = "[contenteditable='true']"

SUBMIT_RPC = "YhhmEf"
STATUS_RPCS = ("jwpduf", "as29s")
#: The submit reply arrived 4.0–4.6 s after the click in both measured runs.
SUBMIT_REPLY_BUDGET_S = 60.0
#: A ``jwpduf`` poll reports status 3 first; the record that carries the signed
#: URLs (``as29s``) followed 2–5 s later in every measured run. Wait that long
#: for it before settling for the URL-less record.
RESULT_URL_GRACE_S = 20.0

VIDEO_MODEL_MENU_LABELS: dict[VideoModel, str] = {
    VideoModel.OMNI_FLASH: "Omni 1.1 Flash",
    VideoModel.VEO_3_1_LITE: "Veo 3.1 - Lite",
    VideoModel.VEO_3_1_FAST: "Veo 3.1 - Fast",
    VideoModel.VEO_3_1_QUALITY: "Veo 3.1 - Quality",
}
ASPECT_LIGATURE: dict[Aspect, str] = {
    Aspect.LANDSCAPE: "crop_16_9",
    Aspect.PORTRAIT: "crop_9_16",
}


def _exact(label: str) -> re.Pattern[str]:
    return re.compile(r"^\s*" + re.escape(label) + r"\s*$")


def _ligature(page: Any, name: str) -> Any:
    """A ``mat-icon`` whose ligature text is exactly ``name`` — for ``filter(has=…)``."""
    return page.locator("mat-icon").filter(has_text=_exact(name))


def _rpcid(url: str) -> str | None:
    m = re.search(r"[?&]rpcids=([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


class MigratedComposer:
    """Settings → prompt → submit → observe, against the migrated editor."""

    # --- readiness ------------------------------------------------------------

    async def ensure_editor(self, page: Page, project_id: str, *, timeout_s: float = 30.0) -> None:
        """Land on ``flow.google.com/project/<id>`` (direct — no labs.google visit
        needed on either kind of account) and wait for the settings trigger."""
        target = MIGRATED_PROJECT_URL.format(project_id=project_id)
        current = str(getattr(page, "url", "") or "")
        if not current.startswith(target):
            log.info("migrated.navigate", url=target)
            await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.locator(READY_ANCHOR).first.wait_for(
                state="visible", timeout=int(timeout_s * 1000)
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the settings trigger ({READY_ANCHOR}) did not become "
                    f"visible within {timeout_s:.0f}s on {page.url} (host=migrated): {e}"
                ),
            ) from e
        log.info("migrated.editor_ready", url=page.url)

    # --- settings ---------------------------------------------------------------

    async def apply_video_settings(self, page: Page, request: GenerateVideoRequest) -> None:
        """Mode, model, aspect, duration, count — through the radios, with read-back.

        Model goes first: like on labs.google the duration row is model-state.
        """
        pane = await self._open_pane(page)
        try:
            await self._select(page, pane, axis="mode", lig="videocam")
            if request.model is not None:
                await self._select_model(page, pane, request.model)
            await self._select(page, pane, axis="aspect", lig=ASPECT_LIGATURE[request.aspect])
            if request.duration is not None:
                await self._select(page, pane, axis="duration", text=f"{request.duration}s")
            await self._select(page, pane, axis="count", text=f"x{request.count}")
            log.info(
                "migrated.settings_applied",
                aspect=request.aspect.value,
                duration=request.duration,
                count=request.count,
                model=request.model.value if request.model else None,
            )
        finally:
            await self._close_pane(page)

    async def _open_pane(self, page: Page) -> Any:
        trigger = page.locator(READY_ANCHOR).first
        if not await trigger.count():
            raise UiSelectorDriftError(
                detail=f"migrated host: settings trigger ({READY_ANCHOR}) missing (host=migrated)"
            )
        await trigger.click(timeout=5000)
        pane = page.locator(OVERLAY).last
        try:
            await pane.locator(RADIOGROUP).first.wait_for(state="visible", timeout=8000)
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    "migrated host: the settings pane opened but rendered no option "
                    "groups ([role='radiogroup']) (host=migrated)"
                ),
            ) from e
        return pane

    async def _close_pane(self, page: Page) -> None:
        """Escape, else toggle the trigger, else click the backdrop (all measured)."""
        await page.keyboard.press("Escape")
        for attempt in ("escape", "trigger", "backdrop"):
            await asyncio.sleep(0.3)
            pane = page.locator(OVERLAY).first
            if not await pane.count() or not await pane.is_visible():
                log.debug("migrated.pane_closed", by=attempt)
                return
            if attempt == "escape":
                await page.locator(READY_ANCHOR).first.click(timeout=3000)
            elif attempt == "trigger":
                backdrop = page.locator(BACKDROP).first
                if await backdrop.count():
                    await backdrop.click(timeout=3000, force=True)
        log.warning("migrated.pane_still_open")

    async def _select(
        self,
        page: Page,
        pane: Any,
        *,
        axis: str,
        lig: str | None = None,
        text: str | None = None,
    ) -> None:
        """Click one radio and read ``aria-checked`` back; re-query once on a stale node."""
        radios = pane.locator(RADIO)
        wanted = text if text is not None else str(lig)
        matches = (
            radios.filter(has=_ligature(page, lig))
            if lig
            else radios.filter(has_text=_exact(wanted))
        )
        target = matches.first
        if not await target.count():
            groups = await pane.locator(RADIOGROUP).count()
            raise ConfigurationError(
                detail=(
                    f"the migrated Flow host renders no '{axis}' control offering {wanted!r} "
                    f"on this account and model ({groups} option groups shown) — drop the "
                    f"option or pick a model that offers it"
                ),
            )
        if await target.get_attribute("aria-checked") == "true":
            return
        for _ in range(2):
            await target.click(timeout=4000)
            await asyncio.sleep(0.2)
            target = matches.first  # re-query: Angular may have re-rendered the row
            if await target.get_attribute("aria-checked") == "true":
                return
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the '{axis}' radio {wanted!r} did not become aria-checked "
                f"after two clicks (host=migrated)"
            ),
        )

    async def _select_model(self, page: Page, pane: Any, model: VideoModel) -> None:
        label = VIDEO_MODEL_MENU_LABELS.get(model)
        if label is None:
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' is not available on the migrated Flow host; "
                    f"offered: {', '.join(VIDEO_MODEL_MENU_LABELS.values())}"
                ),
            )
        button = pane.locator("button").filter(has=_ligature(page, "arrow_drop_down")).first
        if not await button.count():
            raise UiSelectorDriftError(
                detail=(
                    "migrated host: model picker button (arrow_drop_down) not found in the "
                    "settings pane (host=migrated)"
                ),
            )
        current = (await button.text_content() or "").strip()
        if current.lower().startswith(label.lower()):
            return
        await button.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        try:
            await items.first.wait_for(state="visible", timeout=5000)
        except Exception as e:
            raise UiSelectorDriftError(
                detail="migrated host: model menu ([role='menuitem']) did not open (host=migrated)",
            ) from e
        target = items.filter(has_text=re.compile(re.escape(label), re.IGNORECASE)).first
        if not await target.count():
            offered = [t.strip() for t in await items.all_text_contents()]
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"model '{label}' is not offered on this account's migrated Flow host; "
                    f"offered: {', '.join(offered)}"
                ),
            )
        await target.click(timeout=4000)
        log.info("migrated.model_selected", model=label)

    # --- prompt + submit --------------------------------------------------------

    async def send_prompt(self, page: Page, prompt: str) -> None:
        composer = page.locator(COMPOSER).first
        if not await composer.count():
            raise UiSelectorDriftError(
                detail=f"migrated host: composer ({COMPOSER}) not found (host=migrated)",
            )
        await composer.click(timeout=5000)
        await page.keyboard.type(prompt, delay=5)
        log.info("migrated.prompt_typed", chars=len(prompt))

    async def submit_and_observe(
        self,
        page: Page,
        *,
        poll_timeout_s: float,
        on_started: VideoStartedCallback | None,
        project_id: str | None,
    ) -> GenerationRecord:
        """Click submit, then read the page's own ``YhhmEf`` / ``jwpduf`` / ``as29s``
        replies until the record is terminal. Fires ``on_started`` as soon as the
        submit reply names the media id — before the poll, as the labs path does."""
        loop = asyncio.get_running_loop()
        submitted: asyncio.Future[GenerationRecord] = loop.create_future()
        # ``terminal``: failed, or done WITH the signed URL. ``done_no_url``: the
        # first status-3 record that has no URL yet (a poll beats the result RPC).
        terminal: asyncio.Future[GenerationRecord] = loop.create_future()
        done_no_url: asyncio.Future[GenerationRecord] = loop.create_future()
        workflow: dict[str, str] = {}

        def _settle(rec: GenerationRecord) -> None:
            if rec.is_failed or (rec.is_done and rec.video_url):
                if not terminal.done():
                    terminal.set_result(rec)
            elif rec.is_done and not done_no_url.done():
                done_no_url.set_result(rec)

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            rpcid = _rpcid(url) if "batchexecute" in url else None
            if rpcid != SUBMIT_RPC and rpcid not in STATUS_RPCS:
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - an aborted/streamed body is not our frame
                return
            for rid, payload in parse_frames(text):
                if rid == SUBMIT_RPC and not submitted.done():
                    try:
                        rec = generation_record(rid, payload)
                    except WireFormatError as exc:
                        submitted.set_exception(exc)
                        return
                    workflow["id"] = rec.workflow_id
                    log.info(
                        "migrated.submit_observed",
                        rpc=rid,
                        workflow_id=rec.workflow_id,
                        media_id=rec.media_id,
                        status=rec.status,
                    )
                    submitted.set_result(rec)
                    _settle(rec)
                elif rid in STATUS_RPCS and workflow:
                    try:
                        rec = generation_record(rid, payload)
                    except WireFormatError:
                        continue
                    if rec.workflow_id != workflow["id"]:
                        continue
                    log.info("migrated.status", rpc=rid, status=rec.status, bytes=rec.size_bytes)
                    _settle(rec)

        page.on("response", on_response)
        try:
            submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            if not await submit.count() or not await submit.is_enabled():
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the submit button (arrow_forward) is missing or "
                        "disabled after the prompt was typed (host=migrated)"
                    ),
                )
            deadline = time.monotonic() + poll_timeout_s
            await submit.click(timeout=5000)
            log.info("migrated.submit_clicked")
            try:
                first = await asyncio.wait_for(
                    submitted, timeout=min(SUBMIT_REPLY_BUDGET_S, poll_timeout_s)
                )
            except TimeoutError:
                msg = (
                    f"migrated host: no {SUBMIT_RPC} reply within "
                    f"{min(SUBMIT_REPLY_BUDGET_S, poll_timeout_s):.0f}s of clicking submit"
                )
                raise TimeoutError(msg) from None
            started = VideoStarted(
                media_id=first.media_id,
                project_id=project_id or first.project_id,
                flow_operation_id=first.workflow_id,
            )
            if on_started is not None:
                maybe = on_started(started)
                if asyncio.iscoroutine(maybe):
                    await maybe
            final = await self._await_terminal(
                terminal, done_no_url, deadline=deadline, workflow_id=first.workflow_id
            )
            log.info(
                "migrated.result",
                status=final.status,
                done=final.is_done,
                url_host=urlsplit(final.video_url).hostname if final.video_url else None,
                bytes=final.size_bytes,
            )
            return final
        finally:
            page.remove_listener("response", on_response)

    @staticmethod
    async def _await_terminal(
        terminal: asyncio.Future[GenerationRecord],
        done_no_url: asyncio.Future[GenerationRecord],
        *,
        deadline: float,
        workflow_id: str,
    ) -> GenerationRecord:
        """Wait for a terminal record; a done-without-URL record buys a short grace
        for the one that carries the URL, then stands on its own."""
        remaining = max(deadline - time.monotonic(), 0.01)
        done, _ = await asyncio.wait(
            {terminal, done_no_url}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if terminal.done():
            return terminal.result()
        if not done:
            msg = (
                f"migrated host: generation {workflow_id} was not terminal within the poll timeout"
            )
            raise TimeoutError(msg)
        grace = min(RESULT_URL_GRACE_S, max(deadline - time.monotonic(), 0.01))
        try:
            return await asyncio.wait_for(terminal, timeout=grace)
        except TimeoutError:
            log.warning("migrated.result_url_not_observed", grace_s=grace)
            return done_no_url.result()

    # --- download ---------------------------------------------------------------

    @staticmethod
    async def _fetch_mp4(page: Page, record: GenerationRecord) -> bytes:
        """GET the signed URL and prove it is an MP4 (``ftyp`` at offset 4); if it is
        not — the record carries a poster JPEG next to the clip, and a 2026-09-05
        run downloaded that one — try the other URL before giving up."""
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415 - cycle
            _is_allowed_download_host,  # pyright: ignore[reportPrivateUsage]
        )

        seen: list[str] = []
        for url in (record.video_url, record.poster_url):
            if not url:
                continue
            if not _is_allowed_download_host(url):
                raise WireFormatError(
                    detail=(
                        "migrated host: refusing to download from "
                        f"{urlsplit(url).hostname!r} (not an allowed Google host)"
                    ),
                    route="batchexecute:as29s",
                )
            resp = await page.request.get(url, timeout=180_000)
            if resp.status >= 400:
                raise WireFormatError(
                    detail=f"migrated host: signed media URL returned HTTP {resp.status}",
                    status=resp.status,
                    route="flow-content.google",
                )
            body = await resp.body()
            if body[4:8] == b"ftyp":
                if record.size_bytes and len(body) != record.size_bytes:
                    log.warning(
                        "migrated.download_size_mismatch",
                        expected=record.size_bytes,
                        actual=len(body),
                    )
                return body
            seen.append(
                f"{urlsplit(url).path.rsplit('/', 1)[-1]}: {body[:4].hex()} ({len(body)} B)"
            )
        raise WireFormatError(
            detail=(
                "migrated host: no signed URL on the record returned an MP4 "
                f"(ftyp magic); saw {'; '.join(seen) or 'no URLs'}"
            ),
            route="batchexecute:as29s",
        )

    async def download(
        self,
        page: Page,
        record: GenerationRecord,
        out_dir: Path | None,
        *,
        transport: Any,
    ) -> Path | None:
        """The clip from its signed CDN URL; the labs ``media.getMediaUrlRedirect``
        route is the fallback (it answered 404 for a migrated media id, measured
        2026-09-05, so it is tried only when no URL was observed)."""
        if not record.video_url:
            try:
                return await transport._download_video(record.media_id, out_dir, page)  # noqa: SLF001
            except WireFormatError as exc:
                raise WireFormatError(
                    detail=(
                        "migrated host: no signed video URL was observed and the "
                        f"media redirect route answered HTTP {exc.status}"
                    ),
                    status=exc.status,
                    route="batchexecute:as29s",
                ) from exc
        body = await self._fetch_mp4(page, record)
        target_dir = out_dir or Path.cwd()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{record.media_id}.mp4"
        path.write_bytes(body)
        log.info("migrated.download", path=str(path), bytes=len(body))
        return path


async def run_video(
    transport: Any,
    page: Page,
    request: GenerateVideoRequest,
    *,
    project_id: str | None,
    out_dir: Path | None,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    """The migrated-host twin of the labs ``_generate_video_locked`` tail: same
    inputs, same ``VideoResult``, so recorder, CLI, MCP and worker are untouched.

    t2v only for now — i2v/r2v attach media through labs-shaped slots that have
    not been recon'd on this host, and a fresh project can only be created through
    the labs gallery, so the caller must name one (``--project``).
    """
    from gflow_cli.api.transports._common import extract_project_id  # noqa: PLC0415
    from gflow_cli.api.video import Mode, VideoStatus  # noqa: PLC0415
    from gflow_cli.errors import FlowHostMigratedError  # noqa: PLC0415

    if request.mode is not Mode.T2V:
        raise FlowHostMigratedError(
            detail=(
                f"this account's Flow lives on flow.google.com, where gflow drives "
                f"text-to-video only for now; {request.mode.value} is not ported yet (#639)"
            ),
        )
    pid = project_id or extract_project_id(page.url)
    if not pid:
        raise ConfigurationError(
            detail=(
                "generating on the migrated flow.google.com host needs an existing project: "
                "pass --project <id> (see `gflow project list` / `gflow project create`) — "
                "creating one from the editor is not ported to this host yet"
            ),
        )
    log.info("migrated.dispatch", project_id=pid, mode=request.mode.value)
    composer = MigratedComposer()
    await composer.ensure_editor(page, pid)
    await composer.apply_video_settings(page, request)
    await composer.send_prompt(page, request.prompt)
    record = await composer.submit_and_observe(
        page, poll_timeout_s=poll_timeout_s, on_started=on_started, project_id=pid
    )
    status = VideoStatus(
        media_id=record.media_id,
        status=(
            "MEDIA_GENERATION_STATUS_SUCCESSFUL"
            if record.is_done
            else "MEDIA_GENERATION_STATUS_FAILED"
        ),
        error_message=None if record.is_done else f"migrated host reported status {record.status}",
    )
    local_path: Path | None = None
    if download and record.is_done:
        local_path = await composer.download(page, record, out_dir, transport=transport)
    from gflow_cli.api.video import VideoResult as _VideoResult  # noqa: PLC0415

    return _VideoResult(
        status=status,
        local_path=Path(local_path) if local_path is not None else None,
        project_id=pid,
        flow_operation_id=record.workflow_id,
    )
