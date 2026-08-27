"""Tests for FlowApiClient.generate_character_image (issue #145, Task 5).

Covers:
  #1  — generation routes through the UI transport (never a direct POST)
  #5  — parentEntityId mismatch → WireFormatError, workflow NOT committed
  transport-None safety net — transport is None raises RuntimeError before any
         transport call (method-level guard only; see note on scenario #21 below)

NOTE — Scenario #21 (non-Chrome / headless profile rejection):
  That guard lives upstream in ``FlowApiClient.__aenter__`` at browser-launch
  time via ``channel_for_profile()`` + ``launch_persistent_context()``.
  The closest unit-level coverage is in ``tests/test_browser_manager.py``
  (``TestRaceLossWinnerVerification``, ``TestGetOrLaunchBrowserNoLockAttach``,
  etc.) which assert ``ConfigurationError`` from the browser-manager layer.
  A full ``__aenter__``-level unit test would require mocking Playwright's
  ``launch_persistent_context`` to raise on a non-Chrome channel — out of
  scope for this method-level module.

Mirrors the fake-transport injection pattern from test_client_image.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from gflow_cli.api.character import CharacterImageRequest
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.errors import WireFormatError

# ---------------------------------------------------------------------------
# Load the LIVE fixture
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "character_gen_response.json"
_FIXTURE: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

_FIXTURE_WORKFLOWS: list[dict[str, Any]] = _FIXTURE["workflows"]
_ENTITY_ID = _FIXTURE_WORKFLOWS[0]["parentEntityId"]  # "d73ef41a-5fa0-4cef-af3f-ee9f8b20390f"
_WORKFLOW_ID = _FIXTURE_WORKFLOWS[0]["name"]  # "fed25ab9-..."
_PRIMARY_MEDIA_ID = _FIXTURE_WORKFLOWS[0]["metadata"]["primaryMediaId"]  # "542e49ba-..."
_PROJECT_ID = _FIXTURE_WORKFLOWS[0]["projectId"]


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


class _FakeCharTransport:
    """Minimal transport stub for generate_character_image tests."""

    name = "fake-char"

    def __init__(
        self,
        images: list[GeneratedImage] | None = None,
        workflows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._images: list[GeneratedImage] = images or []
        self._workflows: list[dict[str, Any]] = (
            workflows if workflows is not None else list(_FIXTURE_WORKFLOWS)
        )
        self.calls: list[dict[str, Any]] = []
        # Counters for verifying no direct-POST path was taken
        self.post_json_calls: int = 0

    async def setup(self, profile_dir: Path, **_: Any) -> None:  # noqa: ARG002
        pass

    async def refresh_auth(self) -> None:
        pass

    async def teardown(self) -> None:
        pass

    async def generate_images(self, **_: Any) -> list[GeneratedImage]:
        """Should never be called by generate_character_image."""
        self.post_json_calls += 1
        return list(self._images)

    async def generate_character_images(
        self,
        *,
        project_id: str,
        entity_id: str,
        request: Any,
        image_reference_index: int,
        locale: str,
        format_prompt: bool = False,
    ) -> tuple[list[GeneratedImage], list[dict[str, Any]]]:
        self.calls.append(
            {
                "project_id": project_id,
                "entity_id": entity_id,
                "request": request,
                "image_reference_index": image_reference_index,
                "locale": locale,
                "format_prompt": format_prompt,
            }
        )
        return (list(self._images), list(self._workflows))


# ---------------------------------------------------------------------------
# Helper: build a pre-wired client (no Playwright lifecycle)
# ---------------------------------------------------------------------------


def _client_with_transport(
    tmp_path: Path,
    transport: _FakeCharTransport,
) -> FlowApiClient:
    """Build a FlowApiClient with a caller-owned fake transport pre-injected."""
    c = FlowApiClient(profile_dir=tmp_path / "prof", transport=transport)
    c.transport = transport
    c._page = MagicMock()
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGenerateCharacterImage:
    """Scenario #1: generation routes through the UI transport (never a direct POST)."""

    async def test_happy_returns_workflow_and_media(self, tmp_path: Path) -> None:
        """generate_character_image returns (workflow_id, media_id) from the fixture
        and proves it called the UI transport (not a direct _post_json / batch endpoint).
        """
        transport = _FakeCharTransport()
        client = _client_with_transport(tmp_path, transport)

        req = CharacterImageRequest(prompt="portrait of an adult character")
        workflow_id, media_id, local_path = await client.generate_character_image(
            project_id=_PROJECT_ID,
            entity_id=_ENTITY_ID,
            req=req,
            image_reference_index=0,
        )

        # Correct return values extracted from fixture
        assert workflow_id == _WORKFLOW_ID
        assert media_id == _PRIMARY_MEDIA_ID
        # No images in the default fake transport → nothing to download → None
        assert local_path is None

        # Scenario #1: transport WAS called via the UI path
        assert len(transport.calls) == 1
        assert transport.calls[0]["entity_id"] == _ENTITY_ID
        assert transport.calls[0]["project_id"] == _PROJECT_ID

        # Scenario #1: no direct POST (batch_generate_images) was called
        assert transport.post_json_calls == 0

    async def test_locale_is_forwarded_to_transport(self, tmp_path: Path) -> None:
        """Explicit locale is forwarded verbatim to generate_character_images."""
        transport = _FakeCharTransport()
        client = _client_with_transport(tmp_path, transport)

        req = CharacterImageRequest(prompt="test")
        await client.generate_character_image(
            project_id=_PROJECT_ID,
            entity_id=_ENTITY_ID,
            req=req,
            image_reference_index=0,
            locale="fr-FR",
        )

        assert transport.calls[0]["locale"] == "fr-FR"

    async def test_omitted_locale_uses_the_resolved_account_locale(self, tmp_path: Path) -> None:
        """Omitting locale must use the ACCOUNT's locale, not 'en-US' (#580).

        This test previously asserted a default of ``en-US``. That default was the
        bug: on a pt-BR account it routes the character editor to ``/fx/en/...``,
        and Flow's correcting redirect lands *after* ``page.goto`` returns —
        bouncing the page out from under the prompt submit. That is how #395
        presented ("character-route bounce sent the prompt to the project
        composer").
        """
        transport = _FakeCharTransport()
        client = _client_with_transport(tmp_path, transport)
        client._account_locale = "pt"

        await client.generate_character_image(
            project_id=_PROJECT_ID,
            entity_id=_ENTITY_ID,
            req=CharacterImageRequest(prompt="test"),
            image_reference_index=0,
        )

        assert transport.calls[0]["locale"] == "pt"

    async def test_unresolved_account_locale_passes_none_not_en(self, tmp_path: Path) -> None:
        """Unresolved locale => None => bare URL. Never a guessed 'en'."""
        transport = _FakeCharTransport()
        client = _client_with_transport(tmp_path, transport)
        assert client._account_locale is None

        await client.generate_character_image(
            project_id=_PROJECT_ID,
            entity_id=_ENTITY_ID,
            req=CharacterImageRequest(prompt="test"),
            image_reference_index=0,
        )

        assert transport.calls[0]["locale"] is None


_SIGNED_FIFE_URL = (
    "https://lh3.googleusercontent.com/flow-content/abc123"
    "=w1024-h1024?Expires=9999999999&Signature=SECRETSIGNATURE_DEADBEEF"
)


def _make_generated_image(fife_url: str = _SIGNED_FIFE_URL) -> GeneratedImage:
    """A GeneratedImage carrying a signed CDN URL (slot 0 / face)."""
    return GeneratedImage(
        media_name=_PRIMARY_MEDIA_ID,
        workflow_id=_WORKFLOW_ID,
        seed=42,
        prompt="portrait",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url=fife_url,
        dimensions=(1024, 1024),
    )


class TestGenerateCharacterImageDownload:
    """The generated image is downloaded INSIDE the client; only the local path
    (never the signed fifeUrl) is returned to the caller (scenario #16)."""

    async def test_downloads_first_image_and_returns_local_path(self, tmp_path: Path) -> None:
        """generate_character_image downloads images[0] via download_image and
        returns the saved local path as the 3rd tuple element."""
        image = _make_generated_image()
        transport = _FakeCharTransport(images=[image])
        client = _client_with_transport(tmp_path, transport)

        saved = tmp_path / "characters" / "character_x_slot0.png"
        download_calls: list[tuple[GeneratedImage, Path]] = []

        async def _fake_download(img: GeneratedImage, out_path: Path) -> Path:
            download_calls.append((img, out_path))
            return saved

        client.download_image = _fake_download  # type: ignore[method-assign]

        req = CharacterImageRequest(prompt="portrait")
        workflow_id, media_id, local_path = await client.generate_character_image(
            project_id=_PROJECT_ID,
            entity_id=_ENTITY_ID,
            req=req,
            image_reference_index=0,
        )

        assert workflow_id == _WORKFLOW_ID
        assert media_id == _PRIMARY_MEDIA_ID
        # The local saved path is returned — NOT the signed URL.
        assert local_path == saved

        # download_image was called once with the image carrying the fife_url.
        assert len(download_calls) == 1
        called_img, called_out = download_calls[0]
        assert called_img is image
        # The slot index is encoded in the output filename.
        assert "slot0" in called_out.name

    async def test_signed_url_never_returned_to_caller(self, tmp_path: Path) -> None:
        """The signed fifeUrl must NEVER appear in the returned tuple — only the
        download_image call (inside the client) ever sees it (scenario #16)."""
        image = _make_generated_image()
        transport = _FakeCharTransport(images=[image])
        client = _client_with_transport(tmp_path, transport)

        saved = tmp_path / "characters" / "character_x_slot0.png"

        async def _fake_download(img: GeneratedImage, out_path: Path) -> Path:  # noqa: ARG001
            return saved

        client.download_image = _fake_download  # type: ignore[method-assign]

        req = CharacterImageRequest(prompt="portrait")
        result = await client.generate_character_image(
            project_id=_PROJECT_ID,
            entity_id=_ENTITY_ID,
            req=req,
            image_reference_index=0,
        )

        # No element of the returned tuple is (or contains) the signed URL.
        for element in result:
            assert "Signature=" not in str(element)
            assert "Expires=" not in str(element)
            assert _SIGNED_FIFE_URL not in str(element)

    async def test_no_image_returns_none_path_and_does_not_crash(self, tmp_path: Path) -> None:
        """When the transport returns no images, local_path is None (warning
        logged) and the ids still flow through — no crash."""
        transport = _FakeCharTransport(images=[])
        client = _client_with_transport(tmp_path, transport)

        download_called = False

        async def _fake_download(img: GeneratedImage, out_path: Path) -> Path:  # noqa: ARG001
            nonlocal download_called
            download_called = True
            return tmp_path / "never.png"

        client.download_image = _fake_download  # type: ignore[method-assign]

        req = CharacterImageRequest(prompt="portrait")
        workflow_id, media_id, local_path = await client.generate_character_image(
            project_id=_PROJECT_ID,
            entity_id=_ENTITY_ID,
            req=req,
            image_reference_index=0,
        )

        assert workflow_id == _WORKFLOW_ID
        assert media_id == _PRIMARY_MEDIA_ID
        assert local_path is None
        assert download_called is False, "download_image must not be called with no image"


class TestGenerateCharacterImageEntityGuard:
    """Scenario #5: parentEntityId mismatch must raise WireFormatError."""

    async def test_rejects_foreign_workflow(self, tmp_path: Path) -> None:
        """A workflow whose parentEntityId != requested entity_id raises WireFormatError.

        The method must NOT return (workflow_id, media_id) — it must raise.
        Scenario #5: never commit/PATCH a foreign workflow.
        """
        foreign_workflows = [
            {
                **_FIXTURE_WORKFLOWS[0],
                "parentEntityId": "aaaaaaaa-0000-0000-0000-000000000000",
            }
        ]
        transport = _FakeCharTransport(workflows=foreign_workflows)
        client = _client_with_transport(tmp_path, transport)

        req = CharacterImageRequest(prompt="test")
        with pytest.raises(WireFormatError) as exc_info:
            await client.generate_character_image(
                project_id=_PROJECT_ID,
                entity_id=_ENTITY_ID,
                req=req,
                image_reference_index=0,
            )

        # Pin the MEANING, not just "an error happened": the message must name
        # the entity that was not bound and say the image is unattached, so a
        # reader isn't sent hunting a wire-format parser bug (live 2026-07-27).
        detail = exc_info.value.detail or ""
        assert _ENTITY_ID in detail, detail
        assert "did not bind" in detail, detail

    async def test_missing_parent_entity_id_raises(self, tmp_path: Path) -> None:
        """A workflow missing parentEntityId entirely also raises WireFormatError.

        Scenario #5 edge: absent key is as bad as a mismatch.
        """
        workflows_no_parent = [
            {k: v for k, v in _FIXTURE_WORKFLOWS[0].items() if k != "parentEntityId"}
        ]
        transport = _FakeCharTransport(workflows=workflows_no_parent)
        client = _client_with_transport(tmp_path, transport)

        req = CharacterImageRequest(prompt="test")
        with pytest.raises(WireFormatError) as exc_info:
            await client.generate_character_image(
                project_id=_PROJECT_ID,
                entity_id=_ENTITY_ID,
                req=req,
                image_reference_index=0,
            )

        # This is the shape Flow actually returned live on 2026-07-27 — the key
        # is absent entirely — so the message must distinguish "omitted" from a
        # mismatched value.
        assert "omitted it entirely" in (exc_info.value.detail or ""), exc_info.value.detail

    async def test_empty_workflows_raises(self, tmp_path: Path) -> None:
        """If transport returns no workflows, raise WireFormatError (no binding possible)."""
        transport = _FakeCharTransport(workflows=[])
        client = _client_with_transport(tmp_path, transport)

        req = CharacterImageRequest(prompt="test")
        with pytest.raises(WireFormatError):
            await client.generate_character_image(
                project_id=_PROJECT_ID,
                entity_id=_ENTITY_ID,
                req=req,
                image_reference_index=0,
            )


class TestGenerateCharacterImageTransportNoneGuard:
    """Method-level transport-None safety net.

    ``generate_character_image`` raises ``RuntimeError`` immediately when
    ``self.transport is None`` — i.e. the client was not entered via
    ``async with`` (or was entered but transport setup failed).

    This is a method-level safety net, NOT the non-Chrome/headless guard.
    Scenario #21 (non-Chrome / headless profile → ConfigurationError) is
    enforced upstream in ``FlowApiClient.__aenter__`` at browser-launch time;
    see the module-level docstring for the reference tests.
    """

    async def test_transport_none_raises_runtime_error_before_transport_call(
        self, tmp_path: Path
    ) -> None:
        """When transport is None, RuntimeError is raised before any transport call.

        The method-level guard (``if self.transport is None: raise RuntimeError``)
        must fire immediately — no call to ``generate_character_images`` may occur.
        """
        c = FlowApiClient(profile_dir=tmp_path / "prof")
        c.transport = None  # type: ignore[assignment]
        c._page = MagicMock()

        req = CharacterImageRequest(prompt="test")
        with pytest.raises(RuntimeError, match="transport is None"):
            await c.generate_character_image(
                project_id=_PROJECT_ID,
                entity_id=_ENTITY_ID,
                req=req,
                image_reference_index=0,
            )

    async def test_transport_none_guard_fires_before_transport_call_second_variant(
        self, tmp_path: Path
    ) -> None:
        """transport forcibly set to None after construction: RuntimeError fires
        before the transport's generate_character_images is ever invoked.
        """
        transport = _FakeCharTransport()
        client = _client_with_transport(tmp_path, transport)
        # Wipe the transport to simulate 'client used outside async-with'
        client.transport = None  # type: ignore[assignment]

        req = CharacterImageRequest(prompt="test")
        with pytest.raises(RuntimeError, match="transport is None"):
            await client.generate_character_image(
                project_id=_PROJECT_ID,
                entity_id=_ENTITY_ID,
                req=req,
                image_reference_index=0,
            )

        # The fake transport was never touched
        assert transport.calls == [], "transport must NOT have been called"
