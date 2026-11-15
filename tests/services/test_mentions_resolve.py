from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gflow_cli.errors import (
    ConfigurationError,
    DataStoreError,
    MentionIndexUnavailableError,
    NetworkError,
)
from gflow_cli.services.mentions import (
    AssetIndex,
    parse_mentions,
    resolve_mentions,
)


class _FailingCharacterClient:
    """Fake FlowApiClient whose list_characters() always raises, simulating an
    upstream Flow API outage (network/auth/rate-limit/etc.)."""

    async def list_characters(self, project_id: str, *, migrated_read: bool = True) -> list[Any]:
        raise NetworkError("connection reset")


class _EmptyCharacterClient:
    """Fake FlowApiClient whose list_characters() succeeds with zero rows —
    distinct from an outage."""

    async def list_characters(self, project_id: str, *, migrated_read: bool = True) -> list[Any]:
        return []


def _character(entity_id: str, name: str, workflow_ids: tuple[str, ...]) -> SimpleNamespace:
    """A minimal Character-shaped stub (entity_id/display_name/workflow_ids) —
    the shape AssetIndex reads from client.list_characters."""
    return SimpleNamespace(entity_id=entity_id, display_name=name, workflow_ids=workflow_ids)


def test_resolve_entity_without_reference_images_fails_early() -> None:
    # A bare, image-less character (empty workflow_ids) cannot stage as a
    # referenceEntity — resolution must fail here, not deep in the UI attach.
    index = AssetIndex(
        entities=[_character("e1-uuid-12345678901234567890123456", "Zoro", ())],
        media_assets=[],
    )
    tokens = parse_mentions("Hello @Zoro walks")
    with pytest.raises(ConfigurationError, match="no reference images"):
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro walks")


def test_resolve_entity_with_reference_images_ok() -> None:
    # Same entity but WITH a reference image (non-empty workflow_ids) resolves.
    index = AssetIndex(
        entities=[_character("e1-uuid-12345678901234567890123456", "Zoro", ("wf-1",))],
        media_assets=[],
    )
    tokens = parse_mentions("Hello @Zoro walks")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro walks")
    assert len(res.mentions) == 1
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello Zoro walks"


def test_resolve_empty_mentions() -> None:
    index = AssetIndex(entities=[], media_assets=[])
    tokens = parse_mentions("Hello world")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello world")
    assert len(res.mentions) == 0
    assert res.de_tagged_prompt == "Hello world"


def test_resolve_unique_entity() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro walks")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro walks")
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro"
    assert res.mentions[0].kind == "entity"
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello Zoro walks"


def test_resolve_unique_media() -> None:
    media = [{"media_id": "m1-uuid-12345678901234567890123456", "display_name": "logo"}]
    index = AssetIndex(entities=[], media_assets=media)
    tokens = parse_mentions("Hello @logo on tee")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @logo on tee")
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "logo"
    assert res.mentions[0].kind == "media"
    assert res.mentions[0].id == "m1-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello logo on tee"


def test_resolve_greedy_longest_match() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Captain", "entityType": "CHARACTER"},
        },
        {
            "entityId": "e2-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Captain Zoro", "entityType": "CHARACTER"},
        },
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Captain Zoro walks")
    res = resolve_mentions(
        tokens, index, path="image", model="nano2", prompt="Hello @Captain Zoro walks"
    )
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Captain Zoro"
    assert res.mentions[0].id == "e2-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello Captain Zoro walks"

    # Greedy match falls back to word prefix if longest doesn't match
    tokens = parse_mentions("Hello @Captain Nemo walks")
    res = resolve_mentions(
        tokens, index, path="image", model="nano2", prompt="Hello @Captain Nemo walks"
    )
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Captain"
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello Captain Nemo walks"


def test_resolve_entity_shadows_media() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    media = [{"media_id": "m1-uuid-12345678901234567890123456", "display_name": "Zoro"}]
    index = AssetIndex(entities=entities, media_assets=media)
    tokens = parse_mentions("Hello @Zoro")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro")
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro"
    assert res.mentions[0].kind == "entity"
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"
    assert res.mentions[0].shadowed == "m1-uuid-12345678901234567890123456"


def test_resolve_ambiguous_entity() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        },
        {
            "entityId": "e2-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        },
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro")
    assert "Ambiguous mention" in str(exc_info.value)
    assert "e1-uuid-1234" in str(exc_info.value)
    assert "e2-uuid-1234" in str(exc_info.value)


def test_resolve_unknown() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @nonexistent")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @nonexistent")
    assert "Unknown mention" in str(exc_info.value)
    assert "Available assets: Zoro" in str(exc_info.value)


def test_resolve_me_refusal() -> None:
    index = AssetIndex(entities=[], media_assets=[])
    tokens = parse_mentions("Hello @me")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @me")
    assert "avatar likeness is region-gated" in str(exc_info.value)


def test_resolve_video_media_refusal() -> None:
    media = [{"media_id": "m1-uuid-12345678901234567890123456", "display_name": "logo"}]
    index = AssetIndex(entities=[], media_assets=media)
    tokens = parse_mentions("Hello @logo")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="video", model="veo2-blue", prompt="Hello @logo")
    assert "media mentions on the video path are Phase 3" in str(exc_info.value)


def test_resolve_cap_breach() -> None:
    entities = [
        {
            "entityId": f"e{i}-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": f"Zoro{i}", "entityType": "CHARACTER"},
        }
        for i in range(5)
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro0 @Zoro1 @Zoro2 @Zoro3")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(
            tokens, index, path="image", model="imagen4", prompt="Hello @Zoro0 @Zoro1 @Zoro2 @Zoro3"
        )
    assert "reference cap" in str(exc_info.value)


def test_resolve_deduplication() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro and @Zoro")
    res = resolve_mentions(
        tokens, index, path="image", model="nano2", prompt="Hello @Zoro and @Zoro"
    )
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro"


def test_resolve_metacharacters_and_ansi() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro*", "entityType": "CHARACTER"},
        },
        {
            "entityId": "e2-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "\x1b[31mRed\x1b[0mName", "entityType": "CHARACTER"},
        },
    ]
    index = AssetIndex(entities=entities, media_assets=[])

    # Regex metacharacters match literally
    tokens = parse_mentions("Hello @Zoro*")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro*")
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro*"

    # ANSI escape sequences stripped in errors
    tokens = parse_mentions("Hello @nonexistent")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @nonexistent")
    assert "RedName" in str(exc_info.value)
    assert "\x1b[31m" not in str(exc_info.value)


# ---------- AssetIndex.build_for_project: outage vs. empty source (Task B1) ----------


async def test_build_for_project_raises_when_character_source_unavailable() -> None:
    # An outage on the character (entity) source must fail closed with a
    # stable typed error naming the source, not silently degrade to an
    # empty index (which would be indistinguishable from "no characters").
    with pytest.raises(MentionIndexUnavailableError, match="character") as exc_info:
        await AssetIndex.build_for_project(_FailingCharacterClient(), "proj-1")
    assert isinstance(exc_info.value.__cause__, NetworkError)


async def test_build_for_project_raises_when_media_source_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*, db_path: Any, project_id: str) -> list[Any]:
        raise DataStoreError("disk I/O error")

    monkeypatch.setattr("gflow_cli.data.queries.list_project_media_assets", _raise)

    with pytest.raises(MentionIndexUnavailableError, match="media") as exc_info:
        await AssetIndex.build_for_project(_EmptyCharacterClient(), "proj-1")
    assert isinstance(exc_info.value.__cause__, DataStoreError)


async def test_build_for_project_empty_sources_are_not_unavailable() -> None:
    # A source that loads fine with zero rows is NOT an outage — it just
    # means no characters/media exist yet for this project.
    index = await AssetIndex.build_for_project(_EmptyCharacterClient(), "proj-1")
    assert index.entries == []


# ---------- #723: character catalog unreadable → by-name character (image path) ----------


async def test_build_for_project_tolerates_a_character_outage_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On an account served flow.google.com the catalog is read through a labs route
    # that answers 401 while the composer's `@` picker still offers the character.
    monkeypatch.setattr("gflow_cli.data.queries.list_project_media_assets", lambda **_: [])
    index = await AssetIndex.build_for_project(
        _FailingCharacterClient(), "proj-1", characters_optional=True
    )
    assert index.characters_unavailable is True
    assert index.entries == []


def test_unmatched_mention_becomes_a_character_by_its_first_word() -> None:
    index = AssetIndex(characters_unavailable=True)
    prompt = "@Sussy cocinando mientras mira una receta"
    res = resolve_mentions(
        parse_mentions(prompt), index, path="image", model="nano2", prompt=prompt
    )
    assert [(m.name, m.kind, m.id) for m in res.mentions] == [("Sussy", "entity", "")]
    # The name stays as plain text: the chip goes before the prompt, and a mention in
    # mid-sentence still reads correctly.
    assert res.de_tagged_prompt == "Sussy cocinando mientras mira una receta"


def test_a_repeated_by_name_character_is_attached_once() -> None:
    index = AssetIndex(characters_unavailable=True)
    prompt = "@Sussy y @sussy otra vez"
    res = resolve_mentions(
        parse_mentions(prompt), index, path="image", model="nano2", prompt=prompt
    )
    assert [m.name for m in res.mentions] == ["Sussy"]
    assert res.de_tagged_prompt == "Sussy y sussy otra vez"


def test_a_by_name_character_the_caller_already_passed_is_not_added_again() -> None:
    """Review of #979: `--reference-entity <id> --reference-entity-name Sussy` plus
    `@Sussy` must not attach Sussy twice. `existing_refs` holds ids, so the comparison
    is against the caller's names, case-insensitively."""
    index = AssetIndex(characters_unavailable=True)
    prompt = "@sussy cocinando"
    res = resolve_mentions(
        parse_mentions(prompt),
        index,
        path="image",
        model="nano2",
        prompt=prompt,
        existing_refs=["ent-sussy"],
        existing_names=["Sussy"],
    )
    assert res.mentions == []
    assert res.de_tagged_prompt == "sussy cocinando"


def test_media_still_resolves_when_only_characters_are_unavailable() -> None:
    index = AssetIndex(
        media_assets=[{"media_id": "m1-uuid-12345678901234567890123456", "display_name": "logo"}],
        characters_unavailable=True,
    )
    prompt = "Hello @logo on tee"
    res = resolve_mentions(
        parse_mentions(prompt), index, path="image", model="nano2", prompt=prompt
    )
    assert [(m.kind, m.id) for m in res.mentions] == [
        ("media", "m1-uuid-12345678901234567890123456")
    ]


def test_video_never_resolves_a_character_by_name() -> None:
    # The migrated VIDEO driver does not attach by name: an id-less entity there would
    # be dropped from a billed generation, so the fallback is image-only.
    index = AssetIndex(characters_unavailable=True)
    with pytest.raises(ConfigurationError, match="Unknown mention"):
        resolve_mentions(
            parse_mentions("@Sussy runs"), index, path="video", model="", prompt="@Sussy runs"
        )


async def test_resolve_and_apply_names_the_character_without_an_id_on_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.services.mentions import resolve_and_apply

    monkeypatch.setattr("gflow_cli.data.queries.list_project_media_assets", lambda **_: [])
    req = GenerateImageRequest(prompt="@Sussy cocinando")
    out = await resolve_and_apply(
        _FailingCharacterClient(),  # type: ignore[arg-type]
        req,
        path="image",
        project_id="proj-1",
        tool_specs=(),
    )
    assert out.reference_entities == ()
    assert out.reference_entity_names == ("Sussy",)
    assert out.prompt == "Sussy cocinando"


async def test_resolve_and_apply_still_fails_closed_on_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.video import GenerateVideoRequest
    from gflow_cli.services.mentions import resolve_and_apply

    monkeypatch.setattr("gflow_cli.data.queries.list_project_media_assets", lambda **_: [])
    req = GenerateVideoRequest(prompt="@Sussy runs")
    with pytest.raises(MentionIndexUnavailableError, match="character"):
        await resolve_and_apply(
            _FailingCharacterClient(),  # type: ignore[arg-type]
            req,
            path="video",
            project_id="proj-1",
            tool_specs=(),
        )


async def test_resolve_and_apply_keeps_a_passed_character_single(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.services.mentions import resolve_and_apply

    monkeypatch.setattr("gflow_cli.data.queries.list_project_media_assets", lambda **_: [])
    req = GenerateImageRequest(
        prompt="@Sussy cocinando",
        reference_entities=("ent-sussy",),
        reference_entity_names=("Sussy",),
    )
    out = await resolve_and_apply(
        _FailingCharacterClient(),  # type: ignore[arg-type]
        req,
        path="image",
        project_id="proj-1",
        tool_specs=(),
    )
    assert out.reference_entities == ("ent-sussy",)
    assert out.reference_entity_names == ("Sussy",)


@pytest.mark.parametrize("flow_host", ["auto", "flow.google.com"])
async def test_resolve_and_apply_on_flow_google_com_keeps_character_catalog_unavailable(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    flow_host: str,
) -> None:
    """Slice 2 must not change @Name mention routing on flow.google.com (slice 5 owns that)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from gflow_cli.api.character import Character
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest
    from gflow_cli.config import Settings
    from gflow_cli.errors import WireFormatError
    from gflow_cli.services.mentions import resolve_and_apply

    monkeypatch.setattr("gflow_cli.data.queries.list_project_media_assets", lambda **_: [])
    client = FlowApiClient(
        profile_dir=tmp_path / "prof",
        settings=Settings(flow_host=flow_host),
    )
    client._page = MagicMock()  # noqa: SLF001
    client._get_json = AsyncMock(  # type: ignore[method-assign]
        side_effect=WireFormatError(
            detail="Flow RPCs have been deprecated and disabled.", status=404
        )
    )
    sussy = Character(
        entity_id="00000000-0000-4000-8000-000000000004",
        display_name="Sussy",
        project_id="proj-1",
        workflow_ids=("wf-1",),
        voice="Aoede",
        personality=None,
        thumbnail_media_id=None,
    )
    with patch(
        "gflow_cli.api.transports.migrated_character.read_project",
        new=AsyncMock(return_value=MagicMock(characters=(sussy,))),
    ) as migrated_read:
        img_out = await resolve_and_apply(
            client,
            GenerateImageRequest(prompt="@Sussy cocinando"),
            path="image",
            project_id="proj-1",
            tool_specs=(),
        )
        assert img_out.reference_entities == ()
        assert img_out.reference_entity_names == ("Sussy",)
        assert img_out.prompt == "Sussy cocinando"

        with pytest.raises(MentionIndexUnavailableError, match="character"):
            await resolve_and_apply(
                client,
                GenerateVideoRequest(prompt="@Sussy runs"),
                path="video",
                project_id="proj-1",
                tool_specs=(),
            )

        migrated_read.assert_not_awaited()
