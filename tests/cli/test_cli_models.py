"""Click-runner tests for `gflow models` (no network — pure catalog)."""

from __future__ import annotations

import json

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
    assert by_img["NARWHAL"]["aliases"] == ["nano2"]
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
