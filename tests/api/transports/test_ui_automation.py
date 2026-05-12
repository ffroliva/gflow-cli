"""Tests for D.2.4 UiAutomationTransport — TDD.

Mirrors the empirically-validated ``scripts/smoke_worker_style.py`` flow:
Playwright persistent-context launch (internal random CDP port — no public
debug port exposed), UI-driven prompt submission against the Flow editor,
``page.on("response")`` capture of the ``batchGenerateImages`` payload, and
URL extraction from ``media[].image.generatedImage.fifeUrl``.

Each test pins ONE Protocol method's behavior. The implementation lives at
``src/gflow_cli/api/transports/ui_automation.py``.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.ui_automation import UiAutomationTransport

# ---------------------------------------------------------------------------
# Helpers — shared across units
# ---------------------------------------------------------------------------


def _req(prompt: str = "a calm forest at dawn") -> GenerateImageRequest:
    """Build a minimal GenerateImageRequest for ui_automation tests."""
    return GenerateImageRequest(
        prompt=prompt,
        model=Model.NARWHAL,
        aspect=Aspect.PORTRAIT,
        recaptcha_token="not_used_by_ui_automation",
    )


def _flow_200_body() -> dict:
    """Minimal valid batchGenerateImages 200 body (matches real wire shape)."""
    return {
        "media": [
            {
                "name": "projects/proj-uuid/assets/asset-001",
                "workflowId": "wf-001",
                "image": {
                    "generatedImage": {
                        "seed": 42,
                        "prompt": "a calm forest at dawn",
                        "modelNameType": "NARWHAL",
                        "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT",
                        "fifeUrl": "https://lh3.googleusercontent.com/abc123",
                    },
                    "dimensions": {"width": 576, "height": 1024},
                },
            }
        ],
        "workflows": [],
    }


# ---------------------------------------------------------------------------
# Unit 3.1 — Module + Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """UiAutomationTransport must satisfy FlowTransportStrategy."""

    def test_name_attribute_is_ui_automation(self) -> None:
        """Strategy is identified by the registry key 'ui_automation'."""
        assert UiAutomationTransport.name == "ui_automation"

    def test_setup_signature(self) -> None:
        """setup(profile_dir, *, page=None) — matches Protocol § 4.1."""
        sig = inspect.signature(UiAutomationTransport.setup)
        params = sig.parameters
        assert "profile_dir" in params
        assert "page" in params
        assert params["page"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["page"].default is None
        assert inspect.iscoroutinefunction(UiAutomationTransport.setup)

    def test_refresh_auth_signature(self) -> None:
        """refresh_auth() — async, no args beyond self."""
        sig = inspect.signature(UiAutomationTransport.refresh_auth)
        # Only 'self'.
        assert list(sig.parameters) == ["self"]
        assert inspect.iscoroutinefunction(UiAutomationTransport.refresh_auth)

    def test_generate_images_signature(self) -> None:
        """generate_images(*, project_id, request) — async, kwargs-only."""
        sig = inspect.signature(UiAutomationTransport.generate_images)
        params = sig.parameters
        assert "project_id" in params
        assert "request" in params
        assert params["project_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["request"].kind == inspect.Parameter.KEYWORD_ONLY
        assert inspect.iscoroutinefunction(UiAutomationTransport.generate_images)

    def test_teardown_signature(self) -> None:
        """teardown() — async, no args beyond self, idempotent."""
        sig = inspect.signature(UiAutomationTransport.teardown)
        assert list(sig.parameters) == ["self"]
        assert inspect.iscoroutinefunction(UiAutomationTransport.teardown)

    @pytest.mark.asyncio
    async def test_methods_raise_not_implemented_in_skeleton(self) -> None:
        """Skeleton stage — methods exist but raise NotImplementedError until
        the per-method TDD units land them."""
        t = UiAutomationTransport()
        with pytest.raises(NotImplementedError):
            await t.setup(MagicMock())
        with pytest.raises(NotImplementedError):
            await t.refresh_auth()
        with pytest.raises(NotImplementedError):
            await t.generate_images(project_id="x", request=_req())
        with pytest.raises(NotImplementedError):
            await t.teardown()
