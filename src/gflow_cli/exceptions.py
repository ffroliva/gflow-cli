"""gflow_cli.exceptions — standard alias for gflow_cli.errors.

Both module names resolve to the same set of public names.  Library
consumers may use whichever feels more idiomatic; ``gflow_cli.errors``
remains the canonical location.
"""

from __future__ import annotations

from gflow_cli.errors import *  # noqa: F401, F403
from gflow_cli.errors import __all__ as __all__  # re-export __all__
