"""Nightly selector-drift check on flow.google.com, run by the canary (#1083).

The GitHub ``selector-probe`` workflow loaded the labs.google editor with one NextAuth
cookie; labs answers 308 to flow.google.com for every account we hold, so it exited 2
("no recognizable editor") every night from 2026-08-21 and watched nothing. This test
grades the registry's ``migrated_editor`` surface against a real profile instead, with
the probe's own ``resolve`` and report, so a moved selector fails a ``$0`` canary tier
with the selector's key in the report.

Navigate and read only, never submits: ``e2e_auth`` (zero credits). Needs
``GFLOW_CLI_E2E_PROFILE`` and a project id in ``GFLOW_CLI_E2E_PROJECT`` (or
``GFLOW_CLI_PROJECT``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.config import UiMode
from gflow_cli.flow_selectors import registry
from scripts.probe.run_probe import render_report, resolve

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def test_every_migrated_editor_selector_resolves(e2e_profile_dir: Path) -> None:
    project = (
        os.environ.get("GFLOW_CLI_E2E_PROJECT", "") or os.environ.get("GFLOW_CLI_PROJECT", "")
    ).strip()
    if not project:
        pytest.skip("set GFLOW_CLI_E2E_PROJECT to a project this profile owns")
    async with FlowApiClient(
        profile_dir=e2e_profile_dir, visibility=BrowserVisibility.HEADED
    ) as client:
        page = client._page  # noqa: SLF001 - the probe reads the live page, like run_probe
        assert page is not None
        kind = await MigratedComposer().ensure_editor(page, project)
        mode = UiMode.AGENTIC if kind == "agent_only" else UiMode.CLASSIC
        outcomes = await resolve(page, registry.for_surface("migrated_editor"), mode)

    report = render_report(outcomes)
    print(report)  # the canary's JUnit keeps it; keys and grades only, no selectors
    assert not [o for o in outcomes if o.is_failure], report
