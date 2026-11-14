"""Schema types for the generate tools' file destination: ``out_file`` and its deprecated
twin ``output`` (#1158). MCP clients read these descriptions, not the docstrings."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

__all__ = ["DeprecatedOutputParam", "OutFileParam"]

_OUT_FILE_DESCRIPTION = (
    "Optional file to write, inside the configured output dir: a relative path "
    "is taken relative to it; an escape or a directory is refused."
)
OutFileParam = Annotated[str | None, Field(description=_OUT_FILE_DESCRIPTION)]

_DEPRECATED_OUTPUT_DESCRIPTION = (
    "Deprecated: use out_file (removed in a future release). " + _OUT_FILE_DESCRIPTION
)
DeprecatedOutputParam = Annotated[str | None, Field(description=_DEPRECATED_OUTPUT_DESCRIPTION)]
