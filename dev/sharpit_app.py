"""Dev-only entry point: gflow-daemon with the sharpit developer console.

Not part of the wheel (hatch packages only ``src/gflow_cli``) and never imported by
shipped code. sharpit is a dev-only dependency. Run::

    uv run --with "sharpit>=0.3.0" uvicorn dev.sharpit_app:app --reload

or gate a test run on it::

    uv run --with "sharpit>=0.3.0" pytest --sharpit --sharpit-app dev.sharpit_app:app
"""

import sharpit

# Patch sqlite3.connect before the app is imported, so import-time connections are traced.
sharpit.attach_sql_sources()

from gflow_cli.ui.app import app  # noqa: E402 - must come after the patch

sharpit.install(app)

__all__ = ["app"]
