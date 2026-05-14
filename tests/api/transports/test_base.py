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
    is_lazy_string = annotation == "Path"
    is_class_obj = hasattr(annotation, "__name__") and annotation.__name__ == "Path"
    assert is_lazy_string or is_class_obj


def test_protocol_setup_accepts_page_kwarg():
    """setup() must accept an optional `page` keyword argument (S1 shared-page fix)."""
    sig = inspect.signature(FlowTransportStrategy.setup)
    assert "page" in sig.parameters, "setup() must have a `page` kwarg for the shared-page fix"
    param = sig.parameters["page"]
    # Must be keyword-only (defined after *)
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    # Must have a default of None (optional — back-compat for direct callers)
    assert param.default is None


def test_protocol_generate_images_signature_omits_recaptcha_token():
    """Per spec § 4.1 — recaptcha_token lives in GenerateImageRequest, not the Protocol."""
    sig = inspect.signature(FlowTransportStrategy.generate_images)
    assert "recaptcha_token" not in sig.parameters
    assert "request" in sig.parameters
    assert "project_id" in sig.parameters
