"""Tests for shell-friendly multi-prompt `gflow image t2i`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


def test_parse_prompt_lines_skips_blank_and_comment_lines() -> None:
    from gflow_cli.image_batch import parse_prompt_lines

    parsed = parse_prompt_lines(
        "\ufeff first prompt \n\n  # comment\nsecond # literal\n   third   \n",
        source_label="--stdin",
    )

    assert [p.text for p in parsed] == ["first prompt", "second # literal", "third"]
    assert [p.line_number for p in parsed] == [1, 4, 5]
    assert [p.prompt_index for p in parsed] == [0, 1, 2]
    assert all(p.source_label == "--stdin" for p in parsed)


def test_parse_prompt_lines_empty_after_filtering_raises() -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import parse_prompt_lines

    with pytest.raises(ConfigurationError, match="between 1 and 50"):
        parse_prompt_lines("\n# only comment\n   \n", source_label="--stdin")


def test_parse_prompt_lines_over_50_raises() -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import parse_prompt_lines

    text = "\n".join(f"prompt {i}" for i in range(51))
    with pytest.raises(ConfigurationError, match="between 1 and 50"):
        parse_prompt_lines(text, source_label="--stdin")


def test_parse_prompt_lines_long_prompt_reports_source_line() -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import parse_prompt_lines

    with pytest.raises(ConfigurationError) as exc:
        parse_prompt_lines("ok\n" + ("x" * 2001), source_label="--prompts-file prompts.txt")

    msg = str(exc.value)
    assert "--prompts-file prompts.txt" in msg
    assert "line 2" in msg
    assert "2000" in msg


def test_read_prompt_file_rejects_oversized_file(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    prompts = tmp_path / "prompts.txt"
    prompts.write_bytes(b"x" * (512 * 1024 + 1))

    with pytest.raises(ConfigurationError, match="512 KiB"):
        read_prompt_file(prompts)


def test_read_prompt_file_rejects_invalid_utf8(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    prompts = tmp_path / "prompts.txt"
    prompts.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ConfigurationError, match="UTF-8"):
        read_prompt_file(prompts)


def test_read_prompt_file_uses_basename_in_error(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    missing = tmp_path / "private" / "prompts.txt"
    with pytest.raises(ConfigurationError) as exc:
        read_prompt_file(missing)

    assert "--prompts-file prompts.txt" in str(exc.value)
    assert str(tmp_path) not in str(exc.value)


def test_read_prompt_file_sanitizes_basename_in_error(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    weird = tmp_path / "bad[red]\x1b[31m.txt"
    with pytest.raises(ConfigurationError) as exc:
        read_prompt_file(weird)

    msg = str(exc.value)
    assert "\x1b[31m" not in msg
    assert "\\[red]" in msg


def test_read_prompt_file_read_error_uses_basename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    prompts = tmp_path / "prompts.txt"
    prompts.write_text("p1\n", encoding="utf-8")

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", _raise)
    with pytest.raises(ConfigurationError) as exc:
        read_prompt_file(prompts)

    assert "--prompts-file prompts.txt" in str(exc.value)
    assert "permission denied" not in str(exc.value)


def _invoke_t2i(args: list[str]):
    from gflow_cli.cli import main

    return CliRunner().invoke(main, ["image", "t2i", *args], catch_exceptions=False)


def test_t2i_rejects_multiple_prompt_sources_before_profile_resolution(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("p1\n", encoding="utf-8")

    with patch("gflow_cli.cli_image._resolve_profile") as resolve_profile:
        result = _invoke_t2i(["positional", "--prompts-file", str(prompts)])

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower()
    resolve_profile.assert_not_called()


def test_t2i_rejects_expand_with_multiple_prompts() -> None:
    with patch("gflow_cli.cli_image._resolve_profile") as resolve_profile:
        result = _invoke_t2i(["one", "two", "--expand"])

    assert result.exit_code == 2
    assert "single-prompt only" in result.output.lower()
    resolve_profile.assert_not_called()


def test_t2i_rejects_empty_stdin_before_profile_resolution() -> None:
    from gflow_cli.cli import main

    with patch("gflow_cli.cli_image._resolve_profile") as resolve_profile:
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "--stdin"],
            input="# none\n\n",
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    resolve_profile.assert_not_called()


def test_t2i_rejects_oversized_stdin_before_profile_resolution() -> None:
    from gflow_cli.cli import main
    from gflow_cli.image_batch import MAX_PROMPT_FILE_BYTES

    with patch("gflow_cli.cli_image._resolve_profile") as resolve_profile:
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "--stdin"],
            input="x" * (MAX_PROMPT_FILE_BYTES + 1),
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "exceeds the maximum allowed size" in result.output
    resolve_profile.assert_not_called()


def test_t2i_rejects_51_positional_prompts_before_profile_and_output_dir() -> None:
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", *[f"p{i}" for i in range(51)]],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "between 1 and 50" in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


def test_t2i_rejects_long_positional_prompt_before_profile_and_output_dir() -> None:
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "ok", "x" * 2001],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "2000" in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        ("invalid_utf8.txt", b"\xff\xfe\x00", "UTF-8"),
        ("empty.txt", b"# comment\n\n", "between 1 and 50"),
        ("long.txt", ("x" * 2001).encode("utf-8"), "2000"),
        ("too_many.txt", "\n".join(f"p{i}" for i in range(51)).encode("utf-8"), "between 1 and 50"),
    ],
)
def test_t2i_rejects_invalid_prompt_files_before_profile_and_output_dir(
    tmp_path: Path, filename: str, content: bytes, expected: str
) -> None:
    from gflow_cli.cli import main

    path = tmp_path / filename
    path.write_bytes(content)
    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "--prompts-file", str(path)],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert expected in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


def test_t2i_rejects_missing_prompt_file_before_profile_and_output_dir(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    missing = tmp_path / "missing.txt"
    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "--prompts-file", str(missing)],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "--prompts-file missing.txt" in result.output
    assert str(tmp_path) not in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


def test_t2i_rejects_prompt_file_directory_before_profile_and_output_dir(
    tmp_path: Path,
) -> None:
    from gflow_cli.cli import main

    directory = tmp_path / "prompts.txt"
    directory.mkdir()
    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "--prompts-file", str(directory)],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "regular file" in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


def test_t2i_multi_positional_delegates_to_batch_runner(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    async def _fake_run_batch(**_kwargs: object) -> list[object]:
        return []

    out = tmp_path / "out"
    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "profile"),
        patch("gflow_cli.cli_image.run_image_batch", side_effect=_fake_run_batch) as run_batch,
        patch("gflow_cli.cli_image.render_image_batch_summary", return_value=0),
    ):
        result = CliRunner().invoke(
            main,
            [
                "image",
                "t2i",
                "p1",
                "p2",
                "p3",
                "--aspect",
                "16:9",
                "--model",
                "image4",
                "--out",
                str(out),
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    kwargs = run_batch.call_args.kwargs
    assert [p.text for p in kwargs["prompts"]] == ["p1", "p2", "p3"]
    assert [p.output_filename for p in kwargs["prompts"]] == ["prompt_0", "prompt_1", "prompt_2"]
    assert all(p.aspect_ratio == "16:9" for p in kwargs["prompts"])
    assert all(p.model == "image4" for p in kwargs["prompts"])
    assert kwargs["output_dir"] == out
    assert kwargs["project_title"] == "gflow-cli t2i"


def test_t2i_multi_prompt_prints_fanout_before_batch_runner(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    async def _fake_run_batch(**_kwargs: object) -> list[object]:
        return []

    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "profile"),
        patch("gflow_cli.cli_image.run_image_batch", side_effect=_fake_run_batch),
        patch("gflow_cli.cli_image.render_image_batch_summary", return_value=0),
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "p1", "p2", "-n", "4", "--out", str(tmp_path / "out")],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "up to 8 image(s)" in result.output


def test_t2i_single_prompt_fail_fast_is_inert(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "profile"),
        patch("gflow_cli.cli_image._run_t2i") as run_t2i,
        patch("gflow_cli.cli_image.run_image_batch") as run_batch,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "one prompt", "--fail-fast"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    run_t2i.assert_called_once()
    run_batch.assert_not_called()


@pytest.mark.asyncio
async def test_run_one_image_prompt_preserves_raw_prompt_in_request(tmp_path: Path) -> None:
    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.image_batch import BatchPromptItem, run_one_image_prompt, safe_prompt_preview

    raw_prompt = "[red]literal[/red] \x1b[31m escape"
    image = GeneratedImage(
        media_name="img-1",
        workflow_id="wf-1",
        seed=1,
        prompt=raw_prompt,
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://flow-content.google/x",
        dimensions=(768, 1344),
    )
    client = MagicMock()
    client.generate_image = AsyncMock(return_value=image)
    client.download_image = AsyncMock(side_effect=lambda _img, path: path)

    item = BatchPromptItem(index=0, text=raw_prompt, output_filename="prompt_0")
    await run_one_image_prompt(
        client=client,
        project_id="proj",
        idx=0,
        item=item,
        output_dir=tmp_path,
    )

    req = client.generate_image.await_args.kwargs["req"]
    assert req.prompt == raw_prompt
    assert safe_prompt_preview(raw_prompt) != raw_prompt
