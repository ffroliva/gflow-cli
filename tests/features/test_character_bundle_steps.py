"""Offline steps for tests/features/character_bundle.feature (no browser, no Flow)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.character_bundle import read_bundle, write_bundle
from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError

scenarios("character_bundle.feature")


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 24), (10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Any]:
    return {"out": tmp_path / "bundle"}


def _write(world: dict[str, Any], name: str = "Nora", voice: str | None = "Aoede") -> None:
    world["bundle"] = write_bundle(
        world["out"], name=name, voice=voice, personality="calm", images=[("portrait", _jpeg())]
    )


@given(parsers.parse('a character named "{name}" with voice "{voice}" and a portrait'))
def _character(world: dict[str, Any], name: str, voice: str) -> None:
    world["character"] = (name, voice)


@when("its bundle is written")
def _written(world: dict[str, Any]) -> None:
    _write(world, *world["character"])
    world["manifest"] = (world["out"] / "manifest.json").read_text(encoding="utf-8")


@then("the manifest holds only name, voice, personality and images")
def _allowlist(world: dict[str, Any]) -> None:
    assert set(json.loads(world["manifest"])) == {
        "schema_version",
        "name",
        "voice",
        "personality",
        "images",
    }


@then("the manifest contains no URL, address, project id or entity id")
def _no_identity(world: dict[str, Any]) -> None:
    text = world["manifest"]
    assert "://" not in text and "@" not in text
    assert "project" not in text and "entity" not in text


@given(parsers.parse('a written bundle whose manifest names the image "{file}"'))
def _escaping(world: dict[str, Any], file: str) -> None:
    _write(world)
    path = world["out"] / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["images"][0]["file"] = file
    path.write_text(json.dumps(data), encoding="utf-8")
    world["file"] = file


@given("a written bundle whose portrait was replaced after export")
def _swapped(world: dict[str, Any]) -> None:
    _write(world)
    buf = io.BytesIO()
    Image.new("RGB", (16, 24), (200, 0, 0)).save(buf, format="JPEG")
    (world["out"] / "portrait.jpg").write_bytes(buf.getvalue())
    world["file"] = "portrait.jpg"


@when("the bundle is read")
def _read(world: dict[str, Any]) -> None:
    try:
        read_bundle(world["out"])
    except ConfigurationError as exc:
        world["error"] = exc
    else:
        world["error"] = None


@then("it is refused with exit code 11, naming the file")
def _refused(world: dict[str, Any]) -> None:
    exc = world["error"]
    assert isinstance(exc, ConfigurationError)
    assert EXIT_CODE_MAP[ConfigurationError] == 11
    assert world["file"] in str(exc)
