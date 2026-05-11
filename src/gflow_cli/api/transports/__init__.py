"""Transport strategy registry + selection factory.

See docs/superpowers/specs/2026-05-11-gflow-cli-b007-transport-strategy-design.md § 4.2.
"""
from __future__ import annotations

import os

from gflow_cli.api.transports.base import FlowTransportStrategy
from gflow_cli.errors import ConfigurationError

# Strategy implementations registered via _registry().
# Imports are deferred (function-scoped) to keep module import cheap and to
# avoid pulling playwright/httpx into the import path until a strategy is
# actually instantiated.

_DEFAULT_TRANSPORT = "evaluate_fetch"  # S1 — see spec § 4.2.1


def _registry() -> dict[str, type[FlowTransportStrategy]]:
    """Lazy registry import — keeps the factory cheap to import."""
    from gflow_cli.api.transports.bearer import BearerTransport
    from gflow_cli.api.transports.evaluate_fetch import EvaluateFetchTransport
    from gflow_cli.api.transports.sapisidhash import SapisidhashTransport

    return {
        "evaluate_fetch": EvaluateFetchTransport,
        "bearer": BearerTransport,
        "sapisidhash": SapisidhashTransport,
    }


def make_transport(name: str | None = None) -> FlowTransportStrategy:
    """Resolve strategy: explicit `name` arg > GFLOW_CLI_TRANSPORT env > built-in default.

    Raises ConfigurationError listing surviving registered strategies if name is unknown.
    """
    resolved = name or os.getenv("GFLOW_CLI_TRANSPORT") or _DEFAULT_TRANSPORT
    registry = _registry()
    klass = registry.get(resolved)
    if klass is None:
        raise ConfigurationError(
            f"Transport {resolved!r} is not registered. "
            f"Valid options: {sorted(registry)}."
        )
    return klass()


__all__ = ["FlowTransportStrategy", "make_transport"]
