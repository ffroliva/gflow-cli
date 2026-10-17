"""character_bundle: the portable folder an exported character lives in (#639).

A bundle is untrusted input on import — it may come from anyone — so every refusal is
decided here, offline, before a network call. Scenario rows refer to
docs/superpowers/plans/2026-10-16-character-portability/SCENARIO.md.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from gflow_cli.character_bundle import BundleImage, read_bundle, write_bundle
from gflow_cli.errors import ConfigurationError


def _img(fmt: str = "JPEG", color: tuple[int, int, int] = (120, 80, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 48), color).save(buf, format=fmt)
    return buf.getvalue()


def _write(tmp_path: Path, **kw: object) -> Path:
    out = tmp_path / "nora"
    write_bundle(
        out,
        name=str(kw.get("name", "Nora")),
        voice=kw.get("voice", "Aoede"),  # type: ignore[arg-type]
        personality=kw.get("personality", "calm, precise"),  # type: ignore[arg-type]
        images=kw.get("images", [("portrait", _img()), ("body", _img("PNG"))]),  # type: ignore[arg-type]
    )
    return out


def _manifest(bundle: Path) -> dict[str, object]:
    return json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))


def _rewrite(bundle: Path, **changes: object) -> None:
    data = _manifest(bundle)
    data.update(changes)
    (bundle / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


class TestRoundTrip:
    def test_written_then_read_gives_the_same_character(self, tmp_path: Path) -> None:
        read = read_bundle(_write(tmp_path))
        assert (read.name, read.voice, read.personality) == ("Nora", "Aoede", "calm, precise")
        assert [i.role for i in read.images] == ["portrait", "body"]
        assert all(isinstance(i, BundleImage) and i.path.is_file() for i in read.images)

    def test_files_have_fixed_names_and_the_extension_of_their_real_type(
        self, tmp_path: Path
    ) -> None:
        # Scenario 22: the display name is never a path; the type comes from the bytes.
        bundle = _write(tmp_path, name="Laura é: CON/..")
        assert sorted(p.name for p in bundle.iterdir()) == [
            "body.png",
            "manifest.json",
            "portrait.jpg",
        ]

    def test_a_non_ascii_name_survives_as_utf8(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path, name="Laura é 日本")
        raw = (bundle / "manifest.json").read_bytes().decode("utf-8")
        assert "Laura é 日本" in raw
        assert read_bundle(bundle).name == "Laura é 日本"

    def test_a_portrait_alone_is_a_bundle(self, tmp_path: Path) -> None:
        read = read_bundle(_write(tmp_path, images=[("portrait", _img())], voice=None))
        assert [i.role for i in read.images] == ["portrait"]
        assert read.voice is None


class TestIdentity:
    def test_the_manifest_holds_only_the_allowlisted_fields(self, tmp_path: Path) -> None:
        # Scenario 24: nothing that ties a bundle to an account or a signed link.
        data = _manifest(_write(tmp_path))
        assert set(data) == {"schema_version", "name", "voice", "personality", "images"}
        for image in data["images"]:  # type: ignore[union-attr]
            assert set(image) == {"role", "file", "sha256", "mime"}  # type: ignore[arg-type]
        assert "http" not in json.dumps(data)


class TestWriteRefusals:
    def test_a_non_empty_destination_is_refused(self, tmp_path: Path) -> None:
        out = tmp_path / "nora"
        out.mkdir()
        (out / "keep.txt").write_text("mine", encoding="utf-8")
        with pytest.raises(ConfigurationError):
            write_bundle(
                out, name="Nora", voice=None, personality=None, images=[("portrait", _img())]
            )
        assert (out / "keep.txt").read_text(encoding="utf-8") == "mine"

    def test_writing_bytes_that_are_not_an_image_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError):
            write_bundle(
                tmp_path / "x",
                name="Nora",
                voice=None,
                personality=None,
                images=[("portrait", b"<html>not an image</html>")],
            )


class TestReadRefusals:
    """Each refusal is exit 11 and happens before anything is sent (scenarios 15–18)."""

    def test_no_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        with pytest.raises(ConfigurationError, match="manifest"):
            read_bundle(tmp_path / "empty")

    def test_a_refusal_hints_at_the_folder_not_at_transports(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError) as exc:
            read_bundle(tmp_path)
        hint = exc.value.remediation_hint or ""
        assert "character export" in hint and "transport" not in hint

    def test_an_oversized_manifest(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        (bundle / "manifest.json").write_text(" " * (64 * 1024 + 1), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="larger than 64 KB"):
            read_bundle(bundle)

    def test_a_deeply_nested_manifest_is_refused_not_a_crash(self, tmp_path: Path) -> None:
        """PR #987 review: 100k `[` raised RecursionError (exit 1) instead of exit 11."""
        bundle = _write(tmp_path)
        (bundle / "manifest.json").write_text("[" * 60_000, encoding="utf-8")
        with pytest.raises(ConfigurationError, match="not valid"):
            read_bundle(bundle)

    def test_an_unknown_schema_version(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        _rewrite(bundle, schema_version=2)
        with pytest.raises(ConfigurationError, match="schema_version"):
            read_bundle(bundle)

    @pytest.mark.parametrize("escape", ["../secret.jpg", "/etc/passwd", "sub/portrait.jpg", ".."])
    def test_an_image_path_that_is_not_a_bare_file_name(self, tmp_path: Path, escape: str) -> None:
        bundle = _write(tmp_path)
        data = _manifest(bundle)
        data["images"][0]["file"] = escape  # type: ignore[index]
        (bundle / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="bare file name"):
            read_bundle(bundle)

    def test_an_escape_to_a_real_file_with_a_matching_digest(self, tmp_path: Path) -> None:
        """The guard, not a later check, refuses it: the file exists and its sha256
        matches, so only the bare-file-name rule stands between it and an upload."""
        bundle = _write(tmp_path)
        outside = bundle.parent / "outside.jpg"
        outside.write_bytes(_img(color=(9, 9, 9)))
        data = _manifest(bundle)
        entry = data["images"][0]  # type: ignore[index]
        entry["file"] = "../outside.jpg"  # type: ignore[index]
        entry["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()  # type: ignore[index]
        (bundle / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="bare file name"):
            read_bundle(bundle)

    def test_a_symlinked_image(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        secret = tmp_path / "secret.jpg"
        secret.write_bytes(_img())
        (bundle / "portrait.jpg").unlink()
        try:
            (bundle / "portrait.jpg").symlink_to(secret)
        except OSError:  # Windows without the symlink privilege (WinError 1314)
            pytest.skip("symlink creation not permitted in this environment")
        with pytest.raises(ConfigurationError, match="link"):
            read_bundle(bundle)

    def test_an_image_whose_bytes_changed(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        (bundle / "portrait.jpg").write_bytes(_img(color=(1, 2, 3)))
        with pytest.raises(ConfigurationError, match="sha256"):
            read_bundle(bundle)

    def test_an_image_that_is_not_an_image(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        fake = b"#!/bin/sh\necho owned\n"
        (bundle / "portrait.jpg").write_bytes(fake)
        import hashlib

        data = _manifest(bundle)
        data["images"][0]["sha256"] = hashlib.sha256(fake).hexdigest()  # type: ignore[index]
        (bundle / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="not an image"):
            read_bundle(bundle)

    def test_no_portrait(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        data = _manifest(bundle)
        data["images"] = [i for i in data["images"] if i["role"] != "portrait"]  # type: ignore[union-attr,index]
        (bundle / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="portrait"):
            read_bundle(bundle)

    def test_a_duplicated_or_unknown_role(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        data = _manifest(bundle)
        data["images"][1]["role"] = "portrait"  # type: ignore[index]
        (bundle / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            read_bundle(bundle)

    def test_an_empty_name(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        _rewrite(bundle, name="  ​ ")
        with pytest.raises(ConfigurationError, match="name"):
            read_bundle(bundle)


class TestText:
    def test_control_bidi_and_zero_width_characters_are_stripped(self, tmp_path: Path) -> None:
        # Scenario 18: bundle text reaches prompts and MCP replies.
        bundle = _write(tmp_path)
        _rewrite(bundle, name="No‮ra​\x07", personality="calm⁦ and\nkind\x1b[31m")
        read = read_bundle(bundle)
        assert read.name == "Nora"
        assert read.personality == "calm and\nkind[31m"

    def test_oversized_text_is_refused_not_truncated(self, tmp_path: Path) -> None:
        bundle = _write(tmp_path)
        _rewrite(bundle, personality="x" * 5000)
        with pytest.raises(ConfigurationError, match="personality"):
            read_bundle(bundle)
