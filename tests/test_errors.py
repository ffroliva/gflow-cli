"""Tests for gflow_cli.errors — RFC 9457 Problem Details hierarchy."""

from __future__ import annotations

import ast
import json
from pathlib import Path

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
    MediaAttributionError,
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


def test_transport_timeout_default_text_names_no_fixed_ceiling():
    # #1037: the page-RPC ceiling is no longer 30 s; the class text must not claim one.
    err = TransportTimeoutError("x")
    assert "30" not in err.remediation_hint
    assert "30" not in (TransportTimeoutError.__doc__ or "")


def test_an_unknown_write_outcome_is_exit_7_and_never_retryable():
    from gflow_cli.errors import RpcResponseUnavailableError, is_retryable

    err = RpcResponseUnavailableError(detail="x")
    assert _exit_code_for(err) == 7
    assert is_retryable(err) is False


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


def test_batch_integrity_error_advises_repairing_download_without_rerunning() -> None:
    from gflow_cli.errors import BatchIntegrityError

    err = BatchIntegrityError(detail="expected 4 files, got 3")

    assert err.charged == "no"
    assert err.resolved_next_action() == "reconcile"
    assert err.remediation_hint is not None
    assert "retry" not in err.remediation_hint.lower()
    assert "integrity" in err.remediation_hint.lower() or "download" in err.remediation_hint.lower()


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


# ---------- MediaAttributionError (issue #281) ----------


def test_media_attribution_error_exit_code_26() -> None:
    """MediaAttributionError -> exit 26 (current max is 25, so 26 is free).

    Raised when generated media cannot be reliably attributed (agentic DOM-
    scrape ambiguity, or a downstream already-recorded check) -- fail-fast
    over silently downloading/reporting the wrong asset (issue #281)."""
    err = MediaAttributionError(detail="cannot attribute the generation among 3 candidates")
    assert isinstance(err, GFlowError)
    assert EXIT_CODE_MAP[MediaAttributionError] == 26
    assert _exit_code_for(err) == 26


def test_media_attribution_error_problem_details() -> None:
    err = MediaAttributionError(detail="cannot attribute the generation among 3 candidates")
    pd = err.to_problem_details()
    assert pd["type"] == "https://gflow-cli.dev/errors/media-attribution"
    assert pd["title"] == "Generated media could not be attributed"
    assert "remediation_hint" in pd
    assert err.remediation_hint != ""


def test_media_upload_rejected_error_exit_code_27():
    """#287: a Flow upload endpoint 4xx is a typed, scriptable failure —
    distinct from generic error (1) so callers can branch on 're-encode the
    input image' instead of parsing stderr."""
    from gflow_cli.errors import MediaUploadRejectedError

    err = MediaUploadRejectedError(detail="frame image upload rejected (HTTP 400)")
    assert EXIT_CODE_MAP[MediaUploadRejectedError] == 27
    assert next(code for cls, code in EXIT_CODE_MAP.items() if isinstance(err, cls)) == 27


def test_mention_index_unavailable_error_exit_code_29():
    """Task B1: an @mention was present but its catalog source (character or
    media) failed to load. Distinct exit code 29 lets callers branch on 'the
    catalog is unreachable' vs an unknown-mention ConfigurationError (11)."""
    from gflow_cli.errors import MentionIndexUnavailableError

    err = MentionIndexUnavailableError(detail="the character source is unavailable")
    assert EXIT_CODE_MAP[MentionIndexUnavailableError] == 29
    assert next(code for cls, code in EXIT_CODE_MAP.items() if isinstance(err, cls)) == 29


def test_mention_index_unavailable_error_problem_details():
    from gflow_cli.errors import MentionIndexUnavailableError

    err = MentionIndexUnavailableError(detail="the media source is unavailable")
    pd = err.to_problem_details()
    assert pd["type"] == "https://gflow-cli.dev/errors/mention-index-unavailable"
    assert pd["title"] == "Mention index unavailable"
    assert "remediation_hint" in pd
    assert err.remediation_hint != ""


def test_problem_details_incident_is_opaque() -> None:
    """S21: the shared RFC 9457 extension carries ONLY {id, capture_status} —
    never the absolute local path, artifact names, or username."""
    import json
    from pathlib import Path

    from gflow_cli.diagnostics import IncidentRef
    from gflow_cli.errors import FlowAppError

    exc = FlowAppError("crash")
    exc.incident_ref = IncidentRef(
        id="corr-fp",
        capture_status="complete",
        path=Path("/home/CANARYUSER/gflow/incidents/x"),
        artifacts=("ui.json", "sensitive/screenshot.png"),
    )
    pd = exc.to_problem_details()
    assert pd["incident"] == {"id": "corr-fp", "capture_status": "complete"}
    blob = json.dumps(pd)
    assert "CANARYUSER" not in blob
    assert "screenshot" not in blob


def test_problem_details_without_ref_has_no_incident_key() -> None:
    from gflow_cli.errors import FlowAppError

    assert "incident" not in FlowAppError("crash").to_problem_details()


def test_all_domain_errors_provide_remediation_hint() -> None:
    """Assert to_problem_details() returns remediation_hint for all domain error classes."""
    from gflow_cli.errors import CreditCapExceededError, GFlowError

    def get_subclasses(c: type[GFlowError]) -> set[type[GFlowError]]:
        subs = set(c.__subclasses__())
        for sub in list(subs):
            subs.update(get_subclasses(sub))
        return subs

    domain_classes = get_subclasses(GFlowError)
    assert len(domain_classes) >= 30

    for exc_cls in domain_classes:
        if exc_cls is CreditCapExceededError:
            exc = CreditCapExceededError(quoted=None, max_credits=0)
        else:
            try:
                exc = exc_cls()
            except TypeError:
                try:
                    exc = exc_cls("test detail")
                except TypeError:
                    exc = exc_cls(requested="classic")

        pd = exc.to_problem_details()
        hint = pd.get("remediation_hint", "")
        assert isinstance(hint, str) and len(hint) > 0, (
            f"{exc_cls.__name__} missing or empty remediation_hint in to_problem_details()"
        )


def test_specific_remediation_hints() -> None:
    """Assert exact expected default remediation hints on Task 1 error classes."""
    from gflow_cli.errors import (
        ContentPolicyError,
        DataStoreError,
        FrameExtractionError,
        RateLimitError,
        SceneConcatError,
        UiSelectorDriftError,
        WireFormatError,
    )

    assert (
        WireFormatError().remediation_hint
        == "Check request payload parameters or retry with a simpler prompt text. "
        "File a bug at https://github.com/ffroliva/gflow-cli/issues with the "
        "discovery payload above."
    )
    assert (
        ContentPolicyError().remediation_hint
        == "Reduce prompt text or describe <= 1 person per scene"
    )
    assert RateLimitError().remediation_hint == (
        "Daily or per-minute model quota reached; retry with a different model or "
        "wait for quota reset"
    )
    assert (
        DataStoreError().remediation_hint
        == "Check database file permissions or run 'gflow data errors prune'"
    )
    assert (
        SceneConcatError().remediation_hint
        == "Ensure video clip dimensions and codecs match before concatenation"
    )
    # #813: the hint must name the extra AND packages it carries. It used to
    # say only "PyAV", so an operator whose `gflow-cli[chain]` install was missing
    # a required package was told to check the one dependency they already had.
    assert (
        FrameExtractionError().remediation_hint
        == "Verify input video file is readable and non-corrupt. Ensure the gflow-cli[chain] "
        "extra and its packages (av, pillow) are installed: "
        "pip install 'gflow-cli[chain]'"
    )
    # #493: the hint must name the artifacts drift sites actually write (the
    # mode-switch probe produces a diagnostics JSON, not a screenshot) — exact
    # equality so a phantom-artifact ask cannot silently return.
    assert UiSelectorDriftError().remediation_hint == (
        "A Flow editor UI element could not be located — Google may have updated "
        "their frontend. Check for a newer gflow-cli release, then file a bug at "
        "https://github.com/ffroliva/gflow-cli/issues referencing the probe name "
        "and attaching the diagnostics JSON and/or debug screenshot referenced in "
        "this message, plus the incident bundle's report.md when one was written "
        "(review artifacts before sharing — screenshots may show your account "
        "name/avatar; do NOT include tokens or signed URLs)."
    )


def test_reference_not_found_error_exit_code():
    """#493 recon: a reference NAME that Flow's picker does not offer used to be
    a bare Playwright TimeoutError (exit 1). Exit 32 lets scripts branch on
    "that name is not in the picker" instead of guessing at UI drift (23)."""
    from gflow_cli.errors import ReferenceNotFoundError

    err = ReferenceNotFoundError(detail="no media named 'a brass key' in this project's picker")
    assert EXIT_CODE_MAP[ReferenceNotFoundError] == 32
    # The isinstance walk must resolve to 32, not be shadowed by GFlowError (1).
    assert next(code for cls, code in EXIT_CODE_MAP.items() if isinstance(err, cls)) == 32


def test_reference_not_found_error_problem_details():
    from gflow_cli.errors import ReferenceNotFoundError

    err = ReferenceNotFoundError(detail="no media named 'x' in this project's picker")
    body = err.to_problem_details()
    assert body["type"].endswith("/reference-not-found")
    assert body["title"] == "Referenced media was not found"
    # The remediation must not name a CLI flag that does not exist.
    assert "--ref-name" not in err.remediation_hint


def test_upscale_unavailable_title_is_media_neutral() -> None:
    """#966: the video upscale raise site printed "Image upscale unavailable"."""
    from gflow_cli.api.transports.migrated_video_upscale import _unavailable_error

    err = _unavailable_error("1080p")
    assert "image" not in err.title.lower()
    assert "image" not in str(err).lower()
    assert err.to_problem_details()["title"] == "Upscale unavailable for this account"


def test_refused_2k_image_upscale_does_not_advise_2k() -> None:
    """The 2K site inherited the class default, which says "use --scale 2k"."""
    from gflow_cli.api.image_upscale import TargetResolution
    from gflow_cli.api.transports.migrated_upscale import _unavailable_error

    err = _unavailable_error(TargetResolution.RES_2K)
    assert "--scale 2k" not in (err.remediation_hint or "")


def test_nothing_submitted_marks_a_pre_submit_error_charged_no() -> None:
    from gflow_cli.errors import UiSelectorDriftError, nothing_submitted

    with pytest.raises(UiSelectorDriftError) as info, nothing_submitted():
        raise UiSelectorDriftError(detail="no radio")
    assert info.value.charged == "no"


def test_nothing_submitted_withholds_the_claim_once_a_submit_left() -> None:
    """#1320 review: a stray Enter in a closing picker can submit; then it is not 'no'."""
    from gflow_cli.errors import UiSelectorDriftError, nothing_submitted

    with pytest.raises(UiSelectorDriftError) as info, nothing_submitted(sent=lambda: True):
        raise UiSelectorDriftError(detail="chip vanished after the key press")
    assert info.value.charged == "maybe"


def test_batch_partial_error_keeps_free_image_partials_uncharged() -> None:
    from gflow_cli.errors import BatchPartialError, GFlowError

    error = BatchPartialError(
        partial_results=(object(),), cause=GFlowError("later prompt failed", charged="no")
    )

    assert error.charged == "no"


def test_batch_partial_error_inherits_cause_charge_without_partial_results() -> None:
    from gflow_cli.errors import BatchPartialError, GFlowError

    error = BatchPartialError(cause=GFlowError("first prompt failed", charged="maybe"))

    assert error.charged == "maybe"


def test_marking_prior_work_billed_disables_retry_for_retryable_error() -> None:
    from gflow_cli.api.credit_cap import mark_prior_work_billed
    from gflow_cli.errors import FlowAppError, is_retryable

    error = FlowAppError("selector drift", remediation_hint="retry in a moment")
    mark_prior_work_billed(error, True)

    assert error.resolved_next_action() == "reconcile"
    assert is_retryable(error) is False
    problem = error.to_problem_details()
    assert problem["charged"] == "yes"
    assert problem["next_action"] == "reconcile"
    assert "check Flow" in error.remediation_hint
    assert (
        error.remediation_hint
        == "This command already billed Flow for earlier work; check Flow before re-running."
    )


def test_batch_partial_maybe_charged_cause_keeps_its_retry_advice() -> None:
    from gflow_cli.errors import BatchPartialError, TransportTimeoutError, is_retryable

    cause = TransportTimeoutError("prompt 0 timed out", charged="maybe")
    error = BatchPartialError(cause=cause)
    assert error.resolved_next_action() == cause.resolved_next_action()
    assert is_retryable(error) is is_retryable(cause)


def test_mark_prior_work_preserves_non_retry_advice_and_cap_hint() -> None:
    from gflow_cli.api.credit_cap import mark_prior_work_billed
    from gflow_cli.errors import AuthExpiredError, CreditCapExceededError, InsufficientCreditsError

    auth = AuthExpiredError("login required", charged="no")
    auth_hint = auth.remediation_hint
    mark_prior_work_billed(auth, True)
    assert (auth.charged, auth.next_action, auth.remediation_hint) == ("yes", "reauth", auth_hint)

    cap = CreditCapExceededError(quoted=10, max_credits=5)
    cap_hint = cap.remediation_hint
    mark_prior_work_billed(cap, True)
    assert cap.charged == "yes"
    assert cap.remediation_hint == cap_hint

    funds = InsufficientCreditsError("insufficient", charged="no")
    mark_prior_work_billed(funds, True)
    assert funds.resolved_next_action() == "wait"


def test_batch_partial_without_results_inherits_cause_recovery_metadata() -> None:
    from gflow_cli.errors import BatchPartialError, FlowAgentUiError, is_retryable

    cause = FlowAgentUiError("selector drift", charged="no")
    error = BatchPartialError(cause=cause)

    assert error.charged == "no"
    assert error.resolved_next_action() == cause.resolved_next_action()
    assert is_retryable(error) is is_retryable(cause)


@pytest.mark.parametrize(
    ("source", "function_name", "expected_guards"),
    [
        ("_ui_character.py", "_select_character_model", 3),
        ("_ui_navigation.py", "_require_unblocked", 1),
        ("_ui_prompt.py", "_switch_to_image_mode", 2),
        ("_ui_settings.py", "_select_image_model", 3),
        ("_ui_settings.py", "_set_count", 1),
        ("_video_editor_mode.py", "_switch_to_video_mode", 2),
        ("_video_settings.py", "_set_output_count", 1),
        ("_video_settings.py", "_select_video_duration", 1),
        ("_video_settings.py", "_switch_video_sub_mode", 1),
    ],
)
def test_each_labs_presubmit_raise_site_keeps_its_charged_no_guard(
    source: str, function_name: str, expected_guards: int
) -> None:
    """Pin #1336 guards to their owning function so each missing guard is caught."""
    transport_dir = Path(__file__).parents[1] / "src" / "gflow_cli" / "api" / "transports"
    tree = ast.parse((transport_dir / source).read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    ]
    assert len(functions) == 1

    def is_drift_raise(node: ast.Raise) -> bool:
        value = node.exc
        if isinstance(value, ast.Await):
            value = value.value
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "UiSelectorDriftError"
        ) or (
            isinstance(node.exc, ast.Await)
            and isinstance(node.exc.value, ast.Call)
            and isinstance(node.exc.value.func, ast.Attribute)
            and node.exc.value.func.attr == "_mode_switch_error"
        )

    guarded_raises = {
        id(raise_node)
        for with_node in ast.walk(functions[0])
        if isinstance(with_node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "nothing_submitted"
            for item in with_node.items
        )
        for raise_node in ast.walk(with_node)
        if isinstance(raise_node, ast.Raise) and is_drift_raise(raise_node)
    }
    drift_raises = [
        node
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Raise) and is_drift_raise(node)
    ]
    assert len(guarded_raises) == expected_guards
    assert len(guarded_raises) == len(drift_raises)


def test_waf_refusal_final_is_not_retryable_and_not_charged() -> None:
    from gflow_cli.errors import (
        WafRejectionError,
        exit_code_for,
        is_retryable,
        waf_refusal_final,
    )
    from gflow_cli.mcp.tools import _gflow_error_dict

    err = waf_refusal_final(reason="UNUSUAL_ACTIVITY", attempts=2)

    assert isinstance(err, WafRejectionError)
    assert exit_code_for(err) == 10
    assert err.charged == "no"
    assert err.retryable is False
    assert err.next_action == "none"
    assert is_retryable(err) is False

    pd = err.to_problem_details()
    assert pd["charged"] == "no"
    assert pd["next_action"] == "none"
    assert pd["type"] == "https://gflow-cli.dev/errors/waf-rejection"

    mcp_dict = _gflow_error_dict(err)
    assert mcp_dict["charged"] == "no"
    assert mcp_dict["next_action"] == "none"
    assert mcp_dict["retryable"] is False


def test_waf_refusal_final_remediation_names_headed_and_cooldown() -> None:
    from gflow_cli.errors import waf_refusal_final

    err = waf_refusal_final(reason="UNUSUAL_ACTIVITY", attempts=2)

    assert "--browser-visibility headed" in err.remediation_hint
    assert "docs/ACCOUNT_SAFETY.md" in err.remediation_hint
    assert "cooldown" in err.remediation_hint.lower()
    assert "both" in err.remediation_hint.lower() or "attempts" in err.remediation_hint.lower()
    assert "nothing" in err.remediation_hint.lower() and "charged" in err.remediation_hint.lower()
