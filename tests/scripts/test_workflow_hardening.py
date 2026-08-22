"""Workflow-hardening gates (#565).

Offline regression locks for the CI/supply-chain hardening pass:

* every ``actions/checkout`` sets ``persist-credentials: false`` — the default
  leaves the job token in ``.git/config`` for the rest of the job (and inside
  any artifact built from the workspace);
* no ``run:`` block interpolates a ``${{ github.* }}`` context directly — that
  is template injection; the value must arrive through ``env:`` instead;
* the ``zizmor`` static analyser actually runs in CI, pinned;
* the release job does not restore a shared uv cache (cache-poisoning surface
  on the one workflow that publishes artefacts).

These duplicate the ``workflow-audit`` CI job on purpose: the job is the
authority, this is the copy that fails on a developer's machine — and in every
matrix leg — before a push burns a CI cycle.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# Pinned in ci.yml. uvx has no Dependabot ecosystem, so the bump is manual and
# this constant is what makes a drifted pin a failing test rather than a
# silently different analyser.
ZIZMOR_PIN = "zizmor==1.29.0"


def _workflow_paths() -> list[Path]:
    paths = sorted(WORKFLOWS.glob("*.yml"))
    assert paths, f"no workflows found under {WORKFLOWS}"
    return paths


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for job in (doc.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]


@pytest.mark.parametrize("path", _workflow_paths(), ids=lambda p: p.name)
def test_every_checkout_disables_credential_persistence(path: Path) -> None:
    for step in _steps(_load(path)):
        if not str(step.get("uses", "")).startswith("actions/checkout@"):
            continue
        with_block = step.get("with") or {}
        assert with_block.get("persist-credentials") is False, (
            f"{path.name}: actions/checkout must set 'persist-credentials: false' "
            "(the default persists the job token in .git/config)"
        )


@pytest.mark.parametrize("path", _workflow_paths(), ids=lambda p: p.name)
def test_no_run_block_interpolates_a_github_context(path: Path) -> None:
    pattern = re.compile(r"\$\{\{\s*github\.")
    for step in _steps(_load(path)):
        run = step.get("run")
        if not isinstance(run, str):
            continue
        assert not pattern.search(run), (
            f"{path.name}: run block interpolates a ${{{{ github.* }}}} context — "
            "pass it through 'env:' and reference the shell variable instead "
            "(template injection)"
        )


def test_ci_runs_the_zizmor_workflow_audit() -> None:
    ci = _load(WORKFLOWS / "ci.yml")
    audit = (ci.get("jobs") or {}).get("workflow-audit")
    assert audit is not None, "ci.yml: no 'workflow-audit' job"

    commands = " ".join(
        str(step.get("run", "")) for step in audit.get("steps") or [] if isinstance(step, dict)
    )
    assert ZIZMOR_PIN in commands, f"ci.yml: workflow-audit must run pinned {ZIZMOR_PIN}"
    assert ".github/workflows" in commands, "ci.yml: workflow-audit must audit .github/workflows"


def test_release_does_not_restore_a_shared_uv_cache() -> None:
    for step in _steps(_load(WORKFLOWS / "release.yml")):
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
            with_block = step.get("with") or {}
            assert with_block.get("enable-cache") is False, (
                "release.yml: setup-uv must set 'enable-cache: false' — the publishing "
                "workflow must not restore a cache another workflow can write"
            )
