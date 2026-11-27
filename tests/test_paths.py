"""Tests for the XDG-aware path resolver."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from gflow_cli import paths


class TestDefaultRoots:
    def test_default_home_returns_path(self) -> None:
        h = paths.default_home()
        assert isinstance(h, Path)
        # The directory may not exist yet — we don't auto-create.
        assert "gflow-cli" in str(h).lower()

    def test_default_output_dir_returns_path(self) -> None:
        out = paths.default_output_dir()
        assert isinstance(out, Path)
        assert "gflow-cli" in str(out).lower()


class TestCharacterOutputPath:
    def test_layout_and_naming(self) -> None:
        out = Path("/root")
        on = date(2026, 1, 2)
        p = paths.character_output_path(out, entity_id="abc-123", slot=0, on=on)
        assert p == Path("/root/characters/2026-01-02/character_abc-123_slot0.png")

    def test_slot_index_in_name(self) -> None:
        p = paths.character_output_path(Path("/root"), entity_id="e", slot=1, on=date(2026, 1, 2))
        assert p.name == "character_e_slot1.png"

    def test_rejects_unsafe_entity_id(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Unsafe"):
            paths.character_output_path(Path("/root"), entity_id="../../etc", slot=0)


class TestProfileSubdir:
    def test_under_home(self) -> None:
        home = Path("/x/gflow-cli")
        assert paths.profile_subdir(home, "default") == Path("/x/gflow-cli/profile_default")
        assert paths.profile_subdir(home, "work") == Path("/x/gflow-cli/profile_work")


class TestConfigFile:
    def test_under_home(self) -> None:
        home = Path("/x/gflow-cli")
        assert paths.config_file(home) == Path("/x/gflow-cli/config.toml")


class TestVideoOutputPath:
    def test_default_uses_today(self) -> None:
        p = paths.video_output_path(Path("/out"), job_id="abcd-1234")
        assert "videos" in p.parts
        assert "abcd-1234.mp4" == p.name

    def test_explicit_date(self) -> None:
        p = paths.video_output_path(Path("/out"), job_id="x", on=date(2026, 1, 15))
        assert p == Path("/out/videos/2026-01-15/x.mp4")


class TestImageOutputPath:
    def test_indexed_filename(self) -> None:
        p = paths.image_output_path(Path("/out"), job_id="x", index=3, on=date(2026, 1, 15))
        assert p == Path("/out/images/2026-01-15/x_3.png")


class TestResolveBatchOutputDirExpanduser:
    """resolve_batch_output_dir() must expand ``~`` so users can put
    ``~/gflow-output`` in their config files / CLI flags without it being
    interpreted as a literal directory name. Regression guard for the
    examples/sample_config.json default that landed in the root-leak
    cleanup PR."""

    def test_config_value_expanduser(self) -> None:
        home = Path.home()
        out = paths.resolve_batch_output_dir(
            cli_override=None,
            config_value="~/gflow-output/example-batch",
            output_root=Path("/unused"),
        )
        assert out == home / "gflow-output" / "example-batch"
        assert "~" not in str(out)

    def test_cli_override_expanduser(self) -> None:
        home = Path.home()
        out = paths.resolve_batch_output_dir(
            cli_override=Path("~/some/where"),
            output_root=Path("/unused"),
        )
        assert out == home / "some" / "where"

    def test_output_root_expanduser(self) -> None:
        home = Path.home()
        out = paths.resolve_batch_output_dir(
            cli_override=None,
            config_value=None,
            output_root=Path("~/data-root"),
            kind="images",
        )
        expected_prefix = (home / "data-root" / "images").parts
        assert out.parts[: len(expected_prefix)] == expected_prefix

    def test_absolute_config_value_unchanged(self) -> None:
        out = paths.resolve_batch_output_dir(
            cli_override=None,
            config_value="/abs/path",
            output_root=Path("/unused"),
        )
        assert out == Path("/abs/path")


# Realistic magic-byte fixtures used by the extension-sniff tests. The
# remaining body bytes are zero-padding; only the first ~12 bytes matter.
_JPEG_JFIF_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
_JPEG_EXIF_HEAD = b"\xff\xd8\xff\xe1\x00\x10Exif" + b"\x00" * 100
_PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_WEBP_HEAD = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 100


class TestExtensionFromMagic:
    """`extension_from_magic` maps file-magic bytes to a canonical extension."""

    def test_jpeg_jfif(self) -> None:
        assert paths.extension_from_magic(_JPEG_JFIF_HEAD) == ".jpg"

    def test_jpeg_exif(self) -> None:
        assert paths.extension_from_magic(_JPEG_EXIF_HEAD) == ".jpg"

    def test_png(self) -> None:
        assert paths.extension_from_magic(_PNG_HEAD) == ".png"

    def test_webp(self) -> None:
        # RIFF + 4 size bytes + 'WEBP' — must verify both halves.
        assert paths.extension_from_magic(_WEBP_HEAD) == ".webp"

    def test_gif87a(self) -> None:
        assert paths.extension_from_magic(b"GIF87a\x00\x00\x00\x00") == ".gif"

    def test_gif89a(self) -> None:
        assert paths.extension_from_magic(b"GIF89a\x00\x00\x00\x00") == ".gif"

    def test_unknown_returns_none(self) -> None:
        assert paths.extension_from_magic(b"NOT_AN_IMAGE_AT_ALL") is None

    def test_empty_returns_none(self) -> None:
        assert paths.extension_from_magic(b"") is None

    def test_two_bytes_returns_none(self) -> None:
        # JPEG signature is 3 bytes; 2 bytes is insufficient.
        assert paths.extension_from_magic(b"\xff\xd8") is None

    def test_riff_without_webp_returns_none(self) -> None:
        # RIFF is also used by .wav / .avi — only return .webp when bytes 8-11 == 'WEBP'.
        assert paths.extension_from_magic(b"RIFF\x00\x00\x00\x00WAVE") is None


# Real ISO-BMFF (MP4) header from a Flow-generated video mis-saved as .png
# (issue: agentic gflow_generate_image produced a video). Box size + 'ftyp' +
# brand 'isom'/'iso2'/'avc1'/'mp41'.
_MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
_WEBM_HEAD = b"\x1a\x45\xdf\xa3" + b"\x00" * 20


class TestLooksLikeVideo:
    """`looks_like_video` positively detects video containers so an image
    download that returns video content can fail loud instead of silently
    saving a video with an image extension."""

    def test_mp4_isobmff_detected(self) -> None:
        assert paths.looks_like_video(_MP4_HEAD) is True

    def test_webm_matroska_detected(self) -> None:
        assert paths.looks_like_video(_WEBM_HEAD) is True

    def test_png_is_not_video(self) -> None:
        assert paths.looks_like_video(_PNG_HEAD) is False

    def test_jpeg_is_not_video(self) -> None:
        assert paths.looks_like_video(_JPEG_JFIF_HEAD) is False

    def test_arbitrary_bytes_are_not_video(self) -> None:
        # Guard must be conservative: only POSITIVE video detection, so
        # unrecognised/short buffers (and existing arbitrary-byte download
        # tests) are never misclassified as video.
        assert paths.looks_like_video(b"image-bytes") is False
        assert paths.looks_like_video(b"") is False
        assert paths.looks_like_video(b"ftyp") is False  # too short, no size box


class TestCorrectImageExtension:
    """`correct_image_extension` renames a downloaded file to match its actual
    format. Core fix for issue #96 (JPEG bytes written with .png suffix)."""

    def test_jpeg_with_png_suffix_renamed(self, tmp_path: Path) -> None:
        # The exact bug from issue #96: JPEG bytes saved as .png.
        wrong = tmp_path / "abc-123_1.png"
        wrong.write_bytes(_JPEG_JFIF_HEAD)
        corrected = paths.correct_image_extension(wrong)
        assert corrected.name == "abc-123_1.jpg"
        assert corrected.exists()
        assert not wrong.exists()
        assert corrected.read_bytes() == _JPEG_JFIF_HEAD

    def test_matching_png_no_op(self, tmp_path: Path) -> None:
        right = tmp_path / "foo.png"
        right.write_bytes(_PNG_HEAD)
        corrected = paths.correct_image_extension(right)
        assert corrected == right
        assert right.exists()

    def test_matching_jpg_no_op(self, tmp_path: Path) -> None:
        right = tmp_path / "foo.jpg"
        right.write_bytes(_JPEG_JFIF_HEAD)
        corrected = paths.correct_image_extension(right)
        assert corrected == right

    def test_jpeg_extension_alias_not_renamed(self, tmp_path: Path) -> None:
        # ``.jpeg`` is a valid alias of ``.jpg``; treat as a match.
        right = tmp_path / "foo.jpeg"
        right.write_bytes(_JPEG_JFIF_HEAD)
        corrected = paths.correct_image_extension(right)
        assert corrected == right

    def test_unknown_format_no_op(self, tmp_path: Path) -> None:
        f = tmp_path / "foo.png"
        f.write_bytes(b"NOT_AN_IMAGE_DATA_AT_ALL")
        corrected = paths.correct_image_extension(f)
        assert corrected == f
        assert f.exists()

    def test_target_already_exists_no_op(self, tmp_path: Path) -> None:
        # If the corrected target already exists, leave the original alone
        # rather than clobbering. Avoids data loss on retry races.
        wrong = tmp_path / "foo.png"
        wrong.write_bytes(_JPEG_JFIF_HEAD)
        existing = tmp_path / "foo.jpg"
        existing.write_bytes(b"some other content")
        corrected = paths.correct_image_extension(wrong)
        assert corrected == wrong
        assert wrong.exists()
        # Pre-existing target is untouched.
        assert existing.read_bytes() == b"some other content"

    def test_case_insensitive_extension(self, tmp_path: Path) -> None:
        # ``.PNG`` matches ``.png`` for the no-op decision (Windows-friendly).
        right = tmp_path / "foo.PNG"
        right.write_bytes(_PNG_HEAD)
        corrected = paths.correct_image_extension(right)
        assert corrected == right

    def test_webp_with_png_suffix_renamed(self, tmp_path: Path) -> None:
        # Future-proofing: Flow has historically served WebP for some surfaces.
        wrong = tmp_path / "foo.png"
        wrong.write_bytes(_WEBP_HEAD)
        corrected = paths.correct_image_extension(wrong)
        assert corrected.name == "foo.webp"
        assert corrected.exists()


class TestImageDimensions:
    """`image_dimensions` extracts (width, height) from PNG, JPEG, GIF and WebP headers."""

    def test_image_dimensions_for_each_format(self) -> None:
        # PNG: 16x32
        png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0d"
            b"IHDR"
            b"\x00\x00\x00\x10"  # width: 16
            b"\x00\x00\x00\x20"  # height: 32
            b"\x08\x02\x00\x00\x00"
            b"\x90\x91\x68\x36"
        )
        assert paths.image_dimensions(png) == (16, 32)

        # GIF87a: 64x48
        gif87 = b"GIF87a\x40\x00\x30\x00\x00\x00\x00"
        assert paths.image_dimensions(gif87) == (64, 48)

        # GIF89a: 100x200
        gif89 = b"GIF89a\x64\x00\xc8\x00\x00\x00\x00"
        assert paths.image_dimensions(gif89) == (100, 200)

        # JPEG SOF0 (baseline): 80x120 (height=120, width=80)
        jpeg_sof0 = (
            b"\xff\xd8"  # SOI
            b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"  # APP0
            b"\xff\xc0\x00\x11\x08\x00\x78\x00\x50"  # SOF0: len=17, prec=8, h=120, w=80
            b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
            b"\xff\xd9"
        )
        assert paths.image_dimensions(jpeg_sof0) == (80, 120)

        # JPEG SOF2 (progressive): 640x480
        jpeg_sof2 = (
            b"\xff\xd8"
            b"\xff\xc2\x00\x11\x08\x01\xe0\x02\x80"  # SOF2: h=480(0x01e0), w=640(0x0280)
            b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
            b"\xff\xd9"
        )
        assert paths.image_dimensions(jpeg_sof2) == (640, 480)

        # WebP VP8 (simple lossy): 300x200
        # VP8 frame tag: 3 bytes keyframe, start code 0x9d 0x01 0x2a, 2 bytes w, 2 bytes h
        # 16-bit little-endian with 14 bits width, 14 bits height
        # w=300 (0x012c -> 2c 01), h=200 (0x00c8 -> c8 00)
        vp8 = (
            b"RIFF\x20\x00\x00\x00WEBP"
            b"VP8 \x10\x00\x00\x00"
            b"\x00\x00\x00"  # 3-byte frame tag (keyframe)
            b"\x9d\x01\x2a"  # sync code
            b"\x2c\x01"  # width: 300
            b"\xc8\x00"  # height: 200
        )
        assert paths.image_dimensions(vp8) == (300, 200)

        # WebP VP8L (lossless): 1024x768
        # signature 0x2f, then 4 bytes (32-bit LE):
        # 14 bits width-1 (1023 = 0x3ff)
        # 14 bits height-1 (767 = 0x2ff)
        # 1 bit alpha, 3 bits version (0)
        # 1023 | (767 << 14) = 0x3ff | (0x2ff << 14) = 0x3ff | 0x00bfc000 = 0x00bfc3ff
        # packed 32-bit LE: ff c3 bf 00
        vp8l = b"RIFF\x20\x00\x00\x00WEBPVP8L\x05\x00\x00\x00\x2f\xff\xc3\xbf\x00"
        assert paths.image_dimensions(vp8l) == (1024, 768)

        # WebP VP8X (extended): 1920x1080
        # 10 bytes chunk: 4 bytes flags, 3 bytes w - 1, 3 bytes h - 1 (24-bit LE)
        # w-1 = 1919 (0x00077f -> 7f 07 00), h-1 = 1079 (0x000437 -> 37 04 00)
        vp8x = (
            b"RIFF\x20\x00\x00\x00WEBP"
            b"VP8X\x0a\x00\x00\x00"
            b"\x00\x00\x00\x00"  # flags
            b"\x7f\x07\x00"  # canvas width - 1
            b"\x37\x04\x00"  # canvas height - 1
        )
        assert paths.image_dimensions(vp8x) == (1920, 1080)

    def test_image_dimensions_none_for_truncated_or_unknown(self) -> None:
        assert paths.image_dimensions(b"") is None
        assert paths.image_dimensions(b"NOT_AN_IMAGE") is None
        assert paths.image_dimensions(b"\x89PNG\r\n\x1a\n") is None  # truncated PNG
        assert paths.image_dimensions(b"GIF89a\x01\x02") is None  # truncated GIF
        assert paths.image_dimensions(b"\xff\xd8\xff\xc0\x00") is None  # truncated JPEG
        assert paths.image_dimensions(b"RIFF\x10\x00\x00\x00WEBPVP8 ") is None  # truncated WebP
        # Non-image RIFF
        assert paths.image_dimensions(b"RIFF\x20\x00\x00\x00WAVEfmt ") is None

    def test_image_dimensions_edge_cases_and_invalid_branches(self) -> None:
        # PNG: wrong chunk type (not IHDR) or zero dimensions
        bad_chunk = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dNOTI\x00\x00\x00\x10\x00\x00\x00\x20"
        assert paths.image_dimensions(bad_chunk) is None
        zero_w_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR\x00\x00\x00\x00\x00\x00\x00\x20"
        assert paths.image_dimensions(zero_w_png) is None

        # GIF: truncated (< 10 bytes) or zero dimensions
        assert paths.image_dimensions(b"GIF89a\x00\x00") is None
        zero_gif = b"GIF89a\x00\x00\x00\x00\x00\x00"
        assert paths.image_dimensions(zero_gif) is None

        # JPEG: non-0xFF byte where marker expected
        assert paths.image_dimensions(b"\xff\xd8\x00\x00\x00\x00\x00\x00\x00\x00") is None
        # JPEG: runs out of bytes while reading 0xFF padding
        assert paths.image_dimensions(b"\xff\xd8\xff\xff\xff") is None
        assert paths._next_jpeg_marker(b"\xff\xd8\xff\xff", 2, 4) is None
        # JPEG: standalone marker (RST0 0xD0, byte stuffing 0x00, EOI 0xD9) followed by EOF or end
        assert paths.image_dimensions(b"\xff\xd8\xff\xd0\xff\xd9\x00\x00\x00\x00\x00") is None
        # JPEG: seg_len too short (< 2) or segment extends beyond buffer
        assert paths.image_dimensions(b"\xff\xd8\xff\xe0\x00\x01\x00\x00\x00\x00") is None
        assert paths.image_dimensions(b"\xff\xd8\xff\xe0\x00\x20\x00\x00\x00\x00") is None
        assert paths.image_dimensions(b"\xff\xd8\xff\xe0\x00\x00\x00\x00\x00\x00") is None
        # Valid JFIF header followed by marker whose seg_len is truncated (idx + 2 > length)
        jfif_prefix = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        assert paths.image_dimensions(jfif_prefix + b"\xff\xee\x00") is None
        # Non-SOF segment (0xFF 0xEE) processed completely without finding any SOF marker
        assert paths.image_dimensions(jfif_prefix + b"\xff\xee\x00\x02") is None
        # JPEG: non-SOF segment (APP0) with no subsequent SOF
        assert paths.image_dimensions(b"\xff\xd8\xff\xe0\x00\x04\x00\x00\x00\x00") is None
        assert paths.image_dimensions(b"\xff\xd8\xff\xe0\x00\x04\x00\x00\xff\xd8\x00") is None
        # JPEG: SOF with seg_len < 7
        assert paths.image_dimensions(b"\xff\xd8\xff\xc0\x00\x05\x00\x00\x00\x00") is None
        # JPEG: SOF with zero width / height
        zero_sof = (
            b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x00\x00\x50\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
        )
        assert paths.image_dimensions(zero_sof) is None

        # WebP: unsupported subformat (e.g. VP8 )
        unsupported_webp = b"RIFF\x20\x00\x00\x00WEBPUNKN\x00\x00\x00\x00\x00\x00"
        assert paths.image_dimensions(unsupported_webp) is None
        # WebP VP8: bad sync code or zero dimensions
        bad_sync_vp8 = (
            b"RIFF\x20\x00\x00\x00WEBPVP8 \x10\x00\x00\x00\x00\x00\x00\x00\x00\x00\x2c\x01\xc8\x00"
        )
        assert paths.image_dimensions(bad_sync_vp8) is None
        zero_vp8 = (
            b"RIFF\x20\x00\x00\x00WEBPVP8 \x10\x00\x00\x00\x00\x00\x00\x9d\x01\x2a\x00\x00\x00\x00"
        )
        assert paths.image_dimensions(zero_vp8) is None
        # WebP VP8L: truncated (< 25 bytes) or bad signature (not 0x2F)
        trunc_vp8l = b"RIFF\x20\x00\x00\x00WEBPVP8L\x05\x00"
        assert paths.image_dimensions(trunc_vp8l) is None
        bad_sig_vp8l = b"RIFF\x20\x00\x00\x00WEBPVP8L\x05\x00\x00\x00\x00\xff\xc3\xbf\x00"
        assert paths.image_dimensions(bad_sig_vp8l) is None
        # WebP VP8X: truncated (< 30 bytes)
        trunc_vp8x = b"RIFF\x20\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00\x00\x00"
        assert paths.image_dimensions(trunc_vp8x) is None
