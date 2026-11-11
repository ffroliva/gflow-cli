"""Character surface: character entities and character image generation.

Split out of ``client.py`` (#1249); ``FlowApiClient`` composes it. Method
bodies are unchanged.
"""

from __future__ import annotations

import json
from typing import cast
from urllib.parse import quote

import structlog

from gflow_cli.api import routes
from gflow_cli.api._flow_media_edit import MediaEditSurface
from gflow_cli.api._flow_session import _APPLICATION_JSON, JsonObject  # pyright: ignore[reportPrivateUsage]
from gflow_cli.api._flow_workspace import _unwrap_trpc  # pyright: ignore[reportPrivateUsage]
from gflow_cli.api.character import Character, CharacterImageRequest, parse_characters
from gflow_cli.api.dto import (
    GeneratedImage,
)
from gflow_cli.errors import (
    ConfigurationError,
    WireFormatError,
)
from gflow_cli.paths import character_output_path
from gflow_cli.storage import AnyPath

logger = structlog.get_logger(__name__)


class CharacterSurface(MediaEditSurface):
    """Character surface: character entities and character image generation."""

    # --- Character entity API (issue #145) -----------------------------------

    async def create_entity(self, project_id: str) -> str:
        """Mint a fresh CHARACTER entity for *project_id*. Returns the new entityId.

        Maps to ``POST .../trpc/flow.createEntity``.  Session-cookie auth
        (``application/json`` content-type, same as ``createProject``).
        FREE — no reCAPTCHA, no credit.
        """
        body = {"json": {"projectId": project_id}}
        data = await self._post_json(
            routes.CREATE_ENTITY_URL,
            body,
            content_type=_APPLICATION_JSON,
            route_name="createEntity",
        )
        payload = _unwrap_trpc(data)
        entity_id = payload.get("entityId")
        if not entity_id:
            raise WireFormatError(
                detail=f"createEntity returned no entityId; keys={sorted(payload)}",
                route="createEntity",
            )
        logger.debug("character.entity_created", project_id=project_id, entity_id=entity_id)
        return str(entity_id)

    async def list_characters(self, project_id: str) -> list[Character]:
        """Return all CHARACTER entities in *project_id*.

        Maps to ``GET .../trpc/flow.projectInitialData?input=…``.
        Session-cookie auth.  FREE — no reCAPTCHA, no credit.
        """
        trpc_input = json.dumps({"json": {"projectId": project_id}}, separators=(",", ":"))
        url = f"{routes.PROJECT_INITIAL_DATA_URL}?input={quote(trpc_input, safe='')}"
        data = await self._get_json(url, route_name="projectInitialData")
        chars = parse_characters(_unwrap_trpc(data))
        logger.debug("character.list_fetched", project_id=project_id, count=len(chars))
        return chars

    async def get_character(
        self,
        project_id: str,
        *,
        entity_id: str | None = None,
        name: str | None = None,
    ) -> Character:
        """Fetch a single :class:`Character` by ``entity_id`` or ``name``.

        Raises :class:`~gflow_cli.errors.ConfigurationError` when:
        * no character matches (``entity_id`` / ``name`` not found), or
        * multiple characters share the same ``name`` (ambiguous).

        Exactly one of ``entity_id`` or ``name`` must be supplied.

        Note: ``name`` matching is case-sensitive (exact ``display_name`` equality)
        in Phase 1.
        """
        if entity_id is None and name is None:
            msg = "Provide either entity_id or name"
            raise ValueError(msg)
        chars = await self.list_characters(project_id)
        if entity_id is not None:
            match = [c for c in chars if c.entity_id == entity_id]
        else:
            match = [c for c in chars if c.display_name == name]
        if not match:
            lookup = entity_id if entity_id is not None else repr(name)
            raise ConfigurationError(
                detail=f"character not found: {lookup}",
                route="projectInitialData",
                remediation_hint="Run `gflow character list` to see available characters.",
            )
        if len(match) > 1:
            ids = ", ".join(c.entity_id for c in match)
            raise ConfigurationError(
                detail=f"ambiguous character name {name!r} matches multiple entities: {ids}",
                route="projectInitialData",
                remediation_hint="Use --entity-id to select one character unambiguously.",
            )
        return match[0]

    async def patch_entity(
        self,
        *,
        project_id: str,
        entity_id: str,
        display_name: str,
        workflow_ids: list[str],
        voice: str | None = None,
        personality: str | None = None,
    ) -> None:
        """Update a CHARACTER entity's display name, image references, and optional
        voice/personality fields via Bearer PATCH to ``flow/entities``.

        Maps to ``PATCH .../flow/entities``.  Bearer auth (aisandbox).
        FREE — no reCAPTCHA, no credit.

        Only the fields supplied are written; absent optional fields are omitted
        from both the request body and the ``updateMask``.
        """
        character_info: JsonObject = {
            "imageReferences": [{"workflowId": w} for w in workflow_ids],
        }
        update_mask_parts = [
            "entityInfo.displayName",
            "entityInfo.characterInfo.imageReferences",
        ]

        if personality is not None:
            character_info["personalityNotes"] = personality
            update_mask_parts.append("entityInfo.characterInfo.personalityNotes")

        if voice is not None:
            character_info["audioReferences"] = [{"presetVoiceId": voice}]
            update_mask_parts.append("entityInfo.characterInfo.audioReferences")

        entity_info: JsonObject = {
            "displayName": display_name,
            "characterInfo": character_info,
        }
        body: JsonObject = {
            "entity": {
                "projectId": project_id,
                "entityId": entity_id,
                "entityInfo": entity_info,
            },
            "updateMask": ",".join(update_mask_parts),
        }
        await self._patch_json(
            routes.FLOW_ENTITIES_URL,
            body,
            route_name="patchEntity",
        )
        logger.debug(
            "character.entity_patched",
            project_id=project_id,
            entity_id=entity_id,
            workflow_count=len(workflow_ids),
        )

    async def delete_characters(self, project_id: str, entity_ids: list[str]) -> None:
        """Delete one or more CHARACTER entities from *project_id*.

        Maps to ``POST .../v1/flow:batchDeleteAssets`` with body
        ``{"projectId": …, "entityIds": [...]}``.  Bearer auth (aisandbox).
        FREE — no reCAPTCHA, no credit.  Reverse-engineered from the editor's
        "Excluir personagem" button (scripts/dev/spike_char_delete.py).
        """
        if not entity_ids:
            msg = "entity_ids must be non-empty"
            raise ValueError(msg)
        body = {"projectId": project_id, "entityIds": list(entity_ids)}
        await self._post_json(
            routes.BATCH_DELETE_ASSETS_URL,
            body,
            route_name="batchDeleteAssets",
        )
        logger.debug(
            "character.entities_deleted",
            project_id=project_id,
            count=len(entity_ids),
        )

    async def generate_character_image(
        self,
        *,
        project_id: str,
        entity_id: str,
        req: CharacterImageRequest,
        image_reference_index: int = 0,
        locale: str | None = None,
        format_prompt: bool = False,
    ) -> tuple[str, str, AnyPath | None]:
        """Incident/typed-error boundary for the character generation path —
        the same wrap+capture the generate_* methods get (a UI-driven path can
        equally hit WAF/wire-format failures or a closed target)."""
        try:
            return await self._generate_character_image_impl(
                project_id=project_id,
                entity_id=entity_id,
                req=req,
                image_reference_index=image_reference_index,
                locale=locale,
                format_prompt=format_prompt,
            )
        except Exception as e:
            await self._raise_with_incident(e, phase="character_generation")

    async def _workflow_primary_media_id(self, project_id: str, workflow_id: str) -> str | None:
        """Flow's own ``metadata.primaryMediaId`` for *workflow_id*, or ``None``.

        Read from the project listing rather than inferred, because inferring it
        is what corrupted a body workflow: the entity exposes one thumbnail id,
        and reusing it across slots wrote the portrait's media onto the body.
        Best-effort — a listing fault leaves the caller to fall back.
        """
        try:
            listing = await self.fetch_project_listing(project_id)
        except Exception:  # noqa: BLE001 - diagnostic read, never fatal
            logger.warning("character.workflow_media_lookup_failed", exc_info=True)
            return None
        payload = _unwrap_trpc(listing)
        contents = payload.get("projectContents")
        if not isinstance(contents, dict):
            return None
        raw_workflows = cast("JsonObject", contents).get("workflows")
        if not isinstance(raw_workflows, list):
            return None
        workflows = cast("list[object]", raw_workflows)
        for entry in workflows:
            if not isinstance(entry, dict):
                continue
            record = cast("JsonObject", entry)
            if record.get("name") != workflow_id:
                continue
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                continue
            media = cast("JsonObject", metadata).get("primaryMediaId")
            if isinstance(media, str) and media:
                return media
        return None

    async def _character_slot_from_entity(
        self,
        *,
        project_id: str,
        entity_id: str,
        image_reference_index: int,
    ) -> tuple[str, str, AnyPath | None] | None:
        """Read a generated slot's ids back off the entity, or ``None``.

        Returns ``(workflow_id, primary_media_id, local_path)`` in the shape
        :meth:`_generate_character_image_impl` returns, with ``local_path``
        always ``None`` — this path binds ids, it does not download a file.

        ``None`` means "the backend has nothing for this slot", which is the
        honest answer when a generation really did fail: the caller re-raises
        the original timeout rather than inventing a success. A fault in the
        read-back itself is swallowed for the same reason — it is a rescue
        attempt, and it must never displace the failure the user came to see.
        """
        try:
            character = await self.get_character(project_id, entity_id=entity_id)
        except Exception:  # noqa: BLE001 - a rescue must not mask the real error
            logger.warning(
                "character.result_read_back_failed",
                entity_id=entity_id,
                exc_info=True,
            )
            return None
        workflow_ids = tuple(character.workflow_ids or ())
        if len(workflow_ids) <= image_reference_index:
            logger.info(
                "character.result_read_back_empty",
                entity_id=entity_id,
                bound_workflows=len(workflow_ids),
                image_reference_index=image_reference_index,
            )
            return None
        workflow_id = workflow_ids[image_reference_index]
        # Each workflow carries its OWN primaryMediaId; the entity carries only the
        # thumbnail. Handing the thumbnail to every slot made the body slot claim
        # the face's media, and the saga's commit_workflow then PATCHed that id
        # onto the body workflow — corrupting it (observed live 2026-09-06).
        # Reading Flow's value instead makes that commit a no-op.
        media_id = await self._workflow_primary_media_id(project_id, workflow_id) or ""
        if not media_id and image_reference_index == 0:
            # Slot 0 IS the thumbnail by definition, so it is a safe last resort.
            media_id = character.thumbnail_media_id or ""

        # The ids are the character; the file is a convenience. Download it so
        # `--output` and `image_paths` behave the same on both hosts, but never
        # let a CDN hiccup cost the user a generation they already spent quota
        # on — a failed fetch degrades to `None`, it does not raise.
        #
        # `media.getMediaUrlRedirect` is a labs route, and migrated_composer
        # records it 404-ing for a migrated media id. That was measured on a
        # VIDEO id and does not generalise: probed live 2026-09-06 against this
        # path's own portrait id, it answered HTTP 200 with 696 767 bytes of
        # JFIF. Re-probe before assuming either way for a new media kind.
        local_path: AnyPath | None = None
        if media_id:
            try:
                local_path = await self.download(
                    media_id,
                    character_output_path(
                        self.settings.output_dir,
                        entity_id=entity_id,
                        slot=image_reference_index,
                    ),
                )
            except Exception:  # noqa: BLE001 - a missing file never loses the ids
                logger.warning(
                    "character.result_read_back_download_failed",
                    entity_id=entity_id,
                    slot=image_reference_index,
                    exc_info=True,
                )
        return workflow_id, media_id, local_path

    async def _generate_character_image_impl(
        self,
        *,
        project_id: str,
        entity_id: str,
        req: CharacterImageRequest,
        image_reference_index: int = 0,
        locale: str | None = None,
        format_prompt: bool = False,
    ) -> tuple[str, str, AnyPath | None]:
        """Generate a character reference image via the UI transport and return
        ``(workflow_id, primary_media_id, local_path)``.

        All generation is UI-driven (Option B passive capture) — this method
        NEVER posts directly to a generation REST endpoint.  The transport's
        ``generate_character_images`` is the only call that may trigger network
        I/O.

        The generated image is downloaded INSIDE this client boundary using the
        signed ``fifeUrl`` carried by the captured response.  That signed URL is
        used ONLY for the download and is NEVER returned to the caller or logged
        in cleartext — the saga/recorder/DB only ever see the local file path and
        stable ids (scenario #16).  When the captured response carries no
        downloadable image, ``local_path`` is ``None`` (a warning is logged) and
        the method still returns the ids so the saga can proceed.

        Args:
            project_id: Flow project that owns the character entity.
            entity_id: The CHARACTER entity whose editor will be driven.
            req: :class:`~gflow_cli.api.character.CharacterImageRequest` DTO
                (prompt, aspect, model, face_media_id, …).
            image_reference_index: 0-based slot index for the character image
                reference (0 = face/first slot).
            locale: BCP-47 locale forwarded to the UI transport so Flow renders
                in the correct language.  Defaults to ``"en-US"``.
            format_prompt: Whether to click Flow's prompt-format button before
                submitting (rewrites the prompt into Flow's character shape).

        Returns:
            ``(workflow_id, primary_media_id, local_path)`` — the two stable ids
            extracted from the first returned workflow plus the LOCAL path the
            generated image was downloaded to (``None`` if it could not be
            downloaded).  No value is a signed URL.

        Raises:
            RuntimeError: transport is ``None`` (client not entered / not set up).
            :class:`~gflow_cli.errors.WireFormatError`: the returned workflow's
                ``parentEntityId`` does not match ``entity_id`` (foreign workflow
                guard, scenario #5), or no workflows were returned.
        """
        if self.transport is None:
            msg = (
                "FlowApiClient.transport is None — call generate_character_image "
                "inside 'async with client' with a Chrome-strategy profile"
            )
            raise RuntimeError(msg)

        try:
            _raw = await self.transport.generate_character_images(  # type: ignore[attr-defined]
                project_id=project_id,
                entity_id=entity_id,
                request=req,
                image_reference_index=image_reference_index,
                # #580: caller override wins; otherwise the ACCOUNT's own locale.
                # Never "en-US" — a wrong segment bounces the character route, which
                # is how #395 presented.
                locale=locale if locale is not None else self._account_locale,
                format_prompt=format_prompt,
            )
        except TimeoutError:
            # The passive capture listens for `flowMedia:batchGenerateImages`,
            # which is the LABS wire. flow.google.com generates the portrait
            # over its own `batchexecute` (rpcid ogiZ0b) and never calls it, so
            # the listener times out on work that SUCCEEDED — measured live
            # 2026-09-06 on ci-probe, where the entity carried a workflow id and
            # a thumbnail media id the moment the timeout fired.
            #
            # Ask the backend instead of decoding an undocumented envelope. The
            # ids come off the entity itself, so the binding this method exists
            # to verify is true by construction — strictly stronger than the
            # labs path's self-reported parentEntityId.
            bound = await self._character_slot_from_entity(
                project_id=project_id,
                entity_id=entity_id,
                image_reference_index=image_reference_index,
            )
            if bound is None:
                raise
            logger.info(
                "character.result_read_back_from_entity",
                entity_id=entity_id,
                workflow_id=bound[0],
                image_reference_index=image_reference_index,
            )
            return bound
        _images, workflows = cast(
            "tuple[list[GeneratedImage], list[JsonObject]]",
            _raw,
        )

        if not workflows:
            raise WireFormatError(
                detail="generate_character_images returned no workflows",
                route="generateCharacterImage",
            )

        wf: JsonObject = workflows[0]
        parent: str | None = wf.get("parentEntityId")
        if parent != entity_id:
            # This guard is CORRECT and must stay strict: a missing/mismatched
            # parentEntityId means Flow accepted the generation but filed it as
            # an ordinary project image instead of binding it to the character
            # entity. Verified live 2026-07-27 — runs that tripped this left the
            # entity "Untitled Character" with a null thumbnail, while a bound
            # run the day before carried its thumbnail_media_id. Relaxing this
            # would report a hollow character as success.
            missing = "omitted it entirely" if parent is None else f"set it to {parent!r}"
            raise WireFormatError(
                detail=(
                    f"Flow did not bind this generation to character entity "
                    f"{entity_id!r} — the returned workflow {missing}. The image "
                    "was generated but filed as a plain project image, so the "
                    "character has no portrait. This is a Flow-side binding "
                    "failure, not a malformed response: retry, and if it "
                    "persists check whether Flow's character editor still opens "
                    "for this account."
                ),
                route="generateCharacterImage",
            )

        workflow_id: str = cast(str, wf["name"])
        media_id: str = cast(str, wf["metadata"]["primaryMediaId"])

        # ------------------------------------------------------------------
        # Download the generated image INSIDE the client boundary.
        # The signed fifeUrl lives on images[0]; it is used ONLY for the
        # download here and is NEVER returned to the saga or logged in
        # cleartext (scenario #16). The caller receives only the local path.
        # ------------------------------------------------------------------
        local_path: AnyPath | None = None
        image: GeneratedImage | None = _images[0] if _images else None
        if image is not None and image.fife_url:
            out_path = character_output_path(
                self.settings.output_dir,
                entity_id=entity_id,
                slot=image_reference_index,
            )
            local_path = await self.download_image(image, out_path)
        else:
            logger.warning(
                "character.image_no_download_url",
                entity_id=entity_id,
                workflow_id=workflow_id,
                slot=image_reference_index,
            )

        logger.info(
            "character.image_generated",
            entity_id=entity_id,
            workflow_id=workflow_id,
            slot=image_reference_index,
            saved=local_path is not None,
        )
        return workflow_id, media_id, local_path
