"""`gflow docs` works for someone who installed from PyPI (#861, scenario 3).

**Why this test and not a unit test.** The user in #861 ran `uv tool install gflow-cli`
and has no checkout. `docs_catalog` falls back to the repository's own `docs/` when the
package data is absent, which is what makes every other test in this feature pass — and
which would also make all of them pass against a build that ships no documentation at all.
This is the only test that can tell those two worlds apart, so it builds a wheel, installs
it somewhere with no repository in sight, and runs the command there.

**It runs on every plain `pytest`, including PR CI.** `addopts` excludes only
`e2e, live, smoke, containers`, so the `integration` marker does not opt out of anything —
it is a label, not a gate. That is the right outcome (the wheel claim is enforced where it
matters) but it is worth stating, because it costs a build and a venv on every local run.
`uv build` and `uv venv` are the only requirements: no Docker, no network beyond the
resolver's cache, no account.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[2]


def _uv() -> str:
    """`uv` or a skip — but a LOUD one.

    A silent skip would let the PR's headline claim ("proven from an installed wheel")
    degrade to nothing on a runner without uv, reported as a pass. uv is this project's
    build tool and is present in CI, so reaching the skip is itself the signal.
    """
    found = shutil.which("uv")
    if found is None:  # pragma: no cover - uv is this project's build tool
        pytest.skip("uv not on PATH — the installed-wheel guarantee was NOT verified")
    return found


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [_uv(), "build", "--wheel", "--out-dir", str(out)],
        cwd=_REPO,
        check=True,
        capture_output=True,
    )
    built = sorted(out.glob("*.whl"))
    assert built, "uv build produced no wheel"
    return built[0]


def test_the_wheel_carries_the_pages_and_only_the_pages(wheel: Path) -> None:
    """The measured reason `hatch_build.py` is a hook and not two lines of config.

    `force-include = { "docs" = ... }` plus `exclude` was tried on 2026-09-17 and
    hatchling ignored the exclusions: 145 files from `docs/superpowers/` and 3 from
    `docs/assets/` shipped anyway, 4.3 MB of plans, spikes and PNGs. This asserts the
    shape that replaced it.
    """
    with zipfile.ZipFile(wheel) as archive:
        shipped = [n for n in archive.namelist() if "/_docs/" in n]
        assert shipped, "no documentation in the wheel — `gflow docs` would be empty"
        assert not [n for n in shipped if "/assets/" in n]
        assert not [n for n in shipped if "/superpowers/" in n]
        assert all(n.endswith(".md") for n in shipped), shipped[:5]
        assert len(shipped) == len(list((_REPO / "docs").glob("*.md")))


def test_docs_works_from_an_installed_wheel_with_no_checkout(wheel: Path, tmp_path: Path) -> None:
    """Install into a scratch venv and run the command with the repository nowhere near.

    `cwd` is the scratch directory precisely so the `docs/` fallback cannot rescue it: if
    the pages did not ship, `--json` comes back with an empty topic list and this fails.
    """
    venv = tmp_path / "venv"
    subprocess.run([_uv(), "venv", str(venv)], check=True, capture_output=True)  # noqa: S603
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [_uv(), "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
    )

    listed = subprocess.run(  # noqa: S603
        [str(python), "-m", "gflow_cli.cli", "docs", "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    topics = json.loads(listed.stdout)["topics"]
    assert len(topics) > 100, len(topics)
    assert any(t["topic"] == "usage" for t in topics)

    # And the content is there, not just the names — a listing that cannot open a page
    # would still pass everything above.
    page = subprocess.run(  # noqa: S603
        [str(python), "-m", "gflow_cli.cli", "docs", "usage", "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert len(json.loads(page.stdout)["content"]) > 1000
