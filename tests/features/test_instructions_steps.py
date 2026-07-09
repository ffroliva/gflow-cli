"""Step bindings for instructions.feature."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli import cli_instructions
from gflow_cli.api.image import AgentInstruction, ProjectBrief
from gflow_cli.cli import main

scenarios("instructions.feature")


# ---------------------------------------------------------------------------
# Local fixtures & stubs
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, brief: ProjectBrief) -> None:
        self.brief = brief
        self.patched_cards: tuple[AgentInstruction, ...] | None = None

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
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
        if cards is not None:
            self.patched_cards = cards
            self.brief = ProjectBrief(
                enabled=enabled if enabled is not None else self.brief.enabled,
                cards=cards,
                agent_toggle_state=self.brief.agent_toggle_state,
            )
        return self.brief


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {}


@pytest.fixture
def fake_client_holder() -> dict[str, _FakeClient]:
    return {}


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given("a project with no existing instruction cards")
def _project_no_cards(
    monkeypatch: pytest.MonkeyPatch,
    fake_client_holder: dict[str, _FakeClient],
) -> None:
    brief = ProjectBrief(enabled=True, cards=(), agent_toggle_state=None)
    client = _FakeClient(brief)
    fake_client_holder["client"] = client

    monkeypatch.setattr(cli_instructions, "FlowApiClient", lambda **kw: client)
    monkeypatch.setattr(
        cli_instructions, "get_settings", lambda: type("S", (), {"headless": True})()
    )
    monkeypatch.setattr(cli_instructions, "_resolve_profile", lambda p: p or "default")
    monkeypatch.setattr(cli_instructions, "_make_provider_dir", lambda name: Path("/fake"))


@given('a project with an active instruction card "Watercolor"')
def _project_active_card(
    monkeypatch: pytest.MonkeyPatch,
    fake_client_holder: dict[str, _FakeClient],
) -> None:
    card = AgentInstruction(
        id="card-1",
        title="Watercolor",
        text="soft paint style",
        enabled=True,
        image_media_ids=(),
        character_ids=(),
    )
    brief = ProjectBrief(enabled=True, cards=(card,), agent_toggle_state=None)
    client = _FakeClient(brief)
    fake_client_holder["client"] = client

    monkeypatch.setattr(cli_instructions, "FlowApiClient", lambda **kw: client)
    monkeypatch.setattr(
        cli_instructions, "get_settings", lambda: type("S", (), {"headless": True})()
    )
    monkeypatch.setattr(cli_instructions, "_resolve_profile", lambda p: p or "default")
    monkeypatch.setattr(cli_instructions, "_make_provider_dir", lambda name: Path("/fake"))


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when(
    "I run \"gflow instructions add 'Crayon style' "
    "--text 'crayon drawing' "
    "--ref '44444444-4444-4444-4444-444444444444' "
    "--project proj-123\""
)
def _run_add_crayon_style(
    runner: CliRunner,
    cli_result_holder: dict[str, Any],
) -> None:
    cli_result_holder["result"] = runner.invoke(
        main,
        [
            "instructions",
            "add",
            "Crayon style",
            "--text",
            "crayon drawing",
            "--ref",
            "44444444-4444-4444-4444-444444444444",
            "--project",
            "proj-123",
        ],
    )


@when("I run \"gflow instructions disable 'Watercolor' --project proj-123\"")
def _run_disable_watercolor(
    runner: CliRunner,
    cli_result_holder: dict[str, Any],
) -> None:
    cli_result_holder["result"] = runner.invoke(
        main,
        ["instructions", "disable", "Watercolor", "--project", "proj-123"],
    )


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then("the exit code is 0")
def _check_exit_0(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 0, (
        f"Expected exit code 0 but got {result.exit_code}. Output:\n{result.output}"
    )


@then("the project brief contains 1 card")
def _check_brief_contains_one_card(fake_client_holder: dict[str, _FakeClient]) -> None:
    client = fake_client_holder["client"]
    assert client.patched_cards is not None
    assert len(client.patched_cards) == 1


@then('the card "Crayon style" has 1 image reference')
def _check_crayon_style_image_reference(fake_client_holder: dict[str, _FakeClient]) -> None:
    client = fake_client_holder["client"]
    assert client.patched_cards is not None
    card = client.patched_cards[0]
    assert card.title == "Crayon style"
    assert card.image_media_ids == ("44444444-4444-4444-4444-444444444444",)


@then('the card "Watercolor" is disabled')
def _check_watercolor_disabled(fake_client_holder: dict[str, _FakeClient]) -> None:
    client = fake_client_holder["client"]
    assert client.patched_cards is not None
    card = client.patched_cards[0]
    assert card.title == "Watercolor"
    assert card.enabled is False


@pytest.fixture
def manifest_holder() -> dict[str, Any]:
    return {}


@given("a movie manifest with global and per-scene instructions")
def _manifest_with_instructions(tmp_path: Path, manifest_holder: dict[str, Any]) -> None:
    toml_content = """
title = "Instructions Movie"
project = "proj-xyz"

[instructions]
[[instructions.card]]
title = "Cinematic Lighting"
text = "soft light"
enabled = true

[[scenes]]
id = "scene-1"
action = "hero emerges"
[scenes.instructions]
disable = ["Cinematic Lighting"]
[[scenes.instructions.card]]
title = "Fog Atmosphere"
text = "dense fog"
"""
    p = tmp_path / "movie_inst.toml"
    p.write_text(toml_content, encoding="utf-8")
    manifest_holder["path"] = p


@when("I read the movie manifest")
def _read_manifest(manifest_holder: dict[str, Any]) -> None:
    from gflow_cli.movie_manifest import MovieManifest

    manifest_holder["manifest"] = MovieManifest.from_toml_path(manifest_holder["path"])


@then('the manifest title is "Instructions Movie"')
def _check_manifest_title(manifest_holder: dict[str, Any]) -> None:
    manifest = manifest_holder["manifest"]
    assert manifest.title == "Instructions Movie"


@then('the global instructions contain 1 card "Cinematic Lighting"')
def _check_global_instructions(manifest_holder: dict[str, Any]) -> None:
    manifest = manifest_holder["manifest"]
    assert len(manifest.instructions) == 1
    assert manifest.instructions[0].title == "Cinematic Lighting"


@then('the scene "scene-1" has 1 disable override "Cinematic Lighting"')
def _check_scene_disable(manifest_holder: dict[str, Any]) -> None:
    manifest = manifest_holder["manifest"]
    s = manifest.scenes[0]
    assert s.id == "scene-1"
    assert s.instructions is not None
    assert s.instructions.disable == ("Cinematic Lighting",)


@then('the scene "scene-1" has 1 custom card override "Fog Atmosphere"')
def _check_scene_card(manifest_holder: dict[str, Any]) -> None:
    manifest = manifest_holder["manifest"]
    s = manifest.scenes[0]
    assert s.instructions is not None
    assert len(s.instructions.card) == 1
    assert s.instructions.card[0].title == "Fog Atmosphere"
