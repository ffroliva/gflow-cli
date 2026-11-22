"""E2E: a local ``--ref`` file is uploaded into a project once, then mentioned (#772).

Binds ``tests/features/local_ref_uploaded_once.feature``. Selected by ``-m e2e_image``;
see ``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e.** Offline tests prove the catalog lookup and the retry with a fake client.
Only a live run proves the recorded caption is the one Flow's ``@`` picker finds in a
later process, and that the submit carries the recorded media id.

**Host-aware.** On labs the transport has no in-place upload and the editor's own dialog
dedups by file name (#314), so this contract is flow.google.com's; a labs-served run is
skipped with that reason rather than asserted against the wrong contract.

**A recorded image the project no longer holds** (deleted since) is staged by pointing
the catalog row at an id the project does not hold: the composer sees the same thing, no
tile in the grid. The run waits out the in-place search (up to 90 s), then re-uploads.

**Cost.** Two images of daily quota per scenario, zero Veo credits.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import structlog
from pytest_bdd import given, scenarios, then, when

from gflow_cli.config import reset_settings
from gflow_cli.mcp import tools as mcp_tools
from tests.e2e._redacted_env import RedactedEnv

from .test_earlier_run_ref_in_place_bdd import _event_records
from .test_image_i2i_ref_cap_e2e import _tiny_png
from .test_image_uuid_ref_e2e import _json_payload, _run_gflow

scenarios("../features/local_ref_uploaded_once.feature")

_UPLOADED = "migrated.reference_uploaded"
_REUSED = "reference_uploads.reused"
_ATTACHED_IN_PLACE = "migrated.existing_references_attached"


def _info(env: RedactedEnv) -> RedactedEnv:
    # The events asserted here are INFO; the default level hides them since #1041.
    return env | {"GFLOW_CLI_LOG_LEVEL": "INFO"}


def _i2i_args(prompt: str, ref: Path, out: Path, project: list[str]) -> list[str]:
    return ["image", "i2i", prompt, "--ref", str(ref), *project, "--model", "nano2"] + [
        "--out",
        str(out),
        "--json",
    ]


@given(
    "a local reference image already used once by image i2i in a project", target_fixture="world"
)
def _first(e2e_env: RedactedEnv, tmp_path: Path) -> dict[str, Any]:
    ref = _tiny_png(tmp_path / "character.png", color=(200, 120, 40))
    out = tmp_path / "out"
    first = _run_gflow(
        _i2i_args("a small orange tile, studio light", ref, out, ["--project-name", "e2e-772"]),
        _info(e2e_env),
    )
    assert first.returncode == 0, f"first i2i failed; stderr tail: {first.stderr[-800:]!r}"
    events = _event_records(first.stderr)
    names = {str(e.get("event", "")) for e in events}
    if not any(n.startswith("migrated.") for n in names):
        # Skip only on positive evidence of labs: no events at all is a broken test.
        assert any(n.startswith("ui_automation") for n in names), sorted(names)
        pytest.skip("labs served this account: its editor dialog dedups by file name (#314)")
    uploaded = [e for e in events if e.get("event") == _UPLOADED]
    assert len(uploaded) == 1, f"the first run should upload once: {sorted(names)}"
    payload = _json_payload(first.stdout)
    return {
        "env": e2e_env,
        "ref": ref,
        "out": out,
        "project_id": str(payload["project_id"]),
        "media_id": str(uploaded[0]["media_id"]),
    }


@when("image i2i runs again with the same --ref file in that project")
def _again(world: dict[str, Any]) -> None:
    proc = _run_gflow(
        _i2i_args(
            "the same tile, now blue",
            world["ref"],
            world["out"],
            ["--project", world["project_id"]],
        ),
        _info(world["env"]),
    )
    world["proc"] = proc
    world["events"] = _event_records(proc.stderr)
    print(f"[772] project={world['project_id']} media={world['media_id']}")


@when("the MCP image tool runs with the same file as a reference in that project")
def _mcp_again(
    world: dict[str, Any],
    e2e_profile_dir: Path,
    install_log_capture: structlog.testing.LogCapture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del e2e_profile_dir  # selects the real authenticated gflow home
    # The tool must read the catalog row the CLI run wrote: the same isolated DB.
    for key in ("GFLOW_CLI_DB_PATH", "GFLOW_CLI_OUTPUT_DIR", "GFLOW_CLI_HISTORY_PROMPTS"):
        monkeypatch.setenv(key, world["env"][key])
    reset_settings()
    world["result"] = asyncio.run(
        mcp_tools.gflow_generate_image(
            prompt="the same tile, now blue",
            model="nano2",
            reference_images=[str(world["ref"])],
            profile=world["env"]["GFLOW_CLI_PROFILE"],
            project=world["project_id"],
            wait=True,
        )
    )
    world["events"] = list(install_log_capture.entries)
    print(f"[772-mcp] project={world['project_id']} status={world['result'].get('status')}")


@then("the run succeeds and saves one image")
def _succeeds(world: dict[str, Any]) -> None:
    proc = world["proc"]
    assert proc.returncode == 0, f"exit {proc.returncode}; stderr tail: {proc.stderr[-800:]!r}"
    payload = _json_payload(proc.stdout)
    assert len(payload.get("images") or []) == 1, payload
    assert payload.get("ref_count") == 1, payload


@then("the MCP task completes with one image on disk")
def _mcp_succeeds(world: dict[str, Any]) -> None:
    result = world["result"]
    assert result.get("status") == "completed", result
    files = [Path(p) for p in result.get("files") or []]
    assert len(files) == 1 and files[0].stat().st_size > 10_000, files


@then("the recorded upload was mentioned in place, with no second upload")
def _in_place(world: dict[str, Any]) -> None:
    events = world["events"]
    names = [str(e.get("event", "")) for e in events]
    assert _UPLOADED not in names, f"the file was uploaded again: {sorted(set(names))}"
    reused = [e for e in events if e.get("event") == _REUSED]
    assert reused and reused[-1].get("media_id") == world["media_id"], sorted(set(names))
    attached = [e for e in events if e.get("event") == _ATTACHED_IN_PLACE]
    assert attached, f"no {_ATTACHED_IN_PLACE}; observed: {sorted(set(names))}"
    assert list(attached[-1].get("media_ids") or []) == [world["media_id"]], attached[-1]
    guarded = [e for e in events if e.get("event") == "migrated.image_submit_guarded"]
    assert guarded and guarded[-1].get("outcome") == "passed", guarded


_GONE = "00000000-0000-4000-8000-000000000772"


@given("the catalog records that file under an image the project does not hold")
def _forget(world: dict[str, Any]) -> None:
    with sqlite3.connect(world["env"]["GFLOW_CLI_DB_PATH"]) as conn:
        changed = conn.execute(
            "UPDATE assets SET flow_media_id = ? WHERE flow_media_id = ?",
            (_GONE, world["media_id"]),
        ).rowcount
    assert changed == 1, changed


@then("the file was uploaded again after the miss, and the new copy recorded")
def _reuploaded(world: dict[str, Any]) -> None:
    events = world["events"]
    names = [str(e.get("event", "")) for e in events]
    reused = [e for e in events if e.get("event") == _REUSED]
    assert reused and reused[-1].get("media_id") == _GONE, sorted(set(names))
    assert "reference_uploads.recorded_image_gone" in names, sorted(set(names))
    uploaded = [e for e in events if e.get("event") == _UPLOADED]
    assert len(uploaded) == 1, sorted(set(names))
    fresh = str(uploaded[0]["media_id"])
    guarded = [e for e in events if e.get("event") == "migrated.image_submit_guarded"]
    assert guarded and guarded[-1].get("outcome") == "passed", guarded
    with sqlite3.connect(world["env"]["GFLOW_CLI_DB_PATH"]) as conn:
        rows = conn.execute("SELECT 1 FROM assets WHERE flow_media_id = ?", (fresh,)).fetchall()
    assert rows, f"the new upload {fresh} was not recorded"
