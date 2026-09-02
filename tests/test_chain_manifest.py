"""Unit tests for gflow_cli.chain_manifest — JSONL chain-manifest parser (Task 6).

The parser turns an ordered JSONL manifest into ``list[ChainLinkSpec]``, one
spec per line, preserving order. ``prompt`` is required; ``model`` / ``duration``
/ ``aspect`` are optional per-link overrides mapped through the SAME canonical
path the CLI uses (``VideoModel.from_cli`` / ``Aspect.from_cli``). Blank lines
and ``#``-comment lines are skipped. Any malformed input raises
``ChainManifestError`` citing the offending line number.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.video import Aspect, VideoModel
from gflow_cli.chain import ChainLinkSpec
from gflow_cli.chain_manifest import parse_chain_manifest
from gflow_cli.errors import ChainManifestError, ConfigurationError


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "chain.jsonl"
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_parses_minimal_prompt_only_line(tmp_path: Path) -> None:
    path = _write(tmp_path, '{"prompt": "a lone wolf on a ridge"}\n')

    links = parse_chain_manifest(path)

    assert links == [ChainLinkSpec(prompt="a lone wolf on a ridge")]
    assert links[0].model is None
    assert links[0].duration is None
    assert links[0].aspect is None


def test_parses_multi_link_in_order_with_and_without_overrides(tmp_path: Path) -> None:
    """Parsing ``duration`` is NOT the same as a chain accepting it (#634/#635).

    This asserts the parser's contract only — ``run_chain`` rejects any link
    carrying a ``duration``; see
    ``tests/test_chain.py::test_rejects_per_link_duration_up_front``.
    """
    path = _write(
        tmp_path,
        '{"prompt": "first", "model": "veo-lite", "duration": 4, "aspect": "16:9"}\n'
        '{"prompt": "second"}\n'
        '{"prompt": "third", "duration": 8}\n',
    )

    links = parse_chain_manifest(path)

    assert [link.prompt for link in links] == ["first", "second", "third"]
    # Link 0: full overrides, mapped via the canonical from_cli paths.
    assert links[0].model is VideoModel.VEO_3_1_LITE
    assert links[0].duration == 4
    assert links[0].aspect is Aspect.LANDSCAPE
    # Link 1: prompt-only — everything inherits (None).
    assert links[1] == ChainLinkSpec(prompt="second")
    # Link 2: partial override (duration only).
    assert links[2].model is None
    assert links[2].duration == 8
    assert links[2].aspect is None


def test_model_alias_maps_through_from_cli(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"prompt": "p", "model": "veo-quality"}\n{"prompt": "q", "model": "veo_3_1_fast"}\n',
    )

    links = parse_chain_manifest(path)

    assert links[0].model is VideoModel.VEO_3_1_QUALITY
    assert links[1].model is VideoModel.VEO_3_1_FAST


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("9:16", Aspect.PORTRAIT),
        ("16:9", Aspect.LANDSCAPE),
        ("1:1", Aspect.SQUARE),
    ],
)
def test_aspect_token_maps_through_from_cli(tmp_path: Path, token: str, expected: Aspect) -> None:
    path = _write(tmp_path, f'{{"prompt": "p", "aspect": "{token}"}}\n')

    links = parse_chain_manifest(path)

    assert links[0].aspect is expected


# --------------------------------------------------------------------------- #
# Comment / blank-line skipping
# --------------------------------------------------------------------------- #


def test_skips_blank_and_comment_lines_preserving_order(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '# a leading comment\n\n{"prompt": "first"}\n   \n# mid comment\n{"prompt": "second"}\n',
    )

    links = parse_chain_manifest(path)

    assert [link.prompt for link in links] == ["first", "second"]


# --------------------------------------------------------------------------- #
# Empty manifest
# --------------------------------------------------------------------------- #


def test_empty_file_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "")

    with pytest.raises(ChainManifestError):
        parse_chain_manifest(path)


def test_comments_only_file_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "# only a comment\n\n   \n")

    with pytest.raises(ChainManifestError):
        parse_chain_manifest(path)


def test_chain_manifest_error_is_configuration_error(tmp_path: Path) -> None:
    # Inherits ConfigurationError -> exit code 11 via the EXIT_CODE_MAP walk.
    path = _write(tmp_path, "")

    with pytest.raises(ConfigurationError):
        parse_chain_manifest(path)


# --------------------------------------------------------------------------- #
# Malformed rows — each cites the offending line number
# --------------------------------------------------------------------------- #


def test_bad_json_cites_line_number(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"prompt": "ok"}\n{not valid json\n',
    )

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 2" in str(exc_info.value)


def test_non_object_json_cites_line_number(tmp_path: Path) -> None:
    # A JSON array is valid JSON but not a chain-link object.
    path = _write(tmp_path, '["prompt", "not an object"]\n')

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 1" in str(exc_info.value)


def test_missing_prompt_cites_line_number(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"prompt": "ok"}\n{"model": "veo-lite"}\n',
    )

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 2" in str(exc_info.value)


def test_empty_prompt_cites_line_number(tmp_path: Path) -> None:
    path = _write(tmp_path, '{"prompt": "   "}\n')

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 1" in str(exc_info.value)


def test_non_string_prompt_cites_line_number(tmp_path: Path) -> None:
    path = _write(tmp_path, '{"prompt": 123}\n')

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 1" in str(exc_info.value)


def test_unknown_model_cites_line_number(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"prompt": "ok"}\n{"prompt": "bad", "model": "totally-made-up"}\n',
    )

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 2" in str(exc_info.value)


def test_invalid_aspect_cites_line_number(tmp_path: Path) -> None:
    path = _write(tmp_path, '{"prompt": "p", "aspect": "4:3"}\n')

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 1" in str(exc_info.value)


def test_non_int_duration_cites_line_number(tmp_path: Path) -> None:
    path = _write(tmp_path, '{"prompt": "p", "duration": "four"}\n')

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 1" in str(exc_info.value)


def test_bool_duration_rejected(tmp_path: Path) -> None:
    # bool is a subclass of int; a JSON true must NOT slip through as duration.
    path = _write(tmp_path, '{"prompt": "p", "duration": true}\n')

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 1" in str(exc_info.value)


def test_unknown_key_cites_line_number(tmp_path: Path) -> None:
    # Surface a typo'd field rather than silently dropping it.
    path = _write(tmp_path, '{"prompt": "p", "modle": "veo-lite"}\n')

    with pytest.raises(ChainManifestError) as exc_info:
        parse_chain_manifest(path)

    assert "line 1" in str(exc_info.value)
