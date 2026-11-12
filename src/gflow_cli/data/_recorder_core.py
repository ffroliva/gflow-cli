"""``OperationRecorder`` core: lifecycle, attribution and generation metadata (#1249 wave 2).

Moved out of ``recorder.py`` unchanged.
"""

# pyright: reportUnusedFunction=false
# The private helpers here are used by the sibling ``_recording_*`` parts, not only this module.

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gflow_cli.data.redaction import (
    PromptFields,
    PromptMode,
    prompt_fields,
    redact_error_detail,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.errors import GFlowError, MediaAttributionError
from gflow_cli.observability import exception_message_hash

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest
    from gflow_cli.tools.invocation import AppliedTool

    # Both generation requests carry the tool-provenance fields the recorder reads.
    _ToolableRequest = GenerateImageRequest | GenerateVideoRequest


def _new_id() -> str:
    return str(uuid.uuid4())


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _now_utc_iso() -> str:
    """UTC timestamp matching the format used elsewhere in the data layer."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _file_bytes(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _classify_failure(exc: BaseException) -> tuple[str, str | None]:
    """Map an exception to the persisted ``(error_type, error_detail)`` pair (#341).

    :class:`GFlowError`: ``error_type`` is the last segment of the per-class
    RFC 9457 ``problem_type`` URI (falling back to the class name for the base
    ``about:blank`` or a malformed URI); ``error_detail`` is the
    problem-details detail passed through :func:`redact_error_detail`.
    Anything else: class name plus ``sha256:<digest>`` of the message — never
    the message text (privacy rule shared with
    ``observability.emit_unhandled_event``, same digest for correlation).
    """
    if isinstance(exc, GFlowError):
        error_type = type(exc).__name__
        if exc.problem_type.startswith("http"):
            error_type = exc.problem_type.rsplit("/", 1)[-1] or error_type
        detail = exc.to_problem_details().get("detail", "")
        return error_type, redact_error_detail(detail) if detail else None
    return type(exc).__name__, f"sha256:{exception_message_hash(exc)}"


class RecorderCore:
    repository: DataRepository
    prompt_mode: PromptMode

    def __init__(self, repository: DataRepository, *, prompt_mode: PromptMode) -> None:
        self.repository = repository
        self.prompt_mode = prompt_mode
        self._owns_store = False

    def close(self) -> None:
        """Close the underlying store — but only when this recorder created it.

        A repository handed in via ``__init__`` (e.g. the worker daemon reusing
        its own long-lived ``DataStore``) is never owned here, so ``close()``
        on an injected recorder is a no-op; only :meth:`open` sets
        ``_owns_store``.
        """
        if self._owns_store:
            self.repository.store.close()

    def is_media_recorded(self, *, profile_name: str, flow_media_id: str) -> bool:
        """Return True if an asset with this ``flow_media_id`` already exists in
        local history for ``profile_name`` (issue #281 pre-download attribution
        guard). Delegates to ``repository.get_asset_by_flow_media_id`` — pure
        boolean convenience wrapper, no new query logic and no behaviour change
        to any existing method.
        """
        return self.repository.get_asset_by_flow_media_id(profile_name, flow_media_id) is not None

    def verify_media_attribution(
        self,
        *,
        profile_name: str,
        images: Sequence[GeneratedImage],
    ) -> None:
        """Pre-download attribution guard (issue #281): raise if the driver
        returned media that already exists in local history for this profile.

        Second defense layer after the agentic driver's own DOM-scrape ambiguity
        check (``agentic.py await_images``): even a transport that never hits that
        check can still hand back a ``flow_media_id`` already recorded for THIS
        profile, meaning it isn't new. Downloading and attributing it to the
        current generation would silently duplicate/misattribute history with a
        pre-existing asset (the 2026-07-10 production incident). Callers invoke
        this after generation returns and BEFORE downloading so nothing is
        fetched for an already-recorded id.

        Consolidated onto the recorder (issue #283) from three near-identical
        module-level copies previously duplicated across ``cli_image.py``,
        ``image_batch.py``, and ``worker/daemon.py`` — all three call sites
        already hold a recorder instance, so the guard belongs on the data
        layer rather than repeated per call site.

        Also checks for an INTRA-list duplicate: the classic transport can
        return the same ``media_name`` more than once for a single batch
        submission (the agentic transport cannot — its DOM-scrape ambiguity
        check in ``await_images`` already rules this out upstream). Two
        "different" images sharing one ``flow_media_id`` would otherwise both
        be attributed to the same asset row, silently losing one of them.
        This check runs BEFORE the local-history lookup since it is cheaper
        and needs no repository query.
        """
        seen: set[str] = set()
        intra_batch_duplicates: list[str] = []
        for img in images:
            if img.media_name in seen and img.media_name not in intra_batch_duplicates:
                intra_batch_duplicates.append(img.media_name)
            seen.add(img.media_name)
        if intra_batch_duplicates:
            msg = (
                "the driver returned the same media more than once in a single "
                "batch — wrong-media attribution (#281); nothing was downloaded: "
                f"{', '.join(intra_batch_duplicates)}"
            )
            raise MediaAttributionError(msg, route="data.verify_media_attribution")

        already_recorded = [
            img.media_name
            for img in images
            if self.is_media_recorded(profile_name=profile_name, flow_media_id=img.media_name)
        ]
        if already_recorded:
            msg = (
                "the driver returned media that already exists in local history — "
                "wrong-media attribution (#281); nothing was downloaded: "
                f"{', '.join(already_recorded)}"
            )
            raise MediaAttributionError(msg, route="data.verify_media_attribution")

    def _resolve_prompts(
        self,
        request: _ToolableRequest,
    ) -> tuple[PromptFields, str | None]:
        """Resolve the recorded prompt fields and the expansion-to-store.

        ``request.prompt`` is what was actually submitted to Flow. When
        ``request.original_prompt`` is set (i.e. a ``--tool`` rewrote the
        prompt), the user's *original* prompt is recorded as the operation
        prompt and the submitted prompt is persisted separately as
        ``expanded_prompt`` — withheld when ``history_prompts='redacted'``,
        exactly like the original prompt.

        Reading both off the request (rather than a separate ``original_prompt``
        kwarg) keeps the recorded original in lockstep with the submitted prompt
        — they can no longer drift apart at the call site (PR2 §8 / prior-review
        silent-misrecord hazard).
        """
        sent_prompt = request.prompt
        original_prompt = request.original_prompt
        recorded = original_prompt if original_prompt is not None else sent_prompt
        pf = prompt_fields(recorded, mode=self.prompt_mode)
        expanded = (
            sent_prompt if (original_prompt is not None and self.prompt_mode == "store") else None
        )
        return pf, expanded

    def _tool_metadata(self, tool: AppliedTool | None) -> dict[str, object] | None:
        """Build the redaction-aware ``metadata_json.tool`` payload for a
        generation operation, or ``None`` when no tool was applied.

        ``redact_metadata`` only redacts by key-name / sensitive-URL markers, so
        a free-text option (e.g. ``params.style``) would pass through verbatim.
        We therefore branch on :class:`PromptMode` here: in ``redacted`` mode we
        store only ``{name, version, params_hash, config_hash}`` — never the raw
        ``model``/``params`` — reusing :func:`prompt_fields`' sha256 for the
        params digest (council D7).
        """
        if tool is None:
            return None
        params = tool.params_dict()
        if self.prompt_mode == "redacted":
            params_blob = json.dumps(params, sort_keys=True)
            params_hash = prompt_fields(params_blob, mode="store").prompt_hash
            return {
                "name": tool.name,
                "version": tool.version,
                "params_hash": params_hash,
                "config_hash": tool.config_hash,
            }
        return {
            "name": tool.name,
            "version": tool.version,
            "model": tool.model,
            "params": params,
            "config_hash": tool.config_hash,
        }

    def _generation_metadata(
        self,
        request: _ToolableRequest,
    ) -> dict[str, object]:
        """Compose the full ``metadata_json`` payload for a generation operation.

        Built as ONE dict on purpose: :meth:`DataRepository.set_operation_metadata`
        replaces the whole column, so writing tool and entity provenance in two
        sequential calls would silently drop whichever went first.

        ``entity_ids`` / ``entity_names`` answer "which character produced this?"
        (issue #402) — without them a `--reference-entity` generation left no
        trace of the entity anywhere in the catalog, while `--ref` media were
        recorded in full. Order is preserved: it mirrors the attach order the
        transport sent. Both keys are stored verbatim even in ``redacted`` mode —
        they are Flow-side handles the user picked, not prompt text, and
        ``record_character_started`` already stores the same pair unredacted.
        """
        metadata: dict[str, object] = {}
        tool_meta = self._tool_metadata(request.tool)
        if tool_meta is not None:
            metadata["tool"] = tool_meta
        if request.reference_entities:
            metadata["entity_ids"] = list(request.reference_entities)
        if request.reference_entity_names:
            metadata["entity_names"] = list(request.reference_entity_names)
        # #913: a generation made from another image keeps that image's media id here
        # too, so the lineage survives even when the parent's asset row is missing (its
        # download failed, so there is no local file to record it with).
        refs = getattr(request, "refs", ())
        if refs:
            metadata["reference_media_ids"] = [ref.name for ref in refs]
        return metadata
