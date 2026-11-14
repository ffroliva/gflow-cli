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
from __future__ import (
    annotations as annotations,
)

import asyncio as asyncio
import contextlib as contextlib
import json as json
import mimetypes as mimetypes
import re as re
import time as time
from dataclasses import (
    dataclass as dataclass,
)
from dataclasses import (
    field as field,
)
from pathlib import (
    Path as Path,
)
from typing import (
    TYPE_CHECKING as TYPE_CHECKING,
)
from typing import (
    Any as Any,
)
from typing import (
    Literal as Literal,
)
from typing import (
    cast as cast,
)
from urllib.parse import (
    unquote_plus as unquote_plus,
)
from urllib.parse import (
    urlsplit as urlsplit,
)
from uuid import (
    uuid4 as uuid4,
)

import structlog as structlog
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from gflow_cli.api.dto import (
    CreditsInfo as CreditsInfo,
)
from gflow_cli.api.dto import (
    GeneratedImage as GeneratedImage,
)
from gflow_cli.api.dto import (
    ProjectInfo as ProjectInfo,
)
from gflow_cli.api.image import (
    ImageRef as ImageRef,
)
from gflow_cli.api.transports._common import (
    expired_link_hint as expired_link_hint,
)
from gflow_cli.api.transports._common import (
    extract_project_id as extract_project_id,
)
from gflow_cli.api.transports._common import (
    flow_landing_kind as flow_landing_kind,
)
from gflow_cli.api.transports._common import (
    get_signed_media as get_signed_media,
)
from gflow_cli.api.transports._common import (
    raise_if_known_landing as raise_if_known_landing,
)
from gflow_cli.api.transports._common import (
    recoverable_clip_hint as recoverable_clip_hint,
)
from gflow_cli.api.transports._common import (
    safe_page_url as safe_page_url,
)
from gflow_cli.api.transports.batchexecute import (
    GenerationRecord as GenerationRecord,
)
from gflow_cli.api.transports.batchexecute import (
    generation_record as generation_record,
)
from gflow_cli.api.transports.batchexecute import (
    generation_records_carrying as generation_records_carrying,
)
from gflow_cli.api.transports.batchexecute import (
    generation_records_matching as generation_records_matching,
)
from gflow_cli.api.transports.batchexecute import (
    image_records as image_records,
)
from gflow_cli.api.transports.batchexecute import (
    parse_frames as parse_frames,
)
from gflow_cli.api.transports.batchexecute import (
    rpc_errors as rpc_errors,
)
from gflow_cli.api.video import (
    I2V_DEFAULT_MODEL as I2V_DEFAULT_MODEL,
)
from gflow_cli.api.video import (
    Aspect as Aspect,
)
from gflow_cli.api.video import (
    Mode as Mode,
)
from gflow_cli.api.video import (
    VideoModel as VideoModel,
)
from gflow_cli.api.video import (
    VideoResult as VideoResult,
)
from gflow_cli.api.video import (
    VideoStarted as VideoStarted,
)
from gflow_cli.api.video import (
    VideoStatus as VideoStatus,
)
from gflow_cli.errors import (
    CONTENT_SAFETY_REASONS as CONTENT_SAFETY_REASONS,
)
from gflow_cli.errors import (
    ConfigurationError as ConfigurationError,
)
from gflow_cli.errors import (
    ContentPolicyError as ContentPolicyError,
)
from gflow_cli.errors import (
    FlowAppError as FlowAppError,
)
from gflow_cli.errors import (
    FlowHostMigratedError as FlowHostMigratedError,
)
from gflow_cli.errors import (
    GFlowError as GFlowError,
)
from gflow_cli.errors import (
    InsufficientCreditsError as InsufficientCreditsError,
)
from gflow_cli.errors import (
    MediaDownloadError as MediaDownloadError,
)
from gflow_cli.errors import (
    MediaUploadRejectedError as MediaUploadRejectedError,
)
from gflow_cli.errors import (
    ReferenceNotFoundError as ReferenceNotFoundError,
)
from gflow_cli.errors import (
    RpcResponseUnavailableError as RpcResponseUnavailableError,
)
from gflow_cli.errors import (
    TransportTimeoutError as TransportTimeoutError,
)
from gflow_cli.errors import (
    UiSelectorDriftError as UiSelectorDriftError,
)
from gflow_cli.errors import (
    WafRejectionError as WafRejectionError,
)
from gflow_cli.errors import (
    WireFormatError as WireFormatError,
)
from gflow_cli.redaction import (
    redact_sensitive_text as redact_sensitive_text,
)

if TYPE_CHECKING:
    from collections.abc import (
        Awaitable,
        Callable,
    )

    from playwright.async_api import (
        Locator,
        Page,
    )

    from gflow_cli.api.image import (
        GenerateImageRequest,
    )
    from gflow_cli.api.video import (
        GenerateVideoRequest,
        VideoStartedCallback,
    )

    ClipSaver = Callable[[str, bytes], Awaitable[Path]]
from gflow_cli.api.transports import _mc_media
from gflow_cli.api.transports._mc_common import (
    _CARET_TO_END_JS as _CARET_TO_END_JS,
)
from gflow_cli.api.transports._mc_common import (
    _CLICK_POSTMORTEM_JS as _CLICK_POSTMORTEM_JS,
)
from gflow_cli.api.transports._mc_common import (
    _DURATION_IN_KEY as _DURATION_IN_KEY,
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

# Re-export all selectors, constants and helpers from _mc_common (#1249 wave 2)
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
    AGENT_ONLY_RELOAD_WAIT_S as AGENT_ONLY_RELOAD_WAIT_S,
)
from gflow_cli.api.transports._mc_common import (
    AGENT_ONLY_RELOADS as AGENT_ONLY_RELOADS,
)
from gflow_cli.api.transports._mc_common import (
    AGENT_ONLY_SETTLE_S as AGENT_ONLY_SETTLE_S,
)
from gflow_cli.api.transports._mc_common import (
    AGENT_RECOVERY_S as AGENT_RECOVERY_S,
)
from gflow_cli.api.transports._mc_common import (
    ANCHOR_TIMEOUT_S as ANCHOR_TIMEOUT_S,
)
from gflow_cli.api.transports._mc_common import (
    ARROW_SETTLE_MS as ARROW_SETTLE_MS,
)
from gflow_cli.api.transports._mc_common import (
    ARROW_STEP_MS as ARROW_STEP_MS,
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
    DIALOG_CLOSE as DIALOG_CLOSE,
)
from gflow_cli.api.transports._mc_common import (
    EMPTY_CHIP as EMPTY_CHIP,
)
from gflow_cli.api.transports._mc_common import (
    EXISTING_REF_RELOAD_PAUSE_S as EXISTING_REF_RELOAD_PAUSE_S,
)
from gflow_cli.api.transports._mc_common import (
    EXISTING_REF_WAIT_S as EXISTING_REF_WAIT_S,
)
from gflow_cli.api.transports._mc_common import (
    FRAME_COMMIT_GRACE_S as FRAME_COMMIT_GRACE_S,
)
from gflow_cli.api.transports._mc_common import (
    FRAME_COMMIT_HIDDEN_S as FRAME_COMMIT_HIDDEN_S,
)
from gflow_cli.api.transports._mc_common import (
    FRAME_PICKER_OPEN_S as FRAME_PICKER_OPEN_S,
)
from gflow_cli.api.transports._mc_common import (
    FRAME_SEARCH_ATTEMPTS as FRAME_SEARCH_ATTEMPTS,
)
from gflow_cli.api.transports._mc_common import (
    FRAME_SEARCH_RETRY_PAUSE_S as FRAME_SEARCH_RETRY_PAUSE_S,
)
from gflow_cli.api.transports._mc_common import (
    FRAME_THUMB_VISIBLE_S as FRAME_THUMB_VISIBLE_S,
)
from gflow_cli.api.transports._mc_common import (
    FRAME_UPLOAD_S as FRAME_UPLOAD_S,
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
    IMAGE_MODEL_WIRE_TOKENS as IMAGE_MODEL_WIRE_TOKENS,
)
from gflow_cli.api.transports._mc_common import (
    IMAGE_RECOVERY_S as IMAGE_RECOVERY_S,
)
from gflow_cli.api.transports._mc_common import (
    IMAGE_RECOVERY_SETTLE_S as IMAGE_RECOVERY_SETTLE_S,
)
from gflow_cli.api.transports._mc_common import (
    IMAGE_REPLY_BUDGET_S as IMAGE_REPLY_BUDGET_S,
)
from gflow_cli.api.transports._mc_common import (
    IMAGE_SUBMIT_RPC as IMAGE_SUBMIT_RPC,
)
from gflow_cli.api.transports._mc_common import (
    INGREDIENTS_LIGATURE as INGREDIENTS_LIGATURE,
)
from gflow_cli.api.transports._mc_common import (
    INTERPOLATION_SUBMIT_RPC as INTERPOLATION_SUBMIT_RPC,
)
from gflow_cli.api.transports._mc_common import (
    LISTING_READ_BUDGET_S as LISTING_READ_BUDGET_S,
)
from gflow_cli.api.transports._mc_common import (
    LISTING_RPC as LISTING_RPC,
)
from gflow_cli.api.transports._mc_common import (
    LOWER_PRIORITY_TAG as LOWER_PRIORITY_TAG,
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
    MIGRATED_SETTINGS_CONTRACT as MIGRATED_SETTINGS_CONTRACT,
)
from gflow_cli.api.transports._mc_common import (
    MODEL_KEY as MODEL_KEY,
)
from gflow_cli.api.transports._mc_common import (
    NAVIGATION_GRACE_S as NAVIGATION_GRACE_S,
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
    PICKER_CONFIRM as PICKER_CONFIRM,
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
    PROMPT_MATCH_MARGIN_S as PROMPT_MATCH_MARGIN_S,
)
from gflow_cli.api.transports._mc_common import (
    R2V_DURATION_S as R2V_DURATION_S,
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
    RESULT_URL_GRACE_S as RESULT_URL_GRACE_S,
)
from gflow_cli.api.transports._mc_common import (
    STATUS_RPCS as STATUS_RPCS,
)
from gflow_cli.api.transports._mc_common import (
    SUBMIT_BUTTON as SUBMIT_BUTTON,
)
from gflow_cli.api.transports._mc_common import (
    SUBMIT_ENABLE_BUDGET_S as SUBMIT_ENABLE_BUDGET_S,
)
from gflow_cli.api.transports._mc_common import (
    SUBMIT_ENABLE_POLL_S as SUBMIT_ENABLE_POLL_S,
)
from gflow_cli.api.transports._mc_common import (
    SUBMIT_REPLY_BUDGET_S as SUBMIT_REPLY_BUDGET_S,
)
from gflow_cli.api.transports._mc_common import (
    SUBMIT_RPCS as SUBMIT_RPCS,
)
from gflow_cli.api.transports._mc_common import (
    TOOLBAR_ADD as TOOLBAR_ADD,
)
from gflow_cli.api.transports._mc_common import (
    UNUSUAL_ACTIVITY_REASON as UNUSUAL_ACTIVITY_REASON,
)
from gflow_cli.api.transports._mc_common import (
    UPLOAD_MENU_ITEM as UPLOAD_MENU_ITEM,
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
    VISIBLE_OVERLAY as VISIBLE_OVERLAY,
)
from gflow_cli.api.transports._mc_common import (
    AgentModeExit as AgentModeExit,
)
from gflow_cli.api.transports._mc_common import (
    ComposerKind as ComposerKind,
)
from gflow_cli.api.transports._mc_common import (
    ImageAspect as ImageAspect,
)
from gflow_cli.api.transports._mc_common import (
    ModelMenuMatcher as ModelMenuMatcher,
)
from gflow_cli.api.transports._mc_common import (
    _as_list_any as _as_list_any,
)
from gflow_cli.api.transports._mc_common import (
    _ClipByPrompt as _ClipByPrompt,
)
from gflow_cli.api.transports._mc_common import (
    _duration as _duration,
)
from gflow_cli.api.transports._mc_common import (
    _exact as _exact,
)
from gflow_cli.api.transports._mc_common import (
    _first_uuid as _first_uuid,
)
from gflow_cli.api.transports._mc_common import (
    _grid_images as _grid_images,
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
    _new_grid_images as _new_grid_images,
)
from gflow_cli.api.transports._mc_common import (
    _outcome_unknown as _outcome_unknown,
)
from gflow_cli.api.transports._mc_common import (
    _picker_pane as _picker_pane,
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
    _recover_images as _recover_images,
)
from gflow_cli.api.transports._mc_common import (
    _recovery_detail as _recovery_detail,
)
from gflow_cli.api.transports._mc_common import (
    _rpc_reply as _rpc_reply,
)
from gflow_cli.api.transports._mc_common import (
    _select_resolution as _select_resolution,
)
from gflow_cli.api.transports._mc_common import (
    _submit_body_problem as _submit_body_problem,
)
from gflow_cli.api.transports._mc_common import (
    _submit_refusal as _submit_refusal,
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
    _unreadable_rpc_error as _unreadable_rpc_error,
)
from gflow_cli.api.transports._mc_common import (
    _visible_model_picker as _visible_model_picker,
)
from gflow_cli.api.transports._mc_common import (
    _wait_anchor as _wait_anchor,
)
from gflow_cli.api.transports._mc_common import (
    _write_outcome_unknown as _write_outcome_unknown,
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
from gflow_cli.api.transports._mc_settings_pane import (
    ImageModel as ImageModel,
)
from gflow_cli.api.transports._mc_settings_pane import (
    SettingsPaneMixin,
)
from gflow_cli.api.transports.migrated_existing_refs import (
    heal_catalog as _heal_catalog,
)
from gflow_cli.api.transports.migrated_existing_refs import (
    picker_query as _picker_query,
)
from gflow_cli.api.transports.migrated_existing_refs import (
    refreshed_from_picker as refreshed_from_picker,
)
from gflow_cli.api.transports.migrated_existing_refs import (
    require_appended as _require_appended,
)

log = structlog.get_logger(__name__)


class MigratedComposer(SettingsPaneMixin):
    """Settings → prompt → submit → observe, against the migrated editor."""

    # --- start frame (i2v) ------------------------------------------------------

    async def attach_start_frame(self, page: Page, project_id: str, image_path: Path) -> str:
        """Upload ``image_path`` through the editor's own Upload entry, bind it on the
        Start chip by file name, and return the media id the app's ``maseQ`` reply
        named for it — the id the submit body is then asserted to carry.

        The upload is permanent in the Flow project (it lands in the library like any
        other asset). The picker is library-only and searched by display name, so what
        is uploaded is a run-unique COPY of ``image_path`` — see below.
        """
        from gflow_cli.api.client import validate_image_file  # noqa: PLC0415 - cycle

        await validate_image_file(image_path)
        # No outer budget: each leg is bounded, and an outer one firing first would
        # replace the stage-named failure with a generic "attach timed out".
        media_id, display_name = await self._upload_via_toolbar(page, project_id, image_path)
        await self._pick_frame_by_name(page, display_name, media_id)
        return media_id

    async def attach_end_frame(self, page: Page, project_id: str, image_path: Path) -> str:
        """Upload ``image_path`` through the editor's own Upload entry, bind it on the
        End chip by file name, and return the media id — mirroring
        :meth:`attach_start_frame`. The Start chip must already be bound: the pick
        clicks the first REMAINING empty chip and then requires two bound chips, so
        a silently unbound Start surfaces here instead of mis-binding End.
        """
        from gflow_cli.api.client import validate_image_file  # noqa: PLC0415 - cycle

        await validate_image_file(image_path)
        media_id, display_name = await self._upload_via_toolbar(page, project_id, image_path)
        await self._pick_frame_by_name(
            page, display_name, media_id, chip_label="End", expect_bound_chips=2
        )
        return media_id

    async def upload(self, page: Page, project_id: str, image_path: Path) -> tuple[str, str]:
        """Upload ``image_path`` into the project; ``(media_id, run-unique caption)``.

        The public face of :meth:`_upload_via_toolbar`, for a reference that will be
        mentioned in place on later rows instead of uploaded again (#913).
        """
        return await self._upload_via_toolbar(page, project_id, image_path)

    async def _upload_via_toolbar(
        self, page: Page, project_id: str, image_path: Path
    ) -> tuple[str, str]:
        """Toolbar ``+`` → the ``upload`` menu item → the file chooser → the app's own
        ``maseQ`` upload, observed for the media id it returns. Nothing is replayed.

        Returns ``(media_id, display_name)``. The bytes are handed to the chooser with a
        **run-unique display name** (:func:`_unique_display_name`) rather than the file's
        own, because both callers find the upload again by that name — so the caller must
        search for the name returned here, never for ``image_path.name``.
        """
        display_name = _unique_display_name(image_path)
        loop = asyncio.get_running_loop()
        reply: asyncio.Future[tuple[int, str | None]] = loop.create_future()
        route = f"batchexecute:{UPLOAD_RPC}"

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if "batchexecute" not in url or url_rpcid(url) != UPLOAD_RPC or reply.done():
                return
            status = int(getattr(response, "status", 0) or 0)
            text: str | None = ""
            if status == 200:
                try:
                    text = await response.text()
                except Exception:  # noqa: BLE001 - the write may have persisted
                    text = None
            if not reply.done():
                reply.set_result((status, text))

        # The response listener alone cannot tell "the page never asked" from "Flow was
        # slow" — and those are different bugs with opposite fixes (#719: shape A is a
        # blocked send, shape B is a lost reply). One boolean splits them at the point of
        # failure instead of leaving both as one 60 s timeout.
        sent = False

        def on_request(request: Any) -> None:
            nonlocal sent
            url = str(getattr(request, "url", ""))
            if "batchexecute" in url and url_rpcid(url) == UPLOAD_RPC:
                sent = True

        page.on("response", on_response)
        page.on("request", on_request)
        try:
            add = page.locator(TOOLBAR_ADD).first
            if not await add.count():
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the toolbar add button (mat-icon 'add' outside "
                        "flow-prompt-box) is missing (host=migrated)"
                    ),
                )
            await add.click(timeout=5000)
            item = page.locator(UPLOAD_MENU_ITEM).first
            try:
                await item.wait_for(state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000))
            except Exception as e:
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the toolbar menu rendered no upload entry "
                        f"({MENU_ITEM} with the 'upload' ligature) within "
                        f"{FRAME_PICKER_OPEN_S:.0f}s (host=migrated)"
                    ),
                ) from e
            try:
                async with page.expect_file_chooser(
                    timeout=int(FRAME_PICKER_OPEN_S * 1000)
                ) as fc_info:
                    await item.click(timeout=4000)
                chooser = await fc_info.value
            except Exception as e:
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the upload entry opened no file chooser within "
                        f"{FRAME_PICKER_OPEN_S:.0f}s (host=migrated)"
                    ),
                ) from e
            # Flow holds an account's FIRST upload behind a one-time "rights to use this
            # image" confirmation. It renders only once the chooser has handed the file
            # over — so `_dismiss_dialog`, back in `ensure_editor`, never sees it — and
            # Flow sends nothing until a human accepts, which is why the wait below used
            # to expire on a request the page had already declined to make (#719).
            #
            # Counted, never matched. The dialog's two buttons are
            # `button.flow-button-medium` with no ligature and no data attribute,
            # separable only by DOM order, and its copy is translated — no anchor there
            # satisfies this module's locale rule. "A dialog appeared between the file
            # being chosen and the wait expiring" is upload-related by construction: it
            # needs no selector and cannot rot when Angular renames a class. The baseline
            # is taken BEFORE `set_files` so an already-open modal is never blamed.
            #
            # gflow does not click it: accepting affirms that the ACCOUNT OWNER holds the
            # rights to the content, which is not a claim a script may make for someone.
            # Measured 2026-09-08 across 6 runs on ci-probe —
            # docs/superpowers/spikes/2026-09-08-migrated-upload-fails-two-ways.md
            dialogs_before = await page.locator(DIALOG).count()
            # A FilePayload, not a path: the display name Flow lists the asset under is
            # ours to choose, so uniqueness needs no copy on disk (and leaves no temp file
            # to leak). `read_bytes` is off-thread for the same reason the header read in
            # `client.py` is — the file is up to MAX_IMAGE_BYTES.
            await chooser.set_files(
                {
                    "name": display_name,
                    "mimeType": mimetypes.guess_type(image_path.name)[0]
                    or "application/octet-stream",
                    "buffer": await asyncio.to_thread(image_path.read_bytes),
                }
            )
            try:
                status, text = await asyncio.wait_for(reply, timeout=FRAME_UPLOAD_S)
            except TimeoutError:
                try:
                    opened = await page.locator(DIALOG).count() > dialogs_before
                except Exception:  # noqa: BLE001 - a probe is never the failure it reports
                    # The likeliest reason no reply came is that the page died or navigated,
                    # in which case this count raises too. Letting that escape would replace
                    # a mapped exit 27 and its remediation with an unmapped traceback — the
                    # #752 lesson, one surface over.
                    opened = False
                if opened and not sent:
                    raise MediaUploadRejectedError(
                        detail=(
                            f"migrated host: a dialog opened after the file was chosen and no "
                            f"{UPLOAD_RPC} request left the page — most likely Flow's one-time "
                            f"upload-terms confirmation (host=migrated)"
                        ),
                        route=route,
                        remediation_hint=(
                            "Open the project on flow.google.com, upload any image by hand, "
                            "and accept the one-time 'rights to use this image' dialog. gflow "
                            "does not accept it for you: it affirms that YOU hold the rights "
                            "to what you upload. It appears once per account — after that, "
                            "uploads work unattended. If you see a different dialog instead "
                            "(an error, a quota notice, a re-login), that is the one blocking "
                            "the upload. Nothing was spent; re-run when done."
                        ),
                    ) from None
                # Say which half of #719 this is. `sent` is observed, not inferred: the
                # request listener above saw the upload leave the page, or it did not.
                went_out = (
                    "the request left the page and Flow did not answer in time"
                    if sent
                    else "no upload request ever left the page"
                )
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: no {UPLOAD_RPC} reply within {FRAME_UPLOAD_S:.0f}s "
                        f"of choosing the file — {went_out}"
                    ),
                    route=route,
                    remediation_hint=(
                        "If the request left the page, Flow accepted the upload and did not "
                        "answer in time — an intermittent fault on this host (#719), not "
                        "your file: re-run, and the same file usually succeeds. If nothing "
                        "left the page, something client-side stopped it — check for a modal "
                        "on flow.google.com. Either way nothing was spent. Re-encoding the "
                        "image does NOT help; three different files were ruled out in #719."
                    ),
                ) from None
            if status != 200:
                raise MediaUploadRejectedError(
                    detail=f"migrated host: the upload rpc {UPLOAD_RPC} answered HTTP {status}",
                    status=status,
                    route=route,
                )
            if text is None:
                service_error = _unreadable_rpc_error(
                    UPLOAD_RPC, location=f"{safe_page_url(page.url)} (upload={display_name})"
                )
                try:
                    _, tiles = await _project_snapshot(page, project_id, upload_name=display_name)
                    matches = [t["id"] for t in tiles if t["name"] == display_name]
                    if (
                        len(matches) == 1
                        and UUID_RE.fullmatch(matches[0])
                        and matches[0].lower() != project_id.lower()
                    ):
                        log.info("migrated.upload_recovered", media_id=matches[0])
                        return matches[0], display_name
                except Exception as exc:  # noqa: BLE001 - retain the original uncertain reply
                    log.info("migrated.upload_readback_unverified", error_type=type(exc).__name__)
                raise service_error
            media_id = _first_uuid(text)
            if media_id is None and any(
                e.rpcid == UPLOAD_RPC and e.code == 3 for e in rpc_errors(text)
            ):
                # gRPC 3 INVALID_ARGUMENT: Flow refused this file (a 1x1 PNG, measured
                # 3/3 on 2026-10-20), not a protocol break (#755).
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: Flow declined the image {display_name} "
                        f"({UPLOAD_RPC} answered INVALID_ARGUMENT)"
                    ),
                    status=status,
                    route=route,
                    remediation_hint=(
                        "Flow refuses very small or degenerate images. Re-try with a "
                        "normal-sized image; nothing was spent."
                    ),
                )
            if media_id is None:
                raise MediaUploadRejectedError(
                    detail=f"migrated host: {UPLOAD_RPC} answered 200 without a media id",
                    status=status,
                    route=route,
                )
            if media_id.lower() == project_id.lower():
                # The measured reply is ``[media_id, project_id, …]``. If the first
                # UUID is the project's, the shape moved under us — and the submit-body
                # assertion could not catch it, since the project id is in every body.
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: the first id in the {UPLOAD_RPC} reply is the "
                        f"project id ({project_id}), not a new media id — the reply "
                        "shape changed and the upload cannot be bound safely"
                    ),
                    status=status,
                    route=route,
                )
            log.info("migrated.frame_uploaded", media_id=media_id, status=status)
            return media_id, display_name
        finally:
            page.remove_listener("response", on_response)
            page.remove_listener("request", on_request)

    async def reference_existing(
        self,
        page: Page,
        project_id: str,
        request: GenerateImageRequest,
        *,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> tuple[str, ...]:
        """Apply the image settings and mention ``request.refs`` in place, reloading on a miss.

        A just-generated image can be missing from this page load's grid or picker
        search and present after a reload (measured twice, live e2e 2026-10-01). A reload
        resets the settings and the composer, so each attempt redoes all three steps.

        #546 on this host: the picker is searched by the catalog's caption, which goes
        stale when the image is renamed in Flow. The labs listing the resolver reads is
        retired here, so the first miss with a grid tile reads the current captions from
        the page itself (``refreshed_from_picker``) and the reload that follows retries
        with them; the reload also clears the stale ``@query`` text. A caption found this
        way is handed to ``name_resolver`` after the attach, when it can persist one.
        Cached name = optimization; listing = truth; UUID = identity.
        """
        refs = request.refs
        for ref in refs:
            _picker_query(ref)  # an unusable caption is refused before any reload
        stale = {ref.name: ref.display_name for ref in refs}
        deadline = time.monotonic() + EXISTING_REF_WAIT_S
        look_up = True
        while True:
            try:
                tokens = await self.await_existing_references(page, refs)
                await self.apply_image_settings(page, request)
                media_ids = await self.attach_existing_references(page, refs, tokens)
                await _heal_catalog(name_resolver, refs, stale)
                return media_ids
            except ReferenceNotFoundError:
                if look_up:  # one look, spent only when a tile could be matched
                    found = await refreshed_from_picker(self, page, refs)
                    look_up, refs = found is None, found or refs
                if time.monotonic() >= deadline:
                    raise
            log.info("migrated.existing_reference_reload")
            await page.wait_for_timeout(EXISTING_REF_RELOAD_PAUSE_S * 1000)
            await page.reload(wait_until="domcontentloaded", timeout=45_000)
            if await self.ensure_editor(page, project_id) == "agent_only":
                from gflow_cli.api.transports import agent_only_composer as agent  # noqa: PLC0415

                raise agent.refuse_unported("image-to-image")

    async def await_existing_references(
        self, page: Page, refs: tuple[ImageRef, ...]
    ) -> dict[str, str]:
        """Each reference's grid thumbnail token in this page load; a missing one raises.

        Measured (live e2e, 2026-10-01): the project grid is the asset list fetched when
        the editor loads, and it is not updated in place. A row that opened the editor a
        second after its parent was generated polled for 30 s without the tile; the next
        row, after a reload, found it at once. :meth:`reference_existing` reloads.
        """
        tokens = {
            ref.name: str(await page.evaluate(_GRID_TOKEN_JS, ref.name) or "") for ref in refs
        }
        missing = [media_id for media_id, token in tokens.items() if not token]
        if missing:
            log.info("migrated.existing_reference_not_listed", missing=len(missing))
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: image {missing[0]} is not in this project's grid, so "
                    "it cannot be referenced in place"
                ),
            )
        return tokens

    async def attach_existing_references(
        self, page: Page, refs: tuple[ImageRef, ...], tokens: dict[str, str]
    ) -> tuple[str, ...]:
        """Mention images already in the project, chosen by identity; upload nothing.

        Measured (docs/superpowers/spikes/2026-10-01-batch-ref-dropped.md, Gate): the
        ``@`` picker is project-scoped and lists matches by Flow's caption, but captions
        collide and the older match comes first. Each option's thumbnail is
        ``/asb/<token>``; the project grid tile ``img[data-media-id=<uuid>]`` carries the
        same token. So: search by caption, select the option whose token is the
        reference's, and confirm a chip landed. ``_image_body_problem`` still checks the
        submit carries the ids.
        """
        for count, ref in enumerate(refs, start=1):
            query = _picker_query(ref)
            await self._mention_by_token(
                page, query, tokens[ref.name], ref.name, expect_chips=count
            )
        media_ids = tuple(ref.name for ref in refs)
        # Distinct from `migrated.references_attached` (the upload path): this one means
        # nothing was uploaded.
        log.info("migrated.existing_references_attached", count=len(refs), media_ids=media_ids)
        return media_ids

    async def _mention_by_token(
        self, page: Page, query: str, token: str, media_id: str, *, expect_chips: int
    ) -> None:
        """One search in this page load; a miss closes the picker and raises.

        Measured (live e2e, 2026-10-01): when the picker does not offer a just-generated
        image, searching again in the same page load does not help (0 options three times
        over 30 s), while a fresh editor load offers it at once. So retrying belongs to
        :meth:`reference_existing`, which reloads.
        """
        before = await self._caret_to_end(page)
        await page.keyboard.type("@", delay=120)
        await page.wait_for_timeout(2200)
        await page.keyboard.type(query, delay=100)
        await page.wait_for_timeout(2500)
        tokens = [str(t) for t in await page.evaluate(_OPTION_TOKENS_JS, PICKER_OPTION)]
        if token in tokens:
            # The option's position: >0 means another image shares the caption (#21).
            log.info("migrated.existing_reference_option", option_index=tokens.index(token))
            for _ in range(tokens.index(token)):
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(ARROW_SETTLE_MS)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
            chips = await self.read_chips(page)
            # A query can also match a character; only a media chip is this image.
            if len(chips) == expect_chips and chips[-1].get("reference_type") == "media":
                _require_appended(before, chips)
                await page.keyboard.type(" ", delay=80)
                return
        else:
            log.info("migrated.mention_miss", offered=len(tokens), by="token")
            # The picker is a dialog over the composer (measured, gate #39): close it.
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: image {media_id} did not attach as a reference "
                f"({len(tokens)} picker option(s) offered for its caption, none of them it)"
            ),
        )

    async def attach_references(
        self, page: Page, project_id: str, paths: tuple[Path, ...]
    ) -> tuple[str, ...]:
        """Upload each local reference, mention it in the prompt, return the media ids.

        Uploading reuses the i2v toolbar path, so the app's own ``maseQ`` reply names the
        media id — the id :func:`_r2v_body_problem` then asserts the submit carries.
        Binding is different from i2v though: a reference is not a chip slot but an ``@``
        mention in the prompt document, committed with **Enter**. A typed query alone
        leaves the picker open and inserts nothing (measured 2026-09-05), which is the
        failure that would otherwise generate a clip with no references on it.
        """
        media_ids: list[str] = []
        display_names: list[str] = []
        for path in paths:
            # Mentioned by the name the upload was LISTED under, never the source file's:
            # reference images are re-used across runs by design (#792, _unique_display_name).
            media_id, display_name = await self._upload_via_toolbar(page, project_id, path)
            media_ids.append(media_id)
            display_names.append(display_name)
        # Compose after every upload: the file chooser takes keyboard focus, so mentions
        # cannot be interleaved with uploading.
        await self.clear_composer(page)
        for i, name in enumerate(display_names):
            await self._mention_by_name(page, name, expect_chips=i + 1)
        log.info("migrated.references_attached", count=len(paths), media_ids=media_ids)
        return tuple(media_ids)

    async def attach_character_entities(
        self,
        page: Page,
        *,
        entity_ids: tuple[str, ...],
        names: tuple[str, ...],
        clear: bool = True,
    ) -> tuple[str, ...]:
        """Mention each character by name, then prove the chip is that ENTITY (#723).

        The gesture is ``@``, the name, then the option TITLED exactly the name
        (:meth:`_mention_character`) — never the first option, and never a trial commit:
        a chip deleted from the prompt still rides the submit. Then the verification,
        which is not optional:

        **Flow lists characters and media in that one picker and does not rank them.**
        Measured 2026-09-07: the same ``@Kael`` query committed
        ``data-reference-type="entity"`` with the real entity id on one gesture, and
        ``reference_type="media"`` — a JPEG that merely shared the name — on another.
        A media chip where a character was asked for produces a clip that looks right and
        drifts on the next cut, which is the failure characters exist to prevent. So every
        chip is read back and checked for BOTH its kind and its id before anything is
        submitted, exactly as the labs path asserts ``referenceEntities`` on the wire
        (``ui_automation_video.py`` ``_assert_entities_attached``).

        ``names`` drive the gesture and may outnumber ``entity_ids``: a character
        mentioned by name with no catalog to look it up in (#723) has no id to check, so
        only its chip kind is verified. Returns the entity ids the committed chips carry.
        """
        if clear:
            await self.clear_composer(page)
        # An r2v run can carry media references AND characters, so judge only the chips
        # THIS call adds: media chips already on the prompt are legitimate, and clearing
        # them would silently drop references the caller asked for.
        base = len(await self.read_chips(page))
        for i, name in enumerate(names):
            await self._mention_character(page, name, expect_chips=base + i + 1)

        chips = (await self.read_chips(page))[base:]
        not_entities = [c for c in chips if c.get("reference_type") != "entity"]
        if not_entities:
            got = ", ".join(f"{c.get('text')!r} ({c.get('reference_type')})" for c in not_entities)
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: asked for character(s) {', '.join(names)} but the "
                    f"picker committed {got}. Flow offers characters and media under one "
                    f"search and does not rank them, so a file sharing the name can win. "
                    f"Refusing to spend credits on a generation that would carry the wrong "
                    f"reference"
                ),
            )
        # A by-name character has no id to check, so its chip's text (the character's
        # name, measured 2026-09-07) is what proves the picker committed the right one.
        got_names = sorted(str(c.get("text", "")).casefold() for c in chips)
        if got_names != sorted(n.casefold() for n in names):
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: asked for {len(names)} character(s) by name but the "
                    "committed chips carry different names. Refusing to submit"
                ),
            )
        attached = {c.get("entity_id", "") for c in chips}
        missing = [e for e in entity_ids if e not in attached]
        if missing:
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: the prompt carries entity chips {sorted(attached)} but "
                    f"{missing} was requested — a chip of the right kind is not proof it is "
                    f"the right character. Refusing to submit"
                ),
            )
        committed = tuple(c.get("entity_id", "") for c in chips)
        log.info(
            "migrated.character_entities_attached",
            count=len(chips),
            names=list(names),
            entity_ids=list(committed),
        )
        return committed

    async def _mention_character(self, page: Page, name: str, *, expect_chips: int) -> None:
        """Commit the picker option TITLED exactly *name* — one gesture, nothing on trial.

        Measured 2026-10-13 (``scripts/dev/spike_picker_entity_marker.py``, and see
        ``docs/superpowers/spikes/2026-10-13-migrated-image-character-mention.md``): character and
        media options are structurally identical and differ only by a translated subtitle,
        and an image captioned "Sussy cooking…" ranked above the character "Sussy". Trying
        options and deleting the media chips is NOT an option: a deleted chip leaves the
        prompt but its reference stays in the composer's request state and rides the
        submit (live, 2026-10-13: five references on a run whose prompt showed one; chip
        removal measured by ``spike_picker_chip_removal.py``, the leak reproduced by
        ``spike_image_submit_references.py --trial-media``).

        ``.asset-title`` holds the asset's own name — user data, not a translated label —
        so the option whose trimmed title equals the name (case-insensitive) is the
        character, reached with one ArrowDown run and committed with Enter. Several exact
        matches (a media asset literally titled the name) are refused before anything is
        committed, as is no match; a single match that commits a media chip is refused
        too (the caller aborts the run before submit, so that chip never reaches Flow).
        """
        titles: list[str] = []
        before: list[dict[str, str]] = []
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            before = await self._caret_to_end(page)
            # Real keystrokes: see `_mention_by_name`.
            await page.keyboard.type("@", delay=120)
            await page.wait_for_timeout(2200)
            await page.keyboard.type(name, delay=100)
            await page.wait_for_timeout(2500)
            titles = [str(t) for t in await page.evaluate(_OPTION_TITLES_JS, PICKER_OPTION)]
            if titles or attempt == FRAME_SEARCH_ATTEMPTS:
                break
            # The search had not settled: clear the typed `@name` and ask again.
            log.info("migrated.mention_miss", name_chars=len(name), attempt=attempt, offered=0)
            for _ in range(len(name) + 1):
                await page.keyboard.press("Backspace")
            await page.wait_for_timeout(FRAME_SEARCH_RETRY_PAUSE_S * 1000)
        exact = [i for i, t in enumerate(titles) if t.strip().casefold() == name.casefold()]
        # Titles are asset names, often prompt-like captions: report counts, not text,
        # so an error detail (stderr, MCP, the catalog) never carries them.
        offered = f"{len(titles)} option(s)"
        if len(exact) != 1:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)
            why = (
                "no option is titled exactly that"
                if not exact
                else f"{len(exact)} options are titled exactly that, and a character and a "
                "media asset cannot be told apart before one is committed"
            )
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: asked for character {name!r} but {why}; the picker "
                    f"offered {offered}. Refusing before submit — rename the colliding "
                    "asset in Flow, or give the character a one-word name that is its full "
                    "name"
                ),
            )
        index = exact[0]
        # A click on the option commits nothing (measured 2026-10-13); the keyboard does.
        # Only the FINAL highlight has to settle before Enter (`ARROW_SETTLE_MS`), so the
        # arrows go in quickly and the settle is paid once, not per step.
        for _ in range(index):
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(ARROW_STEP_MS)
        if index:
            await page.wait_for_timeout(ARROW_SETTLE_MS)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2500)
        chips = await self.read_chips(page)
        log.info("migrated.character_option", option_index=index, offered=len(titles))
        if len(chips) != expect_chips:
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: committing the option titled {name!r} committed no chip "
                    f"({len(chips)} chip(s), expected {expect_chips}); the picker offered "
                    f"{offered}"
                ),
            )
        _require_appended(before, chips)
        # The kind check lives in `attach_character_entities`: a media chip here is
        # refused there, before any submit.

    async def _caret_to_end(self, page: Page) -> list[dict[str, str]]:
        """Focus the composer with the caret at its END; return the chips already there.

        Every ``@`` mention starts here, so a new chip can only be appended (#997).
        """
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.evaluate(_CARET_TO_END_JS, COMPOSER)
        return await self.read_chips(page)

    async def clear_composer(self, page: Page) -> None:
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(600)

    async def read_chips(self, page: Page) -> list[dict[str, str]]:
        """Every mention now in the prompt, with the kind that decides its wire slot."""
        return await page.evaluate(
            "(sel) => [...document.querySelectorAll(sel)].map(c => ({"
            "  text: (c.textContent || '').trim(),"
            "  entity_id: c.getAttribute('data-entity-id') || '',"
            "  reference_type: c.getAttribute('data-reference-type') || '',"
            "}))",
            MENTION_CHIP,
        )

    async def _mention_by_name(self, page: Page, name: str, *, expect_chips: int) -> None:
        """Insert one mention chip for *name*, and verify it landed.

        Retries like :meth:`_pick_frame_by_name` does, and for the same measured reason:
        the mention picker is backed by the same server-side asset search, which does not
        always index a fresh upload by the first query. Enter is pressed only when the
        picker offers something: with no picker open it submits the generation (see
        docs/superpowers/spikes/2026-11-04-external-prs-1142-1145.md). The picker owns the
        query text, so Escape cancels a miss without touching existing chips, and the run
        is refused if an attached chip disappeared.
        """
        offered: list[str] = []
        chips: list[dict[str, str]] = []
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            prior = await self._caret_to_end(page)
            before = len(prior)
            # `keyboard.type`, never `insert_text`: the latter dispatches input events with
            # no key events, so the mention plugin opens a picker with no query behind it
            # and every later gesture is a no-op. `send_prompt` keeps insert_text on
            # purpose — a newline in prompt text must not submit — so they cannot share a
            # path.
            await page.keyboard.type("@", delay=120)
            await page.wait_for_timeout(2200)
            await page.keyboard.type(name, delay=100)
            await page.wait_for_timeout(2500)
            offered = [t.strip() for t in await page.locator(PICKER_OPTION).all_text_contents()]
            if offered:
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2500)
            chips = await self.read_chips(page)
            if len(chips) == expect_chips:
                _require_appended(prior, chips)
                await page.keyboard.type(" ", delay=80)
                return
            log.info(
                "migrated.mention_miss",
                name=name,
                attempt=attempt,
                chips=len(chips),
                offered=len(offered),
            )
            await page.keyboard.press("Escape")
            chips = await self.read_chips(page)
            if len(chips) < before:
                raise ReferenceNotFoundError(
                    detail=(
                        f"migrated host: clearing the failed {name!r} query removed an "
                        f"already-attached reference ({before} chip(s) before, "
                        f"{len(chips)} after) — the prompt is no longer the one that was "
                        "built, so this run is abandoned rather than submitted"
                    ),
                )
            if attempt == FRAME_SEARCH_ATTEMPTS:
                break
            await page.wait_for_timeout(FRAME_SEARCH_RETRY_PAUSE_S * 1000)
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: {name!r} did not attach as a reference in "
                f"{FRAME_SEARCH_ATTEMPTS} attempts ({len(chips)} chip(s), expected "
                f"{expect_chips}); the picker offered: {', '.join(offered[:6]) or '<nothing>'}"
                " — if the asset IS listed above, the commit gesture is what failed, not "
                "the lookup: a cohort whose picker needs its own confirm (#792) is a "
                "different failure from a missing asset"
            ),
        )

    @staticmethod
    async def _open_frame_picker(page: Page, chip_label: str = "Start") -> Any:
        """Click the first remaining empty chip (Start, then End once Start is bound)
        and wait for the library picker's search box."""
        chip = page.locator(EMPTY_CHIP).first
        if not await chip.count():
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: no empty {chip_label} chip ({EMPTY_CHIP}) to bind the frame "
                    "on — is the Frames submode selected? (host=migrated)"
                ),
            )
        await chip.click(timeout=4000)
        picker = _picker_pane(page)
        try:
            await picker.locator(PICKER_SEARCH).first.wait_for(
                state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000)
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the frame picker ({PICKER}) did not open within "
                    f"{FRAME_PICKER_OPEN_S:.0f}s of clicking the {chip_label} chip (host=migrated)"
                ),
            ) from e
        return picker

    async def _pick_frame_by_name(
        self,
        page: Page,
        name: str,
        media_id: str,
        *,
        chip_label: str = "Start",
        expect_bound_chips: int = 1,
    ) -> None:
        """Empty chip → the library picker → search by display name → first option →
        then ``expect_bound_chips`` bound thumbnails must be visible (1 after Start,
        2 after End). An unbound chip is refused here: an empty Frames submit goes
        out as text-to-video (the labs #125 shape on this host)."""
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            picker = await self._open_frame_picker(page, chip_label)
            search = picker.locator(PICKER_SEARCH).first
            await search.click(timeout=4000)
            await page.keyboard.insert_text(name)
            # Containment, never `_exact` (#860): a picker tile is an icon node followed
            # by the file name, and a locator's text is the concatenation of both --
            # `imageshero-ab12cd34.png` for an asset named `hero-ab12cd34.png`. An anchored
            # match can therefore never hold on this cohort. The display name is
            # run-unique by construction (#792), so a substring is exact enough to be
            # unable to bind a stale copy of the same file.
            options = picker.locator(PICKER_OPTION).filter(has_text=name)
            try:
                await options.first.wait_for(
                    state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000)
                )
            except Exception as e:
                # What the picker DID list is the diagnostic: the display name an upload
                # gets is an observation per account, not a contract.
                listed = [
                    t.strip() for t in await picker.locator(PICKER_OPTION).all_text_contents()
                ]
                await page.keyboard.press("Escape")
                if attempt < FRAME_SEARCH_ATTEMPTS:
                    log.info("migrated.frame_search_miss", attempt=attempt, listed=len(listed))
                    await asyncio.sleep(FRAME_SEARCH_RETRY_PAUSE_S)
                    continue
                raise ReferenceNotFoundError(
                    detail=(
                        f"migrated host: the frame picker lists no asset named {name!r} after "
                        f"{FRAME_SEARCH_ATTEMPTS} searches of {FRAME_PICKER_OPEN_S:.0f}s (media "
                        f"{media_id}) — the tag after the stem is expected: gflow uploads "
                        "under a run-unique display name so the search cannot bind an "
                        "older copy of the same file (#792). The picker listed: "
                        f"{', '.join(repr(t) for t in listed[:8]) or 'nothing'}"
                    ),
                ) from e
            else:
                # Outside the except above on purpose: a click that fails here is not
                # "the picker never listed it", and must not be reported as one.
                await options.first.click(timeout=4000)
                break
        try:
            await _picker_pane(page).wait_for(
                state="hidden", timeout=int(FRAME_COMMIT_GRACE_S * 1000)
            )
        except PlaywrightTimeoutError:
            # Cohort split (#792): on some accounts the option click no longer commits and
            # the picker waits for its own confirm. Only a TIMEOUT lands here — a closed
            # page or a detached frame is a different failure and travels unchanged, or the
            # probe below would re-raise it from inside this handler as an unmapped
            # traceback (the #752 lesson, already learned one method over at the upload).
            #
            # Visibility, not count(): a detached pane's button still counts, and clicking
            # one waits out its own timeout on something that cannot be clicked.
            confirm = _picker_pane(page).locator(PICKER_CONFIRM).first
            had_confirm = await confirm.is_visible()
            if had_confirm:
                try:
                    await self._click(page, confirm, named=PICKER_CONFIRM, timeout=4000)
                except UiSelectorDriftError:
                    # A confirm that would not take the click is not yet the failure: the
                    # picker this cohort's click DID commit may simply be on its way out.
                    # Fall through to the long wait and let the picker have the last word.
                    log.info("migrated.frame_confirm_click_missed", issue_ref="#792")
                else:
                    log.info("migrated.frame_confirm_clicked", media_id=media_id, issue_ref="#792")
            try:
                await _picker_pane(page).wait_for(
                    state="hidden", timeout=int(FRAME_COMMIT_HIDDEN_S * 1000)
                )
            except Exception as e:
                # Say WHICH of the two it was: a picker that ignored its own confirm is
                # a different drift from one that never offered it, and reporting both
                # as "stayed open" is how the next cohort change reads as this one.
                why = (
                    "even after its confirm was clicked"
                    if had_confirm
                    else f"and carries no confirm ({PICKER_CONFIRM})"
                )
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the frame picker stayed open "
                        f"{FRAME_COMMIT_GRACE_S + FRAME_COMMIT_HIDDEN_S:.1f}s after picking "
                        f"{name!r} {why} (host=migrated)"
                    ),
                ) from e
        try:
            await (
                page.locator(BOUND_CHIP)
                .nth(expect_bound_chips - 1)
                .wait_for(state="visible", timeout=int(FRAME_THUMB_VISIBLE_S * 1000))
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the {chip_label} chip did not bind {name!r} — "
                    f"fewer than {expect_bound_chips} bound chip(s) within "
                    f"{FRAME_THUMB_VISIBLE_S:.0f}s of the pick; refusing to submit what "
                    "would go out as text-to-video (host=migrated)"
                ),
            ) from e
        log.info("migrated.frame_bound", media_id=media_id)

    # --- prompt + submit --------------------------------------------------------

    async def send_prompt(self, page: Page, prompt: str, *, append: bool = False) -> None:
        """Type the prompt. ``append=True`` keeps what is already in the composer — the
        mention chips an r2v run just attached — and leaves the caret where the last one
        put it, rather than clicking and moving it."""
        composer = page.locator(COMPOSER).first
        if not await composer.count():
            raise UiSelectorDriftError(
                detail=f"migrated host: composer ({COMPOSER}) not found (host=migrated)",
            )
        if not append:
            # _close_pane's docstring names THIS click as the one that surfaced a
            # stuck settings pane as a bare 5 s TimeoutError naming only the composer.
            await self._click(page, composer, named=COMPOSER, timeout=5000)
        # insert_text dispatches input events without key presses: a newline in the
        # prompt lands as text instead of an Enter that might submit early.
        await page.keyboard.insert_text(prompt)
        log.info("migrated.prompt_typed", chars=len(prompt))

    async def submit_and_observe(
        self,
        page: Page,
        *,
        poll_timeout_s: float,
        on_started: VideoStartedCallback | None,
        project_id: str | None,
        expect_media_id: str | None = None,
        expect_end_media_id: str | None = None,
        expect_reference_ids: tuple[str, ...] = (),
        submit_rpcs: tuple[str, ...] = SUBMIT_RPCS,
        submit_selector: str | None = None,
        request_check: Callable[[str, str], GFlowError | None] | None = None,
        adopt_ids: tuple[str, ...] = (),
        adopt_prompt: str | None = None,
    ) -> GenerationRecord:
        """Click submit, then read the page's own submit / ``jwpduf`` / ``as29s``
        replies until the record is terminal. Fires ``on_started`` as soon as the
        submit reply names the media id — before the poll, as the labs path does.

        ``submit_rpcs``, ``submit_selector`` and ``request_check`` let another surface
        reuse this observer (extend: ``fZytfe``, the editor's own submit button, and a
        verdict on every outgoing ``batchexecute`` request that fails the run at once).

        ``expect_media_id`` (i2v) and ``expect_reference_ids`` (r2v) inspect the
        submit *request* the app sends: its body must carry those ids and the
        matching model key, else the run is a :class:`WireFormatError` — the
        generation the user asked for is not the one Flow is billing. A bound end
        frame (``expect_end_media_id``) switches the submit to the interpolation
        rpc (``nprQif``), whose name travels in the ``f.req`` body rather than the
        URL; that body must carry the interpolation key and both frame ids.

        ``adopt_ids`` are media ids this run uploaded. When Chrome loses the submit reply
        body (#1112), the status poll whose record carries one of them is adopted as the
        submit; each upload is a run-unique copy (18 of 18 distinct reference ids in the
        2026-10-27 spike), so an older clip does not carry it.

        ``adopt_prompt`` does the same job when nothing was uploaded (t2v, one clip, #1141):
        see :class:`_ClipByPrompt`. A caller that passes neither keeps the immediate exit 9."""
        loop = asyncio.get_running_loop()
        submitted: asyncio.Future[GenerationRecord] = loop.create_future()
        route_error: asyncio.Future[GFlowError] = loop.create_future()
        # Body problems the observer saw; raised only if the route guard did not stop them.
        observed: list[GFlowError] = []
        # (request, rpcid) of each submit sent, to place a reply that names no rpcid.
        submit_requests: list[tuple[Any, str]] = []
        submit_key: dict[str, str] = {}
        clicked_at = 0.0
        by_prompt: _ClipByPrompt | None = None
        # Set when a candidate finishes: the listing does not name a clip still generating
        # (measured 2026-11-05, answered in 3 s without it, twice).
        candidate_seen: asyncio.Future[None] = loop.create_future()

        def on_request(request: Any) -> None:
            url = str(getattr(request, "url", ""))
            if "batchexecute" not in url or route_error.done():
                return
            # The URL `rpcids` param is the normal carrier and is tried first. The
            # body fallback exists because the interpolation submit has been reported
            # without that param; on the account measured 2026-09-17 it DID carry it
            # (`nprQif` in the URL, key `omni_flash_i2v_8s_first_last`), so treat the
            # body as a second carrier rather than the rule — cohorts differ here.
            body = request_body(request)
            rpcid = url_rpcid(url) or body_rpcid(body)
            # Record request rpcids too: at timeout the set shows both what went
            # out and what answered (issue #639).
            if rpcid is not None:
                seen_submit_rpcs.add(rpcid)
            if rpcid is None:
                return
            if request_check is not None:
                verdict = request_check(rpcid, body)
                if verdict is not None:
                    route_error.set_result(verdict)
                    return
            if rpcid in submit_rpcs:
                submit_requests.append((request, rpcid))
                # The prompt comes first in the body, and a prompt can read like a key.
                if key := MODEL_KEY.search(body.replace(adopt_prompt or "", "")):
                    submit_key["key"] = key.group(0)
                try:
                    problem = _submit_body_problem(
                        rpcid,
                        body,
                        expect_media_id=expect_media_id,
                        expect_end_media_id=expect_end_media_id,
                        expect_reference_ids=expect_reference_ids,
                    )
                except Exception:  # noqa: BLE001 - a listener must not raise; the route
                    return  # guard runs the same check and aborts the submit on failure
                # Recorded, not raised: this event fires before the route guard, which
                # aborts the same request unsent and names it so. A problem only the
                # observer saw means the route missed it and Flow got the request.
                if problem is not None:
                    observed.append(WireFormatError(detail=problem, route=f"batchexecute:{rpcid}"))
                return
            # An unwatched rpcid is RECORDED (`seen_submit_rpcs`, above) but never
            # adopted as our submit. Adopting by body content was tried and removed:
            # the predicate available here (`_i2v_body_problem`) requires `_i2v_` in
            # the key, which `veo_3_1_interpolation_lite` does not contain — so it
            # could never fire for the veo start+end case it was written for — while
            # for the omni `*_i2v_*_first_last` key it matched broadly enough that any
            # future rpc echoing composer state would be adopted, and a parse failure
            # on an adopted id calls `submitted.set_exception` (fatal) rather than
            # `continue`. That trades a named timeout for a hard failure on an
            # already-billed run. Both submit rpcids we have measured — `eb1hJf`
            # (start only) and `nprQif` (start+end, 2026-09-17) — are in SUBMIT_RPCS,
            # so nothing needs adopting; a third one should be measured and added by
            # name, not guessed at from a body.

        # ``terminal``: failed, or done WITH the signed URL. ``done_no_url``: the
        # first status-3 record that has no URL yet (a poll beats the result RPC).
        terminal: asyncio.Future[GenerationRecord] = loop.create_future()
        done_no_url: asyncio.Future[GenerationRecord] = loop.create_future()
        workflow: dict[str, str] = {}
        # The submit rpc whose reply body was lost, while a status poll may still name it.
        lost: dict[str, str] = {}

        def _settle(rec: GenerationRecord) -> None:
            if rec.is_failed or (rec.is_done and rec.video_url):
                if not terminal.done():
                    terminal.set_result(rec)
            elif rec.is_done and not done_no_url.done():
                done_no_url.set_result(rec)

        def _adopt(rid: str, payload: Any) -> None:
            carrying = generation_records_carrying(rid, payload, adopt_ids)
            matches = {r.workflow_id: r for r in carrying}
            if len(matches) > 1:
                ids = ", ".join(sorted(r.media_id for r in matches.values()))
                log.warning("migrated.video_recovery_ambiguous", rpc=rid, media_ids=ids)
                submitted.set_exception(
                    _outcome_unknown(
                        f"migrated host: the {lost['rpc']} submit reply body was lost and "
                        f"{len(matches)} clips carry this run's upload ({ids})"
                    )
                )
            elif matches:
                (rec,) = matches.values()
                _accept(rid, rec)

        def _accept(rid: str, rec: GenerationRecord) -> None:
            workflow["id"] = rec.workflow_id
            log.info(
                "migrated.video_adopted_from_listing"
                if rid == LISTING_RPC
                else "migrated.video_adopted_from_status",
                rpc=rid,
                workflow_id=rec.workflow_id,
                media_id=rec.media_id,
                status=rec.status,
            )
            submitted.set_result(rec)
            _settle(rec)

        def _note_poll(rid: str, payload: Any) -> None:
            assert by_prompt is not None  # noqa: S101 - set in the same branch as `lost`
            refusal = by_prompt.note_poll(rid, payload)
            if refusal is not None:
                submitted.set_exception(refusal)
            elif not candidate_seen.done() and any(
                not rec.is_running for rec in by_prompt.candidates.values()
            ):
                candidate_seen.set_result(None)

        def _decide_by_prompt(listing: Any | None) -> None:
            assert by_prompt is not None  # noqa: S101 - set in the same branch as `lost`
            try:
                _accept(LISTING_RPC, by_prompt.decide(listing))
            except TransportTimeoutError as refusal:
                submitted.set_exception(refusal)

        # Every batchexecute rpcid seen while waiting for the submit reply. A
        # start+end submit may answer on an rpcid this observer does not watch;
        # recording the set turns an unknown-reply timeout from a mystery into a
        # named fact (issue #639).
        seen_submit_rpcs: set[str] = set()

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            rpcid = url_rpcid(url) if "batchexecute" in url else None
            if rpcid is not None:
                seen_submit_rpcs.add(rpcid)
            if "batchexecute" not in url:
                return
            # `rpcid is None` falls through on purpose: the interpolation reply is a
            # bare `batchexecute` with no `rpcids` param, and `parse_frames` recovers
            # its id from the frame itself (measured 2026-09-17).
            if rpcid is not None and rpcid not in submit_rpcs and rpcid not in STATUS_RPCS:
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - an aborted/streamed body is not our frame
                # #1009/#1010: Chrome can drop a reply body. On the submit reply that
                # loses the workflow id, so no status reply can settle the run either.
                # nprQif answers on a bare URL: tie it to the submit by its request.
                sent = getattr(response, "request", None)
                sub = (
                    rpcid
                    if rpcid in submit_rpcs
                    else next((r for q, r in submit_requests if q is sent), None)
                )
                if sub is None or submitted.done():
                    return
                if adopt_prompt is not None and project_id and "key" in submit_key:
                    nonlocal by_prompt
                    by_prompt = _ClipByPrompt(
                        project_id, adopt_prompt, submit_key["key"], clicked_at
                    )
                if adopt_ids or by_prompt is not None:
                    lost["rpc"] = sub
                    log.warning(
                        "migrated.video_submit_reply_lost",
                        rpc=sub,
                        note="submit reply lost; following the clip in the project, no re-submit",
                    )
                else:
                    submitted.set_exception(
                        _outcome_unknown(
                            f"migrated host: the {sub} submit reply arrived but its "
                            "body was unreadable"
                        )
                    )
                return
            refusal = _submit_refusal(text, submit_rpcs)
            # An envelope arriving after an accepted submit is ignored: that run's outcome
            # is already decided, and it now belongs to the terminal wait.
            if refusal is not None and not submitted.done():
                submitted.set_exception(refusal)
                return
            for rid, payload in parse_frames(text):
                # Record the id the FRAME names, not just the URL's. The interpolation
                # reply arrives on a bare `batchexecute` with no `rpcids` param, so a
                # URL-only diagnostic reports "rpcs seen: none" for the very reply shape
                # whose absence it is trying to explain (#639).
                seen_submit_rpcs.add(rid)
                if rid in submit_rpcs and not submitted.done():
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
                elif rid in STATUS_RPCS and lost and not workflow and not submitted.done():
                    if by_prompt is not None:
                        _note_poll(rid, payload)
                    else:
                        _adopt(rid, payload)
                elif rid in STATUS_RPCS and workflow:
                    try:
                        rec = generation_record(rid, payload, workflow_id=workflow["id"])
                    except WireFormatError:
                        continue
                    log.info("migrated.status", rpc=rid, status=rec.status, bytes=rec.size_bytes)
                    _settle(rec)

        page.on("response", on_response)

        async def guard(route: Any, request: Any) -> None:
            """Abort a wrong submit BEFORE it leaves the browser (#1178): the observer
            above only sees a request already on its way to Flow, which bills it."""
            rpcid: str | None = None
            try:
                body = request_body(request)
                rpcid = url_rpcid(str(getattr(request, "url", ""))) or body_rpcid(body)
                problem = (
                    _submit_body_problem(
                        rpcid,
                        body,
                        expect_media_id=expect_media_id,
                        expect_end_media_id=expect_end_media_id,
                        expect_reference_ids=expect_reference_ids,
                    )
                    if rpcid is not None and rpcid in submit_rpcs
                    else None
                )
            except Exception as exc:  # noqa: BLE001 - an unsettled route hangs the page
                problem = (
                    f"migrated host: the {rpcid} submit could not be checked ({type(exc).__name__})"
                    if rpcid is not None and rpcid in submit_rpcs
                    else None
                )
            if problem is None:
                # fallback, not continue_: an outer guard (extend) must still see it.
                await route.fallback()
                return
            await route.abort()
            log.warning("migrated.submit_refused_before_send", rpc=rpcid)
            if not route_error.done():
                route_error.set_result(
                    WireFormatError(
                        detail=problem,
                        route=f"batchexecute:{rpcid}",
                        remediation_hint=ABORTED_BEFORE_SEND,
                    )
                )

        # Both are ALWAYS armed, t2v included: a stale End chip makes the app submit
        # `nprQif` on a run that asked for no end frame. The route guard is where refusing
        # is free (the request never leaves); the observer is the backstop for a request
        # the route never saw, whose verdict lands once the run settles or times out.
        # Gating on `expect_media_id` once left `_r2v_body_problem` unreached live.
        page.on("request", on_request)
        try:
            await page.route(BATCHEXECUTE_GLOB, guard)
            submit = (
                page.locator(submit_selector).first
                if submit_selector
                else page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            )
            submit_named = submit_selector or "arrow_forward"
            if not await submit.count():
                # A credit shortfall and a moved frontend look identical here: both are
                # "arrow_forward is gone". Ask which one BEFORE naming a culprit --
                # reporting a short balance as selector drift tells the user to file a
                # frontend bug that no code change can fix.
                await _raise_if_out_of_credits(page)
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: the submit button ({submit_named}) is missing "
                        "after the prompt was typed (host=migrated)"
                    ),
                )
            enable_deadline = time.monotonic() + SUBMIT_ENABLE_BUDGET_S
            while not await submit.is_enabled():
                if time.monotonic() >= enable_deadline:
                    # Same question as above, on the other way of giving up on submit.
                    await _raise_if_out_of_credits(page)
                    raise UiSelectorDriftError(
                        detail=(
                            f"migrated host: the submit button ({submit_named}) stayed disabled "
                            f"for {SUBMIT_ENABLE_BUDGET_S:.0f}s after the prompt was typed "
                            "(host=migrated)"
                        ),
                    )
                await asyncio.sleep(SUBMIT_ENABLE_POLL_S)
            deadline = time.monotonic() + poll_timeout_s
            clicked_at = time.time()
            # The credit-spending click: a bare timeout here leaves "did it submit?"
            # unanswerable, which is the worst place in this driver to lose attribution.
            await self._click(page, submit, named=SUBMIT_BUTTON, timeout=5000)
            log.info("migrated.submit_clicked")
            budget = min(SUBMIT_REPLY_BUDGET_S, poll_timeout_s)
            await asyncio.wait(
                {submitted, route_error, candidate_seen},
                timeout=budget,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if by_prompt is not None and by_prompt.candidates and not submitted.done():
                await asyncio.wait(
                    {submitted, route_error, candidate_seen},
                    timeout=max(0.0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            if lost and not submitted.done() and not route_error.done() and project_id:
                # No poll named it (their bodies can be lost too): the project listing,
                # read in a tab opened only now, after the upload's file chooser is done.
                listing = await self._read_listing(
                    page,
                    project_id,
                    budget_s=max(0.0, min(LISTING_READ_BUDGET_S, deadline - time.monotonic())),
                )
                if by_prompt is not None and not submitted.done():
                    _decide_by_prompt(listing)
                elif listing is not None and not submitted.done():
                    _adopt(LISTING_RPC, listing)
            if lost and not submitted.done() and not route_error.done():
                # The clip is being made; a status poll may still name it before the deadline.
                await asyncio.wait(
                    {submitted, route_error},
                    timeout=max(0.0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            if route_error.done():
                # The request is inspected before its reply lands, so a wrong body is
                # named as such and not as whatever the reply then says.
                raise route_error.result()
            if observed:
                raise observed[0]  # the route did not stop it: sent, and Flow bills it
            if not submitted.done() and lost:
                where = MIGRATED_PROJECT_URL.format(project_id=project_id) if project_id else "Flow"
                raise _outcome_unknown(
                    f"migrated host: the {lost['rpc']} submit reply arrived but its body was "
                    f"unreadable, and no status reply named a clip carrying this run's upload "
                    f"within {poll_timeout_s:.0f}s; it may still finish in {where}"
                )
            if not submitted.done():
                seen = ", ".join(sorted(seen_submit_rpcs)) or "none"
                raise _outcome_unknown(
                    f"migrated host: no {'/'.join(submit_rpcs)} reply within "
                    f"{budget:.0f}s of clicking submit (rpcs seen: {seen})"
                )
            first = submitted.result()
            started = VideoStarted(
                media_id=first.media_id,
                project_id=project_id or first.project_id,
                flow_operation_id=first.workflow_id,
                workflow_id=first.workflow_id,
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
            # A submit reply that failed to parse sets an exception on `submitted`;
            # when the body assertion raised first, nobody read it, and asyncio would
            # log "Future exception was never retrieved" at GC. Consume it.
            if submitted.done() and not submitted.cancelled():
                submitted.exception()
            page.remove_listener("response", on_response)
            page.remove_listener("request", on_request)
            try:
                await page.unroute(BATCHEXECUTE_GLOB, guard)
            except Exception as exc:  # noqa: BLE001 - must not mask the real outcome
                log.debug("migrated.submit_unroute_failed", error=type(exc).__name__)

    async def _read_listing(self, page: Page, project_id: str, *, budget_s: float) -> Any | None:
        """The project's ``Zzl0ze`` payload, read in a second tab; ``None`` if none came.

        A second tab, because the generating page must not navigate mid-generation, and
        its own first-load listing was not observed to answer (spike 2026-10-27)."""
        got: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        async def on_response(response: Any) -> None:
            if got.done() or LISTING_RPC not in str(getattr(response, "url", "")):
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - a lost body leaves the fallback empty
                return
            for rid, payload in parse_frames(text):
                if rid == LISTING_RPC and not got.done():
                    got.set_result(payload)

        end = time.monotonic() + budget_s
        tab: Page | None = None
        try:
            tab = await page.context.new_page()
            tab.on("response", on_response)
            await tab.goto(
                MIGRATED_PROJECT_URL.format(project_id=project_id),
                wait_until="domcontentloaded",
                timeout=budget_s * 1000,
            )
            return await asyncio.wait_for(got, max(0.0, end - time.monotonic()))
        # Playwright's TimeoutError is not the builtin one. Any failure here leaves the
        # caller's exit 9, which names the project; this runs after Flow has billed.
        except (TimeoutError, PlaywrightError) as exc:
            log.warning(
                "migrated.video_listing_unanswered", budget_s=budget_s, error=type(exc).__name__
            )
            return None
        finally:
            if tab is not None:
                with contextlib.suppress(PlaywrightError):
                    await tab.close()

    async def _verify_submit_target(self, page: Page, submit: Locator) -> None:
        """Prove the submit button is visible and receives pointer events."""
        try:
            if not await submit.is_visible():
                raise RuntimeError("image submit button is not visible")
            # The JS hit-test reads viewport coordinates; a below-the-fold submit
            # would fail it even though Playwright's click auto-scrolls. Bring the
            # button into the viewport the gate is about to measure.
            await submit.scroll_into_view_if_needed()
            box = await submit.bounding_box()
            if not isinstance(box, dict) or not all(
                isinstance(box.get(key), (int, float)) for key in ("x", "y", "width", "height")
            ):
                raise RuntimeError("image submit button has no usable bounding box")
            state: Any = await page.evaluate(
                """
                ({x, y}) => {
                    // #778's closed vocabulary: tag + framework class tokens. Never the
                    // id or the raw class list, which carry account identifiers (#890).
                    const describe = (element) => {
                        if (!element) return null;
                        const cls = [...element.classList]
                            .filter((c) => /^(cdk|mat|mdc|flow|glue)-/.test(c))
                            .slice(0, 3);
                        return element.tagName.toLowerCase() +
                            (cls.length ? '.' + cls.join('.') : '');
                    };
                    const submit = [...document.querySelectorAll('button')].find((button) =>
                        [...button.querySelectorAll('mat-icon')].some((icon) =>
                            (icon.textContent || '').trim() === 'arrow_forward'));
                    const top = document.elementFromPoint(x, y);
                    const topButton = top?.closest?.('button');
                    const target = Boolean(
                        submit &&
                        (top === submit || submit.contains(top) || topButton === submit)
                    );
                    return {
                        target,
                        top: describe(top),
                    };
                }
                """,
                {
                    "x": float(box["x"]) + float(box["width"]) / 2,
                    "y": float(box["y"]) + float(box["height"]) / 2,
                },
            )
        except UiSelectorDriftError:
            raise
        except Exception as error:
            detail = await self._ui_failure_detail(
                page,
                phase="pre_submit_button_gate",
                selector="button -> arrow_forward",
                detail=(
                    "migrated host: image submit button failed the visibility or pointer "
                    "target check (host=migrated)"
                ),
                error=error,
            )
            raise UiSelectorDriftError(detail=detail) from error
        state_dict: dict[str, Any] = cast(dict[str, Any], state) if isinstance(state, dict) else {}
        if not state_dict.get("target"):
            blocker = state_dict.get("top")
            detail = await self._ui_failure_detail(
                page,
                phase="pre_submit_button_gate",
                selector="button -> arrow_forward",
                detail=(
                    "migrated host: an unexpected element receives pointer events at the "
                    f"image submit target (blocker={blocker}) (host=migrated)"
                ),
                error="submit target is covered",
            )
            raise UiSelectorDriftError(detail=detail)

    async def _pre_submit_gate(self, page: Page) -> Any:
        """Prove the composer is clear and submit is present and hit-testable.

        Runs before any network observer arms. Enablement is waited on

        downstream by the submit path itself (with the out-of-credits
        interrogation); this gate refuses missing/covered targets first.
        """
        await self._dismiss_cookie_bar(page)
        blockers = self._blocking_overlays(page)
        remaining = await blockers.count()
        if remaining:
            detail = await self._ui_failure_detail(
                page,
                phase="pre_submit_overlay_gate",
                selector=VISIBLE_OVERLAY,
                detail=(
                    f"migrated host: {remaining} blocking overlay(s) remain before image "
                    "Generate (host=migrated)"
                ),
                error=f"{remaining} blocking overlay(s)",
            )
            raise UiSelectorDriftError(detail=detail)
        submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
        if not await submit.count():
            await _raise_if_out_of_credits(page)
            detail = await self._ui_failure_detail(
                page,
                phase="pre_submit_button_gate",
                selector="button -> arrow_forward",
                detail="migrated host: image submit button is missing (host=migrated)",
                error="submit button missing",
            )
            raise UiSelectorDriftError(detail=detail)
        await self._verify_submit_target(page, submit)
        return submit

    async def submit_images_and_observe(
        self,
        page: Page,
        request: GenerateImageRequest,
        *,
        reference_ids: tuple[str, ...] = (),
        entity_ids: tuple[str, ...] = (),
    ) -> list[GeneratedImage]:
        """Submit Image mode and decode the completed ``ogiZ0b`` reply.

        ``reference_ids`` (media) and ``entity_ids`` (characters) are the EXACT reference
        set the submit may carry: missing or extra ids abort it before Flow acts (#723).
        """
        # Complete all UI gates before registering network observers or clicking
        # Generate. A deterministic DOM failure therefore cannot spend a request or
        # enter the transport retry loop.
        submit = await self._pre_submit_gate(page)
        loop = asyncio.get_running_loop()
        result: asyncio.Future[list[GeneratedImage]] = loop.create_future()
        route_error: asyncio.Future[WireFormatError] = loop.create_future()
        body_lost: asyncio.Future[None] = loop.create_future()
        grid = await _grid_images(page)
        # References and characters already exist; a late-listed upload is not output.
        baseline = None if grid is None else {*grid, *reference_ids, *entity_ids}

        def on_request(raw_request: Any) -> None:
            url = str(getattr(raw_request, "url", ""))
            if url_rpcid(url) != IMAGE_SUBMIT_RPC or route_error.done():
                return
            problem = _image_body_problem(
                request_body(raw_request), reference_ids, request.model, entity_ids
            )
            if problem is not None:
                route_error.set_result(
                    WireFormatError(detail=problem, route=f"batchexecute:{IMAGE_SUBMIT_RPC}")
                )

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if url_rpcid(url) != IMAGE_SUBMIT_RPC or result.done() or body_lost.done():
                return
            status = int(getattr(response, "status", 0) or 0)
            if status != 200:
                result.set_exception(
                    WireFormatError(
                        detail=f"migrated image submit answered HTTP {status}",
                        status=status,
                        route=f"batchexecute:{IMAGE_SUBMIT_RPC}",
                    )
                )
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - a raw Playwright error here was retried (#1010)
                # #1075: the image was made; read it off the grid instead of the reply.
                body_lost.set_result(None)
                return
            try:
                refusal = _submit_refusal(text, (IMAGE_SUBMIT_RPC,))
                if refusal is not None:
                    raise refusal
                parsed = parse_frames(text)
                records = [
                    record
                    for rpcid, payload in parsed
                    if rpcid == IMAGE_SUBMIT_RPC
                    for record in image_records(rpcid, payload)
                ]
                if not records:
                    log.warning(
                        "migrated.image_submit_unparsed_reply",
                        rpcid=IMAGE_SUBMIT_RPC,
                        text_len=len(text),
                        parsed_count=len(parsed),
                        parsed_rpcids=[p[0] for p in parsed],
                    )
                    raise WireFormatError(
                        detail="migrated image submit returned no ogiZ0b frame",
                        route=f"batchexecute:{IMAGE_SUBMIT_RPC}",
                    )
                images = [
                    GeneratedImage(
                        media_name=record.media_id,
                        workflow_id=record.workflow_id,
                        seed=record.seed,
                        prompt=record.prompt,
                        # #789: the ogiZ0b reply carries no model field. Echoing
                        # the request back would report an attribution this host
                        # never confirmed — and with a hidden model picker (#788)
                        # the selected model can differ from the requested one,
                        # making the echo actively wrong on the single field a
                        # user would check to find out.
                        model_name_type=None,
                        aspect_ratio=request.aspect.value,
                        fife_url=record.image_url,
                        dimensions=record.dimensions,
                        display_name=record.display_name,
                    )
                    for record in records
                ]
            except Exception as exc:  # noqa: BLE001 - delivered through the waiting future
                result.set_exception(exc)
                return
            result.set_result(images)

        def is_image_submit(url: str) -> bool:
            return url_rpcid(url) == IMAGE_SUBMIT_RPC

        async def guard(route: Any, raw_request: Any) -> None:
            problem = await _guard_image_submit(
                route, raw_request, reference_ids, request.model, entity_ids
            )
            found = _image_body_references(request_body(raw_request))
            log.info(
                "migrated.image_submit_guarded",
                outcome="aborted" if problem else "passed",
                references=len(reference_ids),
                characters=len(entity_ids),
                # What the body actually carried, per slot (#723: extras rode slot 2).
                body_media=None if found is None else len(found[0]),
                body_characters=None if found is None else len(found[1]),
            )
            if problem is not None and not route_error.done():
                route_error.set_result(
                    WireFormatError(detail=problem, route=f"batchexecute:{IMAGE_SUBMIT_RPC}")
                )

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            if reference_ids or entity_ids:
                await page.route(is_image_submit, guard)
            enable_deadline = time.monotonic() + SUBMIT_ENABLE_BUDGET_S
            while not await submit.is_enabled():
                if time.monotonic() >= enable_deadline:
                    await _raise_if_out_of_credits(page)
                    raise UiSelectorDriftError(
                        detail="migrated host: image submit stayed disabled (host=migrated)"
                    )
                await asyncio.sleep(SUBMIT_ENABLE_POLL_S)
            await self._click(page, submit, named=SUBMIT_BUTTON, timeout=5000)
            done, _ = await asyncio.wait(
                {result, route_error, body_lost},
                timeout=IMAGE_REPLY_BUDGET_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if route_error.done():
                raise route_error.result()
            if body_lost.done():
                return await _recover_images(page, request, baseline)
            if result not in done:
                raise _outcome_unknown(
                    f"migrated host: no {IMAGE_SUBMIT_RPC} image result within "
                    f"{IMAGE_REPLY_BUDGET_S:.0f}s of clicking submit"
                )
            return result.result()
        finally:
            # Consume the future's exception even on the paths that never read it
            # (a route error raised first, a timeout): otherwise asyncio logs
            # "exception was never retrieved" at GC, in a process that has already
            # reported a different, correct error. Not a no-op — do not delete.
            if result.done() and not result.cancelled():
                result.exception()
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
            if reference_ids or entity_ids:
                try:
                    await page.unroute(is_image_submit, guard)
                except Exception as exc:  # noqa: BLE001 - must not mask the real outcome
                    log.debug("migrated.image_submit_unroute_failed", error=type(exc).__name__)

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
            raise _outcome_unknown(
                f"migrated host: generation {workflow_id} was not terminal within the poll timeout"
            )
        grace = min(RESULT_URL_GRACE_S, max(deadline - time.monotonic(), 0.01))
        try:
            return await asyncio.wait_for(terminal, timeout=grace)
        except TimeoutError:
            log.info("migrated.result_url_not_observed", grace_s=grace)
            return done_no_url.result()

    # --- download ---------------------------------------------------------------


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
