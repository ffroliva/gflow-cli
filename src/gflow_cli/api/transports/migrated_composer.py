"""Drive Flow's migrated ``flow.google.com`` editor (Angular Material).

Supported generation paths are t2v, i2v from a local start frame, r2v from local
reference files, t2i, and i2i from local reference files.

Google is moving accounts from ``labs.google/fx/tools/flow`` onto
``flow.google.com`` (issue #639). The migrated app is the same product on a
different widget toolkit: ligatures live in ``<mat-icon>`` instead of ``<i>``,
the settings popover is a ``cdk-overlay`` pane of ``[role=radiogroup]`` /
``[role=radio]`` buttons instead of ``role=menu`` tabs, the model picker is a
``[role=menu]`` of ``[role=menuitem]``s, and the composer is a ``contenteditable``
(the ``textarea`` next to it is not clickable). On the wire it is ``batchexecute``,
not aisandbox REST: video submit is ``YhhmEf``/``eb1hJf``/``MZZa6b`` and image submit
is ``ogiZ0b``. The app
then polls ``jwpduf`` every 5 s by itself and fetches the result with ``as29s`` — so
this driver **observes** the page's own traffic and adds none. A start frame goes in
through the editor's own upload (``maseQ``) and its library picker. Recon with
measurements: ``docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md``,
``docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md``, and
``docs/superpowers/spikes/2026-09-08-migrated-image-submit-wire.md``.

Every anchor here is structural or a Material Symbols ligature; the only text
matched is a numeric token (``8s``, ``x2``) or a product name (``Veo 3.1 - Lite``).
``aria-label`` values are translated on this host and are never used.

Selector trap recorded by the spike: Playwright's CSS ``:text-matches('\\s…')``
goes through CSS string escaping, which turns ``\\s`` into ``s`` — labels are
matched with a Python-side ``filter(has_text=re.compile(...))`` instead.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from playwright.async_api import (
    Page,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from gflow_cli.api.dto import (
    CreditsInfo,
    GeneratedImage,
    ProjectInfo,
)
from gflow_cli.api.image import (
    Model as ImageModel,  # noqa: F401 # pyright: ignore[reportUnusedImport]
)
from gflow_cli.api.transports import _mc_media
from gflow_cli.api.transports._common import (
    extract_project_id,
    flow_landing_kind,
    raise_if_known_landing,
    safe_page_url,
)
from gflow_cli.api.transports._mc_common import (
    _GRID_TOKEN_JS as _GRID_TOKEN_JS,
)
from gflow_cli.api.transports._mc_common import (
    _I2V_FIRST_LAST_TOKEN as _I2V_FIRST_LAST_TOKEN,
)
from gflow_cli.api.transports._mc_common import (
    _OPTION_TITLES_JS as _OPTION_TITLES_JS,
)
from gflow_cli.api.transports._mc_common import (
    _OPTION_TOKENS_JS as _OPTION_TOKENS_JS,
)
from gflow_cli.api.transports._mc_common import (
    ABORTED_BEFORE_SEND as ABORTED_BEFORE_SEND,
)
from gflow_cli.api.transports._mc_common import (
    AGENT_MODE_CHIP as AGENT_MODE_CHIP,
)
from gflow_cli.api.transports._mc_common import (
    AGENT_MODE_CHIP_ANY as AGENT_MODE_CHIP_ANY,
)
from gflow_cli.api.transports._mc_common import (
    AGENT_ONLY_ANCHOR as AGENT_ONLY_ANCHOR,
)
from gflow_cli.api.transports._mc_common import (
    AGENT_ONLY_EARLY_S as AGENT_ONLY_EARLY_S,
)
from gflow_cli.api.transports._mc_common import (
    AGENT_ONLY_RELOADS as AGENT_ONLY_RELOADS,
)
from gflow_cli.api.transports._mc_common import (
    ANCHOR_TIMEOUT_S as ANCHOR_TIMEOUT_S,
)
from gflow_cli.api.transports._mc_common import (
    ASPECT_LIGATURE as ASPECT_LIGATURE,
)
from gflow_cli.api.transports._mc_common import (
    BATCHEXECUTE_GLOB as BATCHEXECUTE_GLOB,
)
from gflow_cli.api.transports._mc_common import (
    BOUND_CHIP as BOUND_CHIP,
)
from gflow_cli.api.transports._mc_common import (
    COMPOSER as COMPOSER,
)
from gflow_cli.api.transports._mc_common import (
    COOKIE_BAR as COOKIE_BAR,
)
from gflow_cli.api.transports._mc_common import (
    COOKIE_BAR_ACCEPT as COOKIE_BAR_ACCEPT,
)
from gflow_cli.api.transports._mc_common import (
    COOKIE_BAR_REJECT as COOKIE_BAR_REJECT,
)
from gflow_cli.api.transports._mc_common import (
    CREATE_PROJECT_RPC as CREATE_PROJECT_RPC,
)
from gflow_cli.api.transports._mc_common import (
    CREDITS_WARNING as CREDITS_WARNING,
)
from gflow_cli.api.transports._mc_common import (
    DIALOG as DIALOG,
)
from gflow_cli.api.transports._mc_common import (
    EMPTY_CHIP as EMPTY_CHIP,
)
from gflow_cli.api.transports._mc_common import (
    EXISTING_REF_WAIT_S as EXISTING_REF_WAIT_S,
)
from gflow_cli.api.transports._mc_common import (
    FRAME_SEARCH_ATTEMPTS as FRAME_SEARCH_ATTEMPTS,
)
from gflow_cli.api.transports._mc_common import (
    FRAMES_LIGATURE as FRAMES_LIGATURE,
)
from gflow_cli.api.transports._mc_common import (
    IMAGE_ASPECT_LIGATURE as IMAGE_ASPECT_LIGATURE,
)
from gflow_cli.api.transports._mc_common import (
    IMAGE_ASPECT_LIGATURE_MEASURED as IMAGE_ASPECT_LIGATURE_MEASURED,
)
from gflow_cli.api.transports._mc_common import (
    IMAGE_MODEL_MENU_MATCHERS as IMAGE_MODEL_MENU_MATCHERS,
)
from gflow_cli.api.transports._mc_common import (
    IMAGE_SUBMIT_RPC as IMAGE_SUBMIT_RPC,
)
from gflow_cli.api.transports._mc_common import (
    INTERPOLATION_SUBMIT_RPC as INTERPOLATION_SUBMIT_RPC,
)
from gflow_cli.api.transports._mc_common import (
    LISTING_RPC as LISTING_RPC,
)
from gflow_cli.api.transports._mc_common import (
    MENTION_CHIP as MENTION_CHIP,
)
from gflow_cli.api.transports._mc_common import (
    MENU_ITEM as MENU_ITEM,
)
from gflow_cli.api.transports._mc_common import (
    MIGRATED_PROJECT_URL as MIGRATED_PROJECT_URL,
)
from gflow_cli.api.transports._mc_common import (
    MIGRATED_ROOT_URL as MIGRATED_ROOT_URL,
)
from gflow_cli.api.transports._mc_common import (
    MODEL_KEY as MODEL_KEY,
)
from gflow_cli.api.transports._mc_common import (
    NAVIGATION_GRACE_S,
    _as_list_any,
    _write_outcome_unknown,
)
from gflow_cli.api.transports._mc_common import (
    NEW_PROJECT_BUTTON as NEW_PROJECT_BUTTON,
)
from gflow_cli.api.transports._mc_common import (
    OVERLAY as OVERLAY,
)
from gflow_cli.api.transports._mc_common import (
    PAGE_RPC_TIMEOUT_S as PAGE_RPC_TIMEOUT_S,
)
from gflow_cli.api.transports._mc_common import (
    PANE_CLOSE_ESCAPES as PANE_CLOSE_ESCAPES,
)
from gflow_cli.api.transports._mc_common import (
    PICKER as PICKER,
)
from gflow_cli.api.transports._mc_common import (
    PICKER_OPTION as PICKER_OPTION,
)
from gflow_cli.api.transports._mc_common import (
    PICKER_SEARCH as PICKER_SEARCH,
)
from gflow_cli.api.transports._mc_common import (
    PROJECT_TITLE_INPUT as PROJECT_TITLE_INPUT,
)
from gflow_cli.api.transports._mc_common import (
    RADIO as RADIO,
)
from gflow_cli.api.transports._mc_common import (
    RADIOGROUP as RADIOGROUP,
)
from gflow_cli.api.transports._mc_common import (
    READY_ANCHOR as READY_ANCHOR,
)
from gflow_cli.api.transports._mc_common import (
    RENAME_PROJECT_RPC as RENAME_PROJECT_RPC,
)
from gflow_cli.api.transports._mc_common import (
    STATUS_RPCS as STATUS_RPCS,
)
from gflow_cli.api.transports._mc_common import (
    SUBMIT_RPCS as SUBMIT_RPCS,
)
from gflow_cli.api.transports._mc_common import (
    UNUSUAL_ACTIVITY_REASON as UNUSUAL_ACTIVITY_REASON,
)
from gflow_cli.api.transports._mc_common import (
    UPLOAD_RPC as UPLOAD_RPC,
)
from gflow_cli.api.transports._mc_common import (
    UUID_RE as UUID_RE,
)
from gflow_cli.api.transports._mc_common import (
    VIDEO_MODEL_MENU_LABELS as VIDEO_MODEL_MENU_LABELS,
)
from gflow_cli.api.transports._mc_common import (
    VIDEO_MODEL_MENU_MATCHERS as VIDEO_MODEL_MENU_MATCHERS,
)
from gflow_cli.api.transports._mc_common import (
    ModelMenuMatcher as ModelMenuMatcher,
)
from gflow_cli.api.transports._mc_common import (
    _duration as _duration,
)
from gflow_cli.api.transports._mc_common import (
    _first_uuid as _first_uuid,
)
from gflow_cli.api.transports._mc_common import (
    _guard_image_submit as _guard_image_submit,
)
from gflow_cli.api.transports._mc_common import (
    _i2v_body_problem as _i2v_body_problem,
)
from gflow_cli.api.transports._mc_common import (
    _image_body_problem as _image_body_problem,
)
from gflow_cli.api.transports._mc_common import (
    _image_body_references as _image_body_references,
)
from gflow_cli.api.transports._mc_common import (
    _interpolation_body_problem as _interpolation_body_problem,
)
from gflow_cli.api.transports._mc_common import (
    _ligature as _ligature,
)
from gflow_cli.api.transports._mc_common import (
    _navigated_project_id as _navigated_project_id,
)
from gflow_cli.api.transports._mc_common import (
    _project_snapshot as _project_snapshot,
)
from gflow_cli.api.transports._mc_common import (
    _r2v_body_problem as _r2v_body_problem,
)
from gflow_cli.api.transports._mc_common import (
    _raise_if_out_of_credits as _raise_if_out_of_credits,
)
from gflow_cli.api.transports._mc_common import (
    _recovery_detail as _recovery_detail,
)
from gflow_cli.api.transports._mc_common import (
    _rpc_reply as _rpc_reply,
)
from gflow_cli.api.transports._mc_common import (
    _unique_display_name as _unique_display_name,
)
from gflow_cli.api.transports._mc_common import (
    _unported_form as _unported_form,
)
from gflow_cli.api.transports._mc_common import (
    _unported_image_form as _unported_image_form,
)
from gflow_cli.api.transports._mc_common import (
    _wait_anchor as _wait_anchor,
)
from gflow_cli.api.transports._mc_common import (
    body_rpcid as body_rpcid,
)
from gflow_cli.api.transports._mc_common import (
    migrated_can_serve as migrated_can_serve,
)
from gflow_cli.api.transports._mc_common import (
    migrated_images_prefer as migrated_images_prefer,
)
from gflow_cli.api.transports._mc_common import (
    request_body as request_body,
)
from gflow_cli.api.transports._mc_common import (
    url_rpcid as url_rpcid,
)
from gflow_cli.api.transports._mc_submit_observe import SubmitObserveMixin
from gflow_cli.api.video import (
    GenerateVideoRequest,
    Mode,
    VideoResult,
    VideoStartedCallback,
    VideoStatus,
)
from gflow_cli.errors import (
    ConfigurationError,
    FlowAppError,
    FlowHostMigratedError,
    ReferenceNotFoundError,
    RpcResponseUnavailableError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)

if TYPE_CHECKING:
    from collections.abc import (
        Awaitable,
        Callable,
    )

    from gflow_cli.api.image import (
        GenerateImageRequest,
    )

    # Stores a finished clip's bytes under its media id and returns where they went —
    # the transport's one writer for both hosts, so a cloud storage_uri holds here too (#1003).
    ClipSaver = Callable[[str, bytes], Awaitable[Path]]

log = structlog.get_logger(__name__)


class MigratedComposer(SubmitObserveMixin):
    """Settings → prompt → submit → observe, against the migrated editor."""


# Moved bodies call MigratedComposer.fetch_mp4(...) and tests patch the class.
_mc_media.MigratedComposer = MigratedComposer


async def run_video(
    page: Page,
    request: GenerateVideoRequest,
    *,
    project_id: str | None,
    save: ClipSaver,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    """The migrated-host twin of the labs ``_generate_video_locked`` tail: same
    inputs, same ``VideoResult``, so recorder, CLI, MCP and worker are untouched.

    t2v, i2v from local start (and end) frames (uploaded through the editor and
    bound on the Start/End chips by file name), and r2v from local ``--ref``
    files. A frame or reference given by UUID / ``@Name`` is not ported yet. The
    project is created by ``FlowApiClient`` before this runs (#864), so reaching
    here without one means a caller bypassed the client.
    """
    from gflow_cli.api.transports import agent_only_composer as agent  # noqa: PLC0415 - cycle

    unported = _unported_form(request)

    def refuse() -> FlowHostMigratedError:
        return FlowHostMigratedError(
            detail=(
                f"Flow served flow.google.com, where gflow drives "
                f"text-to-video, image-to-video from local start (and end) frames, and "
                f"reference-to-video from local files; {unported} is not ported yet "
                f"(#639) — pass --initial-frame / --ref as local files"
            ),
        )

    # $0 and before the page only when neither composer takes it (see run_images).
    needs_classic = agent.unported_video(request) is not None
    if unported is not None and needs_classic:
        raise refuse()
    pid = project_id or extract_project_id(page.url)
    if not pid:
        raise ConfigurationError(
            detail=(
                "generating on the migrated flow.google.com host needs a project: pass "
                "--project <id> (see `gflow project list` / `gflow project create`)"
            ),
        )
    log.info("migrated.dispatch", project_id=pid, mode=request.mode.value)
    composer = MigratedComposer()
    if await composer.ensure_editor_for(page, pid, needs_classic=needs_classic) == "agent_only":
        return await agent.run_agent_video(
            page,
            request,
            project_id=pid,
            save=save,
            poll_timeout_s=poll_timeout_s,
            download=download,
            on_started=on_started,
        )
    if unported is not None:
        raise refuse()
    await composer.apply_video_settings(page, request)
    media_id: str | None = None
    end_media_id: str | None = None
    reference_ids: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    frame = request.start_image
    if request.mode is Mode.I2V and frame is not None:
        media_id = await composer.attach_start_frame(page, pid, frame)
    if request.mode is Mode.I2V and request.end_image is not None:
        end_media_id = await composer.attach_end_frame(page, pid, request.end_image)
    if request.mode is Mode.R2V and request.reference_images:
        reference_ids = await composer.attach_references(page, pid, request.reference_images)
    if request.reference_entities:
        # #723: characters attach through the SAME `@` picker as media, so they go on
        # after any uploads (whose file chooser steals keyboard focus) and must not clear
        # mentions those uploads already placed.
        entity_ids = await composer.attach_character_entities(
            page,
            entity_ids=tuple(request.reference_entities),
            names=tuple(request.reference_entity_names),
            clear=not reference_ids,
        )
    # The prompt is appended whenever mentions are already in the document: clicking the
    # composer would move the caret away from where the last one left it.
    has_mentions = bool(request.reference_entities) or request.mode is Mode.R2V
    await composer.send_prompt(page, request.prompt, append=has_mentions)
    if request.mode is Mode.R2V:
        attached = await composer.read_chips(page)
        n_images, n_chars = len(request.reference_images), len(request.reference_entities)
        if len(attached) != n_images + n_chars:
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: {n_images} image reference(s) + {n_chars} character(s) "
                    f"= {n_images + n_chars} chip(s) requested but {len(attached)} on the "
                    f"prompt at submit time — refusing to spend credits on a run that would "
                    f"ignore them"
                ),
            )
    record = await composer.submit_and_observe(
        page,
        poll_timeout_s=poll_timeout_s,
        on_started=on_started,
        project_id=pid,
        expect_media_id=media_id,
        expect_end_media_id=end_media_id,
        # The committed entity ids ride the MZZa6b body beside the media ids (route-abort
        # capture, 2026-10-27), so a character-only run is guarded too.
        expect_reference_ids=reference_ids + entity_ids,
        # Only ids uploaded by THIS run identify its clip after a lost reply (#1112);
        # a character's entity id repeats in every clip that cast it.
        adopt_ids=tuple(i for i in (media_id, end_media_id) if i) + reference_ids,
        # Nothing uploaded, so nothing run-unique: found by prompt and model instead (#1141).
        adopt_prompt=(request.prompt if request.mode is Mode.T2V and request.count == 1 else None),
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
        local_path = await composer.download(page, record, save)
    return VideoResult(
        status=status,
        # Not re-wrapped in Path(): a cloud UPath must reach the caller intact (#1003).
        local_path=local_path,
        project_id=pid,
        flow_operation_id=record.workflow_id,
        workflow_id=record.workflow_id,
    )


async def run_images(
    page: Page,
    request: GenerateImageRequest,
    *,
    project_id: str | None,
    out_dir: Path | None = None,
    name_resolver: Callable[[str], str | None] | None = None,
) -> list[GeneratedImage]:
    """Drive supported image requests through the migrated project composer.

    ``name_resolver`` (#546) heals a reference whose catalog caption went stale; see
    :meth:`MigratedComposer.reference_existing`.
    """
    from gflow_cli.api.transports import agent_only_composer as agent  # noqa: PLC0415 - cycle

    unported = _unported_image_form(request)

    def refuse() -> FlowHostMigratedError:
        return FlowHostMigratedError(
            detail=(
                "Flow served flow.google.com, where gflow drives t2i "
                f"and i2i from local files; {unported} is not ported yet (#639)"
            )
        )

    # $0 and before the page only when NEITHER composer takes it: the agent-only one has
    # its own limits (#799) — its Agent-settings pane offers 3:4, the classic pane does not.
    needs_classic = agent.unported_image(request) is not None
    if unported is not None and needs_classic:
        raise refuse()
    pid = project_id or extract_project_id(page.url)
    if not pid:
        raise ConfigurationError(
            detail=(
                "image generation on flow.google.com needs an existing project; pass --project <id>"
            )
        )
    composer = MigratedComposer()
    if await composer.ensure_editor_for(page, pid, needs_classic=needs_classic) == "agent_only":
        return await agent.run_agent_images(page, request, project_id=pid)
    if unported is not None:
        raise refuse()
    reference_ids: tuple[str, ...] = ()
    if request.refs:
        # Images already in this project (a manifest's `batch:N`, #913): referenced in
        # place, never re-uploaded. Finding them may reload, so settings are applied there.
        reference_ids = await composer.reference_existing(
            page, pid, request, name_resolver=name_resolver
        )
    else:
        await composer.apply_image_settings(page, request)
    if request.ref_paths:
        reference_ids = await composer.attach_references(page, pid, request.ref_paths)
        chips = await composer.read_chips(page)
        if len(chips) != len(reference_ids):
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: {len(reference_ids)} image reference(s) uploaded but "
                    f"{len(chips)} mention chip(s) were bound before submit"
                )
            )
    entity_ids: tuple[str, ...] = ()
    if request.reference_entity_names:
        # #723: characters go through the same `@` picker, after any uploads (whose file
        # chooser steals focus) and without clearing the mentions those placed. Each
        # chip is verified to be an ENTITY before anything is submitted.
        entity_ids = await composer.attach_character_entities(
            page,
            entity_ids=tuple(request.reference_entities),
            names=tuple(request.reference_entity_names),
            clear=not reference_ids,
        )
    await composer.send_prompt(page, request.prompt, append=bool(reference_ids) or bool(entity_ids))
    # The submit guard holds the body to EXACTLY these references: a lost character, or
    # any reference that is not on this list, aborts the run before Flow generates it.
    return await composer.submit_images_and_observe(
        page,
        request,
        reference_ids=reference_ids,
        entity_ids=tuple(e for e in entity_ids if e),
    )


async def create_project(
    page: Page, title: str, *, timeout_s: float = PAGE_RPC_TIMEOUT_S
) -> ProjectInfo:
    """Create a project through flow.google.com's projects page, titled ``title``.

    ``timeout_s`` bounds the create reply; the selector wait stays at ``ANCHOR_TIMEOUT_S``.
    Once Create is clicked, a failure never reads as retryable: a lost or late reply is
    recovered from Flow's navigation to the new project, or is "outcome unknown" (#1037).
    Leaves ``page`` on the new project, which is where every caller goes next."""
    await page.goto(MIGRATED_ROOT_URL, wait_until="domcontentloaded", timeout=45_000)
    await MigratedComposer._dismiss_dialog(page)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    button = await _wait_anchor(
        page,
        NEW_PROJECT_BUTTON,
        named="new-project button",
        requested=MIGRATED_ROOT_URL,
        at="migrated.create_project",
        timeout_s=min(timeout_s, ANCHOR_TIMEOUT_S),
    )
    before = str(page.url)
    deadline = time.monotonic() + timeout_s
    try:
        payload = await _rpc_reply(
            page,
            CREATE_PROJECT_RPC,
            lambda: button.click(timeout=15_000),
            timeout_s=timeout_s,
            write=True,
        )
    except (RpcResponseUnavailableError, TransportTimeoutError) as original_error:
        # Create was clicked, so Flow may have made the project. The response event
        # precedes Angular's navigation on a successful create: observe that navigation,
        # never click Create a second time.
        unknown = _write_outcome_unknown(
            original_error,
            CREATE_PROJECT_RPC,
            "Create was already clicked, so Flow may have made this project. Open "
            "https://flow.google.com/ and look for the newest project (it may still carry "
            "Flow's default name) before re-running: a re-run makes a second project. "
            "If it is there, pass its id with --project <id>.",
        )
        try:
            await page.wait_for_url(
                lambda url: _navigated_project_id(str(url), before) is not None,
                timeout=int(max(deadline - time.monotonic(), NAVIGATION_GRACE_S) * 1000),
            )
        except Exception:  # noqa: BLE001 - no verified project means uncertain outcome
            raise unknown from original_error
        created_id = _navigated_project_id(str(page.url), before)
        if created_id is None:
            raise unknown from original_error
        try:
            persisted_title, _ = await _project_snapshot(page, created_id)
        except Exception:  # noqa: BLE001 - no verified project means no recovery
            raise unknown from original_error
        payload = [created_id, [persisted_title]]
        log.info(
            "migrated.project_create_recovered",
            project_id=created_id,
            after=type(original_error).__name__,
        )
    reply = _as_list_any(payload)
    project_id: object = reply[0] if reply else None
    if not isinstance(project_id, str) or not UUID_RE.fullmatch(project_id):
        raise WireFormatError(
            detail=f"{CREATE_PROJECT_RPC} reply carried no project id: {str(payload)[:200]}",
            route=f"batchexecute:{CREATE_PROJECT_RPC}",
        )
    log.info("migrated.project_created", project_id=project_id)
    names = _as_list_any(reply[1]) if len(reply) > 1 else []
    created: object = names[0] if names else None
    if title and created != title:
        await rename_project(page, project_id, title, timeout_s=timeout_s)
        created = title
    return ProjectInfo(project_id=project_id, title=str(created or ""))


async def rename_project(
    page: Page, project_id: str, title: str, *, timeout_s: float = PAGE_RPC_TIMEOUT_S
) -> None:
    """Rename a project through its header title on flow.google.com.

    ``timeout_s`` bounds the rename reply; the selector wait stays at ``ANCHOR_TIMEOUT_S``.
    A lost or late reply is settled by reading the saved title back (#992, #1037)."""
    target = MIGRATED_PROJECT_URL.format(project_id=project_id)
    if not str(getattr(page, "url", "") or "").startswith(target):
        await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    await MigratedComposer._dismiss_dialog(page)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    field = await _wait_anchor(
        page,
        PROJECT_TITLE_INPUT,
        named="project title input",
        requested=target,
        at="migrated.rename_project",
        timeout_s=min(timeout_s, ANCHOR_TIMEOUT_S),
    )

    if await field.input_value() == title:
        persisted_title, _ = await _project_snapshot(page, project_id)
        if persisted_title == title:
            log.info("migrated.project_title_unchanged", project_id=project_id)
            return

    async def submit() -> None:
        try:
            await field.fill(title, timeout=15_000)
            await field.press("Enter", timeout=15_000)
        except PlaywrightTimeoutError as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the project title input ({PROJECT_TITLE_INPUT}) was "
                    f"visible but did not accept the new title on {safe_page_url(page.url)} "
                    "(host=migrated)"
                ),
            ) from e

    try:
        payload = await _rpc_reply(
            page, RENAME_PROJECT_RPC, submit, timeout_s=timeout_s, write=True
        )
    except (RpcResponseUnavailableError, TransportTimeoutError) as original_error:
        unknown = _write_outcome_unknown(
            original_error,
            RENAME_PROJECT_RPC,
            f"The rename was sent but not confirmed. Project {project_id} exists; open it "
            "on https://flow.google.com/ to check its title, and reuse it with "
            f"--project {project_id} rather than creating another.",
        )
        try:
            persisted_title, _ = await _project_snapshot(page, project_id)
        except Exception:  # noqa: BLE001 - no readback means the outcome stays unknown
            raise unknown from original_error
        if persisted_title != title:
            raise unknown from original_error
        payload = [persisted_title]
        log.info(
            "migrated.project_rename_recovered",
            project_id=project_id,
            after=type(original_error).__name__,
        )
    echo = _as_list_any(payload)
    echoed: object = echo[0] if echo else None
    if echoed != title:
        raise WireFormatError(
            detail=f"{RENAME_PROJECT_RPC} echoed title {echoed!r}, expected {title!r}",
            route=f"batchexecute:{RENAME_PROJECT_RPC}",
        )
    log.info("migrated.project_renamed", project_id=project_id)


#: The app's own balance read: no arguments, fired on every load of the app (#795).
#: `[]` -> `[balance, a, b, c, null, balance', [refresh_epoch_s, nanos]]`
#: (docs/superpowers/spikes/2026-10-22-credits-balance-on-nzlxg.md).
CREDITS_RPC = "nzlxg"


async def read_credits(page: Page, *, timeout_s: float = 30.0) -> CreditsInfo:
    """Load flow.google.com and return the balance from the app's own ``nzlxg`` reply.

    Read-only and free: nothing is clicked. The reply is read from the wire, not from the
    account menu, so no translated text is involved.
    """

    async def load() -> None:
        await page.goto(MIGRATED_ROOT_URL, wait_until="domcontentloaded", timeout=45_000)

    try:
        payload = await _rpc_reply(page, CREDITS_RPC, load, timeout_s=timeout_s)
    except TransportTimeoutError as e:
        if flow_landing_kind(page.url) == "public":
            # The generic /about hint says "open the project"; credits has no project.
            # Two causes are measured: a profile with no Google session at all (live
            # `credits list`, 2026-10-22), and healthy cookies held at /about by Google's
            # pending "Confirm it's you" (2026-09-23, denon82; spike 2026-09-23-about-cta-
            # leads-to-google-reauth.md), which `gflow auth login` cannot clear (#902).
            # They are not told apart here; `gflow auth status` does that.
            raise FlowAppError(
                detail=(
                    f"Flow redirected to its public landing page ({safe_page_url(page.url)}) "
                    "instead of the app, so there is no balance to read."
                ),
                route=f"batchexecute:{CREDITS_RPC}",
                remediation_hint=(
                    "Two causes land here. First run `gflow auth status --profile <name>`. "
                    "If it reports no Google session, sign in: `gflow auth login "
                    "--browser chrome --profile <name>`. If it reports you are signed in, "
                    "Google may want the account to confirm its identity, which `gflow auth "
                    "login` cannot clear: open Chrome by hand on the profile directory "
                    '(chrome --user-data-dir="<profile dir>" https://flow.google.com/), click '
                    "through, finish Google's \"Confirm it's you\", wait for the app, close "
                    "Chrome and re-run. See KNOWN_ISSUES."
                ),
                retryable=False,
                charged="no",  # a balance read spends nothing
                next_action="reauth",
            ) from e
        # Signed out, no Flow access: each has its own typed error.
        await raise_if_known_landing(page, requested=MIGRATED_ROOT_URL, at="migrated.credits")
        raise FlowAppError(
            detail=(
                f"flow.google.com loaded ({safe_page_url(page.url)}) but no readable "
                f"{CREDITS_RPC} reply (the credit balance) arrived within {timeout_s:.0f}s: "
                f"{e.detail}"
            ),
            route=f"batchexecute:{CREDITS_RPC}",
            remediation_hint=(
                "Your session is not the problem: the app loaded. Retry once; if it "
                "persists, open flow.google.com and check the balance in the account "
                "menu, then report it on issue #795 — Flow may have moved the balance."
            ),
            charged="no",  # a balance read spends nothing
        ) from e
    try:
        info = CreditsInfo.from_nzlxg(payload)
    except ValueError as e:
        raise WireFormatError(
            detail=str(e),
            route=f"batchexecute:{CREDITS_RPC}",
            remediation_hint=(
                "Flow changed the shape of its balance reply. Report it on issue #795; "
                "generation is unaffected."
            ),
        ) from e
    log.info("migrated.credits_read")
    return info
