"""Live proof that the queued MCP path runs the direct_http image transport (T1.7).

The MCP server queues ``gflow_generate_image(wait=False)`` and ``FlowWorker`` runs it: a
different code path from the CLI's in-process call. This drives ``FlowWorker.process_task``
on a ``t2i`` task whose payload names ``transport: "direct_http"`` and checks the catalog
records the image as made by that transport.

Cost: one image (daily quota, zero credits). Opt-in: ``-m e2e_image`` with
``GFLOW_CLI_E2E_PROFILE`` set.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import pytest

from gflow_cli.worker.codec import task_warnings
from gflow_cli.worker.daemon import FlowWorker

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]


@pytest.mark.asyncio
async def test_the_worker_runs_a_queued_t2i_on_the_direct_transport(
    e2e_profile_dir: Path, tmp_path: Path
) -> None:
    profile_name = e2e_profile_dir.name.removeprefix("profile_")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    worker = FlowWorker(profile_name, str(tmp_path / "worker.db"))
    try:
        worker.db.conn.execute(  # the queue's FK needs the profile row in this isolated DB
            "INSERT INTO profiles(name, profile_dir, first_seen_at) VALUES (?, ?, ?)",
            (profile_name, str(e2e_profile_dir), "2026-01-01T00:00:00Z"),
        )
        task_id = f"e2e-direct-worker-{uuid.uuid4().hex[:8]}"
        worker.repo.enqueue_task(
            task_id,
            profile_name,
            "t2i",
            {
                "prompt": "a glass marble on a wooden table",
                "aspect": "9:16",
                # output_file, not out_dir: out_dir only feeds the client's debug artefacts;
                # without output_file the worker saves into the user's real output folder.
                "output_file": str(out_dir / "direct.jpg"),
                "transport": "direct_http",
            },
        )
        claimed = worker.repo.claim_next_pending(profile_name, f"worker:{profile_name}:e2e")
        assert claimed is not None
        await worker.process_task(claimed)

        done = worker.repo.get_task(task_id)
        assert done is not None
        assert done.status == "completed", f"status={done.status!r} error={done.error!r}"
        rows = worker.db.conn.execute(
            "SELECT metadata_json FROM assets WHERE kind = 'image'"
        ).fetchall()
        assert [json.loads(r[0] or "{}").get("transport") for r in rows] == ["direct_http"]
        assert len(list(out_dir.iterdir())) == 1
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_the_worker_runs_a_queued_i2i_with_one_local_ref_at_count_two(
    e2e_profile_dir: Path, tmp_path: Path
) -> None:
    """#1455: a queued i2i with one local ref and count 2 stays on direct_http.

    The MCP twin of the CLI `-n 2 --ref` e2e: the worker decodes `ref_paths` and `count`
    from the payload, a different path from the CLI's in-process request.
    Cost: two images plus one free upload (daily quota, zero credits).
    """
    from PIL import Image, ImageDraw

    profile_name = e2e_profile_dir.name.removeprefix("profile_")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ref = tmp_path / "ref.png"
    img = Image.new("RGB", (512, 512), (40, 90, 160))
    ImageDraw.Draw(img).ellipse((128, 128, 384, 384), fill=(230, 200, 60))
    img.save(ref)
    worker = FlowWorker(profile_name, str(tmp_path / "worker.db"))
    try:
        worker.db.conn.execute(
            "INSERT INTO profiles(name, profile_dir, first_seen_at) VALUES (?, ?, ?)",
            (profile_name, str(e2e_profile_dir), "2026-01-01T00:00:00Z"),
        )
        task_id = f"e2e-direct-worker-ref-{uuid.uuid4().hex[:8]}"
        worker.repo.enqueue_task(
            task_id,
            profile_name,
            "i2i",
            {
                "prompt": "the same yellow disc as a glass marble on a wooden table",
                "aspect": "9:16",
                "ref_paths": [str(ref)],
                "count": 2,
                "output_file": str(out_dir / "direct.jpg"),
                "transport": "direct_http",
            },
        )
        claimed = worker.repo.claim_next_pending(profile_name, f"worker:{profile_name}:e2e")
        assert claimed is not None
        await worker.process_task(claimed)

        done = worker.repo.get_task(task_id)
        assert done is not None
        assert done.status == "completed", f"status={done.status!r} error={done.error!r}"
        rows = worker.db.conn.execute(
            "SELECT metadata_json FROM assets WHERE kind = 'image'"
        ).fetchall()
        transports = [json.loads(r[0] or "{}").get("transport") for r in rows]
        assert transports.count("direct_http") == 2, transports
        assert len((done.checkpoint or {})["media_ids"]) == 2  # what #1489 counts
        assert task_warnings(done) == {}
        assert "ui_automation" not in transports, transports
        assert len(list(out_dir.iterdir())) == 2
    finally:
        worker.close()
