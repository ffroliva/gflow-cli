from __future__ import annotations

import uvicorn

from gflow_cli.ui.app import app


def run_server(host: str, port: int) -> None:
    """Run the FastAPI server using Uvicorn."""
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        loop="asyncio",
    )
