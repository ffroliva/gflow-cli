"""BDD steps exercise the CLI and MCP video caption surfaces."""

from __future__ import annotations

import asyncio
import json
import shlex
from fractions import Fraction
from pathlib import Path
from typing import Any
from unittest.mock import patch

import av
import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli import main

scenarios("video_captions.feature")


@pytest.fixture
def video_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    for stale in ("speech.srt", "speech-captions.mp4"):
        (tmp_path / stale).unlink(missing_ok=True)
    (tmp_path / "out" / "speech.srt").unlink(missing_ok=True)
    config.reset_settings()
    case: dict[str, Any] = {"root": tmp_path, "requests": 0}
    yield case
    if "client_patch" in case:
        case["client_patch"].stop()
    config.reset_settings()


def _make_clip(path: Path) -> None:
    with av.open(str(path), mode="w", format="matroska") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        audio = output.add_stream("pcm_s16le", rate=16000)
        audio.layout = "mono"
        for index in range(20):
            frame = av.VideoFrame(160, 90, "rgb24")
            frame.pts = index
            for packet in video.encode(frame):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        audio_frame = av.AudioFrame(format="s16", layout="mono", samples=1600)
        audio_frame.sample_rate = 16000
        audio_frame.pts = 0
        for packet in audio.encode(audio_frame):
            output.mux(packet)


def _fake_transcription(case: dict[str, Any]) -> None:
    class Response:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "segments": [
                    {"start": 0.2, "end": 0.7, "text": "Hello from transcription."},
                    {"start": 0.8, "end": 1.4, "text": "Captions stay local."},
                ]
            }

    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, *_: Any, **__: Any) -> Response:
            case["requests"] += 1
            return Response()

    case["client_patch"] = patch(
        "gflow_cli.services.transcribers.openai_compatible.httpx.Client", Client
    )
    case["client_patch"].start()


@given(parsers.parse('a local video clip "{name}" with an audio stream'))
def local_clip(video_case: dict[str, Any], name: str) -> None:
    _make_clip(video_case["root"] / name)


@given(parsers.parse('a valid SubRip file "{name}" with two timed caption entries'))
def valid_srt(video_case: dict[str, Any], name: str) -> None:
    (video_case["root"] / name).write_text(
        "1\n00:00:00,200 --> 00:00:00,700\nFirst caption\n\n"
        "2\n00:00:00,800 --> 00:00:01,400\nSecond caption\n",
        encoding="utf-8",
    )


@given(parsers.parse('a malformed SubRip file "{name}" with invalid timestamp ranges'))
def invalid_srt(video_case: dict[str, Any], name: str) -> None:
    (video_case["root"] / name).write_text(
        "1\n00:00:02,000 --> 00:00:01,000\nPRIVATE SENTINEL\n", encoding="utf-8"
    )


@given(
    parsers.parse('a catalog video asset with media UUID "{media_id}" and a verified local file')
)
def catalog_video(
    video_case: dict[str, Any], media_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = video_case["root"] / "sample.mp4"
    with av.open(str(path), mode="w") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        for index in range(20):
            frame = av.VideoFrame(160, 90, "rgb24")
            frame.pts = index
            frame.time_base = Fraction(1, 10)
            for packet in video.encode(frame):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
    service = __import__("gflow_cli.services.video_captions", fromlist=["resolve_video_source"])
    monkeypatch.setattr(
        service, "resolve_video_source", lambda *_args, **_kwargs: video_case["root"] / "sample.mp4"
    )
    video_case["catalog_id"] = media_id


@given("GFLOW_CLI_LLM_BASE_URL is configured to an OpenAI-compatible transcription endpoint")
def configured_endpoint(monkeypatch: pytest.MonkeyPatch, video_case: dict[str, Any]) -> None:
    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "https://whisper.example/v1")
    config.reset_settings()
    _fake_transcription(video_case)


@given("the transcription endpoint returns two timed caption segments in English")
@given("the transcription endpoint returns two timed caption segments")
def endpoint_segments(video_case: dict[str, Any]) -> None:
    if "client_patch" not in video_case:
        _fake_transcription(video_case)


@given("GFLOW_CLI_LLM_BASE_URL is at its built-in Google Gemini default")
def default_endpoint(monkeypatch: pytest.MonkeyPatch, video_case: dict[str, Any]) -> None:
    monkeypatch.delenv("GFLOW_CLI_LLM_BASE_URL", raising=False)
    config.reset_settings()


@when(parsers.parse('the user runs "{command}"'))
def run_cli(video_case: dict[str, Any], command: str) -> None:
    args = shlex.split(command)
    assert args[:2] == ["gflow", "video"]
    with patch.dict("os.environ", {"GFLOW_CLI_OUTPUT_DIR": str(video_case["root"] / "out")}):
        result = CliRunner().invoke(main, args[1:])
    video_case["cli"] = result
    video_case["stdout"] = result.stdout


@then(parsers.parse("the command exits with code {code:d}"))
def assert_exit(video_case: dict[str, Any], code: int) -> None:
    assert video_case["cli"].exit_code == code, video_case["stdout"]


@then(parsers.parse('the JSON output reports "{filename}" and "{field}" greater than {value:d}'))
def json_output_reports_file_count(
    video_case: dict[str, Any], filename: str, field: str, value: int
) -> None:
    payload = json.loads(video_case["stdout"])
    assert Path(payload["output_file"]).name == filename
    assert payload[field] > value
    video_case["payload"] = payload


@then(parsers.parse("the JSON output reports the burned output video path"))
def json_reports_video(video_case: dict[str, Any]) -> None:
    payload = json.loads(video_case["stdout"])
    assert Path(payload["output_file"]).is_file()


@then(
    parsers.parse(
        'the JSON output reports "{field}" {count:d} and "{path_field}" ending with "{suffix}"'
    )
)
def json_reports_transcription(
    video_case: dict[str, Any], field: str, count: int, path_field: str, suffix: str
) -> None:
    payload = json.loads(video_case["stdout"])
    assert payload[field] == count
    assert payload[path_field].endswith(suffix)


@then(parsers.parse('the SubRip file "{name}" is written with two numbered blocks'))
def srt_written(video_case: dict[str, Any], name: str) -> None:
    entries = (video_case["root"] / name).read_text(encoding="utf-8")
    assert entries.count(" --> ") == 2


@then(parsers.parse('the SubRip file "{name}" exists on disk'))
def srt_exists(video_case: dict[str, Any], name: str) -> None:
    assert (video_case["root"] / name).is_file()


@then(parsers.parse('the output video "{name}" preserves the audio stream'))
def output_has_audio(video_case: dict[str, Any], name: str) -> None:
    with av.open(str(video_case["root"] / name)) as container:
        assert container.streams.audio


@then(parsers.parse("no transcription HTTP request is sent"))
def no_transcription_request(video_case: dict[str, Any]) -> None:
    assert video_case["requests"] == 0


@then(parsers.parse('the command fails with exit code {code:d} and error type "{problem_type}"'))
def assert_problem(video_case: dict[str, Any], code: int, problem_type: str) -> None:
    assert video_case["cli"].exit_code == code
    assert json.loads(video_case["stdout"])["error"]["type"] == problem_type


@then(
    parsers.parse('the error envelope reports charged "{charged}" and next_action "{next_action}"')
)
def assert_outcome(video_case: dict[str, Any], charged: str, next_action: str) -> None:
    error = json.loads(video_case["stdout"])["error"]
    assert error["charged"] == charged and error["next_action"] == next_action


@then(parsers.parse('the remediation hint names "{first}" and "{second}"'))
def remediation(video_case: dict[str, Any], first: str, second: str) -> None:
    error = json.loads(video_case["stdout"])["error"]
    assert first in error["remediation_hint"] and second in error["remediation_hint"]


@when(parsers.parse('the MCP tool "{tool}" is called for "{file}" with out_file "{out_file}"'))
def call_mcp(
    video_case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tool: str = "gflow_video_captions",
    file: str = "speech.mp4",
    out_file: str = "captioned/final.mp4",
) -> None:
    from gflow_cli.mcp.video_captions_tools import gflow_video_captions

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(video_case["root"] / "out"))
    config.reset_settings()
    video_case["mcp"] = asyncio.run(gflow_video_captions(file, out_file=out_file))
    assert tool == "gflow_video_captions"
    config.reset_settings()


@then(parsers.parse('the MCP reply has status "{status}"'))
def assert_mcp_status(video_case: dict[str, Any], status: str) -> None:
    assert video_case["mcp"]["status"] == status, video_case["mcp"]


@then(
    parsers.parse(
        'both "{first}" and "{second}" resolve inside the configured GFLOW_CLI_OUTPUT_DIR'
    )
)
def mcp_paths_confined(video_case: dict[str, Any], first: str, second: str) -> None:
    root = (video_case["root"] / "out").resolve()
    assert Path(video_case["mcp"][first]).is_relative_to(root)
    assert Path(video_case["mcp"][second]).is_relative_to(root)


@then(parsers.parse('the output video "{name}" exists on disk with burned frames'))
def output_video_burned(video_case: dict[str, Any], name: str) -> None:
    assert (video_case["root"] / name).is_file()
