"""gflow-cli — CLI and MCP server for Google Flow."""

import logging as _logging

__version__ = "0.96.1"

# httpx logs every request at INFO as "HTTP Request: <METHOD> <full URL>". gflow's own logs go
# through structlog; these two are third-party stdlib loggers. Once anything sets the root
# logger to INFO (constructing the MCP server does), a batchexecute URL's `f.sid` and `bl`
# would reach stderr, which MCP clients persist (#1229 review). Every gflow process imports
# this package first, so the clamp holds in the CLI, the MCP server and the daemon alike.
for _name in ("httpx", "httpcore"):
    _logging.getLogger(_name).setLevel(_logging.WARNING)
