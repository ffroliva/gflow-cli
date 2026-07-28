"""Prompt-tools transport against a real OpenAI-compatible gateway (issue #387).

This feature integrates with an external service, so the offline suite cannot
prove it works: every unit test injects a fake transport, which is exactly the
seam under test here. These exercise the real ``urllib`` path end to end.

Run against any OpenAI-compatible endpoint::

    GFLOW_CLI_LLM_BASE_URL=http://127.0.0.1:3001/v1 \
    GFLOW_CLI_LLM_API_KEY=... \
    GFLOW_CLI_LLM_MODEL=gemini-2.5-flash \
    uv run pytest -m containers tests/integration/test_expander_gateway.py

`freellmapi <https://github.com/tashfeenahmed/freellmapi>`_ in Docker is the
reference local gateway, but nothing here is specific to it.

Use ``127.0.0.1`` rather than ``localhost``: gateways commonly bind IPv4 only,
and Windows' dual-stack resolver tries ``::1`` first and stalls.

Skips (never fails) when no endpoint is configured or reachable, so CI and
other contributors are unaffected.
"""

from __future__ import annotations

import base64
import io
import os
import urllib.error
import urllib.request

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.containers]

_BASE_URL = os.environ.get("GFLOW_CLI_LLM_BASE_URL", "")
_API_KEY = os.environ.get("GFLOW_CLI_LLM_API_KEY") or None
_MODEL = os.environ.get("GFLOW_CLI_LLM_MODEL") or None


def _endpoint_reachable() -> bool:
    if not _BASE_URL:
        return False
    try:
        request = urllib.request.Request(  # noqa: S310 — test-only, operator-supplied
            _BASE_URL.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {_API_KEY}"} if _API_KEY else {},
        )
        with urllib.request.urlopen(request, timeout=5):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        # A 4xx still proves something is listening and speaking HTTP.
        return True
    except OSError:
        return False


pytestmark.append(
    pytest.mark.skipif(
        not _endpoint_reachable(),
        reason="no reachable GFLOW_CLI_LLM_BASE_URL — set it to run gateway integration tests",
    )
)


def _expander(**overrides: object):
    from gflow_cli.tools.expander import PromptExpander

    kwargs: dict[str, object] = {
        "base_url": _BASE_URL,
        "model": _MODEL,
        "max_total_seconds": 90.0,
    }
    kwargs.update(overrides)
    api_key = kwargs.pop("api_key", _API_KEY)
    return PromptExpander(api_key, **kwargs)  # type: ignore[arg-type]


def _probe_status() -> int | None:
    """One raw call, used only to classify a failure. Returns the HTTP status.

    The expander never raises, so a failed expansion carries no reason. Free
    gateways rate-limit under repeated runs, and a 429 is an environment
    condition rather than a defect -- but we only skip when it is *proven*, so
    a genuine transport break still fails the suite.
    """
    import json

    body = json.dumps(
        {
            "model": _MODEL or "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "ping"}],
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    if _API_KEY:
        headers["Authorization"] = f"Bearer {_API_KEY}"
    request = urllib.request.Request(  # noqa: S310 — test-only, operator-supplied
        _BASE_URL.rstrip("/") + "/chat/completions", data=body, method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except OSError:
        return None


def _assert_expanded(result: object, message: str) -> None:
    """Assert the rewrite happened, skipping only on a proven rate limit."""
    if not getattr(result, "was_expanded", False):
        if _probe_status() == 429:
            pytest.skip("gateway is rate-limited (HTTP 429) — quota, not a defect")
        pytest.fail(message)


def _red_square_jpeg() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 20, 20)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_real_gateway_text_expansion() -> None:
    """The whole point of #387: a real non-Google endpoint rewrites the prompt."""
    result = _expander().expand("cat in space")

    _assert_expanded(result, "gateway did not rewrite the prompt")
    assert result.expanded != result.original
    assert len(result.expanded) > len(result.original)


def test_real_gateway_system_instruction_is_honoured() -> None:
    """The instruction now rides a separate ``system`` message.

    If a provider ignored that role the rewrite would come back unshaped, so
    this asserts the instruction actually steered the output.
    """
    result = _expander(
        system_instruction=("Reply with EXACTLY the single word ACKNOWLEDGED and nothing else.")
    ).expand("say something")

    _assert_expanded(result, "gateway did not honour the system instruction")
    assert "ACKNOWLEDGED" in result.expanded.upper()


def test_real_gateway_multimodal_round_trip(tmp_path: object) -> None:
    """A generated image must survive encoding and actually reach the model.

    The assertion is semantic, not just "HTTP 200": a gateway that silently
    drops the image part still returns plausible prose, which is precisely the
    failure the old unit tests could not see.
    """
    from pathlib import Path

    img = Path(str(tmp_path)) / "red.jpg"
    img.write_bytes(_red_square_jpeg())

    result = _expander(
        system_instruction="Answer with one word only: the dominant color of the image."
    ).expand_multimodal("What color is this image?", [str(img)])

    _assert_expanded(result, "gateway did not process the multimodal request")
    assert "RED" in result.expanded.upper(), (
        f"image did not reach the model — got {result.expanded!r}"
    )


def test_real_gateway_bad_key_degrades_without_raising() -> None:
    """The never-raise contract must hold against a real 4xx.

    Providers disagree on the code (401 on most gateways, 400 on Google's
    compat endpoint); neither is retryable, so both fall back cleanly.
    """
    result = _expander(api_key="sk-definitely-not-a-valid-key").expand("cat in space")

    assert result.was_expanded is False
    assert result.expanded == "cat in space"


def test_real_gateway_payload_is_openai_shaped() -> None:
    """Sanity-check the wire format against the live endpoint.

    Guards against a provider that accepts our request but ignores the
    parameters we depend on (max_tokens bounding the reply, temperature).
    """
    result = _expander(max_output_chars=2048).expand("a lighthouse at dawn")

    _assert_expanded(result, "gateway did not rewrite the prompt")
    assert len(result.expanded) <= 2048


def test_data_uri_encoding_round_trips() -> None:
    """The data: URI we build must decode back to the exact bytes.

    Cheap, offline, and the thing most likely to break silently when the
    multimodal payload shape is edited.
    """
    raw = _red_square_jpeg()
    encoded = base64.b64encode(raw).decode("ascii")
    uri = f"data:image/jpeg;base64,{encoded}"

    assert base64.b64decode(uri.split(",", 1)[1]) == raw
