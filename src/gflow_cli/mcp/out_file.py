"""Schema type for the generate tools' file destination: ``out_file`` (#1158).

MCP clients read this description, not the docstrings.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

__all__ = ["OutFileParam"]

_OUT_FILE_DESCRIPTION = (
    "Optional file to write, inside the configured output dir: a relative path "
    "is taken relative to it; an escape or a directory is refused."
)
OutFileParam = Annotated[str | None, Field(description=_OUT_FILE_DESCRIPTION)]
