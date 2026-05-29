"""Unit tests for the locale-invariant capture helper script."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.dev import capture_locale_invariants as cli


def test_parse_locales_uses_default_when_env_unset() -> None:
    assert cli._parse_locales(None) == ["en-US", "pt-BR", "es-ES"]


def test_parse_locales_accepts_comma_separated_env_override() -> None:
    assert cli._parse_locales("fr-FR, de-DE,ja-JP") == ["fr-FR", "de-DE", "ja-JP"]


def test_flow_url_preserves_full_bcp47_locale_tag() -> None:
    assert cli._flow_url("pt-BR") == "https://labs.google/fx/tools/flow?hl=pt-BR"


def test_resolve_profile_dir_requires_e2e_profile_env() -> None:
    with pytest.raises(SystemExit, match="GFLOW_CLI_E2E_PROFILE"):
        cli._resolve_profile_dir({}, resolver=lambda _name: Path("unused"))


def test_resolve_profile_dir_uses_auth_resolver(tmp_path: Path) -> None:
    profile = tmp_path / "profile_alice"
    profile.mkdir()

    resolved = cli._resolve_profile_dir(
        {"GFLOW_CLI_E2E_PROFILE": "alice"},
        resolver=lambda name: profile if name == "alice" else Path("wrong"),
    )

    assert resolved == profile


def test_launch_options_include_chrome_channel_from_profile(tmp_path: Path) -> None:
    options = cli._launch_options("es-ES", tmp_path, channel_resolver=lambda _path: "chrome")

    assert options["channel"] == "chrome"
    assert options["locale"] == "es-ES"
    assert options["headless"] is False


def test_summarize_returns_cross_locale_stable_values() -> None:
    results = {
        "en-US": {
            "buttons": [
                {"id": "mat-mdc-button-12", "aria_label": "Settings", "icon_ligature": "tune"},
                {"id": "volatile-english", "aria_label": "Prompt", "icon_ligature": "add"},
            ],
            "menuitems": [],
            "tabs": [],
            "textboxes": [],
        },
        "pt-BR": {
            "buttons": [
                {"id": "mat-mdc-button-12", "aria_label": "Settings", "icon_ligature": "tune"},
                {"id": "volatile-portuguese", "aria_label": "Prompt", "icon_ligature": "close"},
            ],
            "menuitems": [],
            "tabs": [],
            "textboxes": [],
        },
    }

    assert cli._summarize(results) == {
        "locales": ["en-US", "pt-BR"],
        "failed_locales": [],
        "stable_id_suffixes": ["12"],
        "stable_aria_labels": ["Prompt", "Settings"],
        "stable_icon_ligatures": ["tune"],
    }
