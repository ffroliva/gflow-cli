"""Tests for gflow_cli.errors — RFC 9457 Problem Details hierarchy."""

from __future__ import annotations

import json

import pytest

from gflow_cli.errors import (
    EXIT_CODE_MAP,
    AuthBrowserRejectedError,
    AuthExpiredError,
    AuthMissingError,
    BrowserEngineUnavailableError,
    ChainPartialError,
    ConfigurationError,
    ContentPolicyError,
    FlowAgentUiError,
    FlowApiError,
    FrameExtractionError,
    GFlowError,
    ModelModeIncompatibilityError,
    NetworkError,
    ProblemDetails,
    RateLimitError,
    TransportTimeoutError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    VideoModelSelectionError,
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
        (ModelModeIncompatibilityError, 17),
    ],
)
def test_exit_code_map_per_class(exc_cls, expected_code):
    assert _exit_code_for(exc_cls(detail="x")) == expected_code


def test_model_mode_incompatibility_error_exit_code_17():
    """Issue #125: distinct exit code 17, NOT its parent ConfigurationError's 11.

    The isinstance walk must hit ModelModeIncompatibilityError (registered
    BEFORE ConfigurationError in EXIT_CODE_MAP) before falling through to the
    parent — otherwise scripted callers can't branch on "incompatible
    model/mode" vs a generic configuration error.
    """
    err = ModelModeIncompatibilityError(detail="omni-flash + i2v invalid")
    assert isinstance(err, ConfigurationError)
    assert _exit_code_for(err) == 17
    assert EXIT_CODE_MAP[ModelModeIncompatibilityError] == 17


def test_video_model_selection_error_exit_code_18():
    """Issue #125: model-select UI failure for i2v gets exit 18 (transport
    reliability), distinct from 17 (incompatible model) and 11 (config)."""
    err = VideoModelSelectionError(detail="could not select veo-lite (issue #125)")
    assert isinstance(err, ConfigurationError)
    assert _exit_code_for(err) == 18
    assert EXIT_CODE_MAP[VideoModelSelectionError] == 18


def test_upscale_unavailable_error_exit_code_22():
    """Issue #171: 4K upscale on a non-Ultra account (or otherwise unavailable
    target resolution) gets a DISTINCT exit code 22, separate from WafRejectionError
    (10) — even though both surface as HTTP 403 — so scripted callers can branch on
    "upgrade your tier" vs "WAF blocked the request" without parsing stderr.
    """
    err = UpscaleUnavailableError(detail="4K requires an Ultra subscription", status=403)
    assert isinstance(err, GFlowError)
    assert not isinstance(err, WafRejectionError)
    assert EXIT_CODE_MAP[UpscaleUnavailableError] == 22
    assert next(code for cls, code in EXIT_CODE_MAP.items() if isinstance(err, cls)) == 22


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


# ---------- Video-chain error classes (Task 1 / Task 3) ----------


def test_frame_extraction_error_exit_code_20() -> None:
    """FrameExtractionError -> exit code 20 (current max is 19, so 20 is free).

    Raised by the PyAV last-frame extractor when ``av`` is missing or the input
    is undecodable; carries an install-hint remediation."""
    err = FrameExtractionError(detail="av not installed")
    assert _exit_code_for(err) == 20
    assert EXIT_CODE_MAP[FrameExtractionError] == 20
    assert isinstance(err, GFlowError)
    # Has a remediation hint (the install-the-extra guidance).
    assert err.remediation_hint != ""


def test_chain_partial_error_exit_code_21_and_partial_results() -> None:
    """ChainPartialError -> exit code 21, carrying the Paths of completed links.

    Mirrors BatchPartialError but for the sequential video chain: a mid-chain
    failure must surface the already-paid-for clips so they are not lost."""
    from pathlib import Path

    completed = [Path("link0.mp4"), Path("link1.mp4")]
    err = ChainPartialError(
        detail="link 2 routed to t2v",
        partial_results=completed,
    )
    assert _exit_code_for(err) == 21
    assert EXIT_CODE_MAP[ChainPartialError] == 21
    assert err.partial_results == completed
    assert all(isinstance(p, Path) for p in err.partial_results)
    assert isinstance(err, GFlowError)


def test_chain_partial_error_partial_results_defaults_empty() -> None:
    """A ChainPartialError raised before any link completes carries an empty
    (but present) ``partial_results`` list — never None."""
    err = ChainPartialError(detail="first link failed")
    assert err.partial_results == []


# ---------- UiSelectorDriftError (issue #183) ----------


def test_ui_selector_drift_error_exit_code_23() -> None:
    """UiSelectorDriftError -> exit code 23 (issue #183).

    Raised when a UI-automation selector cascade finds no matching element,
    indicating that Flow's frontend has changed.  Exit 23 lets scripted
    callers distinguish "UI drifted" from generic error (1)."""
    err = UiSelectorDriftError(
        detail="probe=mode_switch_trigger: no matching element found on the Flow editor."
    )
    assert _exit_code_for(err) == 23
    assert EXIT_CODE_MAP[UiSelectorDriftError] == 23
    assert isinstance(err, GFlowError)
    assert err.remediation_hint != ""


def test_ui_selector_drift_error_problem_details() -> None:
    """UiSelectorDriftError carries RFC 9457 Problem Details with a stable type URI."""
    err = UiSelectorDriftError(detail="probe=image_mode_tab: Image tab not found.")
    pd = err.to_problem_details()
    assert pd["type"] == "https://gflow-cli.dev/errors/ui-selector-drift"
    assert pd["title"] == "Flow UI selector drift"
    assert "image_mode_tab" in pd.get("detail", "")
    assert "remediation_hint" in pd


# ---------- BrowserEngineUnavailableError (patchright engine opt-in) ----------


def test_browser_engine_unavailable_error_exit_code_24() -> None:
    """BrowserEngineUnavailableError -> exit 24, and the isinstance walk lands on
    24 (most-specific) rather than its ConfigurationError parent's 11."""
    err = BrowserEngineUnavailableError(
        detail="the 'patchright' package is not installed",
        remediation_hint="Install it with `pip install patchright`.",
    )
    assert isinstance(err, ConfigurationError)
    assert EXIT_CODE_MAP[BrowserEngineUnavailableError] == 24
    # The ordering invariant must keep the subclass BEFORE its parent so this 24
    # wins over ConfigurationError's 11 in the isinstance walk.
    assert _exit_code_for(err) == 24


def test_browser_engine_unavailable_error_problem_details() -> None:
    err = BrowserEngineUnavailableError(detail="patchright missing")
    pd = err.to_problem_details()
    assert pd["type"] == "https://gflow-cli.dev/errors/browser-engine-unavailable"
    assert pd["title"] == "Selected browser engine is unavailable"
    assert "remediation_hint" in pd


def test_ui_selector_drift_error_not_a_subclass_of_flow_api_error() -> None:
    """UiSelectorDriftError is a direct GFlowError subclass — it is NOT a
    FlowApiError (it is a UI-automation concern, not a wire-protocol error)."""
    err = UiSelectorDriftError(detail="probe=mode_switch_trigger: selector cascade failed.")
    assert isinstance(err, GFlowError)
    assert not isinstance(err, FlowApiError)


# ---------- FlowAgentUiError (Google Flow Agentic UI cohort) ----------


def test_flow_agent_ui_error_exit_code_25() -> None:
    """FlowAgentUiError -> exit 25."""
    err = FlowAgentUiError(detail="Agentic UI detected.")
    assert isinstance(err, GFlowError)
    assert EXIT_CODE_MAP[FlowAgentUiError] == 25
    assert _exit_code_for(err) == 25


def test_flow_agent_ui_error_problem_details() -> None:
    err = FlowAgentUiError(detail="Agentic UI detected.")
    pd = err.to_problem_details()
    assert pd["type"] == "https://gflow-cli.dev/errors/flow-agent-ui"
    assert pd["title"] == "Google Flow Agentic UI detected"
    assert "remediation_hint" in pd
