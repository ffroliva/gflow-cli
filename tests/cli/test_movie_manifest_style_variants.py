"""Tests for movie.toml parsing of [style.variants.*] and per-scene style fields (Issue #239)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.movie_manifest import MovieManifest


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "movie.toml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# [style] prefix / suffix
# ---------------------------------------------------------------------------


class TestStylePrefixSuffix:
    def test_prefix_and_suffix_parse(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"

[style]
prefix = "SCENE 1:"
suffix = "Cinematic, photorealistic."

[[scenes]]
id = "s1"
action = "walks"
""",
            )
        )
        assert m.style.prefix == "SCENE 1:"
        assert m.style.suffix == "Cinematic, photorealistic."

    def test_prefix_and_suffix_default_to_none(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[style]
look = "ink"
[[scenes]]
id = "s1"
action = "walks"
""",
            )
        )
        assert m.style.prefix is None
        assert m.style.suffix is None


# ---------------------------------------------------------------------------
# [style.variants.*]
# ---------------------------------------------------------------------------


class TestStyleVariants:
    def test_variants_parse(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"

[style]
suffix = "Base."

[style.variants.warm]
suffix = "Warm golden-hour grade."

[style.variants.cool]
suffix = "Cool blue grade."

[[scenes]]
id = "s1"
action = "walks"
""",
            )
        )
        assert m.style.variants == {"warm": "Warm golden-hour grade.", "cool": "Cool blue grade."}

    def test_empty_variants_table(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[style]
suffix = "Base."
[style.variants]
[[scenes]]
id = "s1"
action = "walks"
""",
            )
        )
        assert m.style.variants == {}

    def test_variant_with_empty_suffix(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[style]
suffix = "Base."
[style.variants.minimal]
suffix = ""
[[scenes]]
id = "s1"
action = "walks"
""",
            )
        )
        assert m.style.variants.get("minimal") == ""


# ---------------------------------------------------------------------------
# Per-scene style_variant / style_suffix
# ---------------------------------------------------------------------------


class TestSceneStyleFields:
    def test_style_variant_parses(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[style]
suffix = "Base."
[style.variants.warm]
suffix = "Warm."
[[scenes]]
id = "s1"
action = "walks"
style_variant = "warm"
""",
            )
        )
        assert m.scenes[0].style_variant == "warm"

    def test_style_suffix_parses(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[[scenes]]
id = "s1"
action = "walks"
style_suffix = "sunset light"
""",
            )
        )
        assert m.scenes[0].style_suffix == "sunset light"

    def test_style_variant_none_parses(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[style]
suffix = "Cinematic."
[[scenes]]
id = "s1"
action = "walks"
style_variant = "none"
""",
            )
        )
        assert m.scenes[0].style_variant == "none"

    def test_style_fields_default_to_none(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[[scenes]]
id = "s1"
action = "walks"
""",
            )
        )
        assert m.scenes[0].style_variant is None
        assert m.scenes[0].style_suffix is None

    def test_both_style_variant_and_style_suffix(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[style]
suffix = "Base."
[style.variants.warm]
suffix = "Warm."
[[scenes]]
id = "s1"
action = "walks"
style_variant = "warm"
style_suffix = "golden hour"
""",
            )
        )
        assert m.scenes[0].style_variant == "warm"
        assert m.scenes[0].style_suffix == "golden hour"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestStyleValidation:
    def test_invalid_prefix_type_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="style.prefix"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title = "T"
project = "p"
[style]
prefix = 42
[[scenes]]
id = "s1"
action = "walks"
""",
                )
            )

    def test_invalid_suffix_type_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="style.suffix"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title = "T"
project = "p"
[style]
suffix = true
[[scenes]]
id = "s1"
action = "walks"
""",
                )
            )

    def test_variant_suffix_non_string_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="variants.*suffix"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title = "T"
project = "p"
[style.variants.warm]
suffix = 123
[[scenes]]
id = "s1"
action = "walks"
""",
                )
            )

    def test_variant_not_table_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="variants.*table"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title = "T"
project = "p"
[style.variants]
warm = "not a table"
[[scenes]]
id = "s1"
action = "walks"
""",
                )
            )

    def test_invalid_style_variant_type_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="style_variant"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title = "T"
project = "p"
[[scenes]]
id = "s1"
action = "walks"
style_variant = 42
""",
                )
            )

    def test_invalid_style_suffix_type_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="style_suffix"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title = "T"
project = "p"
[[scenes]]
id = "s1"
action = "walks"
style_suffix = true
""",
                )
            )

    def test_unknown_style_variant_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="style_variant.*wram"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title = "T"
project = "p"
[style]
suffix = "Base."
[style.variants.warm]
suffix = "Warm."
[[scenes]]
id = "s1"
action = "walks"
style_variant = "wram"
""",
                )
            )


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_old_manifest_without_variants_still_works(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
title = "T"
project = "p"
[style]
look = "ink"
[[scenes]]
id = "s1"
action = "walks"
""",
            )
        )
        assert m.style.look == "ink"
        assert m.style.prefix is None
        assert m.style.suffix is None
        assert m.style.variants == {}
        assert m.scenes[0].style_variant is None
        assert m.scenes[0].style_suffix is None
