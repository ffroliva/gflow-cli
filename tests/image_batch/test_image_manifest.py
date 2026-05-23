"""Tests for image manifest parsing (TSV + JSON) and run_manifest_image_batch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import (
    MAX_BATCH_PROMPTS,
    BatchPromptItem,
    parse_json_manifest,
    parse_manifest_file,
    parse_tsv_manifest,
    run_manifest_image_batch,
)

# ---------------------------------------------------------------------------
# TSV parsing
# ---------------------------------------------------------------------------


class TestParseTsvManifest:
    def test_minimal_row(self) -> None:
        items = parse_tsv_manifest("a sunny beach\n")
        assert len(items) == 1
        assert items[0].text == "a sunny beach"
        assert items[0].count == 1  # default

    def test_count_column(self) -> None:
        items = parse_tsv_manifest("a beach\t4\n")
        assert items[0].count == 4

    def test_aspect_and_model_columns(self) -> None:
        items = parse_tsv_manifest("a beach\t2\t16:9\tnano-pro\n")
        assert items[0].count == 2
        assert items[0].aspect_ratio == "16:9"
        assert items[0].model == "nano-pro"

    def test_empty_optional_columns_use_defaults(self) -> None:
        items = parse_tsv_manifest("a beach\t\t\t\n")
        assert items[0].count == 1
        assert items[0].aspect_ratio == "9:16"
        assert items[0].model == "nano2"

    def test_skips_comments_and_blanks(self) -> None:
        text = "# header\n\na beach\t2\n# another comment\na forest\n"
        items = parse_tsv_manifest(text)
        assert len(items) == 2
        assert items[0].text == "a beach"
        assert items[1].text == "a forest"

    def test_default_count_override(self) -> None:
        items = parse_tsv_manifest("a beach\n", default_count=3)
        assert items[0].count == 3

    def test_invalid_count_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="not an integer"):
            parse_tsv_manifest("a beach\tbad\n")

    def test_count_out_of_range_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="count must be"):
            parse_tsv_manifest("a beach\t5\n")

    def test_invalid_aspect_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="aspect_ratio"):
            parse_tsv_manifest("a beach\t1\t9:99\n")

    def test_invalid_model_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="model"):
            parse_tsv_manifest("a beach\t1\t\tbogus-model\n")

    def test_empty_prompt_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="prompt column is required"):
            parse_tsv_manifest("\t2\n")

    def test_too_many_prompts_raises(self) -> None:
        lines = "".join(f"prompt {i}\n" for i in range(MAX_BATCH_PROMPTS + 1))
        with pytest.raises(ConfigurationError, match=str(MAX_BATCH_PROMPTS)):
            parse_tsv_manifest(lines)

    def test_empty_manifest_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="between 1 and"):
            parse_tsv_manifest("# only comments\n\n")

    def test_bom_stripped(self) -> None:
        items = parse_tsv_manifest("﻿a beach\n")
        assert items[0].text == "a beach"

    def test_indices_assigned_sequentially(self) -> None:
        text = "alpha\nbeta\ngamma\n"
        items = parse_tsv_manifest(text)
        assert [i.index for i in items] == [0, 1, 2]


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestParseJsonManifest:
    def test_minimal_entry(self) -> None:
        data = [{"text": "a sunny beach"}]
        items = parse_json_manifest(data)
        assert items[0].text == "a sunny beach"
        assert items[0].count == 1

    def test_full_entry(self) -> None:
        data = [{"text": "a beach", "count": 4, "aspect_ratio": "16:9", "model": "nano-pro"}]
        items = parse_json_manifest(data)
        assert items[0].count == 4
        assert items[0].aspect_ratio == "16:9"
        assert items[0].model == "nano-pro"

    def test_default_count_override(self) -> None:
        data = [{"text": "a beach"}]
        items = parse_json_manifest(data, default_count=3)
        assert items[0].count == 3

    def test_entry_count_overrides_default(self) -> None:
        data = [{"text": "a beach", "count": 2}]
        items = parse_json_manifest(data, default_count=4)
        assert items[0].count == 2

    def test_not_a_list_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="top-level array"):
            parse_json_manifest({"text": "a beach"})  # type: ignore[arg-type]

    def test_entry_not_a_dict_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="must be an object"):
            parse_json_manifest(["not-a-dict"])  # type: ignore[list-item]

    def test_too_many_prompts_raises(self) -> None:
        data = [{"text": f"prompt {i}"} for i in range(MAX_BATCH_PROMPTS + 1)]
        with pytest.raises(ConfigurationError, match=str(MAX_BATCH_PROMPTS)):
            parse_json_manifest(data)

    def test_invalid_count_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="count"):
            parse_json_manifest([{"text": "a beach", "count": 5}])


# ---------------------------------------------------------------------------
# parse_manifest_file dispatcher
# ---------------------------------------------------------------------------


class TestParseManifestFile:
    def test_tsv_dispatch(self, tmp_path: Path) -> None:
        f = tmp_path / "m.tsv"
        f.write_text("a beach\t2\n", encoding="utf-8")
        items = parse_manifest_file(f)
        assert items[0].count == 2

    def test_json_dispatch(self, tmp_path: Path) -> None:
        f = tmp_path / "m.json"
        f.write_text(json.dumps([{"text": "a beach", "count": 3}]), encoding="utf-8")
        items = parse_manifest_file(f)
        assert items[0].count == 3

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "m.csv"
        f.write_text("a beach,2\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="Unsupported manifest format"):
            parse_manifest_file(f)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            parse_manifest_file(tmp_path / "ghost.tsv")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "m.json"
        f.write_text("{bad json}", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="not valid JSON"):
            parse_manifest_file(f)

    def test_defaults_propagated(self, tmp_path: Path) -> None:
        f = tmp_path / "m.tsv"
        f.write_text("a beach\n", encoding="utf-8")
        items = parse_manifest_file(f, default_count=4, default_aspect_ratio="16:9")
        assert items[0].count == 4
        assert items[0].aspect_ratio == "16:9"


# ---------------------------------------------------------------------------
# run_manifest_image_batch
# ---------------------------------------------------------------------------


def _make_items(n: int = 2) -> tuple[BatchPromptItem, ...]:
    return tuple(
        BatchPromptItem(text=f"prompt {i}", count=1, index=i, output_filename=f"p{i}")
        for i in range(n)
    )


def _make_batch_factory(
    *,
    project_id: str = "shared-proj",
    fail_on_idx: int | None = None,
) -> type:
    """Build a client factory stub whose transport implements generate_images_batch.

    ``fail_on_idx``: if set, the BatchSubmissionResult at that index will have
    status='fail'. This exercises the _download_results fail-row path.
    """
    from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    fake_image = GeneratedImage(
        media_name="m1",
        workflow_id="wf1",
        seed=1,
        prompt="p",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://example.com/img.png",
        dimensions=(64, 64),
    )

    class _FakeTransport(UiAutomationTransport):
        def __init__(self) -> None:  # skip super().__init__ to avoid Playwright
            pass

    class _FakeClient:
        def __init__(self, **_: object) -> None:
            transport_instance = _FakeTransport()

            def make_results(prompts, **__):  # type: ignore[no-untyped-def]
                results = []
                for idx, req in enumerate(prompts):
                    if fail_on_idx is not None and idx == fail_on_idx:
                        results.append(
                            BatchSubmissionResult(
                                status="fail",
                                project_id=project_id,
                                prompt_idx=idx,
                                prompt_hash="deadbeef",
                                images=(),
                                error=None,
                            )
                        )
                    else:
                        results.append(
                            BatchSubmissionResult(
                                status="ok",
                                project_id=project_id,
                                prompt_idx=idx,
                                prompt_hash="aabbccdd",
                                images=(fake_image,) * req.count,
                            )
                        )
                return results

            async def _batch_impl(prompts, jitter_range, continue_on_error=False):  # type: ignore[no-untyped-def]
                return make_results(prompts)

            transport_instance.generate_images_batch = _batch_impl  # type: ignore[method-assign]
            self.transport = transport_instance

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            pass

        async def download_image(self, img: object, target: Path) -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"PNG")
            return target

    return _FakeClient


class TestRunManifestImageBatch:
    """Smoke-level tests using stub client factories."""

    async def test_calls_transport_once_and_downloads(self, tmp_path: Path) -> None:
        """Orchestrator calls transport.generate_images_batch exactly once, then
        downloads each ok-result's images via client.download_image."""
        factory = _make_batch_factory(project_id="proj-abc")

        outcomes = await run_manifest_image_batch(
            profile_dir=tmp_path,
            headless=True,
            transport=None,
            prompts=_make_items(2),
            output_dir=tmp_path / "out",
            continue_on_error=False,
            jitter_range=(0.0, 0.0),
            client_factory=factory,
        )

        assert len(outcomes) == 2
        assert all(o.status == "ok" for o in outcomes)
        # Each outcome must have a saved file
        for o in outcomes:
            assert len(o.saved_paths) == 1
            assert o.saved_paths[0].exists()

    async def test_fail_row_produces_fail_outcome(self, tmp_path: Path) -> None:
        """A BatchSubmissionResult with status='fail' at idx=1 produces a fail outcome."""
        factory = _make_batch_factory(project_id="proj-abc", fail_on_idx=1)

        outcomes = await run_manifest_image_batch(
            profile_dir=tmp_path,
            headless=True,
            transport=None,
            prompts=_make_items(3),
            output_dir=tmp_path / "out",
            continue_on_error=True,
            jitter_range=(0.0, 0.0),
            client_factory=factory,
        )

        assert len(outcomes) == 3
        assert outcomes[0].status == "ok"
        assert outcomes[1].status == "fail"
        assert outcomes[2].status == "ok"

    async def test_wrong_transport_raises(self, tmp_path: Path) -> None:
        """A non-UiAutomation transport raises RuntimeError."""

        class _WrongTransport:
            pass

        class _WrongClient:
            def __init__(self, **_: object) -> None:
                self.transport = _WrongTransport()

            async def __aenter__(self) -> _WrongClient:
                return self

            async def __aexit__(self, *_: object) -> None:
                pass

        import pytest

        with pytest.raises(RuntimeError, match="ui_automation"):
            await run_manifest_image_batch(
                profile_dir=tmp_path,
                headless=True,
                transport=None,
                prompts=_make_items(1),
                output_dir=tmp_path / "out",
                continue_on_error=False,
                client_factory=_WrongClient,
            )


# ---------------------------------------------------------------------------
# Malformed-row negative fixtures (Wave 3 Task 3.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row, expected_substring",
    [
        ("prompt with bad count\tnot-a-number", "count"),
        ("prompt with bad aspect\t1\t9999:9999", "aspect"),
        ("prompt with unknown model\t1\t16:9\timaginary-model", "model"),
    ],
)
def test_manifest_invalid_row_pins_specific_error(row: str, expected_substring: str) -> None:
    """Each malformed row must surface the field name in the error message."""
    with pytest.raises(ConfigurationError) as exc_info:
        parse_tsv_manifest(
            row + "\n",
            default_count=1,
            default_aspect_ratio="16:9",
            default_model="nano2",
            source_label="<test>",
        )
    assert expected_substring in str(exc_info.value).lower(), exc_info.value


def test_manifest_dispatcher_raises_on_invalid_fixture() -> None:
    """End-to-end: the committed sample_batch_invalid.tsv must raise."""
    fixture = Path("test_assets/sample_batch_invalid.tsv")
    assert fixture.is_file(), "fixture must be committed"
    with pytest.raises(ConfigurationError):
        parse_manifest_file(fixture)
