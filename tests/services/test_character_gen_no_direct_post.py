"""Scenario #1 guardrail: character generation NEVER posts directly to a Flow
generation REST endpoint.

WHY this matters (Option B / 403 wall):
    Flow's image/video *generation* endpoints (``batchAsyncGenerateImage`` etc.)
    are reCAPTCHA/WAF-walled — a direct server-side ``POST`` is answered with
    HTTP 403 (mapped to ``WafRejectionError``; see commit 65c6393). The character
    feature therefore uses **Option B passive UI capture**: generation is driven
    through the UI-automation transport (``transport.generate_character_images``),
    and the captured workflow is read back. If a future refactor ever routed
    character generation through ``_post_json``/``batch_generate_images_url``, it
    would silently 403 in production while unit tests over fakes stayed green.

This module proves the invariant two independent ways so it cannot become
vacuous:

  1. **Runtime spy** — drive the *real* ``character_create`` saga through the
     *real* ``FlowApiClient.generate_character_image`` against a fake transport,
     with ``client._post_json`` and ``routes.batch_generate_images_url`` patched
     to record-and-explode. Assert generation went through the transport and the
     generation REST path was NEVER touched during character gen.

  2. **Source scan** — assert the saga module and the ``generate_character_image``
     method body contain no literal call to ``batch_generate_images_url`` or a
     ``_post_json`` generation POST. A static guard catches a regression even if
     someone adds a code path the runtime test doesn't exercise.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import gflow_cli.api.client as client_mod
from gflow_cli.api.character import CharacterImageRequest
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.services.character_create import character_create

# ---------------------------------------------------------------------------
# Load the LIVE character-gen fixture (same one the api-layer test uses)
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent.parent / "api" / "fixtures" / "character_gen_response.json"
_FIXTURE: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_FIXTURE_WORKFLOWS: list[dict[str, Any]] = _FIXTURE["workflows"]
_ENTITY_ID: str = _FIXTURE_WORKFLOWS[0]["parentEntityId"]
_WORKFLOW_ID: str = _FIXTURE_WORKFLOWS[0]["name"]
_MEDIA_ID: str = _FIXTURE_WORKFLOWS[0]["metadata"]["primaryMediaId"]
_PROJECT_ID: str = _FIXTURE_WORKFLOWS[0]["projectId"]
_NAME = "Knight"


# ---------------------------------------------------------------------------
# Fake transport that records it was the path taken (UI capture)
# ---------------------------------------------------------------------------


class _SpyTransport:
    name = "spy-char"

    def __init__(self) -> None:
        self.generate_character_calls: int = 0
        self.generate_images_calls: int = 0

    async def setup(self, profile_dir: Path, **_: Any) -> None:  # noqa: ARG002
        pass

    async def refresh_auth(self) -> None:
        pass

    async def teardown(self) -> None:
        pass

    async def generate_images(self, **_: Any) -> list[GeneratedImage]:
        # The plain image path — must NEVER be used for character gen.
        self.generate_images_calls += 1
        return []

    async def generate_character_images(
        self,
        *,
        project_id: str,  # noqa: ARG002
        entity_id: str,  # noqa: ARG002
        request: Any,  # noqa: ARG002
        image_reference_index: int,  # noqa: ARG002
        locale: str,  # noqa: ARG002
    ) -> tuple[list[GeneratedImage], list[dict[str, Any]]]:
        self.generate_character_calls += 1
        return ([], list(_FIXTURE_WORKFLOWS))


def _make_recorder() -> MagicMock:
    recorder = MagicMock()
    recorder.record_character_started = MagicMock(return_value="row-1")
    recorder.record_character_partial = MagicMock(return_value=None)
    recorder.record_character_completed = MagicMock(return_value=None)
    recorder.repository = MagicMock()
    recorder.repository.find_incomplete_character = MagicMock(return_value=None)
    return recorder


# ---------------------------------------------------------------------------
# 1) Runtime spy test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_character_gen_routes_through_transport_not_direct_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real saga + real generate_character_image must route through the UI
    transport and never call the generation REST endpoint."""
    transport = _SpyTransport()
    client = FlowApiClient(profile_dir=tmp_path / "prof", transport=transport)
    client.transport = transport
    client._page = MagicMock()

    # Spy on the generation REST seam. _post_json is the only door to any REST
    # POST; batch_generate_images_url is the generation route builder. If
    # character gen ever touches either during generation, explode.
    post_json_urls: list[str] = []

    async def _exploding_post_json(route: str, *_a: Any, **_k: Any) -> Any:
        post_json_urls.append(route)
        raise AssertionError(
            f"character generation made a direct REST POST to {route!r} — "
            "Option B forbids this (403 WAF wall)."
        )

    gen_url_calls: list[str] = []

    def _exploding_gen_url(project_id: str) -> str:
        gen_url_calls.append(project_id)
        raise AssertionError(
            "character generation built a batch_generate_images_url — "
            "Option B forbids the generation REST path (403 WAF wall)."
        )

    monkeypatch.setattr(client, "_post_json", _exploding_post_json)
    monkeypatch.setattr(client_mod.routes, "batch_generate_images_url", _exploding_gen_url)

    # commit_workflow / patch_entity are NON-generation REST ops (free Bearer
    # PATCH/commit). They are allowed; stub them so the saga completes without a
    # real browser/network. We assert specifically that the GENERATION path was
    # untouched, not that the client made zero REST calls.
    client.commit_workflow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    client.patch_entity = AsyncMock(return_value=None)  # type: ignore[method-assign]
    client.create_entity = AsyncMock(return_value=_ENTITY_ID)  # type: ignore[method-assign]

    recorder = _make_recorder()
    face = CharacterImageRequest(prompt="a knight face", model="nano2")

    result = await character_create(
        client,
        recorder,
        profile_name="default",
        profile_dir=tmp_path / "prof",
        project_id=_PROJECT_ID,
        name=_NAME,
        face=face,
        body=None,
        voice=None,
        personality=None,
        locale="en-US",
    )

    # Generation routed through the UI transport exactly once (the face).
    assert transport.generate_character_calls == 1
    # The plain-image transport path was never used.
    assert transport.generate_images_calls == 0
    # The generation REST path was NEVER touched.
    assert post_json_urls == [], f"unexpected direct POSTs: {post_json_urls}"
    assert gen_url_calls == [], f"unexpected generation-url builds: {gen_url_calls}"
    # And the captured workflow id flowed through to the result.
    assert result.workflow_ids == (_WORKFLOW_ID,)
    assert result.primary_media_ids == (_MEDIA_ID,)


# ---------------------------------------------------------------------------
# 2) Source-scan invariant
# ---------------------------------------------------------------------------

_SRC = Path(client_mod.__file__).parent
_SAGA_SRC = _SRC.parent / "services" / "character_create.py"


def _extract_method_body(source: str, method_name: str) -> str:
    """Return the source lines of ``async def <method_name>`` up to (but not
    including) the next top-level ``def``/``class`` at the same or lower indent."""
    lines = source.splitlines()
    start = None
    indent = 0
    for i, line in enumerate(lines):
        m = re.match(rf"(\s*)async def {re.escape(method_name)}\b", line)
        if m:
            start = i
            indent = len(m.group(1))
            break
    assert start is not None, f"{method_name} not found in source"
    body: list[str] = [lines[start]]
    for line in lines[start + 1 :]:
        if (
            line.strip()
            and (len(line) - len(line.lstrip())) <= indent
            and re.match(r"\s*(async def|def|class)\b", line)
        ):
            break
        body.append(line)
    return "\n".join(body)


def test_saga_source_has_no_generation_rest_call() -> None:
    """The saga must not reference the generation REST route builder nor a raw
    _post_json — it may only call client.generate_character_image (UI path)."""
    src = _SAGA_SRC.read_text(encoding="utf-8")
    assert "batch_generate_images_url" not in src, (
        "saga references the generation REST route — Option B violation"
    )
    assert "_post_json" not in src, "saga calls a raw REST POST — Option B violation"
    # Positive assertion: it DOES use the sanctioned UI gen entrypoint.
    assert "generate_character_image" in src


def test_generate_character_image_body_has_no_generation_post() -> None:
    """The generate_character_image method body must route only through the
    transport — never _post_json or the generation-route builder."""
    src = Path(client_mod.__file__).read_text(encoding="utf-8")
    body = _extract_method_body(src, "generate_character_image")
    assert "_post_json" not in body, (
        "generate_character_image makes a direct REST POST — Option B violation"
    )
    assert "batch_generate_images_url" not in body, (
        "generate_character_image builds a generation REST url — Option B violation"
    )
    # Positive: it calls the UI transport entrypoint.
    assert "generate_character_images" in body
