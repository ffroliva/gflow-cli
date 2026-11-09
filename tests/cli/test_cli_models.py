"""Click-runner tests for `gflow models` (no network — pure catalog)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from click.testing import CliRunner

from gflow_cli.cli_models import build_catalog


def test_models_json_lists_image_and_video() -> None:
    runner = CliRunner()
    from gflow_cli.cli import main

    result = runner.invoke(main, ["models", "--json"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) == {"image", "video"}

    by_img = {m["name"]: m for m in data["image"]["models"]}
    assert {"NARWHAL", "GEM_PIX_2", "IMAGEN_3_5"} <= set(by_img)
    # Caps must agree with the i2i ref-cap feature.
    assert by_img["IMAGEN_3_5"]["ref_cap"] == 3
    assert by_img["NARWHAL"]["ref_cap"] == 10
    assert by_img["NARWHAL"]["default"] is True

    by_vid = {m["name"]: m for m in data["video"]["models"]}
    assert by_vid["omni_flash"]["ref_cap"] == 7
    assert by_vid["omni_flash"]["max_duration"] == 10
    assert by_vid["omni_1_2_flash"]["max_duration"] == 15
    assert set(by_vid["omni_1_2_flash"]["aliases"]) == {"omni-1.2-flash", "omni-1.2"}
    assert by_vid["veo_3_1_fast"]["ref_cap"] == 3
    # Veo 3.1 duration controls expose 4s, 6s, and 8s in the current matrix.
    assert by_vid["veo_3_1_fast"]["max_duration"] == 8


def test_models_table_renders_without_json() -> None:
    runner = CliRunner()
    from gflow_cli.cli import main

    result = runner.invoke(main, ["models"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "NARWHAL" in result.output
    assert "omni_flash" in result.output


def test_catalog_aliases_are_all_gen_command_accepted() -> None:
    """Every alias the catalog advertises MUST be accepted by the gen command's
    `--model` Choice — else a UI picks an alias the gen command rejects with a
    Click usage error (exit 2). Regression guard for that exact mismatch."""
    from gflow_cli.cli_models import _VIDEO_CLI_MODELS
    from gflow_cli.image_batch import ALLOWED_MODELS

    catalog = build_catalog()
    for m in catalog["image"]["models"]:
        assert m["aliases"], f"{m['name']} has no CLI alias"
        for a in m["aliases"]:
            assert a in ALLOWED_MODELS, f"image alias {a!r} not in --model Choice"
    for m in catalog["video"]["models"]:
        assert m["aliases"], f"{m['name']} has no CLI alias"
        for a in m["aliases"]:
            assert a in _VIDEO_CLI_MODELS, f"video alias {a!r} not in --model Choice"

    # The previously-broken defaults now resolve to accepted aliases.
    by_img = {m["name"]: m for m in catalog["image"]["models"]}
    assert by_img["NARWHAL"]["aliases"] == ["nano2", "nano21", "nano2.1", "nano-2.1"]
    by_vid = {m["name"]: m for m in catalog["video"]["models"]}
    assert by_vid["omni_flash"]["aliases"] == ["omni-flash"]


def test_catalog_video_aspects_are_all_gen_command_accepted() -> None:
    """Same regression guard as the alias check, applied to video aspects.

    ``video_api.aspect_choices()`` returns 9:16, 16:9, AND 1:1 (the underlying
    ``Aspect`` enum has SQUARE), but ``cli_video.py``'s ``--aspect`` Choice
    only accepts ``9:16`` / ``16:9``. Advertising ``1:1`` in the catalog
    would mislead a UI into passing a value the gen command rejects with a
    Click usage error (exit 2).
    """
    from gflow_cli.cli_models import _VIDEO_CLI_ASPECTS

    catalog = build_catalog()
    video_ratios = {a["ratio"] for a in catalog["video"]["aspects"]}
    assert video_ratios == set(_VIDEO_CLI_ASPECTS), (
        f"catalog video aspects {sorted(video_ratios)} must match "
        f"--aspect Choice {sorted(_VIDEO_CLI_ASPECTS)} exactly; "
        "any drift leaks an unaccepted value to UI consumers."
    )
    assert "1:1" not in video_ratios, (
        "video catalog must NOT advertise 1:1 — `gflow video t2v --aspect 1:1` "
        "exits with Click invalid-choice (only 9:16 / 16:9 are accepted)."
    )


def test_image_models_carry_flow_product_label() -> None:
    """#1038: the catalog names each image model the way Flow's menu does."""
    by_img = {m["name"]: m for m in build_catalog()["image"]["models"]}

    assert by_img["NARWHAL"]["label"] == "Nano Banana 2.1 (labs last showed Nano Banana 2)"
    assert by_img["GEM_PIX_2"]["label"] == "Nano Banana Pro"
    assert by_img["HARBOR_SEAL"]["label"] == "Nano Banana 2 Lite"
    # Imagen 4 left Flow's menu (2026-08-26); never present it as a live name.
    assert by_img["IMAGEN_3_5"]["label"] == "Imagen 4 (not in Flow's menu)"


def test_models_table_shows_label() -> None:
    from gflow_cli.cli import main

    result = CliRunner().invoke(main, ["models"], catch_exceptions=False)

    assert "Nano Banana Pro" in result.output


def test_nano_2_1_spellings_select_the_nano2_tier() -> None:
    """#1038: Flow's menu says 2.1, so `--model nano2.1` and manifests must accept it."""
    from gflow_cli.api.image import Model
    from gflow_cli.image_batch import ALLOWED_MODELS

    for alias in ("nano21", "nano2.1", "nano-2.1"):
        assert alias in ALLOWED_MODELS
        assert Model.from_cli(alias) is Model.NARWHAL


# --- #1196: `models list` / `models show <id>` --------------------------------


def _invoke(*args: str) -> Any:
    from gflow_cli.cli import main

    return CliRunner().invoke(main, ["models", *args], catch_exceptions=False)


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_bare_models_is_an_alias_of_models_list(extra: list[str]) -> None:
    bare = _invoke(*extra)
    listed = _invoke("list", *extra)

    assert bare.exit_code == listed.exit_code == 0, bare.output + listed.output
    assert bare.output == listed.output


def _mcp_entries() -> list[dict[str, Any]]:
    from gflow_cli.mcp.tools import gflow_list_models

    catalog = asyncio.run(gflow_list_models())
    return [m for kind in ("image", "video") for m in catalog[kind]["models"]]


def test_models_show_json_is_the_mcp_entry_for_that_model() -> None:
    """One serializer: `show --json` emits exactly what gflow_list_models carries."""
    for entry in _mcp_entries():
        for model_id in (entry["name"], *entry["aliases"]):
            result = _invoke("show", model_id, "--json")
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == entry, model_id


def test_models_show_matches_case_insensitively() -> None:
    result = _invoke("show", "narwhal", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["name"] == "NARWHAL"


def test_group_json_flag_reaches_show() -> None:
    result = _invoke("--json", "show", "omni-flash")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["name"] == "omni_flash"


def test_models_show_table_names_the_model() -> None:
    result = _invoke("show", "omni-flash")

    assert result.exit_code == 0, result.output
    assert "omni_flash" in result.output
    assert "16:9" in result.output


@pytest.mark.parametrize(("typo", "expected"), [("narwal", "NARWHAL"), ("omni-flsh", "omni-flash")])
def test_unknown_model_exits_2_with_closest_matches(typo: str, expected: str) -> None:
    from gflow_cli.cli import main

    result = CliRunner().invoke(main, ["models", "show", typo])

    assert result.exit_code == 2, result.output
    assert expected in result.output


def test_model_ids_are_unique_across_kinds_ignoring_case() -> None:
    """`show` looks ids up case-insensitively across image AND video."""
    ids = [i.lower() for e in _mcp_entries() for i in {e["name"], *e["aliases"]}]
    assert len(ids) == len(set(ids))
