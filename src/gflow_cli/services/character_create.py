"""Saga: create a Flow Character (persist-before-spend, crash-recoverable).

The saga owns the 4-step character creation transaction:

  1. Entity creation  (free REST)
  2. Persist STARTED  (persist-before-spend gate)
  3. Face generation  (credit)
  4. Body generation  (credit, optional)
  5. PATCH entity     (free REST)
  6. Persist SUCCEEDED

If a crash occurs anywhere after step 2, the STARTED row in the database
contains the entity_id and any workflow/media ids already recorded.  On the
next invocation, :func:`character_create` detects the incomplete row, reuses
the entity_id, and skips any generation steps whose ids are already recorded.

Scenario coverage (mirrors the plan's test-scenario numbering):
  #3  recovery – resume finds entity_id, skips create_entity
  #4  recovery – resume skips already-recorded face slot
  #5  WireFormatError (parentEntityId mismatch) → record FAILED, re-raise
  #22 face then body are SEQUENTIAL (never gathered/parallelised)
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, cast

import structlog

from gflow_cli.api.character import CharacterCreateResult, CharacterImageRequest

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.data.recorder import OperationRecorder

log = structlog.get_logger(__name__)


async def character_create(
    client: FlowApiClient,
    recorder: OperationRecorder,
    *,
    profile_name: str,
    profile_dir: Path,
    project_id: str,
    name: str,
    face: CharacterImageRequest,
    body: CharacterImageRequest | None = None,
    voice: str | None = None,
    personality: str | None = None,
    locale: str | None = None,
    format_prompt: bool = False,
) -> CharacterCreateResult:
    """Orchestrate the persist-before-spend character creation saga.

    Parameters
    ----------
    client:
        A :class:`~gflow_cli.api.client.FlowApiClient` instance.
    recorder:
        An :class:`~gflow_cli.data.recorder.OperationRecorder` instance.
    profile_name:
        Name of the active gflow profile (forwarded to the recorder).
    profile_dir:
        Filesystem path of the active profile directory.
    project_id:
        The Flow project id to create the character in.
    name:
        Display name for the new character.
    face:
        Image-generation parameters for the face reference slot (slot 0).
    body:
        Image-generation parameters for the body reference slot (slot 1).
        ``None`` means a face-only character.
    voice:
        Optional preset voice id.
    personality:
        Optional personality notes (may be redacted by the recorder).
    locale:
        BCP-47 locale forwarded to ``generate_character_image``. ``None``
        (the default) means "use the ACCOUNT's own locale", resolved live
        from Flow — see #580. A hardcoded default silently routed every
        non-``en`` account to the wrong editor URL.
    format_prompt:
        Whether to click Flow's prompt-format button before submitting, which
        rewrites the prompt into Flow's character prompt-engineering shape.

    Returns
    -------
    CharacterCreateResult
    """
    repo = recorder.repository

    # ------------------------------------------------------------------
    # Step 1: resume check — find an incomplete prior attempt
    # ------------------------------------------------------------------
    prior = repo.find_incomplete_character(project_id, name)

    if prior is not None:
        entity_id: str = cast(str, prior["entity_id"])
        row_id: str = cast(str, prior["row_id"])
        prior_wf_ids: list[str] = cast(list[str], prior["workflow_ids"])
        prior_media_ids: list[str] = cast(list[str], prior["primary_media_ids"])
        log.info(
            "character_create.resuming",
            project_id=project_id,
            name=name,
            entity_id=entity_id,
            prior_workflow_count=len(prior_wf_ids),
        )
    else:
        # ------------------------------------------------------------------
        # Step 2: create entity
        # ------------------------------------------------------------------
        entity_id = await client.create_entity(project_id)  # type: ignore[attr-defined]
        log.info(
            "character_create.entity_created",
            project_id=project_id,
            entity_id=entity_id,
        )

        # ------------------------------------------------------------------
        # Step 3: persist STARTED (persist-before-spend gate)
        # ------------------------------------------------------------------
        row_id = recorder.record_character_started(
            profile_name=profile_name,
            profile_dir=profile_dir,
            project_id=project_id,
            entity_id=entity_id,
            name=name,
        )
        prior_wf_ids = []
        prior_media_ids = []
        log.info(
            "character_create.started_recorded",
            row_id=row_id,
            entity_id=entity_id,
        )
        log.info(
            "character.create_started",
            project_id=project_id,
            entity_id=entity_id,
        )

    # Mutable working lists (extended as each slot completes)
    workflow_ids: list[str] = list(prior_wf_ids)
    media_ids: list[str] = list(prior_media_ids)
    # Local on-disk path of each downloaded reference image, in slot order.
    # Slots reused from a recovered run start as None (their image may already
    # be on disk from the prior run; we do NOT re-generate/re-download it).
    image_paths: list[str | None] = [None for _ in prior_wf_ids]

    try:
        # ------------------------------------------------------------------
        # Step 4: face generation (slot 0) — skip if already recorded
        # ------------------------------------------------------------------
        face_done = len(workflow_ids) >= 1
        if not face_done:
            _face_result = cast(
                tuple[str, str, "str | Path | None"],
                await client.generate_character_image(  # type: ignore[attr-defined]
                    project_id=project_id,
                    entity_id=entity_id,
                    req=face,
                    image_reference_index=0,
                    locale=locale,
                    format_prompt=format_prompt,
                ),
            )
            wf0: str = _face_result[0]
            m0: str = _face_result[1]
            p0 = _face_result[2]
            await client.commit_workflow(wf0, project_id=project_id, primary_media_id=m0)  # type: ignore[attr-defined]
            workflow_ids.append(wf0)
            media_ids.append(m0)
            image_paths.append(str(p0) if p0 is not None else None)
            # Persist partial state immediately (crash-safe before body)
            recorder.record_character_partial(
                row_id=row_id,
                workflow_ids=list(workflow_ids),
                primary_media_ids=list(media_ids),
            )
            log.info(
                "character_create.face_done",
                workflow_id=wf0,
                media_id=m0,
            )
        else:
            log.info(
                "character_create.face_skipped_already_recorded",
                workflow_id=workflow_ids[0],
            )

        # ------------------------------------------------------------------
        # Step 5: body generation (slot 1) — sequential, only if requested
        # ------------------------------------------------------------------
        if body is not None:
            body_done = len(workflow_ids) >= 2
            if not body_done:
                # Inject face_media_id into the body request (scenario #22 sequencing)
                face_media_id = media_ids[0]
                body_req = dataclasses.replace(
                    body,
                    image_reference_index=1,
                    face_media_id=face_media_id,
                )
                _body_result = cast(
                    tuple[str, str, "str | Path | None"],
                    await client.generate_character_image(  # type: ignore[attr-defined]
                        project_id=project_id,
                        entity_id=entity_id,
                        req=body_req,
                        image_reference_index=1,
                        locale=locale,
                        format_prompt=format_prompt,
                    ),
                )
                wf1: str = _body_result[0]
                m1: str = _body_result[1]
                p1 = _body_result[2]
                await client.commit_workflow(wf1, project_id=project_id, primary_media_id=m1)  # type: ignore[attr-defined]
                workflow_ids.append(wf1)
                media_ids.append(m1)
                image_paths.append(str(p1) if p1 is not None else None)
                # Persist again with both slots
                recorder.record_character_partial(
                    row_id=row_id,
                    workflow_ids=list(workflow_ids),
                    primary_media_ids=list(media_ids),
                )
                log.info(
                    "character_create.body_done",
                    workflow_id=wf1,
                    media_id=m1,
                )
            else:
                log.info(
                    "character_create.body_skipped_already_recorded",
                    workflow_id=workflow_ids[1],
                )

        # ------------------------------------------------------------------
        # Step 6: PATCH entity (free REST — sets display_name + workflow_ids)
        # ------------------------------------------------------------------
        await client.patch_entity(  # type: ignore[attr-defined]
            project_id=project_id,
            entity_id=entity_id,
            display_name=name,
            workflow_ids=workflow_ids,
            voice=voice,
            personality=personality,
        )
        log.info(
            "character_create.entity_patched",
            entity_id=entity_id,
            workflow_count=len(workflow_ids),
        )

        # ------------------------------------------------------------------
        # Step 7: record SUCCEEDED
        # ------------------------------------------------------------------
        recorder.record_character_completed(
            row_id=row_id,
            workflow_ids=workflow_ids,
            primary_media_ids=media_ids,
            voice=voice,
            personality=personality,
            image_paths=image_paths,
        )
        log.info("character_create.completed", entity_id=entity_id, name=name)
        log.info(
            "character.create_completed",
            entity_id=entity_id,
            workflow_count=len(workflow_ids),
        )

    except Exception as exc:
        # Record FAILED while preserving any partial workflow/media ids already
        # accumulated.  The row is kept intact so the next invocation can
        # detect the STARTED row via find_incomplete_character and resume.
        # NOTE: find_incomplete_character queries for status='started', so we
        # do NOT flip to FAILED here — the row stays STARTED for recoverability.
        # We DO record the partial data though.
        recorder.record_character_partial(
            row_id=row_id,
            workflow_ids=list(workflow_ids),
            primary_media_ids=list(media_ids),
        )
        log.error(
            "character_create.failed",
            entity_id=entity_id,
            error=str(exc),
            exc_info=True,
        )
        raise

    return CharacterCreateResult(
        entity_id=entity_id,
        project_id=project_id,
        workflow_ids=tuple(workflow_ids),
        primary_media_ids=tuple(media_ids),
        name=name,
        voice=voice,
        image_paths=tuple(image_paths),
    )
