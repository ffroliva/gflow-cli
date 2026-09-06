"""The migrated host answers on its own wire, so the ENTITY is the result (#703).

Live 2026-09-06 on ``ci-probe`` (a moved account), a character portrait generation
completed end to end on ``flow.google.com`` — and gflow reported failure. The
driver reached the editor, selected the model, typed the prompt and submitted;
then it waited 180 s for ``aisandbox .../flowMedia:batchGenerateImages``, the
**labs** wire, which that frontend never calls. Reading the entity back from the
backend immediately afterwards returned::

    {"workflow_ids": ["04654114-…"], "thumbnail_media_id": "7e27413f-…"}

Both ids the saga needs, already bound to the entity, while the client raised
``TimeoutError``. The fix is not to decode ``flow.google.com``'s ``batchexecute``
rpcid ``ogiZ0b`` envelope — it is to ask the backend what it did. The ids come
from the entity itself, so binding is proven **by construction**, which is a
stronger guarantee than the labs path's self-reported ``parentEntityId``.

Evidence: ``scripts/dev/spike_migrated_character_ogiz0b_schema.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.character import Character, CharacterImageRequest

PROJECT = "proj-1"
ENTITY = "ent-1"
WF0 = "04654114-5889-4014-baac-8daeb406f5d3"
MEDIA0 = "7e27413f-1841-440e-88ab-bb54e34d8e2d"

REQ = CharacterImageRequest(prompt="a calm portrait", model="nano2")


def _character(*, workflow_ids: tuple[str, ...], thumb: str | None) -> Character:
    return Character(
        entity_id=ENTITY,
        display_name="Untitled Character",
        project_id=PROJECT,
        workflow_ids=workflow_ids,
        voice=None,
        personality=None,
        thumbnail_media_id=thumb,
    )


def _client(monkeypatch: pytest.MonkeyPatch, *, transport_exc: Exception | None) -> Any:
    """A FlowApiClient with its transport stubbed and REST reads faked."""
    from gflow_cli.api.client import FlowApiClient

    client = FlowApiClient.__new__(FlowApiClient)
    client._account_locale = "en"  # noqa: SLF001
    transport = MagicMock()
    if transport_exc is not None:
        transport.generate_character_images = AsyncMock(side_effect=transport_exc)
    client.transport = transport
    return client


@pytest.mark.asyncio
async def test_labs_wire_timeout_falls_back_to_reading_the_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout on the labs wire is not a failure if the backend bound the work."""
    client = _client(monkeypatch, transport_exc=TimeoutError("No batchGenerateImages response"))
    client.get_character = AsyncMock(return_value=_character(workflow_ids=(WF0,), thumb=MEDIA0))

    wf, media, path = await client._generate_character_image_impl(  # noqa: SLF001
        project_id=PROJECT,
        entity_id=ENTITY,
        req=REQ,
        image_reference_index=0,
    )

    assert wf == WF0
    assert media == MEDIA0
    # No settings/download stubbed here, so the fetch degrades — which is the
    # point: the ids survive a download that cannot run.
    assert path is None
    client.get_character.assert_awaited()


@pytest.mark.asyncio
async def test_timeout_with_an_unbound_entity_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No workflow on the entity means the generation really did not happen.

    The fallback must never invent success — if the backend has nothing for this
    slot, the original timeout is the honest answer.
    """
    exc = TimeoutError("No batchGenerateImages response")
    client = _client(monkeypatch, transport_exc=exc)
    client.get_character = AsyncMock(return_value=_character(workflow_ids=(), thumb=None))

    with pytest.raises(TimeoutError):
        await client._generate_character_image_impl(  # noqa: SLF001
            project_id=PROJECT,
            entity_id=ENTITY,
            req=REQ,
            image_reference_index=0,
        )


@pytest.mark.asyncio
async def test_fallback_reads_the_slot_it_generated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slot 1 (body) must take workflow_ids[1], not the face's id."""
    client = _client(monkeypatch, transport_exc=TimeoutError("No batchGenerateImages response"))
    client.get_character = AsyncMock(
        return_value=_character(workflow_ids=(WF0, "wf-body-1"), thumb=MEDIA0)
    )

    wf, _media, _path = await client._generate_character_image_impl(  # noqa: SLF001
        project_id=PROJECT,
        entity_id=ENTITY,
        req=REQ,
        image_reference_index=1,
    )

    assert wf == "wf-body-1"


@pytest.mark.asyncio
async def test_a_failing_readback_does_not_mask_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-back is a rescue attempt; its own fault must not replace the cause."""
    client = _client(monkeypatch, transport_exc=TimeoutError("No batchGenerateImages response"))
    client.get_character = AsyncMock(side_effect=RuntimeError("trpc 500"))

    with pytest.raises(TimeoutError):
        await client._generate_character_image_impl(  # noqa: SLF001
            project_id=PROJECT,
            entity_id=ENTITY,
            req=REQ,
            image_reference_index=0,
        )


@pytest.mark.asyncio
async def test_fallback_downloads_the_portrait_by_media_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The read-back path must still put a file on disk (#703).

    Binding ids without a file left `image_paths: [null]`, so `--output` got
    nothing on the migrated host. `media.getMediaUrlRedirect` DOES resolve a
    migrated portrait id — probed live 2026-09-06 on the Naia Verde portrait:
    HTTP 200, 696 767 bytes, JFIF magic. (The 404 that
    `migrated_composer.py` records for that route was measured on a *video*
    media id; it does not generalise.)
    """
    client = _client(monkeypatch, transport_exc=TimeoutError("No batchGenerateImages response"))
    client.get_character = AsyncMock(return_value=_character(workflow_ids=(WF0,), thumb=MEDIA0))
    client.settings = MagicMock(output_dir=tmp_path)
    downloaded: list[tuple[str, Any]] = []

    async def _download(name: str, out_path: Any) -> Any:
        downloaded.append((name, out_path))
        return out_path

    client.download = AsyncMock(side_effect=_download)

    _wf, _media, path = await client._generate_character_image_impl(  # noqa: SLF001
        project_id=PROJECT,
        entity_id=ENTITY,
        req=REQ,
        image_reference_index=0,
    )

    assert downloaded and downloaded[0][0] == MEDIA0
    assert path is not None, "read-back path must return the downloaded file"


@pytest.mark.asyncio
async def test_a_failed_download_still_returns_the_bound_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A missing file is a degraded success, never a lost character.

    The ids are what bind the portrait to the entity; the local copy is a
    convenience. Losing the ids over a CDN hiccup would strand a generation the
    user already paid quota for.
    """
    client = _client(monkeypatch, transport_exc=TimeoutError("No batchGenerateImages response"))
    client.get_character = AsyncMock(return_value=_character(workflow_ids=(WF0,), thumb=MEDIA0))
    client.settings = MagicMock(output_dir=tmp_path)
    client.download = AsyncMock(side_effect=RuntimeError("CDN 503"))

    wf, media, path = await client._generate_character_image_impl(  # noqa: SLF001
        project_id=PROJECT,
        entity_id=ENTITY,
        req=REQ,
        image_reference_index=0,
    )

    assert (wf, media) == (WF0, MEDIA0)
    assert path is None


@pytest.mark.asyncio
async def test_each_slot_gets_its_own_media_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Slot 1 must not inherit the face's media id (#703).

    The entity exposes a single ``thumbnail_media_id`` — the portrait. Returning
    it for every slot made the body slot claim the face's media, and the saga
    then PATCHed that id onto the body workflow through ``commit_workflow``,
    which writes ``metadata.primaryMediaId``. Confirmed live 2026-09-06: after a
    full create, the body workflow ``0e8e2a8e…`` carried the FACE's media
    ``4daaf9ed…`` with an updateTime matching the run, while its own
    ``displayName``/``batchId`` proved the body image had generated correctly.

    Flow already sets each workflow's own ``primaryMediaId``, so read it from
    the project listing instead of guessing — which also makes the subsequent
    commit a no-op rather than a corruption.
    """
    client = _client(monkeypatch, transport_exc=TimeoutError("No batchGenerateImages response"))
    client.get_character = AsyncMock(
        return_value=_character(workflow_ids=(WF0, "wf-body-1"), thumb=MEDIA0)
    )
    client.settings = MagicMock(output_dir=tmp_path)
    client.download = AsyncMock(side_effect=lambda _n, out: out)
    client.fetch_project_listing = AsyncMock(
        return_value={
            "result": {
                "data": {
                    "json": {
                        "projectContents": {
                            "workflows": [
                                {"name": WF0, "metadata": {"primaryMediaId": MEDIA0}},
                                {"name": "wf-body-1", "metadata": {"primaryMediaId": "media-body"}},
                            ]
                        }
                    }
                }
            }
        }
    )

    _wf, media, _path = await client._generate_character_image_impl(  # noqa: SLF001
        project_id=PROJECT,
        entity_id=ENTITY,
        req=REQ,
        image_reference_index=1,
    )

    assert media == "media-body", f"slot 1 took the face's media id: {media}"
