"""OpenAI-compatible ``POST /v1/images/generations`` on ``gflow serve`` (#1365).

An adapter, not a second generation path: the route validates the OpenAI request, calls
``tools.gflow_generate_image(..., wait=True)`` and maps the reply. The tool keeps owning
profile resolution, the rate limiter, the ``_guarded`` funnel and the queue, so this door
spends exactly what the MCP door spends. ``build_app`` mounts it inside the daemon-token
middleware.

Deliberate deviations from OpenAI's schema: ``b64_json`` is the default and the only
``response_format`` (gflow produces local files, and serving them is out of scope), ``size``
picks the nearest of Flow's five aspects, and ``model`` takes gflow's image aliases.
"""

from __future__ import annotations

import base64
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from gflow_cli.api.image import Model

log = structlog.get_logger()

PATH = "/v1/images/generations"

#: Flow's image aspects (``api.image.Aspect``) as width / height.
_ASPECTS: dict[str, float] = {
    "1:1": 1.0,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "4:3": 4 / 3,
    "3:4": 3 / 4,
}
_SIZE_RE = re.compile(r"^([1-9]\d*)x([1-9]\d*)$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
_MAX_COUNT = 4


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    model: str
    aspect: str
    count: int


class _BadRequestError(Exception):
    def __init__(self, param: str | None, message: str) -> None:
        super().__init__(message)
        self.param = param
        self.message = message


def _error(
    status: int, message: str, *, type_: str, code: str | None = None, param: str | None = None
) -> JSONResponse:
    body = {"error": {"message": message, "type": type_, "code": code, "param": param}}
    return JSONResponse(body, status_code=status)


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
    ratio = math.log(int(match[1]) / int(match[2]))
    return min(_ASPECTS, key=lambda a: abs(math.log(_ASPECTS[a]) - ratio))


def parse_request(body: object) -> ImageRequest:
    """Validate an OpenAI image request; raise ``_BadRequestError`` naming the offending param.

    Fields OpenAI has and Flow does not (``quality``, ``style``, ``user``, ``background``,
    ``output_format`` …) are ignored rather than refused, so stock SDK calls keep working.
    """
    if not isinstance(body, dict):
        raise _BadRequestError(None, "Request body must be a JSON object.")
    fields: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
    prompt = fields.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _BadRequestError("prompt", "prompt is required and must be a non-empty string.")
    response_format = fields.get("response_format", "b64_json")
    if response_format != "b64_json":
        raise _BadRequestError(
            "response_format",
            "Only response_format 'b64_json' is supported: gflow returns the image bytes "
            "and does not host files.",
        )
    count = fields.get("n", 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= _MAX_COUNT:
        raise _BadRequestError("n", f"n must be an integer from 1 to {_MAX_COUNT}.")
    model = fields.get("model") or "nano2"
    try:
        Model.from_cli(model if isinstance(model, str) else repr(model))
    except ValueError as exc:
        raise _BadRequestError("model", str(exc)) from exc
    try:
        aspect = aspect_for_size(fields.get("size"))
    except ValueError as exc:
        raise _BadRequestError("size", str(exc)) from exc
    return ImageRequest(prompt=prompt, model=model, aspect=aspect, count=count)


def _is_loopback(host_header: str) -> bool:
    if host_header.startswith("["):  # IPv6 literal: "[::1]:8000"
        host = host_header.split("]", 1)[0] + "]"
    else:
        host = host_header.rsplit(":", 1)[0]
    return host in _LOOPBACK_HOSTS


def _tool_failure(result: dict[str, Any]) -> JSONResponse:
    """OpenAI error body from the tool's RFC 9457 envelope, at the problem's own status."""
    error: object = result.get("error")
    if isinstance(error, dict):
        problem = cast("dict[str, Any]", error)
        message = str(problem.get("message") or problem.get("detail") or "Generation failed.")
    else:
        problem = {}
        message = str(error or "Generation failed.")
    if result.get("status") == "rate_limited":
        status = 429
    else:
        raw = problem.get("status")
        status = raw if isinstance(raw, int) and 400 <= raw <= 599 else 500  # noqa: PLR2004
    code = problem.get("type")
    return _error(
        status, message, type_="gflow_error", code=code if isinstance(code, str) else None
    )


def _image_route(*, check_host: bool) -> Route:
    async def images_generations(request: Request) -> JSONResponse:
        # Without a daemon token the SDK guards only /mcp against DNS rebinding; this
        # route is a plain Starlette one, so it checks Host itself (scenario 2).
        if check_host and not _is_loopback(request.headers.get("host", "")):
            return _error(421, "Invalid Host header.", type_="invalid_request_error")

        from gflow_cli.mcp import tools
        from gflow_cli.mcp.server import no_spend_active

        if no_spend_active():
            return _error(
                403,
                "Image generation is disabled: GFLOW_MCP_NO_SPEND is set.",
                type_="invalid_request_error",
                code="no_spend",
            )
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "Request body is not valid JSON.", type_="invalid_request_error")
        try:
            req = parse_request(body)
        except _BadRequestError as exc:
            return _error(400, exc.message, type_="invalid_request_error", param=exc.param)

        log.info(
            "openai_api.images.request",
            prompt=req.prompt[:80],
            model=req.model,
            aspect=req.aspect,
            count=req.count,
        )
        result = await tools.gflow_generate_image(
            prompt=req.prompt, model=req.model, aspect=req.aspect, count=req.count, wait=True
        )
        if result.get("status") != "completed":
            log.warning("openai_api.images.failed", status=result.get("status"))
            return _tool_failure(result)
        files = [Path(str(f)) for f in cast("list[object]", result.get("files") or [])]
        if not files:
            return _error(
                502, "Flow reported success but no image file was saved.", type_="gflow_error"
            )
        data = [{"b64_json": base64.b64encode(f.read_bytes()).decode("ascii")} for f in files]
        log.info("openai_api.images.completed", count=len(data))
        return JSONResponse({"created": int(time.time()), "data": data})

    return Route(PATH, images_generations, methods=["POST"])


def routes(*, token: str | None) -> list[Route]:
    """The OpenAI-compatible routes for ``build_app``; Host is checked when no token guards them."""
    return [_image_route(check_host=token is None)]
