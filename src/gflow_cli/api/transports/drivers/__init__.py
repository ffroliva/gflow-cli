"""Pluggable Flow UI driver strategies.

Google Flow serves two composer layouts via a server-side A/B cohort: the
**classic** media UI (inline ``crop_*`` settings, network-captured responses)
and the **agentic** UI (conversational Slate composer, settings under the
``tune`` gear, generation routed through a background Web Worker → captured by
DOM scraping). The cohort flaps per page load.

Each layout is encapsulated behind the :class:`FlowUiDriver` protocol
(:mod:`gflow_cli.api.transports.drivers.base`); :func:`get_ui_driver`
(:mod:`gflow_cli.api.transports.drivers.factory`) probes the DOM and binds the
matching strategy. This package is a leaf dependency of the UI-automation
transport — it never imports back from it.

See docs/AGENT_UI_RECON.md and
docs/superpowers/plans/2026-06-14-agentic-ui-detection/.
"""

from __future__ import annotations

from gflow_cli.api.transports.drivers.agentic import AgenticFlowUiDriver
from gflow_cli.api.transports.drivers.base import FlowUiDriver
from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
from gflow_cli.api.transports.drivers.factory import detect_ui_mode, get_ui_driver

__all__ = [
    "AgenticFlowUiDriver",
    "ClassicFlowUiDriver",
    "FlowUiDriver",
    "detect_ui_mode",
    "get_ui_driver",
]
