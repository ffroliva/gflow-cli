"""Click-runner tests for `gflow image upload` and `gflow image t2i`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from click.testing import CliRunner

from gflow_cli.api.dto import AssetInfo, GeneratedImage


class FakeRecorder:
    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.generated: list[dict] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def record_upload_image(self, **kwargs: object) -> None:
        self.uploads.append(kwargs)

    def record_generated_images(self, **kwargs: object) -> None:
        self.generated.append(kwargs)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_mock_client(*, asset_name: str = "asset-uuid-123") -> MagicMock:
    """Stub FlowApiClient: create_project + upload_image succeed."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(
        return_value=MagicMock(project_id="proj-1", title="gflow-cli upload")
    )
    client.upload_image = AsyncMock(
        return_value=AssetInfo(
            name=asset_name,
            project_id="proj-1",
            workflow_id="wf-1",
            display_name="hero.png",
            width=1024,
            height=1536,
        )
    )
    return client


class TestImageUpload:
    def test_image_upload_prints_uuid(self, runner: CliRunner, tmp_path: Path) -> None:
        png = tmp_path / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")  # placeholder bytes; client is mocked
        client = _make_mock_client(asset_name="asset-uuid-123")

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "upload", str(png)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        # Lock the wire contract: project must be titled "gflow-cli upload" so
        # uploads don't pollute the user's library with untitled drafts.
        client.create_project.assert_awaited_once_with(title="gflow-cli upload")
        client.upload_image.assert_awaited_once()
        # The UUID must be visible in stdout (primary user-facing value).
        assert "asset-uuid-123" in result.output
        # Dimensions ought to surface for human consumption.
        assert "1024" in result.output and "1536" in result.output

    def test_image_upload_uses_default_profile(self, runner: CliRunner, tmp_path: Path) -> None:
        png = tmp_path / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        client = _make_mock_client()

        # _resolve_profile is the single chokepoint — assert it's invoked with None
        # when --profile is omitted, which is what "uses default" means in this CLI.
        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default") as resolve_mock,
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "upload", str(png)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        resolve_mock.assert_called_once_with(None)

    def test_image_upload_errors_on_missing_file(self, runner: CliRunner, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.png"
        # No mocks needed — Click's `exists=True` should reject before any I/O.
        from gflow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "upload", str(missing)],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output
        # Click writes a friendly error mentioning the offending path.
        assert str(missing) in result.output or "does not exist" in result.output.lower()


# ---------------------------------------------------------------------------
# t2i subcommand
# ---------------------------------------------------------------------------


def _make_generated_image(
    *,
    media_name: str = "img-uuid-1",
    seed: int = 12345,
    model: str = "NARWHAL",
    width: int = 768,
    height: int = 1344,
) -> GeneratedImage:
    return GeneratedImage(
        media_name=media_name,
        workflow_id="wf-1",
        seed=seed,
        prompt="a cat",
        model_name_type=model,
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://flow-content.google/x?Signature=abc",
        dimensions=(width, height),
    )


def _make_t2i_client(
    *,
    images: list[GeneratedImage] | None = None,
    payload: bytes = b"\x89PNG\r\n\x1a\n",
) -> MagicMock:
    """Stub FlowApiClient: create_project + generate_image[s_batch] + download_image.

    ``payload`` is the bytes written by the fake ``download_image``. The fake
    mirrors the real client (``api/client.py``): write bytes, then pass through
    ``correct_image_extension`` so the CLI sees the post-rename path. Pass a
    JPEG header to exercise the issue-#96 codepath end-to-end.
    """
    from gflow_cli.paths import correct_image_extension

    images = images or [_make_generated_image()]
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(
        return_value=MagicMock(project_id="proj-1", title="gflow-cli t2i")
    )
    client.generate_image = AsyncMock(return_value=images[0])
    client.generate_images_batch = AsyncMock(return_value=images)

    async def _fake_download(image: GeneratedImage, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)
        return correct_image_extension(out_path)

    client.download_image = AsyncMock(side_effect=_fake_download)
    return client


class TestImageT2I:
    def test_t2i_single_image_writes_one_file(self, runner: CliRunner, tmp_path: Path) -> None:
        images = [_make_generated_image(media_name="m1")]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "t2i", "a cat", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        client.generate_image.assert_awaited_once()
        client.generate_images_batch.assert_not_called()
        # download_image called once for the single image
        assert client.download_image.await_count == 1
        # File should be at out_dir/m1_1.png (no date subdir when --out passed explicitly)
        written = list(out_dir.rglob("*.png"))
        assert len(written) == 1, written

    def test_t2i_multi_image_writes_n_files(self, runner: CliRunner, tmp_path: Path) -> None:
        images = [
            _make_generated_image(media_name="m1", seed=1),
            _make_generated_image(media_name="m2", seed=2),
            _make_generated_image(media_name="m3", seed=3),
        ]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "t2i", "a cat", "-n", "3", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        client.generate_images_batch.assert_awaited_once()
        client.generate_image.assert_not_called()
        assert client.download_image.await_count == 3
        written = sorted(p.name for p in out_dir.rglob("*.png"))
        assert written == ["m1_1.png", "m2_2.png", "m3_3.png"], written

    def test_t2i_invalid_aspect_errors(self, runner: CliRunner) -> None:
        from gflow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "t2i", "a cat", "--aspect", "5:7"],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output

    @pytest.mark.parametrize("count", ["0", "5"])
    def test_t2i_invalid_count_errors(self, runner: CliRunner, count: str) -> None:
        from gflow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "t2i", "a cat", "-n", count],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output

    def test_t2i_passes_model_correctly(self, runner: CliRunner, tmp_path: Path) -> None:
        images = [_make_generated_image(media_name="m1", model="GEM_PIX_2")]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                [
                    "image",
                    "t2i",
                    "a cat",
                    "--model",
                    "nano-pro",
                    "--out",
                    str(out_dir),
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        # Inspect the GenerateImageRequest passed to generate_image.
        from gflow_cli.api.image import Model

        call = client.generate_image.await_args
        assert call is not None
        req = call.kwargs["req"]
        assert req.model == Model.GEM_PIX_2
        assert req.model.value == "GEM_PIX_2"


# ---------------------------------------------------------------------------
# i2i subcommand
# ---------------------------------------------------------------------------


def _make_i2i_client(
    *,
    images: list[GeneratedImage] | None = None,
    upload_uuid: str = "ddb6ef97-262d-49f4-8269-4a28c0fae6a2",
) -> MagicMock:
    """Stub FlowApiClient: create_project + upload_image + generate_image[s_batch]."""
    images = images or [_make_generated_image()]
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(
        return_value=MagicMock(project_id="proj-1", title="gflow-cli i2i")
    )
    client.upload_image = AsyncMock(
        return_value=AssetInfo(
            name=upload_uuid,
            project_id="proj-1",
            workflow_id="wf-1",
            display_name="hero.png",
            width=1024,
            height=1536,
        )
    )
    client.generate_image = AsyncMock(return_value=images[0])
    client.generate_images_batch = AsyncMock(return_value=images)

    async def _fake_download(image: GeneratedImage, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return out_path

    client.download_image = AsyncMock(side_effect=_fake_download)
    return client


class TestImageI2I:
    def test_i2i_attaches_local_ref_paths(self, runner: CliRunner, tmp_path: Path) -> None:
        """Local --ref path lands on req.ref_paths for UI media-dialog attach —
        it must NOT hit the REST uploadImage endpoint (which 401s, see #15/#39)."""
        png = tmp_path / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        client = _make_i2i_client(upload_uuid="should-not-be-used")
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "i2i", "make it cinematic", "--ref", str(png), "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        client.upload_image.assert_not_called()
        # Local file goes to req.ref_paths (UI-attached); refs (wire UUIDs) stays empty.
        call = client.generate_image.await_args
        assert call is not None
        req = call.kwargs["req"]
        assert req.refs == ()
        assert len(req.ref_paths) == 1
        assert req.ref_paths[0] == png.resolve()

    def test_i2i_passes_through_uuid_refs(self, runner: CliRunner, tmp_path: Path) -> None:
        """Bare-UUID --ref must NOT trigger upload_image and must be used verbatim."""
        uuid_str = "ddb6ef97-262d-49f4-8269-4a28c0fae6a2"
        client = _make_i2i_client(upload_uuid="should-not-be-used")
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "i2i", "stylize this", "--ref", uuid_str, "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        client.upload_image.assert_not_called()
        call = client.generate_image.await_args
        assert call is not None
        req = call.kwargs["req"]
        assert len(req.refs) == 1
        assert req.refs[0].name == uuid_str

    def test_i2i_splits_paths_and_uuids(self, runner: CliRunner, tmp_path: Path) -> None:
        """Mixed `--ref path --ref uuid` → local path on ref_paths (UI-attached),
        bare UUID on refs (wire). No REST upload for either."""
        png = tmp_path / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        passthrough_uuid = "22222222-2222-2222-2222-222222222222"
        client = _make_i2i_client(upload_uuid="should-not-be-used")
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                [
                    "image",
                    "i2i",
                    "blend",
                    "--ref",
                    str(png),
                    "--ref",
                    passthrough_uuid,
                    "--out",
                    str(out_dir),
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        client.upload_image.assert_not_called()
        call = client.generate_image.await_args
        assert call is not None
        req = call.kwargs["req"]
        # Local path → ref_paths; bare UUID → refs.
        assert [p for p in req.ref_paths] == [png.resolve()]
        assert [r.name for r in req.refs] == [passthrough_uuid]

    def test_i2i_errors_on_no_refs(self, runner: CliRunner) -> None:
        """`image i2i` without any --ref must exit 2 with a clear message."""
        from gflow_cli.cli import main

        # No mocks — should fail Click validation BEFORE any I/O.
        result = runner.invoke(
            main,
            ["image", "i2i", "a cat"],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output
        # Click's "Missing option '--ref'" is acceptable; we also want users to
        # know t2i exists for text-only generation. The custom message lands in
        # the option's help text and (when raised by us) in stderr.
        lower = result.output.lower()
        assert "ref" in lower

    def test_i2i_errors_on_missing_ref_file(self, runner: CliRunner, tmp_path: Path) -> None:
        """`--ref nonexistent.png` (looks like a path, file missing) → exit 2."""
        missing = tmp_path / "does_not_exist.png"
        from gflow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "i2i", "a cat", "--ref", str(missing)],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output
        lower = result.output.lower()
        assert "does not exist" in lower or "not found" in lower or str(missing) in result.output


# ---------------------------------------------------------------------------
# batch subcommand — flag contract
# ---------------------------------------------------------------------------


class TestImageBatch:
    """CLI contract tests for `gflow image batch`."""

    def test_batch_help_does_not_mention_same_project(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--same-project must NOT appear in --help output after Phase 4 removal."""
        monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
        from gflow_cli.cli import main

        result = runner.invoke(main, ["image", "batch", "--help"])

        assert result.exit_code == 0, f"--help should succeed:\n{result.output}"
        assert "--same-project" not in result.output

    def test_batch_help_describes_always_same_project(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--help must explain that all prompts share one project and mention jitter."""
        monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
        from gflow_cli.cli import main

        result = runner.invoke(main, ["image", "batch", "--help"])

        assert result.exit_code == 0, f"--help should succeed:\n{result.output}"
        lower = result.output.lower()
        # Always-same-project semantics must be described.
        assert (
            "same project" in lower
            or "one project" in lower
            or "shared project" in lower
            or "flow project" in lower
        )
        # Anti-bot jitter rationale must be visible.
        assert "jitter" in lower

    def test_batch_rejects_same_project_flag(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Passing --same-project must produce exit 2 (unknown option) after removal."""
        monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
        manifest = tmp_path / "prompts.tsv"
        manifest.write_text("a cat\n")
        from gflow_cli.cli import main

        result = runner.invoke(main, ["image", "batch", str(manifest), "--same-project"])

        assert result.exit_code == 2, (
            f"--same-project should be rejected as unknown option, got:\n{result.output}"
        )


# ---------------------------------------------------------------------------
# --transport flag (A.6)
# ---------------------------------------------------------------------------


class TestTransportFlag:
    """--transport flag wiring tests for image subcommands."""

    def test_t2i_help_shows_transport_flag(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
        from gflow_cli.cli import main

        result = runner.invoke(main, ["image", "t2i", "--help"])

        assert result.exit_code == 0, f"--help should succeed:\n{result.output}"
        assert "--transport" in result.output

    def test_i2i_help_shows_transport_flag(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
        from gflow_cli.cli import main

        result = runner.invoke(main, ["image", "i2i", "--help"])

        assert result.exit_code == 0, f"--help should succeed:\n{result.output}"
        assert "--transport" in result.output

    def test_upload_help_shows_transport_flag(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
        from gflow_cli.cli import main

        result = runner.invoke(main, ["image", "upload", "--help"])

        assert result.exit_code == 0, f"--help should succeed:\n{result.output}"
        assert "--transport" in result.output

    def test_t2i_rejects_unknown_transport_value(self, runner: CliRunner) -> None:
        from gflow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "t2i", "a cat", "--transport", "nonsense_xyz"],
        )

        assert result.exit_code != 0
        assert "nonsense_xyz" in result.output or "invalid value" in result.output.lower()

    def test_t2i_accepts_valid_transport_evaluate_fetch(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Experimental keys are gated behind GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1 at
        # the CLI Choice list (AUDIT_E1 Section B). Set env + reload so the Click
        # decorator picks up the expanded transport_choices().
        import importlib

        import gflow_cli.cli as _cli_mod
        import gflow_cli.cli_image as _cli_image_mod

        monkeypatch.setenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS", "1")
        importlib.reload(_cli_image_mod)
        importlib.reload(_cli_mod)

        images = [_make_generated_image(media_name="m1")]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client) as mock_cls,
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            result = runner.invoke(
                _cli_mod.main,
                ["image", "t2i", "a cat", "--transport", "evaluate_fetch", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        # transport value must be forwarded to FlowApiClient constructor
        _, kwargs = mock_cls.call_args
        assert kwargs.get("transport") == "evaluate_fetch"

    def test_t2i_accepts_valid_transport_bearer(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib

        import gflow_cli.cli as _cli_mod
        import gflow_cli.cli_image as _cli_image_mod

        monkeypatch.setenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS", "1")
        importlib.reload(_cli_image_mod)
        importlib.reload(_cli_mod)

        images = [_make_generated_image(media_name="m1")]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client) as mock_cls,
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            result = runner.invoke(
                _cli_mod.main,
                ["image", "t2i", "a cat", "--transport", "bearer", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_cls.call_args
        assert kwargs.get("transport") == "bearer"


# ---------------------------------------------------------------------------
# OperationRecorder integration tests
# ---------------------------------------------------------------------------


class TestRecorderIntegration:
    def test_upload_records_upload_image(self, runner: CliRunner, tmp_path: Path) -> None:
        png = tmp_path / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        client = _make_mock_client(asset_name="asset-uuid-123")
        recorder = FakeRecorder()

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image.OperationRecorder.open", return_value=recorder),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "upload", str(png)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert len(recorder.uploads) == 1
        rec = recorder.uploads[0]
        assert rec["profile_name"] == "default"
        assert rec["profile_dir"] == tmp_path / "prof"
        assert rec["image_path"] == png
        assert rec["asset"].name == "asset-uuid-123"
        assert rec["project"].project_id == "proj-1"
        assert recorder.closed

    def test_t2i_records_generated_images_after_download(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        images = [_make_generated_image(media_name="m1")]
        client = _make_t2i_client(images=images)
        recorder = FakeRecorder()
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image.OperationRecorder.open", return_value=recorder),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "t2i", "a cat", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert len(recorder.generated) == 1
        rec = recorder.generated[0]
        assert rec["operation_kind"] == "t2i"
        assert rec["input_media_ids"] == []
        assert rec["profile_name"] == "default"
        assert rec["profile_dir"] == tmp_path / "prof"
        assert rec["images"] == images
        assert len(rec["saved_paths"]) == 1
        assert recorder.closed

    def test_t2i_records_corrected_extension_when_bytes_are_jpeg(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Issue #96 end-to-end: when Flow's CDN serves JPEG bytes for a
        `.png` target, the recorder must receive the corrected `.jpg` path
        — not the original `.png`. This is the surface that broke in PR #93;
        unit + transport tests would have passed there too."""
        images = [_make_generated_image(media_name="m1")]
        # JFIF header followed by padding — sniffer detects this as .jpg.
        jpeg_payload = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64
        client = _make_t2i_client(images=images, payload=jpeg_payload)
        recorder = FakeRecorder()
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image.OperationRecorder.open", return_value=recorder),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "t2i", "a cat", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        rec = recorder.generated[0]
        # Recorder receives the post-rename path — historical trap surface.
        assert len(rec["saved_paths"]) == 1
        saved = rec["saved_paths"][0]
        assert saved.suffix == ".jpg", f"recorder got {saved!r}, expected .jpg"
        assert saved.exists()
        assert saved.read_bytes() == jpeg_payload
        # No stale `.png` left in the output directory.
        assert list(out_dir.rglob("*.png")) == []

    def test_i2i_records_inputs_and_outputs(self, runner: CliRunner, tmp_path: Path) -> None:
        # Post-PR-#50 i2i flow distinguishes two ref shapes:
        #   1. Already-uploaded UUID ref — persists as input_media_ids.
        #   2. Local-file ref — bound via the editor's media dialog by the
        #      transport; has NO Flow media_id at recorder time, so it MUST
        #      NOT appear in input_media_ids.
        png = tmp_path / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        existing_uuid = "ddb6ef97-262d-49f4-8269-4a28c0fae6a2"
        images = [_make_generated_image(media_name="out-img-1")]
        # upload_uuid is unused on the new flow (transport binds local refs
        # directly) but the helper still requires the kwarg.
        client = _make_i2i_client(images=images, upload_uuid=existing_uuid)
        recorder = FakeRecorder()
        out_dir = tmp_path / "out"

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image.OperationRecorder.open", return_value=recorder),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                [
                    "image",
                    "i2i",
                    "make cinematic",
                    "--ref",
                    existing_uuid,  # UUID ref — persists
                    "--ref",
                    str(png),  # local file ref — invisible to recorder
                    "--out",
                    str(out_dir),
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert len(recorder.generated) == 1
        rec = recorder.generated[0]
        assert rec["operation_kind"] == "i2i"
        # Only the UUID ref reaches the recorder. The local-file ref is bound
        # directly at the transport layer and has no media_id to persist.
        assert rec["input_media_ids"] == [existing_uuid]
        assert recorder.closed

    def test_t2i_persistence_failure_after_success_warns_and_succeeds(
        self,
        runner: CliRunner,
        tmp_path: Path,
        install_log_capture: structlog.testing.LogCapture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from gflow_cli.errors import DataStoreError

        images = [_make_generated_image(media_name="m1")]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        class FailingRecorder(FakeRecorder):
            def record_generated_images(self, **kwargs: object) -> None:
                raise DataStoreError("simulated write failure")

        recorder = FailingRecorder()

        # Reset structlog so the install_log_capture fixture's configure() takes effect.
        structlog.reset_defaults()
        # Re-install capture after reset (install_log_capture ran before reset).
        cap = structlog.testing.LogCapture()
        structlog.configure(
            processors=[structlog.contextvars.merge_contextvars, cap],
        )

        # Prevent CLI bootstrap from overwriting the test's LogCapture chain.
        import gflow_cli.cli as _cli_mod

        monkeypatch.setattr(_cli_mod, "configure_logging", lambda *a, **kw: None)

        with (
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image.OperationRecorder.open", return_value=recorder),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "t2i", "a cat", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        # Command must succeed despite recorder failure
        assert result.exit_code == 0, result.output
        # Saved file must be preserved
        written = list(out_dir.rglob("*.png"))
        assert len(written) == 1, f"Expected 1 file, got {written}"
        # structlog warning must be emitted
        events = [e["event"] for e in cap.entries]
        assert "data.persistence_failed_after_success" in events
