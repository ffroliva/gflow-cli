"""Alternative transport strategies retained for future research.

These strategies were explored during the B-007 initiative (2026-05).
Each implements ``FlowTransportStrategy`` and remains importable for
diagnostic and research purposes. None is the default transport.

Enable at the CLI surface by setting ``GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1``
in the invoking environment before running ``gflow``.

Public re-exports::

    from gflow_cli.api.transports.experimental import (
        EvaluateFetchTransport,
        BearerTransport,
        SapisidhashTransport,
    )
"""
from __future__ import annotations

from gflow_cli.api.transports.experimental.bearer import BearerTransport
from gflow_cli.api.transports.experimental.evaluate_fetch import EvaluateFetchTransport
from gflow_cli.api.transports.experimental.sapisidhash import SapisidhashTransport

__all__ = [
    "BearerTransport",
    "EvaluateFetchTransport",
    "SapisidhashTransport",
]
