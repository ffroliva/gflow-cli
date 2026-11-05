r"""Does PR #1142's reload reach the classic composer on a real page? $0.

The agent-only verdict cannot be produced on demand: no profile we hold is in that cohort
(2026-10-17 spike). So the FIRST ``ensure_editor`` verdict is forced to ``agent_only`` and
everything after it is real: the reload, the second verdict, the upload, the settings and
the prompt. The ``ogiZ0b`` submit is aborted, so nothing generates.

Run it from the PR's tree, because it imports that tree's ``run_images``:

    python scripts/dev/spike_agent_only_reload.py <profile> <project> <image.png>

Pre-registered readings (2026-11-04, before the run):

| Observation | Reading |
|---|---|
| 1 reload, verdict classic, one aborted ogiZ0b carrying the upload | the reload path works on a real page |
| the upload or settings fail after the reload | the reload breaks page state: block the PR |
| more than one reload with a classic page | the loop does not stop: block the PR |
| verdict stays agent_only on a classic page | the second verdict is wrong: block the PR |

Not measured either way: whether a reload escapes the real agent-only serving. Only an
account Flow serves that way can show it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gflow_cli.api.transports.migrated_composer as mc  # noqa: E402
from gflow_cli.api.image import Aspect, GenerateImageRequest  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip


async def run(profile: str, project: str, image: Path) -> None:
    report: dict[str, Any] = {"verdicts": [], "reloads": 0, "submits": []}
    real = mc.MigratedComposer.ensure_editor

    async def forced_first(self: Any, page: Any, pid: str) -> str:
        verdict = await real(self, page, pid)
        report["verdicts"].append(verdict)
        return "agent_only" if len(report["verdicts"]) == 1 else verdict

    mc.MigratedComposer.ensure_editor = forced_first  # type: ignore[method-assign]

    async def abort_submit(route: Any, req: Any) -> None:
        if mc.url_rpcid(req.url) != mc.IMAGE_SUBMIT_RPC:
            await route.continue_()
            return
        body = mc.request_body(req)
        report["submits"].append({"bytes": len(body)})
        await route.abort()

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        real_reload = page.reload

        async def counted(*args: Any, **kwargs: Any) -> Any:
            report["reloads"] += 1
            return await real_reload(*args, **kwargs)

        page.reload = counted
        await page.route("**/batchexecute**", abort_submit)
        request = GenerateImageRequest(
            prompt="spike 1142 reload path", aspect=Aspect.LANDSCAPE, ref_paths=(image,)
        )
        started = time.monotonic()
        try:
            await asyncio.wait_for(mc.run_images(page, request, project_id=project), 150)
            report["outcome"] = "returned"
        except Exception as exc:  # noqa: BLE001 - the aborted submit is expected here
            report["outcome"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        report["seconds"] = round(time.monotonic() - started, 1)
    path = default_out_path("spike_agent_only_reload", ".json")
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], sys.argv[2], Path(sys.argv[3])))
