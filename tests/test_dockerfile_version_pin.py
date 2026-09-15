# SPDX-License-Identifier: MIT
"""The container image installs the version this repo actually ships.

`docker/Dockerfile` installs gflow-cli from PyPI by exact version. Nothing tied that
version to `pyproject.toml`, so at the next release the image would go on installing the
previous one — while `docker/README.md`'s "Verified on this image" table, which quotes a
concrete `gflow_cli : <version>`, quietly became a false claim.

Same defect class as the payload-key drop (#628): one value written in two places with
nothing checking they agree. `tests/test_server_json.py` already pins `server.json` this
way; this is the container's equivalent.

Note the ARG's *position* in the Dockerfile is also load-bearing and is asserted below:
it must sit after the Chrome apt layer. Measured 2026-09-15 — an ARG declared before an
expensive layer invalidates it on every version bump, and Chrome is most of this image's
~1.6 GB, so the wrong placement turns a one-line version bump into a full reinstall.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _ROOT / "docker" / "Dockerfile"
_PYPROJECT = _ROOT / "pyproject.toml"

_ARG_RE = re.compile(r"^ARG\s+GFLOW_VERSION=(?P<version>.+?)\s*$", re.MULTILINE)


def _dockerfile() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def _project_version() -> str:
    with _PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_the_dockerfile_pins_the_version_through_an_arg() -> None:
    match = _ARG_RE.search(_dockerfile())
    assert match is not None, (
        "docker/Dockerfile no longer declares `ARG GFLOW_VERSION=<version>`. "
        "If the pin moved, update this test with it — do not delete the gate."
    )


def test_the_image_installs_the_version_this_repo_ships() -> None:
    match = _ARG_RE.search(_dockerfile())
    assert match is not None
    pinned = match.group("version")
    expected = _project_version()
    assert pinned == expected, (
        f"docker/Dockerfile installs gflow-cli=={pinned} but this repo is at {expected}. "
        "Bump `ARG GFLOW_VERSION` in docker/Dockerfile, and re-check the version quoted in "
        "docker/README.md's 'Verified on this image' table."
    )


def test_the_install_step_actually_uses_the_arg() -> None:
    """A pin nothing interpolates is decoration."""
    text = _dockerfile()
    assert "${GFLOW_VERSION}" in text, (
        "`ARG GFLOW_VERSION` is declared but the pip install does not interpolate it, "
        "so the pin has no effect on what the image installs."
    )


def test_the_version_arg_sits_after_the_expensive_chrome_layer() -> None:
    """Placement is a cache contract, not style.

    An ARG declared before an expensive layer invalidates that layer whenever the value
    changes. Chrome is most of this image, so moving this ARG up would turn every version
    bump into a full reinstall — measured, not assumed.
    """
    text = _dockerfile()
    chrome_at = text.find("google-chrome-stable")
    arg_at = text.find("ARG GFLOW_VERSION")
    assert chrome_at != -1, "the google-chrome-stable install disappeared from the Dockerfile"
    assert arg_at != -1, "ARG GFLOW_VERSION disappeared from the Dockerfile"
    assert arg_at > chrome_at, (
        "`ARG GFLOW_VERSION` must be declared AFTER the google-chrome-stable layer. "
        "Declared before it, every gflow version bump busts Chrome's cache and rebuilds "
        "~1.6 GB for a one-line change."
    )
