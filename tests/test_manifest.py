"""Tests for the TSV manifest parser used by `gflow video batch`."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.video import Aspect
from gflow_cli.manifest import ManifestEntry, parse_manifest


class TestParseManifest:
    def test_minimal_t2v_row(self, tmp_path: Path) -> None:
        m = tmp_path / "m.tsv"
        m.write_text("\ta cat in a hat\t\t\t\n", encoding="utf-8")
        entries = parse_manifest(m)
        assert len(entries) == 1
        e: ManifestEntry = entries[0]
        assert e.start_image is None
        assert e.prompt == "a cat in a hat"
        assert e.aspect == Aspect.PORTRAIT
        assert e.output_path is None

    def test_full_i2v_row(self, tmp_path: Path) -> None:
        m = tmp_path / "m.tsv"
        m.write_text("input.png\tpush in\t\t16:9\tout.mp4\n", encoding="utf-8")
        entries = parse_manifest(m)
        e = entries[0]
        assert e.start_image == Path("input.png")
        assert e.prompt == "push in"
        assert e.aspect == Aspect.LANDSCAPE
        assert e.output_path == Path("out.mp4")

    def test_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        m = tmp_path / "m.tsv"
        m.write_text(
            "# header line\n\nin.png\tprompt-a\t\t\t\n# another comment\n\tprompt-b\t\t\t\n",
            encoding="utf-8",
        )
        entries = parse_manifest(m)
        assert len(entries) == 2
        assert entries[0].prompt == "prompt-a"
        assert entries[1].prompt == "prompt-b"

    def test_missing_prompt_raises(self, tmp_path: Path) -> None:
        m = tmp_path / "m.tsv"
        m.write_text("in.png\t\t\t\t\n", encoding="utf-8")
        with pytest.raises(ValueError, match="prompt is required"):
            parse_manifest(m)

    def test_invalid_aspect_raises(self, tmp_path: Path) -> None:
        m = tmp_path / "m.tsv"
        m.write_text("\tprompt\t\t9:99\t\n", encoding="utf-8")
        with pytest.raises(ValueError, match="9:99"):
            parse_manifest(m)
