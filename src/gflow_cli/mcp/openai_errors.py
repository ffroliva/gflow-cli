"""gflow problem details → OpenAI's error object, for the OpenAI-compatible routes (#1365).

gflow has one error system: RFC 9457 problem details. The OpenAI-compatible facade does not
invent a second one; it translates at the edge into the body OpenAI clients parse,
``{"error": {"message", "type", "param", "code"}}``, in OpenAI's vocabulary. gflow's own
facts (``gflow_type``, ``exit_code``, ``charged``, ``retryable``, ``next_action``,
``operation_id``, ``task_id``) ride along as extra keys inside ``error``, which OpenAI
clients ignore.

Every error response also says ``x-should-retry: false`` unless gflow reports the failure
as retryable with nothing charged: the official SDKs retry 5xx twice by default, and a
retry of a generation that may have been billed spends again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from starlette.responses import JSONResponse

ERROR_TYPES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "rate_limit_error",
        "server_error",
    }
)

#: Seconds an OpenAI client should wait after a 429: one refill of the token bucket.
RETRY_AFTER_SECONDS = 20

_FACTS = ("exit_code", "charged", "retryable", "next_action", "operation_id")
_ERRORS = "https://gflow-cli.dev/errors/"
_RENEW_HINT = " Run 'gflow auth renew' on the machine running gflow serve."


@dataclass(frozen=True)
class OpenAIError:
    status: int
    type: str
    code: str


_CONTENT_POLICY = OpenAIError(400, "invalid_request_error", "content_policy_violation")
_INVALID_VALUE = OpenAIError(400, "invalid_request_error", "invalid_value")
_RATE_LIMITED = OpenAIError(429, "rate_limit_error", "rate_limit_exceeded")
# A 401/403 from Flow is about the daemon's saved Google session, not the caller's key.
# Passing it through would make an OpenAI client rotate a daemon token that is fine.
_UPSTREAM_AUTH = OpenAIError(502, "server_error", "upstream_auth_failed")
_TIMEOUT = OpenAIError(504, "server_error", "generation_timeout")

#: gflow problem type → OpenAI error. A type not listed falls back on its HTTP status.
BY_GFLOW_TYPE: dict[str, OpenAIError] = {
    _ERRORS + "content-policy": _CONTENT_POLICY,
    _ERRORS + "bad-parameter": _INVALID_VALUE,
    _ERRORS + "reference-not-found": _INVALID_VALUE,
    _ERRORS + "rate-limit": _RATE_LIMITED,
    _ERRORS + "insufficient-credits": OpenAIError(429, "rate_limit_error", "insufficient_quota"),
    _ERRORS + "auth-expired": _UPSTREAM_AUTH,
    _ERRORS + "auth-missing": _UPSTREAM_AUTH,
    _ERRORS + "aisandbox-auth": _UPSTREAM_AUTH,
    _ERRORS + "auth-browser-rejected": _UPSTREAM_AUTH,
    _ERRORS + "transport-timeout": _TIMEOUT,
}


def _by_status(status: object) -> OpenAIError:
    if not isinstance(status, int) or not 400 <= status <= 599:  # noqa: PLR2004
        return OpenAIError(500, "server_error", "generation_failed")
    if status in (401, 403):
        return _UPSTREAM_AUTH
    if status == 429:  # noqa: PLR2004
        return _RATE_LIMITED
    if status < 500:  # noqa: PLR2004
        return OpenAIError(status, "invalid_request_error", "invalid_value")
    return OpenAIError(status, "server_error", "generation_failed")


def classify(result: dict[str, Any]) -> OpenAIError:
    """The OpenAI error for a tool envelope that did not complete."""
    status = result.get("status")
    if status in ("pending", "processing"):
        return _TIMEOUT
    if status == "rate_limited":
        return _RATE_LIMITED
    problem = _problem(result)
    return BY_GFLOW_TYPE.get(str(problem.get("type"))) or _by_status(problem.get("status"))


def _problem(result: dict[str, Any]) -> dict[str, Any]:
    error = result.get("error")
    return cast("dict[str, Any]", error) if isinstance(error, dict) else {}


def error_response(
    status: int,
    message: str,
    *,
    type_: str,
    code: str,
    param: str | None = None,
    extra: dict[str, Any] | None = None,
    retry: bool = False,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """OpenAI's error object; ``x-should-retry: false`` unless ``retry`` is set."""
    all_headers = dict(headers or {})
    if not retry:
        all_headers["x-should-retry"] = "false"
    if status == 429:  # noqa: PLR2004
        all_headers["retry-after"] = str(RETRY_AFTER_SECONDS)
    body = {"message": message, "type": type_, "param": param, "code": code, **(extra or {})}
    return JSONResponse({"error": body}, status_code=status, headers=all_headers)


def tool_failure(result: dict[str, Any]) -> JSONResponse:
    """Translate a ``gflow_generate_image`` envelope that did not complete."""
    mapped = classify(result)
    problem = _problem(result)
    task_id = result.get("task_id")
    if mapped is _TIMEOUT and "type" not in problem:
        message = (
            f"The generation did not finish while the request waited (task {task_id}); "
            "it may still complete in Flow."
        )
    else:
        error = result.get("error")
        message = str(problem.get("message") or problem.get("detail") or error or "")
        message = message or "Generation failed."
    if mapped is _UPSTREAM_AUTH:
        message += _RENEW_HINT
    extra = {k: problem[k] for k in _FACTS if problem.get(k) is not None}
    if "type" in problem:
        extra["gflow_type"] = problem["type"]
    if task_id is not None:
        extra["task_id"] = task_id
    retry = (
        problem.get("retryable") is True
        and problem.get("charged") == "no"
        and mapped.status not in (502, 504)
    )
    return error_response(
        mapped.status, message, type_=mapped.type, code=mapped.code, extra=extra, retry=retry
    )
