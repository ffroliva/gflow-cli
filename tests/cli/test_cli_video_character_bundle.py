"""`gflow video t2v|r2v --character FOLDER` wiring (#639): several folders, one clip.
The service is pinned in tests/services/test_character_portability.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from gflow_cli.cli import main


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(main, ["video", *args], catch_exceptions=False)


def _folders(tmp_path: Path) -> tuple[Path, Path]:
    nora, kael = tmp_path / "nora", tmp_path / "kael"
    nora.mkdir()
    kael.mkdir()
    return nora, kael


def test_t2v_hands_every_folder_to_the_runner(tmp_path: Path) -> None:
    nora, kael = _folders(tmp_path)
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "p"),
        patch("gflow_cli.cli_video.run_with_handlers") as run,
        patch("gflow_cli.cli_video._run_t2v") as runner,
    ):
        result = _invoke("t2v", "Nora and Kael", "--character", str(nora), "--character", str(kael))
        run.call_args.args[0]()

    assert result.exit_code == 0, result.output
    assert runner.call_args.kwargs["characters"] == (nora, kael)


def test_r2v_hands_the_folders_to_the_runner(tmp_path: Path) -> None:
    from PIL import Image

    nora, _ = _folders(tmp_path)
    ref = tmp_path / "cafe.png"
    Image.new("RGB", (8, 8)).save(ref)
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "p"),
        patch("gflow_cli.cli_video.run_with_handlers") as run,
        patch("gflow_cli.cli_video._run_r2v") as runner,
    ):
        result = _invoke("r2v", "Nora in the cafe", "--ref", str(ref), "--character", str(nora))
        run.call_args.args[0]()

    assert result.exit_code == 0, result.output
    assert runner.call_args.kwargs["characters"] == (nora,)


def test_i2v_has_no_character_option(tmp_path: Path) -> None:
    nora, _ = _folders(tmp_path)
    result = _invoke("i2v", "x", "--character", str(nora))
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_r2v_counts_characters_against_the_reference_cap_before_any_browser(
    tmp_path: Path,
) -> None:
    """Council D8: `--ref a --ref b --ref c --character X` on a 3-reference model raised a
    ValueError after the browser opened (exit 1, "Unexpected error"). Counted up front,
    like `image i2i` does, it is a usage error (exit 2)."""
    from PIL import Image

    nora, _ = _folders(tmp_path)
    refs = []
    for i in range(3):
        ref = tmp_path / f"r{i}.png"
        Image.new("RGB", (8, 8)).save(ref)
        refs += ["--ref", str(ref)]
    with patch("gflow_cli.cli_video._resolve_profile") as resolve:
        result = _invoke("r2v", "x", "--model", "veo-fast", *refs, "--character", str(nora))
    assert result.exit_code == 2, result.output
    assert "reference" in result.output
    resolve.assert_not_called()


async def test_a_bad_character_folder_is_refused_before_flow_opens(tmp_path: Path) -> None:
    """Council D7/D8: the folder was read only after the project existed, so a bad one
    left an empty project behind. It is read before the client opens now."""
    import pytest

    from gflow_cli.cli_video import _generate_and_report
    from gflow_cli.errors import ConfigurationError

    empty = tmp_path / "not-a-bundle"
    empty.mkdir()
    with (
        patch("gflow_cli.cli_video.FlowApiClient") as client,
        pytest.raises(ConfigurationError, match="manifest"),
    ):
        await _generate_and_report(
            object(),
            profile_name="p",
            profile_dir=tmp_path,
            out_dir=None,
            as_json=True,
            project_name="t",
            characters=(empty,),
        )
    client.assert_not_called()


async def test_the_bundled_request_is_the_one_generated(tmp_path: Path) -> None:
    """Council D4: patching `_run_t2v` hid whether `_generate_and_report` applies the
    bundles at all. The request `apply_character_bundles` returns must be the one sent."""
    from unittest.mock import AsyncMock, MagicMock

    import pytest

    from gflow_cli.cli_video import _generate_and_report

    bundled = MagicMock(name="bundled-request")
    client = MagicMock()
    client.create_project = AsyncMock(return_value=MagicMock(project_id="proj"))
    client.generate_video = AsyncMock(side_effect=RuntimeError("stop after generate"))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    nora, _ = _folders(tmp_path)

    async def passthrough(_client: Any, req: Any, **_: Any) -> Any:
        return req

    with (
        patch("gflow_cli.cli_video.FlowApiClient", return_value=ctx),
        patch("gflow_cli.services.character_portability.validate_bundles"),
        patch(
            "gflow_cli.services.character_portability.apply_character_bundles",
            AsyncMock(return_value=bundled),
        ) as apply,
        patch("gflow_cli.services.mentions.resolve_and_apply", passthrough),
        patch("gflow_cli.cli_video.OperationRecorder"),
        pytest.raises((RuntimeError, SystemExit, ValueError)),  # stops after generate
    ):
        await _generate_and_report(
            MagicMock(name="request"),
            profile_name="p",
            profile_dir=tmp_path,
            out_dir=None,
            as_json=True,
            project_name="t",
            characters=(nora,),
        )
    assert apply.await_args.kwargs["bundles"] == (nora,)
    assert apply.await_args.kwargs["project_id"] == "proj"
    assert client.generate_video.await_args.kwargs["req"] is bundled
