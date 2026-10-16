"""Export a character to a bundle folder, import it into another project/account (#639)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from gflow_cli.api.character import Character, CharacterCreateResult, Voice
from gflow_cli.character_bundle import write_bundle
from gflow_cli.errors import ConfigurationError
from gflow_cli.services import character_portability as portability

NORA = Character(
    entity_id="00000000-0000-4000-8000-000000000004",
    display_name="Nora",
    project_id="proj-a",
    workflow_ids=("wf-0", "wf-1"),
    voice="Aoede",
    personality="calm",
    thumbnail_media_id=None,
)


def _jpeg(color: tuple[int, int, int] = (90, 60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 24), color).save(buf, format="JPEG")
    return buf.getvalue()


def _export_client() -> MagicMock:
    client = MagicMock()
    client.read_character_for_export = AsyncMock(
        return_value=(NORA, [("portrait", _jpeg()), ("body", _jpeg((1, 2, 3)))])
    )
    return client


class TestExport:
    async def test_the_bundle_holds_the_character_and_its_images(self, tmp_path: Path) -> None:
        client = _export_client()

        bundle = await portability.export_character(
            client, project_id="proj-a", name="Nora", out=tmp_path / "nora"
        )

        assert (bundle.name, bundle.voice, bundle.personality) == ("Nora", "Aoede", "calm")
        assert [i.role for i in bundle.images] == ["portrait", "body"]
        manifest = (tmp_path / "nora" / "manifest.json").read_text(encoding="utf-8")
        assert NORA.entity_id not in manifest and "proj-a" not in manifest

    async def test_a_used_destination_is_refused_before_flow_is_read(self, tmp_path: Path) -> None:
        out = tmp_path / "nora"
        out.mkdir()
        (out / "x").write_text("x", encoding="utf-8")
        client = _export_client()

        with pytest.raises(ConfigurationError):
            await portability.export_character(client, project_id="proj-a", name="Nora", out=out)
        client.read_character_for_export.assert_not_awaited()


def _bundle(tmp_path: Path, voice: str | None = "Aoede") -> Path:
    out = tmp_path / "nora"
    write_bundle(
        out,
        name="Nora",
        voice=voice,
        personality="calm",
        images=[("portrait", _jpeg()), ("body", _jpeg((1, 2, 3)))],
    )
    return out


def _import_client(voices: tuple[str, ...] = ("Aoede", "Charon")) -> MagicMock:
    client = MagicMock()
    client.list_voices = AsyncMock(return_value=tuple(Voice(v) for v in voices))
    return client


@pytest.fixture
def create(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    result = CharacterCreateResult(
        entity_id="new-entity",
        project_id="proj-b",
        workflow_ids=("w0", "w1"),
        primary_media_ids=("m0", "m1"),
        name="Nora",
        voice="Aoede",
        image_paths=(),
    )
    mock = AsyncMock(return_value=result)
    monkeypatch.setattr(portability, "character_create_from_files", mock)
    return mock


async def _import(tmp_path: Path, client: Any, **kw: Any) -> CharacterCreateResult:
    return await portability.import_character(
        client,
        MagicMock(),
        profile_name="b",
        profile_dir=tmp_path / "profile_b",
        bundle_dir=kw.pop("bundle_dir"),
        project_id="proj-b",
        **kw,
    )


class TestImport:
    async def test_the_character_is_rebuilt_from_the_bundle_files(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path)

        await _import(tmp_path, _import_client(), bundle_dir=bundle)

        kwargs = create.await_args.kwargs
        assert (kwargs["name"], kwargs["voice"], kwargs["personality"]) == ("Nora", "Aoede", "calm")
        assert kwargs["portrait"] == bundle / "portrait.jpg"
        assert kwargs["body"] == bundle / "body.jpg"
        assert kwargs["project_id"] == "proj-b"

    async def test_a_voice_the_target_lacks_is_refused_before_anything_is_created(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path, voice="Enceladus")

        with pytest.raises(ConfigurationError) as exc:
            await _import(tmp_path, _import_client(("Aoede",)), bundle_dir=bundle)
        assert "Enceladus" in str(exc.value) and "--voice" in str(exc.value)
        create.assert_not_awaited()

    async def test_overrides_win_and_are_checked_too(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path)

        await _import(tmp_path, _import_client(), bundle_dir=bundle, name="Nora B", voice="charon")

        kwargs = create.await_args.kwargs
        assert (kwargs["name"], kwargs["voice"]) == ("Nora B", "Charon")

    async def test_a_tampered_bundle_never_reaches_flow(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path)
        (bundle / "portrait.jpg").write_bytes(_jpeg((255, 0, 0)))
        client = _import_client()

        with pytest.raises(ConfigurationError):
            await _import(tmp_path, client, bundle_dir=bundle)
        client.list_voices.assert_not_awaited()
        create.assert_not_awaited()

    async def test_a_bundle_without_a_voice_imports_without_one(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path, voice=None)
        client = _import_client()

        await _import(tmp_path, client, bundle_dir=bundle)

        assert create.await_args.kwargs["voice"] is None
        client.list_voices.assert_not_awaited()


def test_the_manifest_written_is_json_not_python_repr(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))


# --- apply_character_bundles: generate directly from a bundle (option C) -----------------


def _req(**kw: Any) -> Any:
    from gflow_cli.api.image import GenerateImageRequest

    return GenerateImageRequest(prompt="Nora reading", **kw)


def _apply_client(characters: list[Character]) -> MagicMock:
    client = _import_client()
    client.list_characters = AsyncMock(return_value=characters)
    return client


async def _apply(tmp_path: Path, client: Any, req: Any, *bundles: Path) -> Any:
    return await portability.apply_character_bundles(
        client,
        MagicMock(),
        req,
        project_id="proj-b",
        profile_name="b",
        profile_dir=tmp_path / "profile_b",
        bundles=bundles,
    )


class TestApplyCharacterBundles:
    async def test_an_absent_character_is_imported_and_attached(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path)

        req = await _apply(tmp_path, _apply_client([]), _req(), bundle)

        create.assert_awaited_once()
        assert req.reference_entities == ("new-entity",)
        assert req.reference_entity_names == ("Nora",)

    async def test_a_character_already_in_the_project_is_reused_by_name(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path)
        existing = NORA.__class__(**{**NORA.__dict__, "project_id": "proj-b"})

        req = await _apply(tmp_path, _apply_client([existing]), _req(), bundle)

        create.assert_not_awaited()
        assert req.reference_entities == (NORA.entity_id,)

    async def test_two_characters_with_the_name_is_refused_not_guessed(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path)
        twin = NORA.__class__(**{**NORA.__dict__, "entity_id": "other-id"})

        with pytest.raises(ConfigurationError, match="other-id"):
            await _apply(tmp_path, _apply_client([NORA, twin]), _req(), bundle)
        create.assert_not_awaited()

    async def test_a_bad_bundle_is_refused_before_flow_is_read(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path)
        (bundle / "portrait.jpg").write_bytes(_jpeg((255, 0, 0)))
        client = _apply_client([])

        with pytest.raises(ConfigurationError):
            await _apply(tmp_path, client, _req(), bundle)
        client.list_characters.assert_not_awaited()

    async def test_the_same_character_already_mentioned_is_not_attached_twice(
        self, tmp_path: Path, create: AsyncMock
    ) -> None:
        bundle = _bundle(tmp_path)
        req = _req(reference_entities=("new-entity",), reference_entity_names=("Nora",))

        req = await _apply(tmp_path, _apply_client([]), req, bundle)

        assert req.reference_entities == ("new-entity",)
