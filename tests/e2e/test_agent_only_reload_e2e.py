"""Live proof that a reference request reloads past the agent-only composer (#1142).

The first composer check lands on the live page for real, then reports ``agent_only``.
Everything after it is real: the reload, the next check, and the upload into the project. The run
stops at the image settings, so nothing is generated.

**Why the verdict is forced.** No account here is served the agent-only composer (#799),
so this proves the wiring on both doors, not that a reload escapes that composer on an
account that gets it. **Cost: $0**, one small image uploaded into the project.
Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` and ``GFLOW_CLI_E2E_PROJECT``.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import structlog
from click.testing import CliRunner

from gflow_cli.api.transports.migrated_composer import MigratedComposer

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


class _StopBeforeGenerateError(RuntimeError):
    """Raised at the image settings, after the upload, so nothing is generated."""


def _force_first_verdict(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    real = MigratedComposer.ensure_editor
    verdicts: list[str] = []

    async def first_agent_only(
        self: MigratedComposer, page: Any, project_id: str, **kw: Any
    ) -> Any:
        kind = await real(self, page, project_id, **kw)  # the real landing and readiness wait
        if kw:  # ensure_editor's own slow-mount retry (timeout_s=): not a new verdict
            return kind
        verdicts.append("agent_only" if not verdicts else kind)
        return verdicts[-1]

    async def stop(*_: Any, **__: Any) -> None:
        raise _StopBeforeGenerateError("uploaded; stopping before generation")

    monkeypatch.setattr(MigratedComposer, "ensure_editor", first_agent_only)
    monkeypatch.setattr(MigratedComposer, "apply_image_settings", stop)
    return verdicts


def _ref(tmp_path: Path) -> Path:
    from PIL import Image

    ref = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (200, 120, 40)).save(ref)
    return ref


def _project() -> str:
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not project:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing migrated project")
    return project


def _assert_reloaded_then_uploaded(
    verdicts: list[str], events: Sequence[Mapping[str, Any]]
) -> None:
    names = [str(e.get("event")) for e in events]
    assert verdicts[:2] == ["agent_only", "classic"], f"forced, then the live page: {verdicts}"
    assert names.count("migrated.agent_only_reload") == 1, names
    assert "migrated.reference_uploaded" in names, f"the upload never ran: {names}"


async def test_mcp_reference_image_reloads_past_the_agent_only_composer(
    e2e_profile_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    from gflow_cli.mcp import tools

    del e2e_profile_dir  # selects the real authenticated gflow home
    project = _project()
    verdicts = _force_first_verdict(monkeypatch)
    result = await tools.gflow_generate_image(
        prompt="unused: never submitted",
        model="nano2",
        reference_images=[str(_ref(tmp_path))],
        profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
        project=project,
        wait=True,
    )
    _assert_reloaded_then_uploaded(verdicts, list(install_log_capture.entries))
    assert result.get("status") != "completed", result.get("status")


async def test_cli_ref_reloads_past_the_agent_only_composer(
    e2e_profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.cli import main

    del e2e_profile_dir
    args = ["image", "i2i", "unused: never submitted", "--ref", str(_ref(tmp_path))]
    args += ["--project", _project(), "--model", "nano2", "--out", str(tmp_path / "out")]
    args += ["--profile", os.environ["GFLOW_CLI_E2E_PROFILE"].strip()]
    verdicts = _force_first_verdict(monkeypatch)
    # The command runs its own event loop, so it is invoked off this test's loop.
    result = await asyncio.to_thread(CliRunner().invoke, main, args)
    # The CLI installs its own logging, so its events are read from its stderr.
    events = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    _assert_reloaded_then_uploaded(verdicts, events)
    assert result.exit_code != 0, "stopped before generation, so the command cannot succeed"
