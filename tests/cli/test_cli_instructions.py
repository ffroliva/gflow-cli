"""Tests for the `gflow instructions` CLI command group."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gflow_cli import cli_instructions
from gflow_cli.api.dto import AssetInfo
from gflow_cli.api.image import AgentInstruction, ProjectBrief
from gflow_cli.cli import main

# ---------------------------------------------------------------------------
# Fake/Stub DTO instances & Client
# ---------------------------------------------------------------------------

_CARD_A = AgentInstruction(
    id="card-id-a",
    title="Crayon art",
    text="Flat 2D children drawing",
    enabled=True,
    image_media_ids=("media-uuid-1",),
    character_ids=(),
)

_CARD_B = AgentInstruction(
    id="card-id-b",
    title="Noir theme",
    text="Cinematic high contrast",
    enabled=False,
    image_media_ids=(),
    character_ids=("hero-char-name",),
)

_BRIEF_STUB = ProjectBrief(
    enabled=True,
    cards=(_CARD_A, _CARD_B),
    agent_toggle_state="AGENT_TOGGLE_STATE_ENABLED",
)


class _FakeClient:
    """Fake FlowApiClient stubbing instructions methods."""

    def __init__(self, brief: ProjectBrief | None = None, **_kw):
        self.brief = brief if brief is not None else _BRIEF_STUB
        self.patched_payloads: list[dict] = []
        self.uploads: list[tuple[str, Path]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get_agent_info(self, project_id: str) -> ProjectBrief:
        return self.brief

    async def patch_agent_info(
        self,
        project_id: str,
        *,
        enabled: bool | None = None,
        cards: tuple[AgentInstruction, ...] | None = None,
    ) -> ProjectBrief:
        payload = {"project_id": project_id}
        if enabled is not None:
            payload["enabled"] = enabled
        if cards is not None:
            payload["cards"] = cards
            # Update the local brief mock cards
            self.brief = ProjectBrief(
                enabled=enabled if enabled is not None else self.brief.enabled,
                cards=cards,
                agent_toggle_state=self.brief.agent_toggle_state,
            )
        self.patched_payloads.append(payload)
        return self.brief

    async def upload_image(self, project_id: str, image_path: Path) -> AssetInfo:
        self.uploads.append((project_id, image_path))
        return AssetInfo(
            name="media-upload-uuid",
            project_id=project_id,
            workflow_id="wf-upload-uuid",
            display_name=image_path.name,
            width=512,
            height=512,
        )


def _patch_client(monkeypatch, client: _FakeClient):
    monkeypatch.setattr(cli_instructions, "FlowApiClient", lambda **kw: client)
    monkeypatch.setattr(
        cli_instructions, "get_settings", lambda: type("S", (), {"headless": True})()
    )
    monkeypatch.setattr(cli_instructions, "_resolve_profile", lambda p: p or "default")
    monkeypatch.setattr(cli_instructions, "_make_provider_dir", lambda name: Path("/fake"))


# ---------------------------------------------------------------------------
# instructions list
# ---------------------------------------------------------------------------


def test_list_cards_tabular_output(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    res = CliRunner().invoke(
        main,
        ["instructions", "list", "--project", "proj-123"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Crayon art" in res.output
    assert "Noir theme" in res.output
    assert "proj-123" in res.output


def test_list_cards_json_output(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    res = CliRunner().invoke(
        main,
        ["instructions", "list", "--project", "proj-123", "--json"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "ok"
    assert data["project_id"] == "proj-123"
    assert len(data["cards"]) == 2
    assert data["cards"][0]["title"] == "Crayon art"
    assert data["cards"][0]["id"] == "card-id-a"


def test_list_requires_project():
    res = CliRunner().invoke(
        main,
        ["instructions", "list"],
    )
    # exit 2 (Click's missing option code)
    assert res.exit_code == 2


# ---------------------------------------------------------------------------
# instructions add
# ---------------------------------------------------------------------------


def test_add_card_happy_path(monkeypatch, tmp_path: Path):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    # Local file reference upload path mock
    local_ref = tmp_path / "art.png"
    local_ref.write_bytes(b"fake png content")

    res = CliRunner().invoke(
        main,
        [
            "instructions",
            "add",
            "Golden Glow",
            "--text",
            "Warm afternoon sun",
            "--ref",
            "44444444-4444-4444-4444-444444444444",  # asset UUID
            "--ref",
            str(local_ref),  # local image
            "--ref",
            "hero-character",  # character name
            "--project",
            "proj-123",
        ],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Added enabled card Golden Glow" in res.output

    # Check upload triggered
    assert len(client.uploads) == 1
    assert client.uploads[0] == ("proj-123", local_ref.resolve())

    # Check patched payload contains the new card + the existing 2
    assert len(client.patched_payloads) == 1
    cards = client.patched_payloads[0]["cards"]
    assert len(cards) == 3
    new_card = cards[2]
    assert new_card.title == "Golden Glow"
    assert new_card.text == "Warm afternoon sun"
    assert new_card.enabled is True
    # image refs: 1 UUID + 1 uploaded media-upload-uuid
    assert new_card.image_media_ids == ("44444444-4444-4444-4444-444444444444", "media-upload-uuid")
    assert new_card.character_ids == ("hero-character",)


# ---------------------------------------------------------------------------
# instructions enable / disable
# ---------------------------------------------------------------------------


def test_enable_card_by_title(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    res = CliRunner().invoke(
        main,
        ["instructions", "enable", "Noir theme", "--project", "proj-123"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Enabled card Noir theme" in res.output

    assert len(client.patched_payloads) == 1
    cards = client.patched_payloads[0]["cards"]
    # Noir theme is cards[1], it should now be enabled=True
    assert cards[1].enabled is True


def test_disable_card_by_id(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    res = CliRunner().invoke(
        main,
        ["instructions", "disable", "--id", "card-id-a", "--project", "proj-123"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Disabled card Crayon art" in res.output

    assert len(client.patched_payloads) == 1
    cards = client.patched_payloads[0]["cards"]
    # Crayon art is cards[0], it should now be enabled=False
    assert cards[0].enabled is False


def test_selector_missing_or_ambiguous_fails_with_exit_2(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    # 1. Title not found
    res = CliRunner().invoke(
        main,
        ["instructions", "enable", "Missing Card", "--project", "proj-123"],
    )
    assert res.exit_code == 2
    assert "Error: no instruction card matches title 'Missing Card'" in res.output

    # 2. Both title and --id provided
    res = CliRunner().invoke(
        main,
        ["instructions", "enable", "Crayon art", "--id", "card-id-a", "--project", "proj-123"],
    )
    assert res.exit_code == 2
    assert "Error: Provide exactly one of TITLE or --id" in res.output


# ---------------------------------------------------------------------------
# instructions rm
# ---------------------------------------------------------------------------


def test_remove_card(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    res = CliRunner().invoke(
        main,
        ["instructions", "rm", "Crayon art", "--project", "proj-123"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Removed card Crayon art" in res.output

    assert len(client.patched_payloads) == 1
    cards = client.patched_payloads[0]["cards"]
    # Crayon art should be dropped, leaving only Noir theme
    assert len(cards) == 1
    assert cards[0].title == "Noir theme"


# ---------------------------------------------------------------------------
# instructions apply (declarative sync)
# ---------------------------------------------------------------------------


def test_apply_toml_sync(monkeypatch, tmp_path: Path):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    toml_file = tmp_path / "brief.toml"
    toml_file.write_text(
        """
[[card]]
title = "Water color"
text = "soft watercolor painting"
ref = ["character-uuid-1"]
enabled = true

[[card]]
title = "Vintage filter"
text = "faded 70s look"
enabled = false
""",
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        main,
        ["instructions", "apply", str(toml_file), "--project", "proj-123"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Applied 2 card(s) to project proj-123" in res.output

    assert len(client.patched_payloads) == 1
    cards = client.patched_payloads[0]["cards"]
    assert len(cards) == 2
    assert cards[0].title == "Water color"
    assert cards[0].text == "soft watercolor painting"
    assert cards[0].character_ids == ("character-uuid-1",)
    assert cards[0].enabled is True

    assert cards[1].title == "Vintage filter"
    assert cards[1].text == "faded 70s look"
    assert cards[1].enabled is False


def test_apply_json_list_sync(monkeypatch, tmp_path: Path):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    json_file = tmp_path / "brief.json"
    json_file.write_text(
        json.dumps(
            [
                {
                    "title": "Futurism",
                    "text": "sharp metallic speedlines",
                    "ref": ["33333333-3333-3333-3333-333333333333"],
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        main,
        ["instructions", "apply", str(json_file), "--project", "proj-123"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Applied 1 card(s)" in res.output

    cards = client.patched_payloads[0]["cards"]
    assert len(cards) == 1
    assert cards[0].title == "Futurism"
    assert cards[0].image_media_ids == ("33333333-3333-3333-3333-333333333333",)


# ---------------------------------------------------------------------------
# instructions toggle-mode
# ---------------------------------------------------------------------------


def test_toggle_mode_on(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    res = CliRunner().invoke(
        main,
        ["instructions", "toggle-mode", "--on", "--project", "proj-123"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Agent mode on for project proj-123" in res.output

    assert len(client.patched_payloads) == 1
    assert client.patched_payloads[0]["enabled"] is True


def test_toggle_mode_off(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    res = CliRunner().invoke(
        main,
        ["instructions", "toggle-mode", "--off", "--project", "proj-123"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Agent mode off for project proj-123" in res.output

    assert len(client.patched_payloads) == 1
    assert client.patched_payloads[0]["enabled"] is False
