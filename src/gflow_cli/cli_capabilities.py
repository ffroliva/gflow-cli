"""`gflow capabilities` — what this gflow can and cannot do, machine-readably (#1197).

gflow drives Flow through a browser and Flow's private API, so some operations an
API-based generation service offers have no equivalent here (server-side cancel,
idempotent submit), and some depend on the Flow frontend gflow drives. This module is
the one source of truth for that declaration: ``gflow capabilities --json`` and the MCP
resource ``capabilities://self`` both serialize :func:`current_capabilities` with
:func:`to_json`, byte for byte.

No network call. The only input is ``Settings.flow_host`` (``GFLOW_CLI_FLOW_HOST``):
nothing on disk records which host Flow served a profile, and under ``auto`` that is
decided per request at run time. The document reports what gflow can *drive* on each
host, never what a host can do (AGENTS.md, Host-Membership Discipline). Key meanings:
docs/CAPABILITIES.md.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from gflow_cli.config import get_settings

console = Console()

Value = Literal["yes", "no", "partial", "unknown"]

GENERATE_IMAGE = "generate.image"
GENERATE_VIDEO = "generate.video"
CREDITS_BALANCE = "credits.balance"

SCHEMA_VERSION = 1

_MIGRATED_IMAGE = (
    "flow.google.com: gflow drives text-to-image and image-to-image from local files "
    "(with @Character mentions); the agent-only composer (#799) takes text-to-image only."
)
_MIGRATED_VIDEO = (
    "flow.google.com: gflow drives text-to-video, image-to-video from a local "
    "--initial-frame, reference-to-video from local --ref files and/or a named "
    "character, and a single-segment extend; the agent-only composer (#799) takes "
    "text-to-video only. Other requests are not ported to that composer yet."
)
_AUTO_PREFIX = (
    "Host is chosen per request from what Flow serves (not checked here). "
    "Requests gflow cannot drive on flow.google.com yet fall back to the labs driver "
    "only when Flow serves labs. "
)
_LABS_NOTE = (
    "GFLOW_CLI_FLOW_HOST=labs.google: gflow uses its labs driver only, and generation "
    "fails with exit 36 if Flow serves this account flow.google.com. Which host Flow "
    "serves is not checked offline, hence unknown."
)

# Keys whose value does not depend on the host gflow drives.
_FIXED: dict[str, tuple[Value, str]] = {
    "submit.no_wait": (
        "partial",
        "MCP only: wait=false on gflow_generate_image / gflow_generate_video returns a "
        "task id. The CLI has no --no-wait yet (#1194).",
    ),
    "status": (
        "partial",
        "MCP only: tasks/get reports a wait=false task. No CLI `status <id>` yet (#1194).",
    ),
    "wait": (
        "partial",
        "CLI generation commands block until done; MCP tasks/get carries the result once "
        "a wait=false task finishes. No CLI `wait <id>` yet (#1194).",
    ),
    "download": (
        "yes",
        "Generation commands download on completion; `gflow data download` "
        "(MCP gflow_download_media) recovers an asset whose download failed.",
    ),
    "upload": (
        "partial",
        "`gflow image upload` uploads one image and prints its asset id (CLI only); the "
        "MCP generate tools upload local reference paths themselves. No top-level "
        "`upload` yet (#1200).",
    ),
    "abort.local": (
        "partial",
        "Ctrl-C stops a CLI run; MCP tasks/cancel stops a wait=false task. Neither "
        "stops a generation Flow already accepted. No `cancel <id>` yet (#1198).",
    ),
    "cancel.server": (
        "no",
        "gflow cannot cancel a generation Flow has accepted; it runs and is charged.",
    ),
    "idempotent_submit": (
        "no",
        "A retried submit is a new generation and is charged again.",
    ),
    "reconcile": (
        "partial",
        "`gflow data sync --names` refreshes catalog names and presence from Flow "
        "(CLI only, #543). No `reconcile <id>` yet (#1199).",
    ),
    CREDITS_BALANCE: (
        "partial",
        "The Veo video-credit balance only, read from flow.google.com's balance reply in "
        "the profile's browser (#795): `gflow credits`, MCP gflow_get_credits. Image "
        "models use separate per-model daily quotas gflow does not read; tier fields "
        "are null.",
    ),
}

#: Every CLI leaf -> the capability key it exercises, or None when the command is
#: outside this lifecycle vocabulary (sessions, local catalog, config, docs, servers).
#: tests/mcp/test_capabilities.py fails when a command is added without an entry.
COMMAND_CAPABILITY: dict[str, str | None] = {
    "image t2i": GENERATE_IMAGE,
    "image i2i": GENERATE_IMAGE,
    "image batch": GENERATE_IMAGE,
    "run": GENERATE_IMAGE,
    "video t2v": GENERATE_VIDEO,
    "video i2v": GENERATE_VIDEO,
    "video r2v": GENERATE_VIDEO,
    "video chain": GENERATE_VIDEO,
    "video extend": GENERATE_VIDEO,
    "movie run": GENERATE_VIDEO,
    "scene create": GENERATE_VIDEO,
    "data download": "download",
    "image upload": "upload",
    "data sync": "reconcile",
    "credits user": CREDITS_BALANCE,
    "credits list": CREDITS_BALANCE,
    "auth": None,
    "auth list": None,
    "auth login": None,
    "auth logout": None,
    "auth renew": None,
    "auth status": None,
    "auth use": None,
    "capabilities": None,
    "character create": None,
    "character list": None,
    "character rm": None,
    "character show": None,
    "character voices": None,
    "data errors export": None,
    "data errors prune": None,
    "data list errors": None,
    "data list images": None,
    "data list profiles": None,
    "data list projects": None,
    "data list videos": None,
    "data media": None,
    "data prune": None,
    "data stats": None,
    "docs": None,
    "doctor": None,
    "image upscale": None,
    "instructions add": None,
    "instructions apply": None,
    "instructions disable": None,
    "instructions enable": None,
    "instructions list": None,
    "instructions rm": None,
    "instructions toggle-mode": None,
    "mcp run": None,
    "mcp setup": None,
    "models": None,
    "movie template": None,
    "project create": None,
    "project list": None,
    "project rename": None,
    "project show": None,
    "scene show": None,
    "serve": None,
    "telemetry off": None,
    "telemetry on": None,
    "telemetry show": None,
    "telemetry status": None,
    "tools list": None,
    "tools run": None,
    "tools show": None,
    "update": None,
    "video upscale": None,
}


def build_capabilities(flow_host: str) -> dict[str, Any]:
    """The capability document for one ``flow_host`` setting (pure, no I/O)."""
    if flow_host == "labs.google":
        image: tuple[Value, str] = ("unknown", _LABS_NOTE)
        video: tuple[Value, str] = ("unknown", _LABS_NOTE)
    elif flow_host == "flow.google.com":
        image = ("partial", _MIGRATED_IMAGE)
        video = ("partial", _MIGRATED_VIDEO)
    else:
        image = ("partial", _AUTO_PREFIX + _MIGRATED_IMAGE)
        video = ("partial", _AUTO_PREFIX + _MIGRATED_VIDEO)
    entries = {GENERATE_IMAGE: image, GENERATE_VIDEO: video, **_FIXED}
    return {
        "schema": SCHEMA_VERSION,
        "adapter": "flow-browser",
        "host": flow_host,
        "capabilities": {key: value for key, (value, _) in entries.items()},
        "notes": {key: note for key, (_, note) in entries.items()},
    }


def current_capabilities() -> dict[str, Any]:
    """The document for this process's settings."""
    return build_capabilities(get_settings().flow_host)


def to_json(doc: dict[str, Any]) -> str:
    """The one serialization both surfaces return."""
    return json.dumps(doc, indent=2)


@click.command("capabilities")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON document.")
def capabilities(as_json: bool) -> None:
    """Show what gflow can drive: yes / no / partial / unknown per capability.

    Offline: reads only GFLOW_CLI_FLOW_HOST. Key meanings: docs/CAPABILITIES.md.
    The MCP resource capabilities://self returns the same document.
    """
    doc = current_capabilities()
    if as_json:
        click.echo(to_json(doc))
        return
    table = Table(title=f"gflow capabilities (host setting: {doc['host']})")
    table.add_column("capability", style="bold")
    table.add_column("value")
    table.add_column("notes", overflow="fold")
    for key, value in doc["capabilities"].items():
        table.add_row(key, value, escape(doc["notes"].get(key, "")))
    console.print(table)
