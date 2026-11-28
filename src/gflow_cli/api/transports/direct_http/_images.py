# pyright: reportPrivateUsage=false
"""Direct image generation (``ogiZ0b``) and image upload (``maseQ``) on flow.google.com."""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

from gflow_cli.api.generation_route import IMAGE_WIRE_MODELS
from gflow_cli.api.image import ImageRef, Model
from gflow_cli.api.transports._mc_common import _first_uuid
from gflow_cli.api.transports._mc_submit_observe import ogiz0b_reply_images
from gflow_cli.api.transports.direct_http._gateway import (
    _client,
    _DirectFallbackError,
    _post_request,
    _send_once,
)
from gflow_cli.api.transports.direct_http._mint import _mint
from gflow_cli.api.transports.direct_http._session import _session_headers, _shell
from gflow_cli.api.transports.frame_builder import build_maseq_body, build_ogiz0b_body
from gflow_cli.api.transports.migrated_character import read_project
from gflow_cli.errors import WireFormatError
from gflow_cli.paths import extension_from_magic

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.image import GenerateImageRequest

log = structlog.get_logger(__name__)

# The model name an ogiZ0b body carries on flow.google.com. BELUGA is what the page sends
# for "Nano Banana 2.1", which replaced NARWHAL in Flow's catalogue (#958); HARBOR_SEAL and
# GEM_PIX_2 were generated live on direct ogiZ0b (#1549). Every other model routes to the UI
# transport until its wire name is measured. One table, shared with the route predicate.
_WIRE_MODEL: dict[Model, str] = IMAGE_WIRE_MODELS

_CHARACTER_READ_S = 15.0  # a bare @Name's project read; past it, the UI picker decides
_RECONCILE_HINT = (
    "Flow may have accepted this image request before the error, so gflow did not resend "
    "it. Check the project in Flow for the new image before running the command again."
)
_UPLOAD_RECONCILE_HINT = (
    "Flow may have stored this upload before the error, so gflow did not resend it. Check "
    "the project in Flow for the uploaded image before running the command again."
)
_SUBMIT = ("ogiZ0b", "IMAGE_GENERATION", "image submit", _RECONCILE_HINT)
_UPLOAD = ("maseQ", "UPLOAD_IMAGE", "image upload", _UPLOAD_RECONCILE_HINT)
# The MIME type sent with an upload comes from the bytes, not the file name: a JPEG named
# without an extension must not go out as image/png. The caller's validator already refused
# anything that is not one of these.
_UPLOAD_MIME = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


async def _resolve_character(
    page: Page, project_id: str, request: GenerateImageRequest
) -> tuple[str, str]:
    """Read path: the ``(entity_id, name)`` pair the frame carries, as the project holds it.

    Both forms are checked against Flow's own project reply (``read_project``, which also
    lands the page on the project for the mint), as the UI does when it attaches one:
    - an id (``--reference-entity``) must be one the project holds; the chip uses Flow's
      name for it. A stale or foreign id falls back before any POST.
    - a bare mention carries only the name: exactly one character must match it by the
      picker's rule (trimmed, case-insensitive), or the UI picker decides.
    The project read also measured the difference live: with it the request was accepted,
    without it Flow refused an id-carrying request twice (exit 10, 2026-11-23).
    """
    names = request.reference_entity_names
    try:
        # Bounded: a slow read must fall back, not hold the generate lock for minutes.
        # One outer deadline: read_project's own budget is per attempt, and it retries.
        project = await asyncio.wait_for(
            read_project(page, project_id, timeout_s=_CHARACTER_READ_S), _CHARACTER_READ_S
        )
    except Exception as exc:
        raise _DirectFallbackError(f"character_read:{type(exc).__name__}") from exc
    if request.reference_entities:
        wanted_id = request.reference_entities[0]
        matches = [c for c in project.characters if c.entity_id == wanted_id]
    else:  # the picker's own rule (_mc_mention.py): trimmed, case-insensitive, unique
        wanted = names[0].strip().casefold()
        matches = [c for c in project.characters if c.display_name.strip().casefold() == wanted]
    if len(matches) != 1:
        raise _DirectFallbackError(f"character_matches:{len(matches)}")
    log.info("direct.character_resolved", source="project_read")
    return (matches[0].entity_id, matches[0].display_name)


async def run_direct_images(
    *,
    page: Page,
    profile_dir: Path,
    project_id: str,
    request: GenerateImageRequest,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> list[GeneratedImage]:
    """One direct image generation: read the session, mint, build, send once, parse."""
    started = time.perf_counter()
    headers = await _session_headers(page, profile_dir)
    async with _client(http_transport) as client:
        tokens = await _shell(client, profile_dir, headers)
        characters = (
            (await _resolve_character(page, project_id, request),)
            if request.reference_entity_names
            else ()
        )
        mint_started = time.perf_counter()
        token = await _mint(page, project_id, _SUBMIT[1])
        mint_ms = int((time.perf_counter() - mint_started) * 1000)
        body = build_ogiz0b_body(
            prompt=request.prompt,
            model=_WIRE_MODEL[request.model],
            project_id=project_id,
            recaptcha_token=token,
            at_token=tokens.at,
            batch_id=str(uuid.uuid4()).upper(),
            entry_seeds=tuple(1 + secrets.randbelow(2**31 - 1) for _ in range(request.count)),
            aspect=request.aspect,
            references=tuple((ref.name, ref.display_name) for ref in request.refs),
            characters=characters,
        )
        log.info(
            "direct.frame_built",
            model=request.model.value,
            prompt_length=len(request.prompt),
            count=request.count,
        )
        post_started = time.perf_counter()
        response = await _send_once(
            client, _post_request("ogiZ0b", project_id, tokens, headers, body), profile_dir, _SUBMIT
        )
    log.info(
        "direct.image_post",
        status=response.status_code,
        mint_ms=mint_ms,
        post_ms=int((time.perf_counter() - post_started) * 1000),
        total_ms=int((time.perf_counter() - started) * 1000),
    )
    images = ogiz0b_reply_images(response.text, request)
    return [replace(img, transport="direct_http") for img in images]


async def run_direct_upload(
    *,
    page: Page,
    profile_dir: Path,
    project_id: str,
    path: Path,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> ImageRef:
    """One direct ``maseQ`` upload: read the session, mint ``UPLOAD_IMAGE``, send once.

    The caller has validated the file (size, image header). The display name gets a run-
    unique suffix, as the UI's own upload does, so a later chip names this image only.
    """
    started = time.perf_counter()
    headers = await _session_headers(page, profile_dir)
    async with _client(http_transport) as client:
        tokens = await _shell(client, profile_dir, headers)
        token = await _mint(page, project_id, _UPLOAD[1])
        image = await asyncio.to_thread(path.read_bytes)
        display_name = f"{path.stem}-{secrets.token_hex(4)}{path.suffix}"
        body = build_maseq_body(
            project_id=project_id,
            recaptcha_token=token,
            at_token=tokens.at,
            image_bytes=image,
            mime=_UPLOAD_MIME[extension_from_magic(image[:12]) or ".png"],
            display_name=display_name,
            request_ids=(str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()),
        )
        response = await _send_once(
            client, _post_request("maseQ", project_id, tokens, headers, body), profile_dir, _UPLOAD
        )
    media_id = _first_uuid(response.text)
    if media_id is None:
        raise WireFormatError(
            detail="direct maseQ upload returned no media id", route="batchexecute:maseQ"
        )
    log.info("direct.upload", total_ms=int((time.perf_counter() - started) * 1000))
    return ImageRef(name=media_id, display_name=display_name, in_project=True)
