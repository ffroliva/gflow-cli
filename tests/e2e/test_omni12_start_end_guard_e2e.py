"""Omni 1.2 start+end on flow.google.com, on both doors, at $0 (#1178).

Each run goes through the real CLI command or MCP tool. A SAFETY route is installed on the
page before ``submit_and_observe`` arms the production guard; Playwright runs the later
route first, so the production guard decides, and anything it lets through reaches the
safety route, which records the body and aborts it. Nothing is ever sent, so nothing is
billed. The safety route matches every ``batchexecute`` and reads the rpc from the URL or
the body: Omni 1.2's ``nprQif`` names its rpc in the body only, and a URL-keyed route
missed it and billed two clips in the spike
(docs/superpowers/spikes/2026-11-08-omni12-interpolation-key.md).

- **right**: real frames. The production guard passes the submit; the safety route must
  see ``nprQif`` with an Omni 1.2 ``*_i2v_fl_*`` key carrying both uploaded ids.
- **wrong**: the end frame id gflow expects is swapped for one never uploaded. The
  production guard must abort the submit unsent (the error says nothing was billed) and
  the safety route must see no submit at all.

Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` and ``GFLOW_CLI_E2E_PROJECT`` (an
existing migrated project). The account needs credits: with none, Flow replaces Send with
its insufficient-credits warning and the run stops at exit 37 before any submit.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.transports.migrated_composer import MigratedComposer

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

NEVER_UPLOADED = "00000000-0000-4000-8000-000000000000"


def _project() -> str:
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not project:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing migrated project")
    return project


def _frames(tmp_path: Path) -> tuple[Path, Path]:
    from PIL import Image

    start, end = tmp_path / "start.png", tmp_path / "end.png"
    Image.new("RGB", (720, 1280), (220, 60, 40)).save(start)
    Image.new("RGB", (720, 1280), (40, 90, 220)).save(end)
    return start, end


@pytest.fixture
def safety(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Abort every submit the production guard lets through; record what it carried."""
    seen: dict[str, Any] = {"submits": [], "ids": {}}
    real_observe = MigratedComposer.submit_and_observe
    real_start, real_end = MigratedComposer.attach_start_frame, MigratedComposer.attach_end_frame

    async def net(route: Any, request: Any) -> None:
        body = mc.request_body(request)
        rpcid = mc.url_rpcid(str(request.url)) or mc.body_rpcid(body)
        if rpcid in mc.SUBMIT_RPCS:
            key = mc.MODEL_KEY.search(body)
            seen["submits"].append(
                {"rpcid": rpcid, "key": key.group(0) if key else "", "body": body}
            )
            await route.abort()  # $0: never sent
            return
        await route.fallback()

    async def observe(self: MigratedComposer, page: Any, **kw: Any) -> Any:
        await page.route(mc.BATCHEXECUTE_GLOB, net)
        try:
            return await real_observe(self, page, **kw)
        finally:
            await page.unroute(mc.BATCHEXECUTE_GLOB, net)

    async def start(self: MigratedComposer, *a: Any, **k: Any) -> str:
        seen["ids"]["start"] = await real_start(self, *a, **k)
        return seen["ids"]["start"]

    async def end(self: MigratedComposer, *a: Any, **k: Any) -> str:
        seen["ids"]["end"] = await real_end(self, *a, **k)
        return NEVER_UPLOADED if seen.get("wrong") else seen["ids"]["end"]

    monkeypatch.setattr(MigratedComposer, "submit_and_observe", observe)
    monkeypatch.setattr(MigratedComposer, "attach_start_frame", start)
    monkeypatch.setattr(MigratedComposer, "attach_end_frame", end)
    yield seen


async def _run(door: str, tmp_path: Path) -> str:
    """Run the door; return what it reported (CLI stderr+stdout, or the MCP envelope)."""
    start, end = _frames(tmp_path)
    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    if door == "cli":
        from gflow_cli.cli import main

        args = ["video", "i2v", str(start), "a slow dolly", "--end-frame", str(end)]
        args += ["--model", "omni-1.2-flash", "--duration", "8", "--resolution", "360p"]
        args += ["--project", _project(), "--profile", profile, "--json"]
        args += ["--out-dir", str(tmp_path / "out")]
        result = await asyncio.to_thread(CliRunner().invoke, main, args)
        return f"{result.stderr}\n{result.output}"
    from gflow_cli.mcp import tools

    envelope = await tools.gflow_generate_video(
        prompt="a slow dolly",
        mode="i2v",
        initial_frame=str(start),
        end_frame=str(end),
        model="omni_1_2_flash",
        duration=8,
        resolution="360p",
        profile=profile,
        project=_project(),
        wait=True,
    )
    return json.dumps(envelope)


@pytest.mark.parametrize("door", ["cli", "mcp"])
async def test_a_right_omni12_start_end_submit_passes_the_guard(
    door: str, e2e_profile_dir: Path, tmp_path: Path, safety: dict[str, Any]
) -> None:
    del e2e_profile_dir
    report = await _run(door, tmp_path)
    assert len(safety["submits"]) == 1, f"no submit reached the safety route: {report[-800:]}"
    hit = safety["submits"][0]
    assert hit["rpcid"] == mc.INTERPOLATION_SUBMIT_RPC, hit["rpcid"]
    assert mc._I2V_FIRST_LAST_TOKEN.search(hit["key"]), hit["key"]  # pyright: ignore[reportPrivateUsage]
    both_ids = safety["ids"]["start"] in hit["body"] and safety["ids"]["end"] in hit["body"]
    assert both_ids, "the submit body did not carry both uploaded frame ids"


@pytest.mark.parametrize("door", ["cli", "mcp"])
async def test_a_wrong_omni12_start_end_submit_is_stopped_unsent(
    door: str, e2e_profile_dir: Path, tmp_path: Path, safety: dict[str, Any]
) -> None:
    del e2e_profile_dir
    safety["wrong"] = True
    report = await _run(door, tmp_path)
    assert safety["submits"] == [], "the production guard let a wrong submit through"
    assert "billed nothing" in report, report[-800:]
