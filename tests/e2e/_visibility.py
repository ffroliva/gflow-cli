"""The browser visibility the e2e matrix asks for, read from the outer environment.

The direct e2e files pass it on to the CLI subprocess or the MCP tool, so a run with
``GFLOW_CLI_BROWSER_VISIBILITY=headed`` exercises the headed launch instead of repeating the
headless one. Unset, it is ``auto``.
"""

from __future__ import annotations

import os

#: Read at import, before any fixture clears the variable for the code under test.
OUTER_VISIBILITY = os.environ.get("GFLOW_CLI_BROWSER_VISIBILITY") or "auto"


def expected_visibility() -> str:
    """The visibility a measured direct shape reports under :data:`OUTER_VISIBILITY`."""
    return "headed" if OUTER_VISIBILITY == "headed" else "headless"
