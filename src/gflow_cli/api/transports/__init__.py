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

_DEFAULT_TRANSPORT = "ui_automation"

# Strategies under active research. Hidden from CLI Choice lists unless
# GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1 is set in the environment. The factory
# itself accepts these keys at all times (so explicit opt-in via env var
# GFLOW_CLI_TRANSPORT or Python API still works).
EXPERIMENTAL_TRANSPORTS: tuple[str, ...] = (
    "evaluate_fetch",
    "bearer",
    "sapisidhash",
)


def _registry() -> dict[str, type[FlowTransportStrategy]]:
    """Lazy registry import — keeps the factory cheap to import."""
    from gflow_cli.api.transports.experimental.bearer import BearerTransport
    from gflow_cli.api.transports.experimental.evaluate_fetch import (
        EvaluateFetchTransport,
    )
    from gflow_cli.api.transports.experimental.sapisidhash import SapisidhashTransport
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    return {
        "ui_automation": UiAutomationTransport,
        "evaluate_fetch": EvaluateFetchTransport,
        "bearer": BearerTransport,
        "sapisidhash": SapisidhashTransport,
    }


def transport_choices() -> list[str]:
    """Strategy keys exposed in CLI ``--transport`` Choice lists.

    By default only the production-validated strategy (``ui_automation``).
    Set ``GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1`` to expose all registered
    strategies including the experimental ones.
    """
    if os.getenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS") == "1":
        return list(_registry())
    return [k for k in _registry() if k not in EXPERIMENTAL_TRANSPORTS]


def make_transport(name: str | None = None) -> FlowTransportStrategy:
    """Resolve strategy: explicit `name` arg > GFLOW_CLI_TRANSPORT env > built-in default.

    Raises ConfigurationError listing all registered strategies if name is unknown.
    Note: the factory accepts experimental keys regardless of the
    ``GFLOW_CLI_EXPERIMENTAL_TRANSPORTS`` env var — gating is at the CLI
    Choice list only, so the Python API stays unrestricted.
    """
    resolved = name or os.getenv("GFLOW_CLI_TRANSPORT") or _DEFAULT_TRANSPORT
    registry = _registry()
    klass = registry.get(resolved)
    if klass is None:
        msg = f"Transport {resolved!r} is not registered. Valid options: {sorted(registry)}."
        raise ConfigurationError(
            msg,
        )
    return klass()


__all__ = [
    "EXPERIMENTAL_TRANSPORTS",
    "FlowTransportStrategy",
    "make_transport",
    "transport_choices",
]
