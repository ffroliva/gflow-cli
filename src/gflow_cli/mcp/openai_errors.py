"""gflow problem details → OpenAI's error object, for the OpenAI-compatible routes (#1365).

gflow has one error system: RFC 9457 problem details. The OpenAI-compatible facade does not
invent a second one; it translates at the edge into the body OpenAI clients parse,
``{"error": {"message", "type", "param", "code"}}``, in OpenAI's vocabulary. gflow's own
facts (``gflow_type``, ``exit_code``, ``charged``, ``retryable``, ``next_action``,
``operation_id``, ``retry_after``, ``task_id``) ride along as extra keys inside ``error``,
which OpenAI clients ignore.

Every error response says ``x-should-retry: false`` except the local token-bucket refusal:
the official SDKs retry 5xx and 429 by default, and a retry of a generation that may have
been billed, or that hit Flow's daily quota, spends again or fails again.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from starlette.responses import JSONResponse

#: Seconds an OpenAI client should wait after a 429: one refill of the token bucket.
RETRY_AFTER_SECONDS = 20

_FACTS = ("exit_code", "charged", "retryable", "next_action", "operation_id", "retry_after")
_ERRORS = "https://gflow-cli.dev/errors/"
_RENEW_HINT = " Run 'gflow auth renew' on the machine running gflow serve."
_MISCONFIGURED_PREFIX = "The gflow serve daemon is misconfigured: "
_UNFINISHED = frozenset({"pending", "processing"})


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
# Flow refused the daemon's request (WAF, reCAPTCHA): the session is not the cause.
_UPSTREAM_REJECTED = OpenAIError(502, "server_error", "upstream_rejected")
_UPSTREAM_ERROR = OpenAIError(502, "server_error", "upstream_error")
_MISCONFIGURED = OpenAIError(500, "server_error", "server_misconfigured")
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
    _ERRORS + "waf-rejection": _UPSTREAM_REJECTED,
    _ERRORS + "recaptcha-mint": _UPSTREAM_REJECTED,
    _ERRORS + "no-profile": _MISCONFIGURED,
    _ERRORS + "no-default-profile": _MISCONFIGURED,
    _ERRORS + "configuration": _MISCONFIGURED,
    _ERRORS + "profile-access": _MISCONFIGURED,
    _ERRORS + "profile-engine-downgrade": _MISCONFIGURED,
    _ERRORS + "browser-engine-unavailable": _MISCONFIGURED,
    _ERRORS + "transport-timeout": _TIMEOUT,
}

#: bad-parameter refusals about the profile or project. The route never sends either, so
#: these describe the daemon's environment (GFLOW_CLI_PROFILE, GFLOW_CLI_PROJECT).
_DAEMON_PARAM_TITLES = frozenset({"Invalid Profile", "Default Project Belongs To Another Profile"})


def _by_status(status: object) -> OpenAIError:
    if not isinstance(status, int) or not 400 <= status <= 599:  # noqa: PLR2004
        return OpenAIError(500, "server_error", "generation_failed")
    if status in (401, 403):
        return _UPSTREAM_AUTH
    if status == 429:  # noqa: PLR2004
        return _RATE_LIMITED
    if status < 500:  # noqa: PLR2004
        return _UPSTREAM_ERROR  # Flow's 4xx answers the daemon's request, not the caller's
    return OpenAIError(status, "server_error", "generation_failed")


def classify(result: dict[str, Any]) -> OpenAIError:
    """The OpenAI error for a tool envelope that did not complete."""
    # ``task_status`` is how wait=True reports a row it left unfinished.
    if result.get("status") in _UNFINISHED or result.get("task_status") in _UNFINISHED:
        return _TIMEOUT
    if result.get("status") == "rate_limited":
        return _RATE_LIMITED
    problem = _problem(result)
    if problem.get("title") in _DAEMON_PARAM_TITLES:
        return _MISCONFIGURED
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
        all_headers.setdefault("retry-after", str(RETRY_AFTER_SECONDS))
    body = {"message": message, "type": type_, "param": param, "code": code, **(extra or {})}
    return JSONResponse({"error": body}, status_code=status, headers=all_headers)


def _message(result: dict[str, Any], mapped: OpenAIError) -> str:
    problem = _problem(result)
    if mapped is _TIMEOUT and "type" not in problem:
        return (
            f"The generation did not finish while the request waited "
            f"(task {result.get('task_id')}); it may still complete in Flow."
        )
    error = result.get("error")
    text = problem.get("detail") or problem.get("message") or problem.get("title")
    if not text and isinstance(error, str):
        text = error
    message = str(text or "Generation failed.")
    if mapped is _UPSTREAM_AUTH:
        return message + _RENEW_HINT
    if mapped is _MISCONFIGURED:
        return _MISCONFIGURED_PREFIX + message
    return message


def tool_failure(result: dict[str, Any]) -> JSONResponse:
    """Translate a ``gflow_generate_image`` envelope that did not complete."""
    mapped = classify(result)
    problem = _problem(result)
    extra = {k: problem[k] for k in _FACTS if problem.get(k) is not None}
    if "type" in problem:
        extra["gflow_type"] = problem["type"]
    if (task_id := result.get("task_id")) is not None:
        extra["task_id"] = task_id
    headers: dict[str, str] = {}
    retry_after = problem.get("retry_after")
    if isinstance(retry_after, int | float) and not isinstance(retry_after, bool):
        headers["retry-after"] = str(max(1, math.ceil(retry_after)))
    return error_response(
        mapped.status,
        _message(result, mapped),
        type_=mapped.type,
        code=mapped.code,
        extra=extra,
        # Only the local bucket refused before anything ran, and it refills on its own.
        retry=result.get("status") == "rate_limited",
        headers=headers,
    )
