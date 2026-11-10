"""`gflow capabilities` — what this gflow can and cannot do, machine-readably (#1197).

gflow drives Flow through a browser and Flow's private API, so some operations an
API-based generation service offers have no equivalent here (server-side cancel,
idempotent submit), and some depend on the Flow frontend gflow drives. This module is
the one source of truth for that declaration: ``gflow capabilities --json`` and the MCP
resource ``capabilities://self`` both serialize :func:`build_capabilities` through
:func:`gflow_cli.json_output.dumps`. The one difference is the MCP server's no-spend
mode, which registers no generate tool, so the resource reports generation as ``no``.

No call to Flow. The only input is ``Settings.flow_host`` (``GFLOW_CLI_FLOW_HOST``):
nothing on disk records which host Flow served a profile, and under ``auto`` that is
decided per request at run time. The document reports what gflow can *drive* on each
host, never what a host can do (AGENTS.md, Host-Membership Discipline). Key meanings:
docs/CAPABILITIES.md.
"""

from __future__ import annotations

from typing import Any, Literal

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from gflow_cli import json_output
from gflow_cli.config import get_settings

console = Console()

Value = Literal["yes", "no", "partial", "unknown"]
Entry = tuple[Value, str]

SCHEMA_VERSION = 1

GENERATE_IMAGE = "generate.image"
GENERATE_VIDEO = "generate.video"
SUBMIT_NO_WAIT = "submit.no_wait"
UPLOAD = "upload"

# What gflow cannot drive on flow.google.com yet: migrated_composer._unported_image_form,
# _unported_form, and ui_automation's image-batch refusal, each exit 36.
_MIGRATED_IMAGE = (
    "flow.google.com: gflow drives text-to-image and image-to-image from local files "
    "(with @Character mentions), and references to images the catalog records in this "
    "--project (#913/#995). Not driven there yet (exit 36): Agent instructions, the "
    "image4 (Imagen 4) model, `image batch`, a Flow media UUID the catalog does not "
    "record in this --project, existing-image references mixed with local files, and a "
    "character given by id without a name. The agent-only composer (#799) takes "
    "text-to-image only."
)
_MIGRATED_VIDEO = (
    "flow.google.com: gflow drives text-to-video, image-to-video from a local "
    "--initial-frame, reference-to-video from local --ref files and/or a named "
    "character, and a single-segment extend. Not driven there yet (exit 36): a frame "
    "or reference given by Flow media UUID or @Name, character references on t2v/i2v, "
    "a character given by id without a name, and extend chains, --scene and --seed. "
    "The agent-only composer (#799) takes text-to-video only."
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
_UPLOAD_BASE = (
    "`gflow image upload` uploads one image and prints its asset id (CLI only); the "
    "MCP generate tools upload local reference paths themselves. No top-level "
    "`upload` yet (#1200)."
)
_UPLOAD_MIGRATED = (
    " flow.google.com: an uploaded id is not taken as a video frame or reference "
    "(exit 36), and as an image reference only when the catalog records it in the same "
    "--project (#995)."
)
_NO_SPEND = (
    "This MCP server runs no-spend (GFLOW_MCP_NO_SPEND / --no-spend): the generate "
    "tools are not registered."
)

# Keys whose value does not depend on the host gflow drives.
_FIXED: dict[str, Entry] = {
    SUBMIT_NO_WAIT: (
        "partial",
        "MCP only: wait=false on the generate tools returns a task id, polled with "
        "tasks/get, which needs a client with the SEP-2663 Tasks extension. The CLI has "
        "no --no-wait yet (#1194).",
    ),
    "status": (
        "partial",
        "MCP only: tasks/get reports a wait=false task (a client with the SEP-2663 Tasks "
        "extension). No CLI `status <id>` yet (#1194).",
    ),
    "wait": (
        "partial",
        "CLI generation commands block until done; MCP tasks/get carries the result once "
        "a wait=false task finishes. No CLI `wait <id>` yet (#1194).",
    ),
    "download": (
        "partial",
        "Generation commands download on completion. `gflow data download` (MCP "
        "gflow_download_media) recovers a failed video download only; an image media "
        "id is refused (exit 11, #877).",
    ),
    "abort.local": (
        "partial",
        "Ctrl-C stops a CLI run; MCP tasks/cancel (a client with the SEP-2663 Tasks "
        "extension) stops a wait=false task. Neither stops a generation Flow already "
        "accepted, which may still be charged. No `cancel <id>` yet (#1198).",
    ),
    "cancel.server": (
        "no",
        "gflow cannot cancel a generation Flow has accepted; it runs and may still be charged.",
    ),
    "idempotent_submit": (
        "no",
        "A retried submit is a new generation and may be charged again.",
    ),
    "reconcile": (
        "partial",
        "`gflow data sync --names` refreshes catalog names and presence from Flow "
        "(CLI only, #543). No `reconcile <id>` yet (#1199).",
    ),
    "credits.balance": (
        "partial",
        "The Veo video-credit balance only, read from flow.google.com's balance reply in "
        "the profile's browser (#795): `gflow credits`, MCP gflow_get_credits. Image "
        "models use separate per-model daily quotas gflow does not read; tier fields "
        "are null.",
    ),
}


def _host_entries(flow_host: str) -> dict[str, Entry]:
    if flow_host == "labs.google":
        return {
            GENERATE_IMAGE: ("unknown", _LABS_NOTE),
            GENERATE_VIDEO: ("unknown", _LABS_NOTE),
            UPLOAD: ("partial", _UPLOAD_BASE),
        }
    prefix = "" if flow_host == "flow.google.com" else _AUTO_PREFIX
    return {
        GENERATE_IMAGE: ("partial", prefix + _MIGRATED_IMAGE),
        GENERATE_VIDEO: ("partial", prefix + _MIGRATED_VIDEO),
        UPLOAD: ("partial", _UPLOAD_BASE + _UPLOAD_MIGRATED),
    }


def build_capabilities(flow_host: str, *, no_spend: bool = False) -> dict[str, Any]:
    """The capability document for one ``flow_host`` setting (pure, no I/O).

    ``no_spend`` is the MCP server's mode: its generate tools are not registered, so
    nothing can be generated or submitted through it (#495: name only what exists).
    """
    host = _host_entries(flow_host)
    entries: dict[str, Entry] = {
        GENERATE_IMAGE: host[GENERATE_IMAGE],
        GENERATE_VIDEO: host[GENERATE_VIDEO],
        **_FIXED,
    }
    entries[UPLOAD] = host[UPLOAD]
    if no_spend:
        for key in (GENERATE_IMAGE, GENERATE_VIDEO, SUBMIT_NO_WAIT):
            entries[key] = ("no", _NO_SPEND)
    order = (GENERATE_IMAGE, GENERATE_VIDEO, SUBMIT_NO_WAIT, "status", "wait", "download")
    keys = [*order, UPLOAD, *(k for k in _FIXED if k not in order)]
    return {
        "schema": SCHEMA_VERSION,
        "adapter": "flow-browser",
        "host": flow_host,
        "capabilities": {key: entries[key][0] for key in keys},
        "notes": {key: entries[key][1] for key in keys},
    }


def current_capabilities(*, no_spend: bool = False) -> dict[str, Any]:
    """The document for this process's settings."""
    return build_capabilities(get_settings().flow_host, no_spend=no_spend)


@click.command("capabilities")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON document.")
def capabilities(as_json: bool) -> None:
    """Show what gflow can drive: yes / no / partial / unknown per capability.

    Makes no call to Flow: reads only GFLOW_CLI_FLOW_HOST. Key meanings:
    docs/CAPABILITIES.md. The MCP resource capabilities://self returns the same
    document (except on a no-spend server, where generation is "no").
    """
    doc = current_capabilities()
    if as_json:
        json_output.emit(doc)
        return
    table = Table(title=f"gflow capabilities (host setting: {doc['host']})")
    table.add_column("capability", style="bold")
    table.add_column("value")
    table.add_column("notes", overflow="fold")
    for key, value in doc["capabilities"].items():
        table.add_row(key, value, escape(doc["notes"].get(key, "")))
    console.print(table)
