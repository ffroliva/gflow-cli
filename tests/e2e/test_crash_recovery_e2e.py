"""Live E2E: the C5 crash-recovery "safe stub" for a real Flow video submit.

Hits the **real Google Flow API** and therefore:
  - Is NOT collected by default ``pytest`` runs (deselected by the repo's
    ``-m 'not e2e ...'`` addopts).
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e``
  - Requires a logged-in Chrome profile (Pro/Ultra account).
  - Burns exactly ONE Veo credit total (see two-phase design below).

## Behavior under test

If the worker dies AFTER a paid submit (checkpoint phase ``submit_attempted``
or ``remote_started``) but before a terminal result, startup recovery
(``recover_processing``) must mark the task ``indeterminate`` and NEVER
resubmit. A pre-submit crash recovers as ``failed`` instead (covered by the
unit suite in ``tests/worker/test_queue_reconciliation.py`` — no live handle
exists yet, so there is nothing for this live test to add there).

## Two-phase design (spends exactly ONE real credit)

A fragile mid-flight kill (actually interrupting the worker process while a
real generation is in flight) would be timing-critical, non-deterministic,
and could leave the real Flow-side generation running with nothing watching
it. Instead:

1. **Real submit, capture the real handle.** Drive ONE real t2v generation
   (model ``veo-lite``, aspect ``9:16``, no ``--duration`` override) through
   ``FlowWorker.process_task`` — the actual worker code path, with the real
   checkpoint observer wired exactly as production wires it — against an
   isolated temp SQLite DB, and let it run to completion. Read back the real
   ``remote_started`` checkpoint document the observer persisted. This is the
   live proof that a REAL generation produces a real ``remote_started``
   checkpoint row via the real observer -> ``update_checkpoint`` ->
   ``generation_queue.checkpoint_json`` path. (The one credit spent here.)

2. **Simulate the crash + prove recovery.** Write a FRESH ``processing``
   queue row whose checkpoint IS that captured ``remote_started`` document
   (i.e. "we crashed right after observing this handle"). Call
   ``recover_processing`` with a submit-COUNTING client stub and assert:
   the task's terminal status becomes ``indeterminate``, the non-secret
   handle (``operation_id`` / ``media_ids``) survives untouched in the
   checkpoint, and the counting client's submit counters stay at 0 — no
   resubmit, no second credit, no generation method invoked at all.

``classify_interrupted`` (``worker/queue.py``) keys ONLY on the checkpoint's
``phase`` field, so step 2's hand-built row exercises exactly the same
recovery path a genuine crash-after-submit would hit, deterministically,
using a checkpoint document that is byte-for-byte what a real crash would
have left behind.

This test does NOT cover auto-reconciliation (turning ``indeterminate`` back
into a terminal outcome via a live handle -> status readback) — that
reconcile hook is deliberately unimplemented; see ``recover_processing``'s
docstring (the F1 / D3 / D4 seam). ``counting_client`` proves today's
behavior (unused, zero resubmits), not the future reconciler.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import recover_processing

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_video, pytest.mark.e2e_data]

_PROMPT = "a calm forest at dawn, cinematic"


class _CountingClient:
    """Recovery reconcile-hook stub — proves recovery never resubmits.

    ``recover_processing`` currently never calls its ``client`` argument at
    all (the F1 live-page reconcile seam is unimplemented — see its
    docstring), so both counters staying at 0 is the load-bearing assertion
    that recovery performed no generation call whatsoever.
    """

    def __init__(self) -> None:
        self.image_submit_count = 0
        self.video_submit_count = 0

    async def generate_image(self, **_: Any) -> None:
        self.image_submit_count += 1

    async def generate_video(self, **_: Any) -> None:
        self.video_submit_count += 1


@pytest.mark.asyncio
async def test_crash_after_remote_started_recovers_as_indeterminate_without_resubmit(
    e2e_profile_dir: Path, tmp_path: Path
) -> None:
    profile_name = e2e_profile_dir.name.removeprefix("profile_")
    db_path = tmp_path / "crash_recovery.db"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    worker = FlowWorker(profile_name, str(db_path))
    try:
        # Isolated DB: the FK on generation_queue.profile_name needs a
        # matching profiles row. This DB is unique to this test run (tmp_path)
        # so the user's real gflow.db history can never be touched.
        worker.db.conn.execute(
            "INSERT INTO profiles(name, profile_dir, first_seen_at) VALUES (?, ?, ?)",
            (profile_name, str(e2e_profile_dir), "2026-01-01T00:00:00Z"),
        )

        # ---- Phase 1: real submit through the real worker path -----------
        real_task_id = f"e2e-crash-recovery-real-{uuid.uuid4().hex[:8]}"
        worker.repo.enqueue_task(
            real_task_id,
            profile_name,
            "t2v",
            {
                "prompt": _PROMPT,
                "aspect": "9:16",
                "model": "veo-lite",
                "out_dir": str(out_dir),
            },
        )
        claimed = worker.repo.claim_next_pending(profile_name, f"worker:{profile_name}:e2e-test")
        assert claimed is not None, "failed to claim the freshly enqueued t2v task"

        await worker.process_task(claimed)

        completed = worker.repo.get_task(real_task_id)
        assert completed is not None
        assert completed.status == "completed", (
            f"expected the real t2v submit to complete; got status={completed.status!r} "
            f"error={completed.error!r}"
        )

        real_checkpoint = worker.repo.read_checkpoint(real_task_id)
        assert real_checkpoint is not None, "observer never persisted a checkpoint"
        assert real_checkpoint["phase"] == "remote_started", (
            f"expected the last persisted checkpoint phase to be remote_started, "
            f"got {real_checkpoint!r}"
        )
        # operation_id (flow_operation_id) is best-effort/optional by design —
        # root-caused 2026-07-21: it's captured only when the generate
        # response includes operations[0].operation.name, and is None when
        # absent (observed live on veo-lite, not just omni-flash). media_id
        # is the canonical handle: poll and download key off it, and nothing
        # in the CLI queries by operation_id. So the REQUIRED safety
        # assertion is that a canonical handle (media_ids) is present;
        # operation_id, if present, must merely be the right optional type.
        assert real_checkpoint["media_ids"], "remote_started checkpoint missing media_ids"
        assert real_checkpoint["operation_id"] is None or isinstance(
            real_checkpoint["operation_id"], str
        ), "operation_id must be a str or None (optional field)"

        # ---- Phase 2: simulate the crash, prove recovery ------------------
        crash_task_id = f"e2e-crash-recovery-sim-{uuid.uuid4().hex[:8]}"
        worker.repo.enqueue_task(crash_task_id, profile_name, "t2v", {"prompt": _PROMPT})
        worker.repo.update_task_status(crash_task_id, "processing")
        # The checkpoint IS the real remote_started document captured in
        # Phase 1 — "we crashed right after the observer persisted this handle".
        worker.repo.write_checkpoint(crash_task_id, real_checkpoint)

        counting_client = _CountingClient()
        counts = recover_processing(worker.repo, profile_name, counting_client)

        assert counts == {"failed": 0, "indeterminate": 1}

        recovered = worker.repo.get_task(crash_task_id)
        assert recovered is not None
        assert recovered.status == "indeterminate", (
            f"a task crashed after remote_started must recover as indeterminate, "
            f"got {recovered.status!r}"
        )

        recovered_checkpoint = worker.repo.read_checkpoint(crash_task_id)
        assert recovered_checkpoint is not None
        assert recovered_checkpoint["operation_id"] == real_checkpoint["operation_id"], (
            "recovery must preserve the real handle untouched"
        )
        assert recovered_checkpoint["media_ids"] == real_checkpoint["media_ids"]

        # The load-bearing safety assertion: recovery invoked NO generation
        # method — no resubmit, no second credit.
        assert counting_client.image_submit_count == 0
        assert counting_client.video_submit_count == 0
    finally:
        worker.close()
