"""OpenAI-compatible ``POST /v1/images/generations`` on ``gflow serve`` (#1365).

An adapter, not a second generation path: the route validates the OpenAI request, calls
``tools.gflow_generate_image(..., wait=True)`` and maps the reply. The tool keeps owning
profile resolution, the rate limiter, the ``_guarded`` funnel and the queue, so this door
spends exactly what the MCP door spends. ``build_app`` mounts it inside the daemon-token
middleware; failures are translated by :mod:`gflow_cli.mcp.openai_errors`.

Deliberate deviations from OpenAI's schema: ``model`` takes Flow's image models only (the
endpoint is a facade over Google Flow), ``b64_json`` is the default and the only
``response_format`` (gflow produces local files, and serving them is out of scope),
``size`` picks the nearest of Flow's five aspects, and ``stream`` is refused.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from gflow_cli.api.image import Model
from gflow_cli.mcp import openai_errors

log = structlog.get_logger()

PATH = "/v1/images/generations"
#: The request body cap, the same 4 MiB the MCP SDK applies to ``/mcp``.
MAX_BODY_BYTES = 4 * 1024 * 1024

#: Flow's image aspects (``api.image.Aspect``) as width / height.
_ASPECTS: dict[str, float] = {
    "1:1": 1.0,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "4:3": 4 / 3,
    "3:4": 3 / 4,
}
_SIZE_RE = re.compile(r"^([1-9]\d*)x([1-9]\d*)$")
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_COUNT = 4
_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_FLOW_MODELS = "nano2 (default), nano2-lite, nano-pro, image4"


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    model: str
    aspect: str
    count: int


class _RequestError(Exception):
    """A request refused before the tool runs; becomes OpenAI's error object."""

    def __init__(
        self,
        message: str,
        *,
        param: str | None = None,
        status: int = 400,
        code: str = "invalid_value",
        type_: str = "invalid_request_error",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.param = param
        self.status = status
        self.code = code
        self.type_ = type_
        self.headers = headers

    def response(self) -> JSONResponse:
        return openai_errors.error_response(
            self.status,
            self.message,
            type_=self.type_,
            code=self.code,
            param=self.param,
            headers=self.headers,
        )


def aspect_for_size(size: object) -> str:
    """Map an OpenAI ``size`` to the nearest Flow aspect; raise ``ValueError`` if unusable.

    ``None``/``"auto"`` is square, an aspect string passes through, ``WxH`` picks the aspect
    whose ratio is closest on a log scale (so 2:1 and 1:2 are equally far from 1:1).
    """
    if size is None or size == "auto":
        return "1:1"
    if not isinstance(size, str):
        raise ValueError(f"size must be a string, got {type(size).__name__}")
    if size in _ASPECTS:
        return size
    match = _SIZE_RE.match(size)
    if match is None:
        raise ValueError(f"size must be 'WIDTHxHEIGHT', 'auto' or one of {sorted(_ASPECTS)}")
    # log of each side, not of their quotient: a huge width overflows a float division.
    ratio = math.log(int(match[1])) - math.log(int(match[2]))
    return min(_ASPECTS, key=lambda a: abs(math.log(_ASPECTS[a]) - ratio))


def _field(fields: dict[str, Any], name: str, default: object) -> object:
    """``fields[name]``, with an explicit JSON ``null`` read as absent."""
    value = fields.get(name)
    return default if value is None else value


def _parse_model(fields: dict[str, Any]) -> str:
    model = _field(fields, "model", "nano2")
    try:
        if not isinstance(model, str):
            raise ValueError(f"model must be a string, got {type(model).__name__}")
        Model.from_cli(model)
    except ValueError as exc:
        raise _RequestError(
            f"{exc}. This endpoint is a facade over Google Flow and accepts Flow image "
            f"models only: {_FLOW_MODELS}.",
            param="model",
        ) from exc
    return model


def _parse_count(fields: dict[str, Any]) -> int:
    count = _field(fields, "n", 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= _MAX_COUNT:
        raise _RequestError(f"n must be an integer from 1 to {_MAX_COUNT}.", param="n")
    return count


def parse_request(body: object) -> ImageRequest:
    """Validate an OpenAI image request; raise ``_RequestError`` naming the offending param.

    Fields OpenAI has and Flow does not (``quality``, ``style``, ``user``, ``background``,
    ``output_format`` …) are ignored rather than refused, so stock SDK calls keep working.
    """
    if not isinstance(body, dict):
        raise _RequestError("Request body must be a JSON object.")
    fields = cast("dict[str, Any]", body)
    prompt = fields.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _RequestError("prompt is required and must be a non-empty string.", param="prompt")
    if _field(fields, "stream", False) is not False:
        raise _RequestError("Streaming is not supported; omit stream.", param="stream")
    if _field(fields, "response_format", "b64_json") != "b64_json":
        raise _RequestError(
            "Only response_format 'b64_json' is supported: gflow returns the image bytes "
            "and does not host files.",
            param="response_format",
        )
    count = _parse_count(fields)
    model = _parse_model(fields)
    try:
        aspect = aspect_for_size(_field(fields, "size", None))
    except ValueError as exc:
        raise _RequestError(str(exc), param="size") from exc
    return ImageRequest(prompt=prompt, model=model, aspect=aspect, count=count)


def _is_loopback(netloc: str) -> bool:
    try:
        return urlsplit(netloc if "//" in netloc else f"//{netloc}").hostname in _LOOPBACK_NAMES
    except ValueError:
        return False


def _check_headers(request: Request, *, check_origin: bool) -> None:
    """Refuse what a hostile web page or a rebinding DNS name could send to the daemon.

    Without a token, a page in the user's browser can POST here: a ``text/plain`` body needs
    no CORS preflight, so Content-Type and Origin are checked, and Host guards against DNS
    rebinding. With a token, the browser cannot attach the ``Authorization`` header.
    """
    if request.method != "POST":
        raise _RequestError(
            "Only POST is supported.",
            status=405,
            code="method_not_allowed",
            headers={"Allow": "POST"},
        )
    if check_origin and not _is_loopback(request.headers.get("host", "")):
        raise _RequestError("Invalid Host header.", status=421, code="invalid_host")
    content_type = request.headers.get("content-type", "").lower()
    if not content_type.startswith("application/json"):
        raise _RequestError(
            "Content-Type must be application/json.", status=415, code="unsupported_media_type"
        )
    origin = request.headers.get("origin")
    if check_origin and origin is not None and not _is_loopback(origin):
        raise _RequestError(
            "Cross-origin requests are refused when no daemon token is set.",
            status=403,
            code="invalid_origin",
            type_="permission_error",
        )


async def _read_json(request: Request) -> object:
    too_large = _RequestError(
        f"Request body exceeds {MAX_BODY_BYTES} bytes.", status=413, code="request_too_large"
    )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise too_large
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY_BYTES:
            raise too_large
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise _RequestError("Request body is not valid JSON.", code="invalid_json") from exc


def _encode(files: list[Path]) -> list[dict[str, str]]:
    return [{"b64_json": base64.b64encode(f.read_bytes()).decode("ascii")} for f in files]


async def _images_response(result: dict[str, Any]) -> JSONResponse:
    files = [Path(str(f)) for f in cast("list[object]", result.get("files") or [])]
    extra = {"charged": "yes", "task_id": result.get("task_id")}
    if not files:
        return openai_errors.error_response(
            502,
            "Flow reported success but no image file was saved.",
            type_="server_error",
            code="no_output",
            extra=extra,
        )
    try:
        data = await asyncio.to_thread(_encode, files)
    except OSError as exc:
        log.warning("openai_api.images.unreadable", task_id=result.get("task_id"), error=str(exc))
        return openai_errors.error_response(
            500,
            f"The image was generated but its file could not be read: {exc}",
            type_="server_error",
            code="output_unreadable",
            extra=extra,
        )
    log.info("openai_api.images.completed", count=len(data))
    return JSONResponse({"created": int(time.time()), "data": data})


def _image_route(*, check_origin: bool) -> Route:
    async def images_generations(request: Request) -> JSONResponse:
        from gflow_cli.mcp import tools
        from gflow_cli.mcp.server import no_spend_active

        try:
            _check_headers(request, check_origin=check_origin)
            if no_spend_active():
                raise _RequestError(
                    "Image generation is disabled: GFLOW_MCP_NO_SPEND is set.",
                    status=403,
                    code="no_spend",
                    type_="permission_error",
                )
            req = parse_request(await _read_json(request))
        except _RequestError as exc:
            return exc.response()

        result = await tools.gflow_generate_image(
            prompt=req.prompt, model=req.model, aspect=req.aspect, count=req.count, wait=True
        )
        if result.get("status") != "completed":
            log.warning("openai_api.images.failed", status=result.get("status"))
            return openai_errors.tool_failure(result)
        return await _images_response(result)

    # Every verb reaches the handler, so a GET gets OpenAI's 405 body, not Starlette's.
    return Route(PATH, images_generations, methods=_ALL_METHODS)


def routes(*, token: str | None) -> list[Route]:
    """The OpenAI-compatible routes for ``build_app``; Host/Origin checked when no token."""
    return [_image_route(check_origin=token is None)]
