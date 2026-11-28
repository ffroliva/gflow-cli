# pyright: reportPrivateUsage=false
"""Direct HTTP transport for image generation on flow.google.com.

A package at the import path the single module had: ``_session`` (cookies, user agent,
shell tokens), ``_mint`` (the reCAPTCHA token), ``_gateway`` (one batchexecute POST),
``_images`` (generation and upload) and ``_transport`` (``DirectHttpTransport``). The route
predicate ``direct_can_serve`` lives in ``generation_route`` and is re-exported here.
``direct_http._mint`` is the submodule, not the function, so a patch on its constants lands.
"""

from __future__ import annotations

from gflow_cli.api.generation_route import direct_can_serve
from gflow_cli.api.transports.direct_http._images import (
    _CHARACTER_READ_S,
    _WIRE_MODEL,
    build_maseq_body,
    build_ogiz0b_body,
    read_project,
    run_direct_images,
    run_direct_upload,
)
from gflow_cli.api.transports.direct_http._mint import (
    _MINT_BUDGET_S,
    _MINT_RETRY_S,
)
from gflow_cli.api.transports.direct_http._session import (
    _session_headers,
    _shell,
)
from gflow_cli.api.transports.direct_http._transport import DirectHttpTransport

__all__ = [
    "_CHARACTER_READ_S",
    "_MINT_BUDGET_S",
    "_MINT_RETRY_S",
    "_WIRE_MODEL",
    "DirectHttpTransport",
    "_session_headers",
    "_shell",
    "build_maseq_body",
    "build_ogiz0b_body",
    "direct_can_serve",
    "read_project",
    "run_direct_images",
    "run_direct_upload",
]
