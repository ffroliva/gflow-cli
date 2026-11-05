r"""Does PR #1142's reload run on the path the CLI takes? $0 if the refusal holds.

The 2026-11-04 spike called ``run_images`` directly, and the council found that bypasses
``upload_reference_in_place``. This drives the real ``gflow image i2i --ref`` in-process,
from the PR's tree, with only the first ``ensure_editor`` verdict forced to ``agent_only``.

    python scripts/dev/spike_agent_only_cli_path.py <profile> <project> <new-image.png>

Pre-registered readings (2026-11-04, before the run):

| Observation | Reading |
|---|---|
| 0 reloads, refused with the agent-only refusal | confirmed: the PR does not reach the CLI path |
| 1 reload, then the upload proceeds | the council is wrong: the PR covers the CLI path |
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from playwright.async_api import Page  # noqa: E402

import gflow_cli.api.transports.migrated_composer as mc  # noqa: E402

report: dict[str, Any] = {"verdicts": [], "reloads": 0}
real = mc.MigratedComposer.ensure_editor
real_reload = Page.reload


async def forced_first(self: Any, page: Any, pid: str) -> str:
    verdict = await real(self, page, pid)
    report["verdicts"].append(verdict)
    return "agent_only" if len(report["verdicts"]) == 1 else verdict


async def counted(self: Any, *args: Any, **kwargs: Any) -> Any:
    report["reloads"] += 1
    return await real_reload(self, *args, **kwargs)


mc.MigratedComposer.ensure_editor = forced_first  # type: ignore[method-assign]
Page.reload = counted  # type: ignore[method-assign]

if __name__ == "__main__":
    from gflow_cli.cli import main as gflow_main

    profile, project, image = sys.argv[1:4]
    args = ["image", "i2i", "spike 1142 cli path", "--ref", image, "--project", project]
    args += ["--profile", profile]
    try:
        gflow_main(args=args, standalone_mode=False)
        report["outcome"] = "returned"
    except BaseException as exc:  # noqa: BLE001 - the refusal is what is measured
        report["outcome"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    print("REPORT " + json.dumps(report), flush=True)
