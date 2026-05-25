"""Tests for gflow_cli.errors — RFC 9457 Problem Details hierarchy."""

from __future__ import annotations

import json

import pytest

from gflow_cli.errors import (
    EXIT_CODE_MAP,
    AuthBrowserRejectedError,
    AuthExpiredError,
    AuthMissingError,
    ConfigurationError,
    ContentPolicyError,
    FlowApiError,
    GFlowError,
    NetworkError,
    ProblemDetails,
    RateLimitError,
    TransportTimeoutError,
    WafRejectionError,
    WireFormatError,
)

# ---------- parametrized to_problem_details() round-trip table ----------


@pytest.mark.parametrize(
    "exc_cls, kwargs, expect_keys, expect_absent, expected_status",
    [
        # AuthExpiredError — minimal
        (
            AuthExpiredError,
            {
                "detail": "401",
                "status": 401,
                "instance": "gflow:error:abc",
                "route": "createProject",
            },
            {"type", "title", "status", "detail", "instance", "remediation_hint", "route"},
            set(),
            401,
        ),
        # RateLimitError — with retry_after
        (
            RateLimitError,
            {
                "detail": "429",
                "status": 429,
                "instance": "gflow:error:def",
                "route": "batchGenerateImages",
            },
            {"type", "title", "status", "detail", "instance", "remediation_hint", "route"},
            set(),
            429,
        ),
        # ContentPolicyError — status MUST be omitted (RFC 9457: 200 conflates with success)
        (
            ContentPolicyError,
            {
                "detail": "empty media[]",
                "instance": "gflow:error:ghi",
                "route": "batchGenerateImages",
            },
            {"type", "title", "detail", "instance", "remediation_hint", "route"},
            {"status"},
            None,
        ),
        # NetworkError — exhausted retries
        (
            NetworkError,
            {
                "detail": "503 after 3 retries",
                "status": 503,
                "instance": "gflow:error:jkl",
                "route": "createProject",
            },
            {"type", "title", "status", "detail", "instance", "remediation_hint", "route"},
            set(),
            503,
        ),
        # WireFormatError — minimal (no detail, no instance)
        (
            WireFormatError,
            {},
            {"type", "title", "remediation_hint"},
            {"status", "detail", "instance", "route"},
            None,
        ),
    ],
)
def test_to_problem_details_table(exc_cls, kwargs, expect_keys, expect_absent, expected_status):
    exc = exc_cls(**kwargs)
    pd: ProblemDetails = exc.to_problem_details()
    assert expect_keys.issubset(pd.keys()), f"missing keys: {expect_keys - pd.keys()}"
    assert expect_absent.isdisjoint(pd.keys()), (
        f"unexpected keys present: {expect_absent & pd.keys()}"
    )
    if expected_status is not None:
        # Use .get() — `status` is `total=False` on the TypedDict, so direct
        # subscript trips pyright's reportTypedDictNotRequiredAccess.
        assert pd.get("status") == expected_status
    # Round-trips through JSON without TypeError
    assert json.loads(json.dumps(pd)) == pd


def test_problem_type_uris_stable():
    """Lock the URIs — they're greppable identifiers in production logs."""
    assert (
        AuthBrowserRejectedError.problem_type
        == "https://gflow-cli.dev/errors/auth-browser-rejected"
    )
    assert AuthExpiredError.problem_type == "https://gflow-cli.dev/errors/auth-expired"
    assert RateLimitError.problem_type == "https://gflow-cli.dev/errors/rate-limit"
    assert ContentPolicyError.problem_type == "https://gflow-cli.dev/errors/content-policy"
    assert NetworkError.problem_type == "https://gflow-cli.dev/errors/network"
    assert WireFormatError.problem_type == "https://gflow-cli.dev/errors/wire-format"
    assert FlowApiError.problem_type == "https://gflow-cli.dev/errors/api-error"
    assert GFlowError.problem_type == "about:blank"


# ---------- EXIT_CODE_MAP isinstance walk ----------


class _SyntheticAuthError(AuthExpiredError):
    """Hypothetical future subclass — must inherit AuthExpired's exit code 3."""


def _exit_code_for(exc: GFlowError) -> int:
    for cls, code in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            return code
    return 1


def test_exit_code_map_synthetic_subclass_inherits_parent_code():
    # The whole point of the isinstance walk: subclass inherits parent's code.
    assert _exit_code_for(_SyntheticAuthError(detail="expired again")) == 3


@pytest.mark.parametrize(
    "exc_cls, expected_code",
    [
        (AuthExpiredError, 3),
        (AuthBrowserRejectedError, 14),
        (RateLimitError, 4),
        (ContentPolicyError, 5),
        (NetworkError, 6),
        (WireFormatError, 7),
    ],
)
def test_exit_code_map_per_class(exc_cls, expected_code):
    assert _exit_code_for(exc_cls(detail="x")) == expected_code


def test_exit_code_map_ordering_invariant():
    """Most-specific classes MUST appear before parent classes in EXIT_CODE_MAP.

    The isinstance walk returns the FIRST match, so adding a parent before its
    subclasses would mask the subclass's code.
    """
    seen: list[type] = []
    for cls in EXIT_CODE_MAP:
        for prior in seen:
            assert not issubclass(cls, prior), (
                f"{cls.__name__} is a subclass of {prior.__name__} but appears AFTER it; "
                f"swap their order in EXIT_CODE_MAP."
            )
        seen.append(cls)


# ---------- FlowApiError legacy constructor (back-compat) ----------


def test_flow_api_error_legacy_positional_constructor():
    exc = FlowApiError(401, "body text", route="createProject")
    assert exc.status == 401
    assert exc.route == "createProject"
    assert exc.body == "body text"
    assert "HTTP 401" in str(exc)


def test_flow_api_error_new_style_constructor():
    exc = FlowApiError("custom detail", status=500, route="r", instance="gflow:error:x")
    assert exc.status == 500
    assert exc.route == "r"
    assert exc.detail == "custom detail"
    assert exc.body == ""


def test_typed_subclass_caught_by_flow_api_error_clause():
    """Back-compat: legacy `except FlowApiError` MUST catch typed subclasses."""
    raised: FlowApiError | None = None
    try:
        raise AuthExpiredError(detail="x", status=401)
    except FlowApiError as e:
        raised = e
    assert isinstance(raised, AuthExpiredError)
    assert isinstance(raised, FlowApiError)
    assert isinstance(raised, GFlowError)


# ---------- _redact_for_log mandate ----------


def test_flow_api_error_legacy_body_redaction_mandate():
    """The body argument MUST be passed through _redact_for_log BEFORE construction.

    Convention: callers redact at the raise site. This test asserts that *if* a
    caller forgets, the body is at least truncated to 200 chars in detail —
    documented behavior.
    """
    long_body = "x" * 1000
    exc = FlowApiError(500, long_body, route="r")
    pd = exc.to_problem_details()
    # detail is truncated/sanitized — full 1000-char body must NOT appear verbatim.
    assert len(pd.get("detail", "")) <= 250  # 200 body + "HTTP 500: " prefix


# ---------- WireFormatError discovery payload ----------


def test_wire_format_error_carries_discovery_fields():
    exc = WireFormatError(
        detail="unknown shape",
        status=200,
        instance="gflow:error:xyz",
        route="batchGenerateImages",
        discovery={
            "route_name": "batchGenerateImages",
            "http_status": 200,
            "content_type": "application/json",
            "top_level_keys": ["error", "status"],
            "body_prefix_redacted": '{"error": "..."}',
        },
    )
    assert exc.discovery["top_level_keys"] == ["error", "status"]
    assert exc.discovery["http_status"] == 200


# ---------- RateLimitError retry_after ----------


def test_rate_limit_error_carries_retry_after():
    exc = RateLimitError(detail="429", status=429, retry_after=42.0)
    assert exc.retry_after == 42.0


def test_rate_limit_error_retry_after_defaults_to_none():
    """Default `retry_after` is None — branch missed by the table test."""
    exc = RateLimitError(detail="429", status=429)
    assert exc.retry_after is None


# ---------- T1 review-loop regression tests ----------


def test_content_policy_error_explicit_status_200_still_omitted():
    """Class-level enforcement of RFC 9457: even if a caller passes status=200
    (e.g. the literal upstream Flow HTTP status), to_problem_details() MUST
    NOT include `status` — a 2xx code on a Problem Details object conflates
    error with success. The instance attribute is preserved for telemetry
    (observability emits it as the `upstream_status` extension)."""
    exc = ContentPolicyError(detail="empty media[]", status=200)
    pd = exc.to_problem_details()
    assert "status" not in pd
    assert exc.status == 200  # preserved on the instance for log emission


def test_flow_api_error_one_arg_legacy_constructor():
    """One-arg legacy form: body defaults to ''. Branch missed by the
    two-arg test."""
    exc = FlowApiError(401)
    assert exc.status == 401
    assert exc.body == ""


def test_flow_api_error_bool_does_not_silently_take_legacy_path():
    """`bool` is a subclass of `int`, so `isinstance(True, int)` is True.
    Without the explicit `and not isinstance(args[0], bool)` guard, a caller
    accidentally passing a boolean would silently take the legacy path with
    `status=True`. After the fix, bools fall through to the new-style branch
    and `status` is unset."""
    exc = FlowApiError(True)
    assert exc.status is None  # bool did NOT become status


# ---------- Task A.1 — transport strategy exception classes ----------


def test_transport_timeout_error_exit_code():
    err = TransportTimeoutError("hung for 31s on batchGenerateImages")
    assert _exit_code_for(err) == 9
    assert "31s" in str(err)


def test_waf_rejection_error_exit_code():
    err = WafRejectionError("HTTP 403 from aisandbox-pa")
    assert _exit_code_for(err) == 10


def test_configuration_error_exit_code():
    err = ConfigurationError("Transport 'foo' is not registered.")
    assert _exit_code_for(err) == 11


def test_auth_missing_error_exit_code():
    err = AuthMissingError("SAPISID cookie missing in profile")
    assert _exit_code_for(err) == 8


# ---------- BatchPartialError and BatchIntegrityError ----------


def test_batch_partial_error_carries_partial_results() -> None:
    from gflow_cli.api.dto import BatchSubmissionResult
    from gflow_cli.errors import BatchPartialError, GFlowError

    partial = BatchSubmissionResult(
        status="ok",
        project_id="p1",
        prompt_idx=0,
        prompt_hash="aa",
        images=(),
    )
    cause = GFlowError(detail="upstream timeout", route="batch")
    err = BatchPartialError(
        detail="batch failed on prompt 1",
        route="batch",
        partial_results=(partial,),
        cause=cause,
    )
    assert err.partial_results == (partial,)
    assert err.cause is cause
    assert isinstance(err, GFlowError)


def test_batch_integrity_error_carries_indices() -> None:
    from gflow_cli.errors import BatchIntegrityError, GFlowError

    err = BatchIntegrityError(
        detail="expected 4 files, got 3",
        route="batch",
        prompt_indices=(1, 2),
    )
    assert err.prompt_indices == (1, 2)
    assert isinstance(err, GFlowError)


# ---------- gflow_cli.exceptions alias ----------


def test_exceptions_module_is_alias_for_errors() -> None:
    """gflow_cli.exceptions must re-export the same objects as gflow_cli.errors.

    Both module names must resolve to identical class objects — ``is`` check
    ensures no accidental duplicate-class creation (which would break
    ``except GFlowError`` clauses imported from one module while the raise
    site uses the other).
    """
    import gflow_cli.errors as _errors
    import gflow_cli.exceptions as _exceptions

    assert _exceptions.GFlowError is _errors.GFlowError
    assert _exceptions.FlowApiError is _errors.FlowApiError
    assert _exceptions.AuthExpiredError is _errors.AuthExpiredError
    assert _exceptions.ContentPolicyError is _errors.ContentPolicyError
    assert _exceptions.EXIT_CODE_MAP is _errors.EXIT_CODE_MAP
    assert _exceptions.ProblemDetails is _errors.ProblemDetails
