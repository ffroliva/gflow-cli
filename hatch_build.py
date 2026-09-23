"""Put the documentation inside the wheel, so `gflow docs` can read it (#861).

``docs/`` is not a Python package and must not become one, but the pages have to travel
with the distribution: the user in #861 is an agent that ran ``uv tool install gflow-cli``
and has no checkout to fall back on. A command that can only print GitHub URLs to that
user does not solve the problem it was filed for.

**Why a hook and not two lines of config.** The obvious form is
``force-include = { "docs" = "gflow_cli/_docs" }`` plus ``exclude`` for the parts that
should not ship. That was built and inspected on 2026-09-17, and **hatchling's exclude
patterns do not apply to forced inclusions**: 145 files under ``docs/superpowers/`` and 3
under ``docs/assets/`` landed in the wheel anyway, 4.3 MB of them. The alternative --
listing 126 pages by hand in ``pyproject.toml`` -- drifts the first time someone adds one.

A glob resolved at build time is the only shape that ships exactly the top-level pages and
cannot go stale, because the glob IS the list. Measurement:
``docs/superpowers/plans/2026-09-17-gflow-docs-command/SCENARIO.md`` § M1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

#: Where the pages land inside the installed package. `gflow_cli.docs_catalog` reads this
#: name through `importlib.resources`; the two must agree.
DOCS_PACKAGE_DIR = "gflow_cli/_docs"


class DocsBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    """Force-include every top-level ``docs/*.md`` as package data."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # An editable install resolves `gflow_cli` to `src/`, so `_docs` there is never
        # read -- `docs_catalog` finds the checkout's own `docs/` instead. Copying 126
        # pages into site-packages anyway left a directory named after the package with
        # no `__init__.py` in it, recopied on every `uv sync` and stale by the next edit.
        if version == "editable":
            return
        # Non-recursive on purpose: `docs/assets/` is 2 MB of images and
        # `docs/superpowers/` is plans and spikes. Neither is a page the command lists,
        # and shipping them was measured at 4.3 MB for no reader.
        pages = sorted(Path(self.root, "docs").glob("*.md"))
        if not pages:
            # `Path.glob` on a missing directory returns empty, so without this the hook
            # emits a structurally valid wheel in which `gflow docs` is empty for every
            # user -- and `release.yml` is `uv build` then publish, with nothing in
            # between that would notice. Measured: a sdist with `docs/` removed built a
            # 767 KB wheel carrying zero pages and exit 0. Fail the build instead.
            raise ValueError(
                f"no documentation pages found under {Path(self.root, 'docs')} -- "
                "`gflow docs` would ship empty; refusing to build (#861)"
            )
        for page in pages:
            build_data["force_include"][str(page)] = f"{DOCS_PACKAGE_DIR}/{page.name}"
