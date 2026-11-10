"""gflow-cli — CLI and MCP server for Google Flow."""

import logging as _logging

__version__ = "0.96.1"

# A deliberate exception to the structlog-only rule: this configures third-party loggers,
# it does not log. httpx logs every request at INFO as "HTTP Request: <METHOD> <full URL>".
# Once anything sets the root
# logger to INFO (constructing the MCP server does), a batchexecute URL's `f.sid` and `bl`
# would reach stderr, which MCP clients persist (#1229 review). Every gflow process imports
# this package first, so the clamp holds in the CLI, the MCP server and the daemon alike.
for _name in ("httpx", "httpcore"):
    _logging.getLogger(_name).setLevel(_logging.WARNING)
