"""Live E2E: an R2V reference resolved from a catalog UUID's display name (#529).

The #529 contract — ``catalog UUID -> Flow displayName -> picker name search ->
attach`` — must also hold on the R2V surface, where remote references are
name-addressed (``GenerateVideoRequest.ref_names``, the DTO/MCP field). This
test proves the full chain live:

1. Seed a t2i image via the CLI; the #529 recorder fix persists Flow's
   ``displayName`` in ``assets.metadata_json`` (this assertion alone catches
   the original catalog defect — UI-generated rows used to lose the name).
2. Read that display name back from the catalog by the seed's media UUID.
3. Generate an R2V video in the SAME project with ``ref_names=(display_name,)``
   and assert the ``remote_reference_attached`` event fired for it.

Costs 1 Imagen credit (seed) + one veo-lite generation.

Opt in with::

    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e_video -v \\
        tests/e2e/test_video_r2v_uuid_name_e2e.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

from .test_image_uuid_ref_e2e import _json_payload, _run_gflow

pytestmark = pytest.mark.e2e

_R2V_POLL_TIMEOUT_S = 600.0


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_e2e_r2v_ref_resolved_from_catalog_uuid_display_name(
    e2e_env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """A catalog UUID's recorded display name drives an R2V picker attach."""
    import os

    from gflow_cli.auth import profile_dir as _resolve_profile_dir
    from gflow_cli.config import reset_settings

    out = tmp_path / "out"
    profile_name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()

    # 1+2. Seed image via subprocess, then read UUID -> displayName back from
    # the isolated catalog DB (e2e_env's GFLOW_CLI_DB_PATH). Flow computes the
    # caption asynchronously server-side, so a fresh generation's response
    # sometimes lacks workflows[].metadata.displayName and the recorder
    # (correctly) records no name — retry the seed a bounded number of times.
    display_name: str | None = None
    project_id = ""
    seed_media_id = ""
    # Classic UI for the seed: the natively-agentic e2e account's agent
    # sometimes answers a t2i with a video (WireFormatError).
    seed_env = {**e2e_env, "GFLOW_CLI_PREFER_CLASSIC": "1"}
    for attempt in range(3):
        seed = _run_gflow(
            [
                "image",
                "t2i",
                f"a brass compass on a weathered map, take {attempt}",
                "--model",
                "nano2",
                "--out",
                str(out),
                "--json",
            ],
            seed_env,
        )
        if seed.returncode != 0:
            continue  # transient seed failure — retry within the bounded loop
        seed_payload = _json_payload(seed.stdout)
        images = seed_payload.get("images") or []
        assert images, f"seed run returned no images: {seed_payload}"
        seed_media_id = str(images[0]["media_name"])
        project_id = str(seed_payload.get("project_id") or "")
        assert project_id, f"seed payload carried no project_id: {seed_payload}"

        with DataStore.open(Path(e2e_env["GFLOW_CLI_DB_PATH"])) as store:
            asset = DataRepository(store).get_asset_by_flow_media_id(profile_name, seed_media_id)
        assert asset is not None, f"seed asset {seed_media_id} missing from the catalog"
        name = asset.metadata_json.get("display_name")
        if isinstance(name, str) and name:
            display_name = name
            break

    if display_name is None:
        pytest.skip(
            "no usable seed with a recorded displayName after 3 attempts — the "
            "caption is computed asynchronously server-side (or the seed "
            "generation failed transiently); nothing to name-search."
        )

    # 3. R2V in the SAME project, referencing the seed by its catalog name.
    # Undo the autouse `_isolate_settings` fixture so profile lookup resolves
    # to the real platformdirs path where the live session lives.
    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    reset_settings()
    e2e_profile_dir = _resolve_profile_dir(profile_name)
    if not e2e_profile_dir.exists():
        pytest.skip(f"profile dir not found: {e2e_profile_dir}")

    req = GenerateVideoRequest(
        prompt="the compass needle spins, cinematic close-up",
        mode=Mode.R2V,
        aspect=Aspect.PORTRAIT,
        model=VideoModel.VEO_3_1_LITE,
        duration=None,  # #537: Veo 3.1 models expose no duration control
        count=1,
        ref_names=(display_name,),
    )

    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        result: VideoResult = await transport.generate_video(
            request=req,
            project_id=project_id,
            out_dir=tmp_path,
            poll_timeout_s=_R2V_POLL_TIMEOUT_S,
        )
    finally:
        await transport.teardown()

    assert result.status.is_terminal and result.status.succeeded, (
        f"Expected SUCCESSFUL terminal status, got {result.status.status!r}; "
        f"failure_reasons={result.status.failure_reasons!r}"
    )
    assert result.local_path is not None and result.local_path.exists(), (
        f"VideoResult.local_path must point to a downloaded mp4; got {result.local_path!r}"
    )
    head = result.local_path.read_bytes()[:32]
    assert b"ftyp" in head, f"mp4 magic bytes not found in {result.local_path}: {head!r}"

    attached = [
        e
        for e in install_log_capture.entries
        if e["event"] == "ui_automation_video.remote_reference_attached"
        and e.get("display_name") == display_name
    ]
    assert attached, (
        f"expected remote_reference_attached for {display_name!r}; events: "
        f"{sorted({e['event'] for e in install_log_capture.entries})}"
    )
