"""E2E: a lost submit reply body never re-submits a billed generation (#1010).

Binds ``tests/features/migrated_submit_body_loss.feature`` (``-m e2e_image``) and its video
twin ``migrated_video_submit_body_loss.feature`` (``-m e2e_video``, opt in with
``GFLOW_CLI_E2E_RUN_VIDEO=1``); see ``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e.** Offline, a fake page proves the composer types the error. Only a real
submit proves the CLICK is the billed act the client must not repeat: before the fix this
exact run left two images in the project (spike
``docs/superpowers/spikes/2026-10-21-migrated-submit-reply-body-loss.md``).

**How the loss is provoked.** ``Response.text`` raises the exact error #1009 captured for
the first ``ogiZ0b`` reply; every submit request is counted on the browser context.

**The image is recovered, not reported lost (#1075).** Flow made it on every reported run,
so the run reads the new tile off the project grid and downloads it; the one-submit count
still proves nothing re-submitted.

**A video run that uploaded a file is followed to its clip (#1112).** The r2v rows run
the CLI (in-process, so the patched ``Response.text`` reaches it) and the MCP tool; each
counts the submit replies the observer read, so a second submit would show as two.

**A text-to-video clip is followed too (#1141).** Nothing was uploaded, so it is found by
prompt and model, and the project listing confirms it is the only one.

**Cost.** Image: one image of daily quota, zero credits. Video: one Veo lite clip per
text-to-video row and one r2v clip per surface row.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api._generated import Response
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.transports import migrated_composer as mc

scenarios("../features/migrated_submit_body_loss.feature")
scenarios("../features/migrated_video_submit_body_loss.feature")

LOST_BODY = (
    "Response.text: Protocol error (Network.getResponseBody): "
    "No data found for resource with given identifier"
)


@pytest.fixture
def world(e2e_profile_dir: Path) -> dict[str, Any]:
    return {"profile": e2e_profile_dir, "submits": 0, "lost": 0}


def _lose_first_body(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, rpcids: tuple[str, ...]
) -> None:
    real_text = Response.text

    async def text(self: Response) -> str:
        if mc.url_rpcid(self.url) in rpcids:
            world["replies"] = world.get("replies", 0) + 1
        if mc.url_rpcid(self.url) in rpcids and not world["lost"]:
            world["lost"] += 1
            raise PlaywrightError(LOST_BODY)
        return await real_text(self)

    monkeypatch.setattr(Response, "text", text)


def _run_counting(world: dict[str, Any], rpcids: tuple[str, ...], generate: Any) -> None:
    def count(req: Any) -> None:
        # The URL carries the rpcid; `post_data` raises on a gzipped body inside the listener.
        if mc.url_rpcid(req.url) in rpcids:
            world["submits"] += 1

    async def run() -> None:
        async with FlowApiClient(profile_dir=world["profile"], headless=False) as client:
            assert client._context is not None  # noqa: SLF001
            client._context.on("request", count)  # noqa: SLF001
            try:
                world["result"] = await generate(client)
            except Exception as exc:  # noqa: BLE001 — the raised error is what Then checks
                world["error"] = exc

    asyncio.run(run())


def _assert_one_submit(world: dict[str, Any]) -> None:
    if not world["lost"]:
        pytest.fail(f"no submit reply was reached, so nothing was tested: {world.get('error')!r}")
    assert world["submits"] == 1, world["submits"]


@given("an image submit whose reply body Chrome cannot return")
def _armed(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _lose_first_body(world, monkeypatch, (mc.IMAGE_SUBMIT_RPC,))


@when("gflow generates the image on the migrated host")
def _generate(world: dict[str, Any], tmp_path: Path) -> None:
    async def generate(client: FlowApiClient) -> Path:
        image = await client.generate_image(
            req=GenerateImageRequest(prompt="a teal origami crane on a wooden table")
        )
        return Path(str(await client.download_image(image, tmp_path / "recovered.png")))

    _run_counting(world, (mc.IMAGE_SUBMIT_RPC,), generate)


def _png(path: Path, size: int = 256) -> Path:
    """A plain gradient PNG; Flow declines a 1x1 (#755), so it is a real size."""
    import struct
    import zlib

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    rows = b"".join(
        b"\x00" + b"".join(bytes((x, y, 128)) for x in range(size)) for y in range(size)
    )
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    return path


@when("gflow generates an image from a local reference on the migrated host")
def _generate_i2i(world: dict[str, Any], tmp_path: Path) -> None:
    ref = _png(tmp_path / "ref.png")

    async def generate(client: FlowApiClient) -> Path:
        image = await client.generate_image(
            req=GenerateImageRequest(
                prompt="the same colours as a watercolour landscape", ref_paths=(ref,)
            )
        )
        world["media_name"] = image.media_name
        return Path(str(await client.download_image(image, tmp_path / "recovered.png")))

    _run_counting(world, (mc.IMAGE_SUBMIT_RPC,), generate)


@then("gflow returns the one image the submit made, not the uploaded reference")
def _recovered_not_reference(world: dict[str, Any]) -> None:
    _recovered(world)
    # The reference is a 256x256 gradient; Flow's output is never that file back.
    assert world["result"].read_bytes() != (world["result"].parent / "ref.png").read_bytes()


@then("exactly one image submit reached Flow")
def _one_submit(world: dict[str, Any]) -> None:
    _assert_one_submit(world)


@given("a video submit whose reply body Chrome cannot return")
def _armed_video(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend credits on the lost video reply e2e")
    _lose_first_body(world, monkeypatch, mc.SUBMIT_RPCS)


@then("gflow returns the one image the submit made, downloadable")
def _recovered(world: dict[str, Any]) -> None:
    assert "error" not in world, repr(world.get("error"))
    path: Path = world["result"]
    assert path.stat().st_size > 1000, path.stat().st_size


_R2V_PROMPT = "a blue card propped on a wooden jetty at dusk, static camera"


@pytest.fixture
def real_home(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The CLI row resolves ``--profile`` against the real gflow home, not the test's."""
    from gflow_cli.config import Settings, reset_settings

    with monkeypatch.context() as m:
        m.delenv("GFLOW_CLI_HOME", raising=False)
        home = Settings().home
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    reset_settings()
    yield home
    reset_settings()


_T2V_PROMPT = "a paper boat drifting on a calm pond, static camera"


@when("the CLI generates a lite text-to-video clip")
def _cli_t2v(world: dict[str, Any], tmp_path: Path, real_home: Path) -> None:
    from click.testing import CliRunner

    from gflow_cli.cli_video import video

    del real_home
    out = tmp_path / "cli"
    world["out"] = out
    result = CliRunner().invoke(
        video,
        [
            "t2v",
            _T2V_PROMPT,
            "--model",
            "veo-lite",
            "--out-dir",
            str(out),
            "--profile",
            os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
        ],
    )
    assert result.exit_code == 0, result.output


@when("the MCP video tool generates a lite text-to-video clip")
def _mcp_t2v(world: dict[str, Any]) -> None:
    from gflow_cli.mcp import tools as mcp_tools

    result = asyncio.run(
        mcp_tools.gflow_generate_video(
            prompt=_T2V_PROMPT,
            mode="t2v",
            model="lite",
            profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
            wait=True,
        )
    )
    assert result["status"] == "completed", result
    world["files"] = [Path(p) for p in result["files"]]


@when("the CLI generates an r2v clip from a local reference image")
def _cli_r2v(world: dict[str, Any], tmp_path: Path, real_home: Path) -> None:
    from click.testing import CliRunner

    from gflow_cli.cli_video import video

    del real_home
    out = tmp_path / "cli"
    world["out"] = out
    result = CliRunner().invoke(
        video,
        [
            "r2v",
            _R2V_PROMPT,
            "--ref",
            str(_png(tmp_path / "card.png")),
            "--aspect",
            "16:9",
            "--out-dir",
            str(out),
            "--profile",
            os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
        ],
    )
    assert result.exit_code == 0, result.output


@when("the MCP video tool generates an r2v clip from a local reference image")
def _mcp_r2v(world: dict[str, Any], tmp_path: Path) -> None:
    from gflow_cli.mcp import tools as mcp_tools

    result = asyncio.run(
        mcp_tools.gflow_generate_video(
            prompt=_R2V_PROMPT,
            mode="r2v",
            aspect="16:9",
            reference_images=[str(_png(tmp_path / "card.png"))],
            profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
            wait=True,
        )
    )
    assert result["status"] == "completed", result
    world["files"] = [Path(p) for p in result["files"]]


@then("exactly one video submit reply reached gflow")
def _one_reply(world: dict[str, Any]) -> None:
    assert world["lost"] == 1, "the ablation never fired: the run proves nothing"
    assert world.get("replies") == 1, world.get("replies")


@then("the clip is on disk")
def _clip_on_disk(world: dict[str, Any]) -> None:
    files = world.get("files") or list(world["out"].glob("*.mp4"))
    assert files and all(p.stat().st_size > 100_000 for p in files), files
