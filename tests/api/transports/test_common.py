"""Tests for gflow_cli.api.transports._common shared utilities.

RED phase: all tests fail with ModuleNotFoundError until _common.py is created.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from gflow_cli.api.transports._common import (
    BEARER_DEFAULT_TTL_S,
    FLOW_URL,
    PER_CALL_TIMEOUT_S,
    REFRESH_SAFETY_MARGIN_S,
    flow_host_kind,
    interpret_response,
    mint_batch_id,
)
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    NetworkError,
    RateLimitError,
    WafRejectionError,
    WireFormatError,
)

# ---------------------------------------------------------------------------
# Helper — build a minimal valid wire-format media item
# ---------------------------------------------------------------------------


def _make_media_item(
    name: str = "asset-uuid-1",
    workflow_id: str = "wf-001",
    seed: int = 42,
    prompt: str = "a test prompt",
    model_name_type: str = "NARWHAL",
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_PORTRAIT",
    fife_url: str = "https://lh3.example.com/img?foo=bar",
    width: int = 512,
    height: int = 512,
) -> dict:  # type: ignore[type-arg]
    return {
        "name": name,
        "workflowId": workflow_id,
        "image": {
            "generatedImage": {
                "seed": seed,
                "prompt": prompt,
                "modelNameType": model_name_type,
                "aspectRatio": aspect_ratio,
                "fifeUrl": fife_url,
            },
            "dimensions": {"width": width, "height": height},
        },
    }


def _resp(status: int, body: object) -> MagicMock:
    """Create a minimal httpx-like response mock."""
    text = body if isinstance(body, str) else json.dumps(body)
    return MagicMock(status_code=status, text=text)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants_are_canonical() -> None:
    assert FLOW_URL == "https://labs.google/fx/tools/flow?hl=en"
    assert PER_CALL_TIMEOUT_S == 30
    assert BEARER_DEFAULT_TTL_S == 3600
    assert REFRESH_SAFETY_MARGIN_S == 60


# ---------------------------------------------------------------------------
# mint_batch_id
# ---------------------------------------------------------------------------


def test_mint_batch_id_returns_uuid_string() -> None:
    bid = mint_batch_id()
    assert isinstance(bid, str)
    assert len(bid) == 36  # uuid4 canonical form: 8-4-4-4-12


def test_mint_batch_id_is_unique() -> None:
    assert mint_batch_id() != mint_batch_id()


# ---------------------------------------------------------------------------
# interpret_response — happy path
# ---------------------------------------------------------------------------


def test_interpret_response_200_returns_generated_images() -> None:
    payload = {"media": [_make_media_item()]}
    resp = _resp(200, payload)
    images = interpret_response("test_strategy", resp)
    assert len(images) == 1
    img = images[0]
    assert img.media_name == "asset-uuid-1"
    assert img.fife_url == "https://lh3.example.com/img?foo=bar"
    assert img.seed == 42
    assert img.dimensions == (512, 512)


def test_interpret_response_200_multiple_images() -> None:
    payload = {"media": [_make_media_item(name="a"), _make_media_item(name="b")]}
    resp = _resp(200, payload)
    images = interpret_response("test_strategy", resp)
    assert len(images) == 2
    assert {img.media_name for img in images} == {"a", "b"}


# ---------------------------------------------------------------------------
# interpret_response — error branches
# ---------------------------------------------------------------------------


def test_interpret_response_401_raises_auth_expired() -> None:
    resp = _resp(401, "Unauthorized")
    with pytest.raises(AuthExpiredError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_403_raises_waf_rejection() -> None:
    resp = _resp(403, "Forbidden")
    with pytest.raises(WafRejectionError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_429_raises_rate_limit() -> None:
    resp = _resp(429, "Too Many Requests")
    with pytest.raises(RateLimitError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_500_raises_network_error() -> None:
    resp = _resp(500, "Internal Server Error")
    with pytest.raises(NetworkError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_503_raises_network_error() -> None:
    resp = _resp(503, "Service Unavailable")
    with pytest.raises(NetworkError):
        interpret_response("test_strategy", resp)


def test_interpret_response_empty_media_raises_content_policy() -> None:
    resp = _resp(200, {"media": []})
    with pytest.raises(ContentPolicyError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_missing_media_key_raises_wire_format() -> None:
    resp = _resp(200, {"not_media": []})
    with pytest.raises(WireFormatError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_non_json_body_raises_wire_format() -> None:
    resp = _resp(200, "this is not json")
    # MagicMock auto-sets text; override with raw string
    resp.text = "this is not json"
    with pytest.raises(WireFormatError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_unexpected_status_raises_wire_format() -> None:
    resp = _resp(302, "redirect")
    with pytest.raises(WireFormatError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_strategy_name_in_403_message() -> None:
    """Strategy name must appear in error message for traceability."""
    resp = _resp(403, "denied")
    with pytest.raises(WafRejectionError) as exc_info:
        interpret_response("bearer_strategy", resp)
    assert "bearer_strategy" in str(exc_info.value)


def test_interpret_response_non_json_body_chained_from_json_decode_error() -> None:
    """WireFormatError for non-JSON must chain the original JSONDecodeError."""
    resp = _resp(200, "bad json {{")
    resp.text = "bad json {{"
    with pytest.raises(WireFormatError) as exc_info:
        interpret_response("s1", resp)
    assert exc_info.value.__cause__ is not None
    import json as _json

    assert isinstance(exc_info.value.__cause__, _json.JSONDecodeError)


# ---------------------------------------------------------------------------
# extract_project_id
# ---------------------------------------------------------------------------


from gflow_cli.api.transports._common import extract_project_id  # noqa: E402


def test_extract_project_id_from_flow_url() -> None:
    assert extract_project_id("https://labs.google/fx/tools/flow/project/abc-123?x=1") == "abc-123"


def test_extract_project_id_returns_none_for_gallery_url() -> None:
    assert extract_project_id("https://labs.google/fx/tools/flow") is None


# ---------------------------------------------------------------------------
# #341: response bodies are redacted BEFORE truncation into error messages
# ---------------------------------------------------------------------------


def test_interpret_response_403_redacts_bearer_token_in_body() -> None:
    body = "denied; auth was Bearer ya29.a0Af-verysecrettoken1234567890 for this call"
    with pytest.raises(WafRejectionError) as excinfo:
        interpret_response("sapisidhash", _resp(403, body))
    assert "ya29" not in str(excinfo.value)
    assert "<redacted:secret>" in str(excinfo.value)


def test_interpret_response_500_redacts_signed_url_in_body() -> None:
    body = "err at https://cdn.example/x?X-Goog-Signature=abcdef123456"
    with pytest.raises(NetworkError) as excinfo:
        interpret_response("bearer", _resp(500, body))
    assert "abcdef123456" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# #639: Flow's migration to flow.google.com — host classification
# ---------------------------------------------------------------------------


class TestFlowHostKind:
    """``flow_host_kind`` is the single place that knows which origins serve
    Flow. It is TOTAL: any input that is not a parseable https Flow URL returns
    ``None`` rather than raising, because both call sites read ``page.url`` on a
    best-effort path where a probe error must never abort the real diagnosis.
    """

    def test_labs_host_is_labs(self) -> None:
        assert flow_host_kind("https://labs.google/fx/tools/flow?hl=en") == "labs"

    def test_localised_labs_path_is_labs(self) -> None:
        assert flow_host_kind("https://labs.google/fx/pt/tools/flow/project/abc") == "labs"

    def test_migrated_host_is_migrated(self) -> None:
        assert flow_host_kind("https://flow.google.com/project/abc-123") == "migrated"

    def test_unknown_host_is_none(self) -> None:
        assert flow_host_kind("https://example.com/") is None

    def test_accounts_google_is_none(self) -> None:
        assert flow_host_kind("https://accounts.google.com/v3/signin/identifier") is None

    def test_host_match_is_exact_not_substring(self) -> None:
        """Security: the gate this replaces was ``"labs.google" in page.url``,
        which an attacker-controlled URL satisfies in a query string or path."""
        assert flow_host_kind("https://evil.example/?next=labs.google/fx/tools/flow") is None
        assert flow_host_kind("https://labs.google.evil.example/fx/tools/flow") is None
        assert flow_host_kind("https://flow.google.com.evil.example/project/x") is None

    def test_subdomain_of_labs_is_not_flow(self) -> None:
        assert flow_host_kind("https://cdn.labs.google/fx/tools/flow") is None

    def test_host_is_case_insensitive(self) -> None:
        assert flow_host_kind("https://FLOW.GOOGLE.COM/project/x") == "migrated"

    def test_non_https_is_none(self) -> None:
        assert flow_host_kind("http://flow.google.com/project/x") is None

    def test_non_string_input_is_none(self) -> None:
        """A MagicMock page whose ``.url`` was never set must not raise."""
        assert flow_host_kind(MagicMock()) is None
        assert flow_host_kind(None) is None

    def test_malformed_url_is_none(self) -> None:
        assert flow_host_kind("https://[oops") is None
        assert flow_host_kind("") is None
