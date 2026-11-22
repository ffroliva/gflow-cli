"""Offline BDD coverage for the audio narration command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli import main

scenarios("audio_narrate.feature")


@pytest.fixture
def narration_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GFLOW_CLI_GEMINI_TTS_API_KEY", raising=False)
    config.reset_settings()
    case: dict[str, Any] = {"root": tmp_path, "requests": 0}
    yield case
    config.reset_settings()


@given("edge TTS is unavailable")
def edge_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "edge_tts", None)


@given(parsers.parse('narration output "{name}" already contains "{contents}"'))
def existing_output(narration_case: dict[str, Any], name: str, contents: str) -> None:
    (narration_case["root"] / name).write_text(contents, encoding="utf-8")


@given("Gemini TTS has a configured key")
def gemini_key(monkeypatch: pytest.MonkeyPatch, narration_case: dict[str, Any]) -> None:
    case = narration_case

    class SpyClient:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> SpyClient:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def post(self, *_: object, **__: object) -> httpx.Response:
            case["requests"] += 1
            pytest.fail("Gemini request was sent unexpectedly")

    monkeypatch.setenv("GFLOW_CLI_GEMINI_TTS_API_KEY", "test-key")
    monkeypatch.setattr("gflow_cli.services.speech_synthesizers.gemini.httpx.Client", SpyClient)
    config.reset_settings()


@given("Gemini TTS returns an error containing private diagnostics")
def gemini_error(monkeypatch: pytest.MonkeyPatch, narration_case: dict[str, Any]) -> None:
    case = narration_case

    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def post(self, *_: object, **__: object) -> httpx.Response:
            case["requests"] += 1
            return httpx.Response(500, text="private narration test-key")

    monkeypatch.setenv("GFLOW_CLI_GEMINI_TTS_API_KEY", "test-key")
    monkeypatch.setattr("gflow_cli.services.speech_synthesizers.gemini.httpx.Client", Client)
    config.reset_settings()


@when(parsers.parse('I narrate "{text}" to "{name}" with provider "{provider}"'))
def narrate_text(narration_case: dict[str, Any], text: str, name: str, provider: str) -> None:
    result = CliRunner().invoke(
        main, ["audio", "narrate", text, "--out-file", name, "--provider", provider, "--json"]
    )
    narration_case["result"] = result


@when(parsers.parse('I narrate "{text}" to "{name}" with provider "{provider}" and rate "{rate}"'))
def narrate_rate(
    narration_case: dict[str, Any], text: str, name: str, provider: str, rate: str
) -> None:
    result = CliRunner().invoke(
        main,
        [
            "audio",
            "narrate",
            text,
            "--out-file",
            name,
            "--provider",
            provider,
            "--rate",
            rate,
            "--json",
        ],
    )
    narration_case["result"] = result


@then("audio narration fails with a configuration error")
def configuration_error(narration_case: dict[str, Any]) -> None:
    result = narration_case["result"]
    assert result.exit_code == 11, result.stdout
    assert (
        json.loads(result.stdout)["error"]["type"] == "https://gflow-cli.dev/errors/configuration"
    )


@then("audio narration fails with a typed provider error")
def provider_error(narration_case: dict[str, Any]) -> None:
    result = narration_case["result"]
    assert result.exit_code == 6, result.stdout
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "https://gflow-cli.dev/errors/network"
    assert error["charged"] == "maybe"  # the request reached Google, which may bill it


@then("no narration file is created")
def output_missing(narration_case: dict[str, Any]) -> None:
    assert not (narration_case["root"] / "missing.mp3").exists()


@then(parsers.parse('narration output "{name}" still contains "{contents}"'))
def output_preserved(narration_case: dict[str, Any], name: str, contents: str) -> None:
    assert (narration_case["root"] / name).read_text(encoding="utf-8") == contents


@then("no Gemini request is sent")
def no_gemini_request(narration_case: dict[str, Any]) -> None:
    assert narration_case["requests"] == 0


@then("the response omits the private text and key")
def response_redacted(narration_case: dict[str, Any]) -> None:
    output = narration_case["result"].stdout
    assert "private narration" not in output
    assert "test-key" not in output
    assert narration_case["requests"] == 1
