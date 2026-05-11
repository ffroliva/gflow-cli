"""FlowTransportStrategy Protocol shape tests."""
from __future__ import annotations

import inspect

from gflow_cli.api.transports.base import FlowTransportStrategy


def test_protocol_has_required_methods():
    members = dict(inspect.getmembers(FlowTransportStrategy))
    for method in ("setup", "refresh_auth", "generate_images", "teardown"):
        assert method in members, f"FlowTransportStrategy missing {method}"


def test_protocol_setup_signature():
    sig = inspect.signature(FlowTransportStrategy.setup)
    assert "profile_dir" in sig.parameters
    # from __future__ import annotations makes annotations lazy strings
    annotation = sig.parameters["profile_dir"].annotation
    assert annotation == "Path" or (hasattr(annotation, "__name__") and annotation.__name__ == "Path")


def test_protocol_generate_images_signature_omits_recaptcha_token():
    """Per spec § 4.1 — recaptcha_token lives in GenerateImageRequest, not the Protocol."""
    sig = inspect.signature(FlowTransportStrategy.generate_images)
    assert "recaptcha_token" not in sig.parameters
    assert "request" in sig.parameters
    assert "project_id" in sig.parameters
