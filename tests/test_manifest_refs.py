"""Manifest references: what each command accepts, and what is refused up front (#913).

Every ``ref`` / ``reference_entity`` form used to be parsed and then ignored (measured
live, docs/superpowers/spikes/2026-10-01-batch-ref-dropped.md). Now ``gflow run --config``
honours ``"ref": "batch:N"``; every other form, and any reference in ``gflow image
batch``, is refused before browser work, naming the row and the field.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main as cli_main
from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import parse_batch_item_dict

_NOT_YET = [
    ("ref", "11111111-1111-1111-1111-111111111111"),
    ("reference_entity", "11111111-1111-1111-1111-111111111111"),
    ("reference_entity", "batch:0"),
]


@pytest.mark.parametrize(("field", "value"), _NOT_YET)
def test_parse_refuses_forms_that_are_not_wired(field: str, value: str) -> None:
    with pytest.raises(ConfigurationError) as info:
        parse_batch_item_dict({"text": "x", field: value}, 1)
    assert f"prompts[1].{field}" in str(info.value)
    assert "#913" in str(info.value)


def test_parse_accepts_a_batch_reference() -> None:
    assert parse_batch_item_dict({"text": "x", "ref": "batch:0"}, 1).ref == "batch:0"


@pytest.mark.parametrize("field", ["ref", "reference_entity"])
def test_parse_accepts_explicit_null(field: str) -> None:
    item = parse_batch_item_dict({"text": "x", field: None}, 0)
    assert item.ref is None
    assert item.reference_entity is None


def _rows(field: str, value: str) -> list[dict[str, str]]:
    return [{"text": "a red apple"}, {"text": "the same apple, green", field: value}]


def _run_config(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    cfg = tmp_path / "run.json"
    cfg.write_text(json.dumps({"prompts": rows}), encoding="utf-8")
    return cfg


@pytest.mark.parametrize(("field", "value"), _NOT_YET)
def test_run_config_refuses_unwired_forms_before_any_browser(
    tmp_path: Path, field: str, value: str
) -> None:
    cfg = _run_config(tmp_path, _rows(field, value))  # type: ignore[arg-type]
    with patch("gflow_cli.cli_run.FlowApiClient") as client:
        result = CliRunner().invoke(cli_main, ["run", "--config", str(cfg)])
    assert result.exit_code == 11, result.output
    assert f"prompts[1].{field}" in result.output
    client.assert_not_called()


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([{"text": "a"}, {"text": "b", "ref": "batch:7"}], "batch:7"),
        ([{"text": "a", "ref": "batch:0"}], "itself"),
        ([{"text": "a", "ref": "batch:1"}, {"text": "b", "ref": "batch:0"}], "cycle"),
        ([{"text": "a", "count": 2}, {"text": "b", "ref": "batch:0"}], "makes 2 images"),
        ([{"text": "a"}, {"text": "b", "ref": "batch:01"}], "batch:01"),
    ],
)
def test_run_config_refuses_bad_batch_references_before_any_browser(
    tmp_path: Path, rows: list[dict[str, object]], expected: str
) -> None:
    cfg = _run_config(tmp_path, rows)
    with patch("gflow_cli.cli_run.FlowApiClient") as client:
        result = CliRunner().invoke(cli_main, ["run", "--config", str(cfg)])
    assert result.exit_code == 11, result.output
    assert expected in result.output
    client.assert_not_called()


@pytest.mark.parametrize(("field", "value"), [("ref", "batch:0"), *_NOT_YET])
def test_image_batch_refuses_every_reference_before_any_browser(
    tmp_path: Path, field: str, value: str
) -> None:
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(_rows(field, value)), encoding="utf-8")
    with patch("gflow_cli.cli_image.FlowApiClient") as client:
        result = CliRunner().invoke(cli_main, ["image", "batch", str(manifest)])
    # `image batch` reports manifest problems as a usage error (exit 2), as it does for
    # every other malformed manifest field (`_as_usage_error`, cli_image.py).
    assert result.exit_code == 2, result.output
    assert f"prompts[1].{field}" in result.output
    if field == "ref" and value.startswith("batch:"):
        assert "gflow run --config" in result.output
    client.assert_not_called()


# --- local-file references (PR C) -------------------------------------------------

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _cfg_with_ref(tmp_path: Path, ref: str) -> Path:
    return _run_config(tmp_path, [{"text": "a"}, {"text": "b", "ref": ref}])


def test_a_local_file_ref_resolves_against_the_config_folder(tmp_path: Path) -> None:
    from gflow_cli.cli_run import BatchConfig

    (tmp_path / "refs").mkdir()
    (tmp_path / "refs" / "photo.png").write_bytes(_PNG)
    cfg = _cfg_with_ref(tmp_path, "refs/photo.png")
    config = BatchConfig.from_json_path(cfg)
    assert config.prompts[1].ref == str((tmp_path / "refs" / "photo.png").resolve())


@pytest.mark.parametrize(
    ("ref", "content", "expected"),
    [
        ("missing.png", None, "does not exist"),
        ("notes.png", b"just some text, not an image", "not a supported image"),
        ("11111111-1111-1111-1111-111111111111", None, "media id"),
    ],
)
def test_a_bad_local_file_ref_is_refused_before_any_browser(
    tmp_path: Path, ref: str, content: bytes | None, expected: str
) -> None:
    if content is not None:
        (tmp_path / ref).write_bytes(content)
    cfg = _cfg_with_ref(tmp_path, ref)
    with patch("gflow_cli.cli_run.FlowApiClient") as client:
        result = CliRunner().invoke(cli_main, ["run", "--config", str(cfg)])
    assert result.exit_code == 11, result.output
    flat = " ".join(result.output.split()).lower()  # Rich wraps long messages
    assert "prompts[1].ref" in flat and expected in flat
    client.assert_not_called()


def test_image_batch_still_refuses_a_file_ref(tmp_path: Path) -> None:
    (tmp_path / "photo.png").write_bytes(_PNG)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(_rows("ref", "photo.png")), encoding="utf-8")
    result = CliRunner().invoke(cli_main, ["image", "batch", str(manifest)])
    assert result.exit_code == 2, result.output
    assert "gflow run --config" in result.output
