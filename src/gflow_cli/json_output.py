"""Machine-readable ``--json`` payload builders + emitter.

Pure module (no I/O beyond the single :func:`emit` ``click.echo``): every
builder takes already-constructed domain objects and returns a JSON-native
``dict``. Kept dependency-light (only :mod:`gflow_cli.errors` + typing) so it
can be imported from both ``_cli_helpers`` (error path) and the command
modules without an import cycle, and unit-tested without a browser/network.

The shapes here are the stable contract a programmatic caller (e.g. a worker
that shells out to ``gflow``) parses instead of scraping the Rich tables.
"""

from __future__ import annotations

import json
import traceback
from typing import TYPE_CHECKING, Any

from gflow_cli.errors import (
    EXIT_CODE_MAP,
    BrowserSessionClosedError,
    GFlowError,
    NetworkError,
    RateLimitError,
    TransportTimeoutError,
    WafRejectionError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.video import GenerateVideoRequest, VideoResult

# Transient failures the caller can retry without operator intervention — WAF
# bounce, rate-limit/quota, transport timeout, network blip, a dropped browser
# session. Everything else (auth/content-policy/config/security) is terminal:
# retrying the identical request will fail the same way. A worker keys its
# "retry vs absorb-and-stop" decision off this flag.
_RETRYABLE: tuple[type[GFlowError], ...] = (
    WafRejectionError,
    RateLimitError,
    TransportTimeoutError,
    NetworkError,
    BrowserSessionClosedError,
)


def emit(payload: dict[str, Any]) -> None:
    """Print *payload* as indented JSON on stdout (the only side effect here)."""
    import click

    click.echo(json.dumps(payload, indent=2))


def exit_code_for(exc: GFlowError) -> int:
    """Mirror ``_cli_helpers._exit_code_for`` — most-specific class wins.

    Public so command modules that emit their own ``--json`` payload (e.g.
    ``video chain`` on a :class:`~gflow_cli.errors.ChainPartialError`) can exit
    with the same mapped code the shared handler would have used, without
    re-raising through the handler (which would emit a second JSON document).
    """
    for cls, code in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            return code
    return 1


# Backwards-compatible private alias (kept for the existing error path call
# sites in this module).
_exit_code = exit_code_for


def error_payload(exc: GFlowError) -> dict[str, Any]:
    """``{status: fail, error: {...RFC 9457..., class, exit_code, retryable}}``.

    Reuses :meth:`GFlowError.to_problem_details` so the JSON error carries the
    same fields (title/detail/remediation_hint/type) the Rich path prints,
    plus the concrete class name, mapped process exit code, and the retryable
    flag the caller branches on.
    """
    return {
        "status": "fail",
        "error": {
            **exc.to_problem_details(),
            "class": type(exc).__name__,
            "exit_code": _exit_code(exc),
            "retryable": isinstance(exc, _RETRYABLE),
        },
    }


def unexpected_payload(debug: BaseException | None = None) -> dict[str, Any]:
    """Privacy-safe payload for a non-:class:`GFlowError`, by default.

    Mirrors ``_handle_unhandled_error``: never leaks the raw message or stack —
    only the fact that an unclassified error occurred — unless ``debug`` is
    passed (the caller has already confirmed GFLOW_CLI_DEBUG_TRACEBACK=1), in
    which case ``detail``/``traceback`` carry the real exception. Always exit
    code 1.
    """
    error: dict[str, Any] = {
        "class": "UnexpectedError",
        "title": "Unexpected error",
        "exit_code": 1,
        "retryable": False,
    }
    if debug is not None:
        error["detail"] = str(debug)
        error["traceback"] = "".join(traceback.format_exception(debug))
    return {"status": "fail", "error": error}


def image_result(
    *,
    command: str,
    project_id: str,
    model: str,
    images: list[GeneratedImage],
    saved_paths: list[Path],
    ref_count: int | None = None,
) -> dict[str, Any]:
    """Complete result for a single-prompt ``image t2i`` / ``image i2i`` run.

    Every field captured by :class:`gflow_cli.api.dto.GeneratedImage` is
    surfaced (nothing dropped); ``local_path`` is the on-disk PNG the CLI
    downloaded. ``ref_count`` is set only by i2i (None ⇒ key omitted).
    """
    items: list[dict[str, Any]] = []
    for img, path in zip(images, saved_paths, strict=True):
        width, height = img.dimensions
        items.append(
            {
                "media_name": img.media_name,
                "workflow_id": img.workflow_id,
                "seed": img.seed,
                "prompt": img.prompt,
                "model_name_type": img.model_name_type,
                "aspect_ratio": img.aspect_ratio,
                "dimensions": {"width": width, "height": height},
                "fife_url": img.fife_url,
                "is_signed_url": img.is_signed_url,
                "local_path": str(path),
            },
        )
    payload: dict[str, Any] = {
        "status": "ok",
        "command": command,
        "project_id": project_id,
        "model": model,
        "count": len(items),
        "images": items,
    }
    if ref_count is not None:
        payload["ref_count"] = ref_count
    return payload


def video_result(
    *,
    command: str,
    request: GenerateVideoRequest,
    result: VideoResult,
) -> dict[str, Any]:
    """Complete result for a ``video t2v`` / ``i2v`` / ``r2v`` run.

    Shape follows what Flow actually returns for video (``VideoResult`` +
    ``VideoStatus``): no ``fife_url``/``dimensions``/per-output ``seed`` like
    images — those simply do not exist on the video wire, so they are absent
    rather than faked. ``status`` is "ok"/"fail" mirroring ``succeeded``;
    ``generation_status`` is the raw ``MEDIA_GENERATION_STATUS_*`` wire value.
    """
    st = result.status
    return {
        "status": "ok" if st.succeeded else "fail",
        "command": command,
        "media_id": st.media_id,
        "generation_status": st.status,
        "succeeded": st.succeeded,
        "local_path": str(result.local_path) if result.local_path is not None else None,
        "failure_reasons": list(st.failure_reasons),
        "error_message": st.error_message,
        "request": {
            "model": request.model.value if request.model is not None else None,
            "mode": request.mode.value,
            "aspect": request.aspect.value,
            "duration": request.duration,
            "count": request.count,
            "seed": request.seed,
        },
    }
