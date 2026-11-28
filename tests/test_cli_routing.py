"""Route-aware client construction: every launch decides its visibility from planned routes.

SCENARIO.md rows 6 (a UI-bound shape launches headed), 11 and 33 (a credit-capped video routes
to the UI) and 34 (a mixed run launches headed; an all-direct run launches headless).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli import config
from gflow_cli._cli_helpers import (
    client_visibility,
    image_route,
    upscale_route,
    video_client_visibility,
    video_route,
)
from gflow_cli.api.generation_route import Route
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel


@pytest.fixture(autouse=True)
def _auto_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default settings: auto visibility, the default transport, no cap, no proxy."""
    from gflow_cli.api import flow_wire

    for name in (
        "GFLOW_CLI_BROWSER_VISIBILITY",
        "GFLOW_CLI_HEADLESS",
        "GFLOW_CLI_TRANSPORT",
        "GFLOW_CLI_MAX_CREDITS",
        "GFLOW_CLI_FLOW_HOST",
        "GFLOW_CLI_BROWSER_PROXY",
        "GFLOW_CLI_UI_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    config.reset_settings()
    yield
    config.reset_settings()


def measured_t2v(**overrides: object) -> GenerateVideoRequest:
    fields: dict[str, object] = {
        "prompt": "a lighthouse at dusk",
        "mode": Mode.T2V,
        "model": VideoModel.OMNI_1_2_FLASH,
        "duration": 4,
        "resolution": "360p",
        "aspect": Aspect.LANDSCAPE,
    }
    fields.update(overrides)
    return GenerateVideoRequest(**fields)  # type: ignore[arg-type]


def measured_t2i(**overrides: object) -> GenerateImageRequest:
    fields: dict[str, object] = {
        "prompt": "a lighthouse at dusk",
        "model": ImageModel.NARWHAL,
        "aspect": ImageAspect.LANDSCAPE,
    }
    fields.update(overrides)
    return GenerateImageRequest(**fields)  # type: ignore[arg-type]


# --- the helper ---------------------------------------------------------------------------


def test_ui_only_construction_is_headed_under_auto() -> None:
    assert client_visibility(config.get_settings()) is BrowserVisibility.HEADED


def test_measured_shapes_route_direct_and_launch_headless() -> None:
    routes = (video_route(measured_t2v()), image_route(measured_t2i()))
    assert routes == (Route.DIRECT, Route.DIRECT)
    assert client_visibility(config.get_settings(), routes) is BrowserVisibility.HEADLESS


def test_unmeasured_video_shape_routes_ui() -> None:
    assert video_route(measured_t2v(model=VideoModel.VEO_3_1_FAST, duration=8)) is Route.UI


def test_capped_video_routes_ui() -> None:
    """The request carries its effective cap; callers resolve ``GFLOW_CLI_MAX_CREDITS`` into
    it (``test_no_max_credits_route_ignores_configured_cap``, the worker tests below)."""
    assert video_route(measured_t2v(max_credits=50)) is Route.UI


def test_mention_in_a_video_prompt_routes_ui() -> None:
    """A mention resolves to a reference after launch, which the direct path cannot send."""
    assert video_route(measured_t2v(prompt="@Ada walks on the beach")) is Route.UI


def test_kill_switch_routes_every_shape_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    assert video_route(measured_t2v(), transport="ui_automation") is Route.UI
    assert image_route(measured_t2i(), transport="ui_automation") is Route.UI
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "ui_automation")
    assert video_route(measured_t2v()) is Route.UI
    row = {
        "flow_workflow_id": "wf",
        "model": "omni_1_2_flash",
        "aspect": "landscape",
        "duration_seconds": 4,
    }
    assert upscale_route("1080p", row) is Route.UI


def test_agentic_ui_mode_routes_images_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "agentic")
    config.reset_settings()
    assert image_route(measured_t2i()) is Route.UI


def test_proxy_routes_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.api import flow_wire

    monkeypatch.setattr(flow_wire, "getproxies", lambda: {"https": "http://proxy:8080"})
    assert video_route(measured_t2v()) is Route.UI
    assert image_route(measured_t2i()) is Route.UI


def test_explicit_headless_setting_keeps_its_meaning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_BROWSER_VISIBILITY", "headless")
    config.reset_settings()
    assert client_visibility(config.get_settings()) is BrowserVisibility.HEADLESS


async def test_empty_balance_refused_before_launch_for_a_direct_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli.api import flow_wire
    from gflow_cli.errors import InsufficientCreditsError, exit_code_for

    async def zero(_profile_dir: Path) -> object:
        return type("Info", (), {"credits": 0})()

    monkeypatch.setattr(flow_wire, "read_credits", zero)
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")  # the balance read is a wire read
    config.reset_settings()
    with pytest.raises(InsufficientCreditsError) as exc:
        await video_client_visibility(config.get_settings(), tmp_path, (Route.DIRECT,))
    assert exit_code_for(exc.value) == 37


async def test_balance_not_read_when_every_clip_routes_ui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli.api import flow_wire

    async def boom(_profile_dir: Path) -> object:
        raise AssertionError("the balance is read only for a planned direct clip")

    monkeypatch.setattr(flow_wire, "read_credits", boom)
    visibility = await video_client_visibility(config.get_settings(), tmp_path, (Route.UI,))
    assert visibility is BrowserVisibility.HEADED


# --- construction sites -------------------------------------------------------------------


class _StopError(Exception):
    """Raised from the fake client's ``__aenter__``: the test needs only the construction."""


def _recording_client() -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(side_effect=_StopError())
    client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=client)


def _launched(factory: MagicMock, tmp_path: Path) -> BrowserVisibility:
    """The visibility the real client launches at for the recorded construction (a
    construction that passes none takes the UI path's)."""
    from gflow_cli.api.client import FlowApiClient

    visibility = factory.call_args.kwargs.get("visibility")
    return FlowApiClient(profile_dir=tmp_path, visibility=visibility).visibility


def test_a_client_built_without_routes_takes_the_ui_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.client import FlowApiClient

    assert FlowApiClient(profile_dir=tmp_path).visibility is BrowserVisibility.HEADED
    monkeypatch.setenv("GFLOW_CLI_BROWSER_VISIBILITY", "headless")
    config.reset_settings()
    assert FlowApiClient(profile_dir=tmp_path).visibility is BrowserVisibility.HEADLESS


@pytest.fixture
def positive_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.api import flow_wire

    async def balance(_profile_dir: Path) -> object:
        return type("Info", (), {"credits": 100})()

    monkeypatch.setattr(flow_wire, "read_credits", balance)


def _invoke_t2v(tmp_path: Path, *args: str) -> MagicMock:
    from gflow_cli.cli import main

    factory = _recording_client()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_video.FlowApiClient", factory),
        patch("gflow_cli.cli_video.OperationRecorder"),
    ):
        CliRunner().invoke(main, ["video", "t2v", "a lighthouse at dusk", *args])
    factory.assert_called_once()
    return factory


@pytest.mark.usefixtures("positive_balance")
def test_unmeasured_shape_constructs_headed_client(tmp_path: Path) -> None:
    """SCENARIO.md row 6: an unmeasured shape takes the UI path, launched headed."""
    factory = _invoke_t2v(tmp_path, "--model", "veo-fast", "--duration", "8")
    assert _launched(factory, tmp_path) is BrowserVisibility.HEADED


@pytest.mark.usefixtures("positive_balance")
def test_measured_t2v_constructs_headless_client(tmp_path: Path) -> None:
    factory = _invoke_t2v(
        tmp_path,
        *("--model", "omni-1.2-flash", "--duration", "4", "--resolution", "360p"),
        *("--aspect", "16:9"),
    )
    assert _launched(factory, tmp_path) is BrowserVisibility.HEADLESS


def test_capped_t2v_constructs_headed_client(tmp_path: Path) -> None:
    """SCENARIO.md row 11: ``--max-credits`` on a measured key routes to the UI."""
    factory = _invoke_t2v(
        tmp_path,
        *("--model", "omni-1.2-flash", "--duration", "4", "--resolution", "360p"),
        *("--aspect", "16:9", "--max-credits", "50"),
    )
    assert _launched(factory, tmp_path) is BrowserVisibility.HEADED


def test_ui_only_commands_launch_headed_under_auto(tmp_path: Path) -> None:
    """SCENARIO.md row 6: a command with no direct route passes no routes (headed)."""
    from gflow_cli.cli import main

    factory = _recording_client()
    with (
        patch("gflow_cli.cli_character._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_character._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_character.FlowApiClient", factory),
    ):
        CliRunner().invoke(main, ["character", "list", "--project", "proj-1"])
    factory.assert_called_once()
    assert _launched(factory, tmp_path) is BrowserVisibility.HEADED


def _chain_manifest(tmp_path: Path, n: int) -> Path:
    path = tmp_path / "chain.jsonl"
    path.write_text("\n".join(f'{{"prompt": "link {i}"}}' for i in range(n)), encoding="utf-8")
    return path


def _invoke_chain(tmp_path: Path, done: int, *args: str) -> MagicMock:
    from gflow_cli.cli_video import video

    recorder = MagicMock()
    links = []
    for i in range(done):
        clip = tmp_path / f"done{i}.mp4"
        clip.touch()
        links.append(MagicMock(link_index=i, local_path=str(clip)))
    recorder.completed_links.return_value = links
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=client)
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.data.chain_repo.ChainLinkRecorder.open", return_value=recorder),
        patch("gflow_cli.cli_video.FlowApiClient", factory),
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock, return_value=[]) as run,
    ):
        CliRunner().invoke(video, ["chain", str(_chain_manifest(tmp_path, 3)), "--yes", *args])
    factory.assert_called_once()
    run.assert_awaited_once()
    return factory


def test_chain_with_a_ui_clip_launches_headed(tmp_path: Path) -> None:
    """SCENARIO.md row 34: link 0 is text-to-video on an unmeasured key: the run is headed."""
    factory = _invoke_chain(tmp_path, 0)
    assert _launched(factory, tmp_path) is BrowserVisibility.HEADED


@pytest.mark.usefixtures("positive_balance")
def test_all_direct_chain_launches_headless_and_reuses_page(tmp_path: Path) -> None:
    """SCENARIO.md row 34: a resumed chain of portrait Veo 3.1 Lite i2v links (the measured
    i2v key) launches headless, once, for every remaining link."""
    factory = _invoke_chain(tmp_path, 1, "--resume-from", "chain-abc")
    assert _launched(factory, tmp_path) is BrowserVisibility.HEADLESS


async def test_movie_run_with_a_ui_clip_launches_headed(tmp_path: Path) -> None:
    """SCENARIO.md row 34: a movie with a scene that needs the UI launches headed."""
    from gflow_cli import cli_movie
    from gflow_cli.composition import Scene, StyleSpec
    from gflow_cli.movie_manifest import MovieManifest, MovieState

    manifest = MovieManifest(
        title="T",
        project="p",
        style=StyleSpec(),
        characters={},
        scenes=(Scene(id="a", action="a wide shot", duration=8, model="veo-fast"),),
    )
    factory = _recording_client()
    with (
        patch.object(cli_movie, "FlowApiClient", factory),
        patch.object(cli_movie, "OperationRecorder"),
        pytest.raises(_StopError),
    ):
        await cli_movie._run_movie(  # pyright: ignore[reportPrivateUsage]
            manifest=manifest,
            state=MovieState(title="T", project="p"),
            state_path=tmp_path / "state.json",
            profile_name="default",
            profile_dir=tmp_path,
            out_dir=tmp_path,
            continue_on_error=False,
        )
    assert factory.call_args.kwargs["visibility"] is BrowserVisibility.HEADED


async def _run_rows(tmp_path: Path, rows: tuple[Any, ...], *, manifest: bool = False) -> object:
    from gflow_cli import image_batch

    factory = _recording_client()
    with pytest.raises(_StopError):
        if manifest:
            await image_batch.run_manifest_image_batch(
                profile_dir=tmp_path,
                transport=None,
                prompts=rows,
                output_dir=tmp_path,
                continue_on_error=False,
                client_factory=factory,
            )
        else:
            await image_batch.run_image_batch(
                profile_dir=tmp_path,
                transport=None,
                prompts=rows,
                output_dir=tmp_path,
                continue_on_error=False,
                project_title="t",
                client_factory=factory,
                _command="run",
                project_id="proj-1",
            )
    return _launched(factory, tmp_path)


async def test_run_and_image_batch_with_a_ui_row_launch_headed(tmp_path: Path) -> None:
    """SCENARIO.md row 34: one row with a reference makes the whole run headed; the
    stay-mounted ``image batch`` drives the Flow UI for every row, so it is headed too."""
    from gflow_cli.image_batch import BatchPromptItem

    t2i = BatchPromptItem(text="a lighthouse", index=0)
    child = BatchPromptItem(text="the same lighthouse at night", index=1, ref="batch:0")
    assert await _run_rows(tmp_path, (t2i, child)) is BrowserVisibility.HEADED
    assert await _run_rows(tmp_path, (t2i,), manifest=True) is BrowserVisibility.HEADED


async def test_all_text_to_image_run_launches_headless(tmp_path: Path) -> None:
    from gflow_cli.image_batch import BatchPromptItem

    rows = tuple(BatchPromptItem(text=f"lighthouse {i}", index=i) for i in range(4))
    assert await _run_rows(tmp_path, rows) is BrowserVisibility.HEADLESS


async def _worker_video_visibility(tmp_path: Path, payload: dict[str, object]) -> object:
    from gflow_cli.data.store import DataStore
    from gflow_cli.worker.daemon import FlowWorker
    from gflow_cli.worker.queue import QueueRepository

    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DataStore.open(tmp_path / "queue.db")
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', 'C:/profiles/default', '2026-06-24T00:00:00Z')"
    )
    task = QueueRepository(store).enqueue_task(
        task_id="task-route", profile_name="default", task_type="t2v", payload=payload
    )
    worker = FlowWorker("default", str(store.path))
    factory = _recording_client()
    try:
        with patch("gflow_cli.worker.daemon.FlowApiClient", factory):
            await worker.process_task(task)
    finally:
        worker.close()
        store.close()
    factory.assert_called_once()
    return _launched(factory, tmp_path)


_MEASURED_T2V_PAYLOAD: dict[str, object] = {
    "prompt": "a lighthouse at dusk",
    "model": "omni-1.2-flash",
    "duration": 4,
    "resolution": "360p",
    "aspect": "16:9",
}


@pytest.mark.usefixtures("positive_balance")
async def test_worker_routes_after_effective_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCENARIO.md row 11: the configured cap is resolved before the route, so a job with no
    cap of its own never launches headless while ``GFLOW_CLI_MAX_CREDITS`` is set."""
    visibility = await _worker_video_visibility(tmp_path / "open", _MEASURED_T2V_PAYLOAD)
    assert visibility is BrowserVisibility.HEADLESS
    monkeypatch.setenv("GFLOW_CLI_MAX_CREDITS", "50")
    config.reset_settings()
    visibility = await _worker_video_visibility(tmp_path / "capped", _MEASURED_T2V_PAYLOAD)
    assert visibility is BrowserVisibility.HEADED


async def test_mcp_capped_video_constructs_headed_client(tmp_path: Path) -> None:
    """SCENARIO.md row 33: MCP ``max_credits`` travels in the queued payload; the worker
    builds the client headed for it."""
    payload = {**_MEASURED_T2V_PAYLOAD, "max_credits": 50}
    assert await _worker_video_visibility(tmp_path, payload) is BrowserVisibility.HEADED


# --- council FIX items ---------------------------------------------------------------------


def test_dotenv_kill_switch_forces_ui_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``.env`` line reaches the settings, never ``os.environ``: the CLI honours it as the
    worker does."""
    import os

    from gflow_cli.api.transports import make_transport

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GFLOW_CLI_TRANSPORT=ui_automation\n", encoding="utf-8")
    config.reset_settings()
    assert "GFLOW_CLI_TRANSPORT" not in os.environ
    assert image_route(measured_t2i()) is Route.UI
    assert make_transport().name == "ui_automation"


def test_t2i_harbor_seal_count4_plans_headed(tmp_path: Path) -> None:
    """HARBOR_SEAL was measured with count 1 only: ``-n 4`` plans the UI, launched headed."""
    from gflow_cli.cli import main

    factory = _recording_client()
    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_image.FlowApiClient", factory),
        patch("gflow_cli.cli_image.OperationRecorder"),
    ):
        CliRunner().invoke(
            main,
            [
                *("image", "t2i", "a lighthouse"),
                *("--model", "nano2-lite", "--aspect", "9:16", "-n", "4"),
            ],
        )
    factory.assert_called_once()
    assert factory.call_args.kwargs["visibility"] is BrowserVisibility.HEADED


def test_batch_row_count4_harbor_seal_plans_ui() -> None:
    """The planned row is the request the batch sends, count included."""
    from gflow_cli.image_batch import BatchPromptItem, planned_row_route

    one = BatchPromptItem(text="a lighthouse", aspect_ratio="9:16", model="nano2-lite")
    assert planned_row_route(one, None) is Route.DIRECT
    four = BatchPromptItem(text="a lighthouse", aspect_ratio="9:16", model="nano2-lite", count=4)
    assert planned_row_route(four, None) is Route.UI


@pytest.mark.usefixtures("positive_balance")
async def test_movie_run_all_direct_clips_launches_headless(tmp_path: Path) -> None:
    """No movie scene reaches a measured key today (a scene carries no resolution), so the
    planner is pinned to direct: the run must launch headless from the planned routes."""
    from gflow_cli import cli_movie
    from gflow_cli.composition import Scene, StyleSpec
    from gflow_cli.movie_manifest import MovieManifest, MovieState

    manifest = MovieManifest(
        title="T",
        project="p",
        style=StyleSpec(),
        characters={},
        scenes=(Scene(id="a", action="a wide shot", duration=4, model="omni-1.2-flash"),),
    )
    factory = _recording_client()
    with (
        patch.object(cli_movie, "FlowApiClient", factory),
        patch.object(cli_movie, "OperationRecorder"),
        patch.object(cli_movie, "_planned_scene_route", return_value=Route.DIRECT),
        pytest.raises(_StopError),
    ):
        await cli_movie._run_movie(  # pyright: ignore[reportPrivateUsage]
            manifest=manifest,
            state=MovieState(title="T", project="p"),
            state_path=tmp_path / "state.json",
            profile_name="default",
            profile_dir=tmp_path,
            out_dir=tmp_path,
            continue_on_error=False,
        )
    assert factory.call_args.kwargs["visibility"] is BrowserVisibility.HEADLESS


def test_t2v_empty_balance_exits_37_before_client_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api import flow_wire
    from gflow_cli.cli import main

    async def zero(_profile_dir: Path) -> object:
        return type("Info", (), {"credits": 0})()

    monkeypatch.setattr(flow_wire, "read_credits", zero)
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")  # the balance read is a wire read
    config.reset_settings()
    factory = _recording_client()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_video.FlowApiClient", factory),
        patch("gflow_cli.cli_video.OperationRecorder"),
    ):
        result = CliRunner().invoke(
            main,
            [
                *("video", "t2v", "a lighthouse at dusk", "--model", "omni-1.2-flash"),
                *("--duration", "4", "--resolution", "360p", "--aspect", "16:9"),
            ],
        )
    assert result.exit_code == 37
    factory.assert_not_called()


@pytest.mark.usefixtures("positive_balance")
def test_no_max_credits_route_ignores_configured_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-max-credits`` sends an uncapped request: it routes on its own shape, while the
    same command without the flag still takes the configured cap to the UI."""
    monkeypatch.setenv("GFLOW_CLI_MAX_CREDITS", "50")
    config.reset_settings()
    measured = ("--model", "omni-1.2-flash", "--duration", "4", "--resolution", "360p")
    capped = _invoke_t2v(tmp_path, *measured, "--aspect", "16:9")
    assert _launched(capped, tmp_path) is BrowserVisibility.HEADED
    uncapped = _invoke_t2v(tmp_path, *measured, "--aspect", "16:9", "--no-max-credits")
    assert _launched(uncapped, tmp_path) is BrowserVisibility.HEADLESS
    assert video_route(measured_t2v()) is Route.DIRECT
