from __future__ import annotations

import shlex
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli import main
from gflow_cli.services.mentions import AssetIndex

scenarios("asset_tagging.feature")


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {"result": None}


@pytest.fixture
def active_index() -> dict[str, Any]:
    return {"index": AssetIndex([], [])}


@pytest.fixture
def captured_calls() -> dict[str, list[Any]]:
    return {"t2i": [], "t2v": [], "i2i": [], "r2v": []}


@pytest.fixture(autouse=True)
def _patch_profile_and_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr("gflow_cli.cli_image._make_provider_dir", lambda name: tmp_path)
    monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", lambda profile: "test")
    monkeypatch.setattr("gflow_cli.cli_video._make_provider_dir", lambda name: tmp_path)


@pytest.fixture(autouse=True)
def _patch_mentions_index(monkeypatch: pytest.MonkeyPatch, active_index: dict[str, Any]) -> None:
    async def _fake_build(cls: Any, client: Any, project_id: str) -> Any:
        return active_index["index"]

    monkeypatch.setattr(
        "gflow_cli.services.mentions.AssetIndex.build_for_project", classmethod(_fake_build)
    )


@pytest.fixture(autouse=True)
def _patch_client_lifecycle(
    monkeypatch: pytest.MonkeyPatch, captured_calls: dict[str, list[Any]]
) -> None:
    # No-op browser setup/lifecycle
    async def _fake_aenter(self: Any) -> Any:
        return self

    async def _fake_aexit(self: Any, *args: Any) -> None:
        pass

    monkeypatch.setattr("gflow_cli.api.client.FlowApiClient.__aenter__", _fake_aenter)
    monkeypatch.setattr("gflow_cli.api.client.FlowApiClient.__aexit__", _fake_aexit)

    # Mock _resolve_project to avoid loading pages
    from gflow_cli.api.dto import ProjectInfo

    fake_project = ProjectInfo(project_id="proj-123", title="fake")

    async def _fake_resolve_project(*args: Any, **kwargs: Any) -> tuple[ProjectInfo, bool]:
        return fake_project, False

    monkeypatch.setattr("gflow_cli.cli_image._resolve_project", _fake_resolve_project)

    # Mock _download_images as async function
    async def _fake_download_images(*args: Any, **kwargs: Any) -> list[Path]:
        return [Path("/fake/path.png")]

    monkeypatch.setattr("gflow_cli.cli_image._download_images", _fake_download_images)

    # Intercept generate_image and generate_images_batch
    async def _fake_generate_image(self: Any, project_id: str, req: Any) -> Any:
        captured_calls["t2i"].append((project_id, req))
        from gflow_cli.api.dto import GeneratedImage

        return GeneratedImage(
            media_name="fake-img-uuid",
            workflow_id="workflow-123",
            seed=1337,
            prompt=req.prompt,
            model_name_type="NARWHAL",
            aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
            fife_url="https://fake.url/image.jpg",
            dimensions=(1024, 1024),
        )

    async def _fake_generate_images_batch(self: Any, project_id: str, req: Any, count: int) -> Any:
        captured_calls["t2i"].append((project_id, req))
        from gflow_cli.api.dto import GeneratedImage

        return [
            GeneratedImage(
                media_name="fake-img-uuid",
                workflow_id="workflow-123",
                seed=1337,
                prompt=req.prompt,
                model_name_type="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                fife_url="https://fake.url/image.jpg",
                dimensions=(1024, 1024),
            )
        ]

    monkeypatch.setattr("gflow_cli.api.client.FlowApiClient.generate_image", _fake_generate_image)
    monkeypatch.setattr(
        "gflow_cli.api.client.FlowApiClient.generate_images_batch", _fake_generate_images_batch
    )

    # Intercept generate_video
    async def _fake_generate_video(self: Any, req: Any, **kwargs: Any) -> Any:
        captured_calls["t2v"].append((kwargs.get("project_id"), req))
        if "on_started" in kwargs and kwargs["on_started"] is not None:
            from gflow_cli.api.video import VideoStarted

            kwargs["on_started"](VideoStarted(media_id="fake-vid", operation_name="op-123"))
        from gflow_cli.api.video import VideoResult, VideoStatus

        status = VideoStatus(media_id="fake-vid", state="SUCCEEDED")
        return VideoResult(status=status, local_path=Path("/fake/path.mp4"))

    monkeypatch.setattr("gflow_cli.api.client.FlowApiClient.generate_video", _fake_generate_video)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Generator[None, None, None]:
    config.reset_settings()
    yield
    config.reset_settings()


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given("a project with no assets")
def _no_assets(active_index: dict[str, Any]) -> None:
    active_index["index"] = AssetIndex([], [])


@given('a project with two characters named "Zoro"')
def _two_zoros(active_index: dict[str, Any]) -> None:
    entities = [
        {"entityId": "zoro-id-1", "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"}},
        {"entityId": "zoro-id-2", "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"}},
    ]
    active_index["index"] = AssetIndex(entities=entities, media_assets=[])


@given("a project with five characters")
def _five_characters(active_index: dict[str, Any]) -> None:
    entities = [
        {
            "entityId": f"zoro-{i}",
            "entityInfo": {"displayName": f"Zoro{i}", "entityType": "CHARACTER"},
        }
        for i in range(5)
    ]
    active_index["index"] = AssetIndex(entities=entities, media_assets=[])


@given("a project with some assets")
def _some_assets(active_index: dict[str, Any]) -> None:
    entities = [
        {"entityId": "zoro-id", "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"}}
    ]
    active_index["index"] = AssetIndex(entities=entities, media_assets=[])


@given('a project with a media asset named "logo"')
def _media_logo(active_index: dict[str, Any]) -> None:
    media = [{"media_id": "logo-id", "display_name": "logo"}]
    active_index["index"] = AssetIndex(entities=[], media_assets=media)


@given('a project with character "Zoro" and media "logo"')
def _character_and_media(active_index: dict[str, Any]) -> None:
    entities = [
        {"entityId": "zoro-id", "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"}}
    ]
    media = [{"media_id": "logo-id", "display_name": "logo"}]
    active_index["index"] = AssetIndex(entities=entities, media_assets=media)


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when(parsers.parse("I run {command_line}"))
def _run_gflow_command(
    runner: CliRunner,
    cli_result_holder: dict[str, Any],
    command_line: str,
) -> None:
    parts = shlex.split(command_line)
    assert parts[0] == "gflow"
    args = parts[1:]
    cli_result_holder["result"] = runner.invoke(main, args)


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then(parsers.parse("the command should fail with exit code {code:d}"))
def _command_should_fail(cli_result_holder: dict[str, Any], code: int) -> None:
    res = cli_result_holder["result"]
    assert res is not None
    if res.exc_info:
        import traceback

        traceback.print_exception(*res.exc_info)
    assert res.exit_code == code, (
        f"Expected exit code {code}, got {res.exit_code}. Output:\n{res.output}"
    )


@then("the command should succeed")
def _command_should_succeed(cli_result_holder: dict[str, Any]) -> None:
    res = cli_result_holder["result"]
    assert res is not None
    if res.exc_info:
        import traceback

        traceback.print_exception(*res.exc_info)
    assert res.exit_code == 0, f"Command failed: {res.output}"


@then(parsers.parse('the output should contain "{text}"'))
def _output_should_contain(cli_result_holder: dict[str, Any], text: str) -> None:
    res = cli_result_holder["result"]
    assert res is not None
    assert text in res.output


@then("the output should list no available assets")
def _output_should_list_no_assets(cli_result_holder: dict[str, Any]) -> None:
    res = cli_result_holder["result"]
    assert res is not None
    assert "Available assets: <none>" in res.output or "Available assets:" not in res.output


@then("the output should list the candidate ids")
def _output_should_list_candidate_ids(cli_result_holder: dict[str, Any]) -> None:
    res = cli_result_holder["result"]
    assert res is not None
    assert "zoro-id-1" in res.output
    assert "zoro-id-2" in res.output


@then(parsers.parse('the image prompt should be de-tagged to "{expected_prompt}"'))
def _image_prompt_detagged(captured_calls: dict[str, list[Any]], expected_prompt: str) -> None:
    assert len(captured_calls["t2i"]) > 0
    # Lookup the req generated in the fake generate_image
    req = captured_calls["t2i"][0][1]
    assert req.prompt == expected_prompt
    # Also check reference staging on the request
    assert "zoro-id" in req.reference_entities
    # Check media references
    assert "logo-id" in [ref.name for ref in req.refs]
