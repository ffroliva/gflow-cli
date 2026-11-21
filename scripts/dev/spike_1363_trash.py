#!/usr/bin/env python3
"""#1363 live spike ($0, 0 generations): move a cataloged image to Flow's Trash and restore it.

Question
--------
When a generated media item on ``flow.google.com`` is moved to Flow's Trash from the project
UI and then restored from the Trash view:
1. Which ``batchexecute`` RPC id(s) (or ``aisandbox-pa.googleapis.com`` REST call(s)) are
   sent on Trash and on Restore, and what are their request and reply payload shapes?
2. Which identifier(s) (``flow_media_id``, ``flow_project_id``, ``flow_workflow_id``) sit at
   which positional slots in the request and reply frames, and does either call carry a
   page-minted token slot (``<tok:N>``)?
3. What structural DOM anchors (icon ligatures, ARIA roles, custom-element boundaries, and
   ``href`` attributes — never display text labels or positional ``[role=menuitem]`` order)
   open the item's menu, trigger Trash, confirm any dialog, navigate to the Trash view, and
   trigger Restore?

Cost & Safety Discipline
------------------------
* Generates NOTHING (0 credits, 0 image quota).
* Requires an existing disposable IMAGE from the profile's catalog via ``--media-id`` and
  an explicit ``--yes-trash-this`` confirmation flag. When ``--media-id`` is omitted, lists
  the newest cataloged images for the profile/project and exits with code 2 without opening
  a browser.
* Route guard is attached ONLY to ``**/batchexecute**`` and
  ``https://aisandbox-pa.googleapis.com/**`` (a catch-all route breaks the driver). Known
  billed generation and upscale RPCs (``YhhmEf``, ``eb1hJf``, ``MZZa6b``, ``nprQif``,
  ``ogiZ0b``, ``fZytfe``, ``SPrCad``, ``p0UkFb``) and aisandbox generation/upscale routes
  are unconditionally aborted before leaving the browser.
* Best-effort restore runs in a ``finally`` block on any exception after the Trash click.
  If restore cannot be confirmed, the script prints the ``media_id`` and the Trash view URL
  so the operator can restore the item by hand.

Pre-registered Readings (committed before the first live run)
-------------------------------------------------------------
* **Reading 1 — Single ``batchexecute`` RPC per transition (e.g. ``pGCYOe`` field-mask
  update):** One ``batchexecute`` RPC appears in the ``trash`` window (and not in the
  preceding ``idle`` window), receives HTTP 200 with no RPC error, and carries the media id
  (and/or project id or workflow id) at fixed positional slots; the tile disappears from the
  project grid and appears in the Trash view. A corresponding ``batchexecute`` RPC in the
  ``restore`` window moves it back.
  - If the captured request frame contains no ``<tok:N>`` slot, the RPC is a candidate for a
    direct cookie + ``at`` call (subject to a direct-write verification pass).
  - If the captured request frame contains a ``<tok:N>`` slot, the operation requires a
    page-minted token or UI-driven transport.
* **Reading 2 — Multi-RPC sequence (mutation + metadata/listing update, e.g. ``pGCYOe`` +
  ``o8DA4`` or ``Zzl0ze``):** The ``trash`` window sends a primary media mutation plus a
  project-level update (such as ``o8DA4`` resetting ``thumbnail_media_key``) or a listing
  refresh (``Zzl0ze``). Whether the second RPC is required or conditional on the item being
  the project thumbnail is determined by checking the ``o8DA4`` field mask.
* **Reading 3 — REST call on ``aisandbox-pa.googleapis.com``:** No ``batchexecute`` write
  fires in the ``trash`` window; instead an XHR/fetch to ``aisandbox-pa.googleapis.com``
  carries the target id(s). Media trash then belongs on the REST session path.
* **Reading 4 — No network call in the action window ("client-only" or capture miss):**
  Interpretable as client-only only if the positive control (``Zzl0ze`` captured with HTTP
  200 on project load) held in the same run AND the item moved between the grid and the
  Trash view. If the positive control failed or the item did not move, the run is
  **unmeasured**.
* **Reading 5 — Structural anchor miss (0/N candidate selectors matched):** Evidence about
  the candidate selector list, never evidence that Flow lacks Trash or Restore. The script
  records the visible structural DOM inventory (custom elements, button/menuitem roles,
  ``mat-icon`` and ``i.google-symbols`` ligatures, and ``href`` attributes) at the point of
  the miss so the next rung can target the measured anchor.

Usage
-----
    python scripts/dev/spike_1363_trash.py --self-test
    python scripts/dev/spike_1363_trash.py --project <project-uuid> --inventory \\
        [--profile <profile>]
    python scripts/dev/spike_1363_trash.py --profile <profile> [--project <project-uuid>]
    python scripts/dev/spike_1363_trash.py --profile <profile> --project <project-uuid> \\
        --media-id <media-uuid> --yes-trash-this --dry-run
    python scripts/dev/spike_1363_trash.py --profile <profile> --project <project-uuid> \\
        --media-id <media-uuid> --yes-trash-this [--show-mask]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api.transports import migrated_composer as mc  # noqa: E402
from gflow_cli.api.transports import migrated_extend as mx  # noqa: E402
from gflow_cli.api.transports._common import raise_if_known_landing  # noqa: E402
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors  # noqa: E402
from gflow_cli.api.transports.migrated_upscale import wait_for_or_none  # noqa: E402
from gflow_cli.config import get_settings  # noqa: E402
from gflow_cli.data.models import AssetKind  # noqa: E402
from gflow_cli.data.queries import ImageRow, list_images  # noqa: E402
from gflow_cli.data.repository import DataRepository  # noqa: E402
from gflow_cli.data.store import DataStore  # noqa: E402
from gflow_cli.errors import DataStoreError  # noqa: E402
from gflow_cli.services.media_recovery import resolve_asset  # noqa: E402

OUT_ROOT = _ROOT / "scripts" / "dev" / "_spike_out"

#: Known billed generation and upscale batchexecute RPCs — unconditionally aborted.
BILLED_RPCS: frozenset[str] = frozenset(
    {"YhhmEf", "eb1hJf", "MZZa6b", "nprQif", "ogiZ0b", "fZytfe", "SPrCad", "p0UkFb"}
)
#: Known aisandbox REST substrings that trigger generation or upscale — unconditionally aborted.
BILLED_REST_PATTERNS: tuple[str, ...] = (
    "batchAsyncGenerateVideo",
    "batchGenerateImages",
    "upsample",
    "generateVideo",
    "generateImage",
)
#: Known non-trash write RPCs that are allowed only when relevant (e.g. o8DA4 thumbnail reset).
PROJECT_DELETE_RPC = "QI2zvc"
FORBIDDEN_WRITE_RPCS: frozenset[str] = frozenset({PROJECT_DELETE_RPC, "jHPbke", "maseQ"})

B64_PNG = "iVBORw0KGgo"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
ENUM_RE = re.compile(r"^(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9_]{2,40})$")
TOK_SLOT_RE = re.compile(r"^<tok:\d+>$")

# ---------------------------------------------------------------------------
# Structural selectors (100% locale-invariant: custom elements, roles, hrefs,
# and Material Symbols ligatures in <mat-icon> or <i class="google-symbols">).
# Positional [role=menuitem] indices and display-text selectors are forbidden.
# ---------------------------------------------------------------------------

#: Custom-element boundaries that wrap a single grid tile on flow.google.com.
TILE_CONTAINER_TAGS: tuple[str, ...] = (
    "flow-grid-tile-container",
    "flow-tile-container",
    "flow-image-tile",
    "flow-video-tile",
)

#: Shell readiness selector across project grid and Trash views.
SHELL_READY_SELECTOR = (
    "flow-project-nav-list, mat-nav-list, cdk-virtual-scroll-viewport, "
    "img[data-media-id], flow-trash-action-bar"
)

#: Overflow menu triggers checked inside a hovered tile or detail toolbar.
OVERFLOW_TRIGGER_SELECTORS: tuple[str, ...] = (
    "button[aria-haspopup='menu']:has(mat-icon:text-is('more_vert'))",
    "button[aria-haspopup='menu']:has(mat-icon:text-is('more_horiz'))",
    "button:has(mat-icon:text-is('more_vert'))",
    "button:has(mat-icon:text-is('more_horiz'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('more_vert'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('more_horiz'))",
    "button:has(i.google-symbols:text-is('more_vert'))",
    "button:has(i.google-symbols:text-is('more_horiz'))",
)

#: Tile-inner overflow trigger candidates (scoped inside TILE_CONTAINER_TAGS).
TILE_OVERFLOW_INNER_SELECTORS: tuple[str, ...] = (
    *OVERFLOW_TRIGGER_SELECTORS,
    "flow-more-options-menu button",
    "flow-tile-hover-footer button[aria-haspopup='menu']",
    "flow-tile-hover-footer button[aria-haspopup]",
    "button.mat-mdc-menu-trigger",
)

#: Non-header overflow triggers (excludes the two project-level more_vert buttons in top bars).
NON_HEADER_OVERFLOW_SELECTORS: tuple[str, ...] = (
    "xpath=//button[.//mat-icon[normalize-space()='more_vert' or normalize-space()='more_horiz']]"
    "[not(ancestor::flow-navigation-header) and not(ancestor::flow-tile-view-header)]",
    "xpath=//button[.//i[contains(@class,'google-symbols') and "
    "(normalize-space()='more_vert' or normalize-space()='more_horiz')]]"
    "[not(ancestor::flow-navigation-header) and not(ancestor::flow-tile-view-header)]",
)

#: Trash action inside an open menu overlay (anchored on the delete ligature, never item order).
TRASH_MENUITEM_SELECTORS: tuple[str, ...] = (
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('delete'))",
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('delete_outline'))",
    ".cdk-overlay-pane [role='menuitem']:has(i.google-symbols:text-is('delete'))",
    ".cdk-overlay-pane [role='menuitem']:has(i.google-symbols:text-is('delete_outline'))",
    "[role='menu'] [role='menuitem']:has(mat-icon:text-is('delete'))",
    "[role='menu'] [role='menuitem']:has(mat-icon:text-is('delete_outline'))",
    "[role='menu'] [role='menuitem']:has(i.google-symbols:text-is('delete'))",
    "[role='menu'] [role='menuitem']:has(i.google-symbols:text-is('delete_outline'))",
)

#: Direct Trash / delete button on a tile or in the detail view toolbar.
TRASH_BUTTON_SELECTORS: tuple[str, ...] = (
    "button:has(mat-icon:text-is('delete'))",
    "button:has(mat-icon:text-is('delete_outline'))",
    "button:has(i.google-symbols:text-is('delete'))",
    "button:has(i.google-symbols:text-is('delete_outline'))",
)

#: Confirmation dialog container and structural primary/accept action selectors.
DIALOG_CONTAINER_SELECTOR = "[role='dialog'], [role='alertdialog'], mat-dialog-container"
DIALOG_CONFIRM_SELECTORS: tuple[str, ...] = (
    "[role='dialog'] button[data-mdc-dialog-action='accept']",
    "[role='dialog'] button[data-mdc-dialog-action='confirm']",
    "[role='alertdialog'] button[data-mdc-dialog-action='accept']",
    "[role='dialog'] button:has(mat-icon:text-is('delete'))",
    "[role='alertdialog'] button:has(mat-icon:text-is('delete'))",
    "[role='dialog'] button:has(i.google-symbols:text-is('delete'))",
    "[role='dialog'] button.mat-mdc-unelevated-button",
    "[role='dialog'] button.mdc-button--unelevated",
    "[role='dialog'] button.mat-flat-button",
    "[role='dialog'] button[color='warn']",
    "[role='dialog'] button[color='primary']",
    "[role='alertdialog'] button.mat-mdc-unelevated-button",
    "[role='alertdialog'] button.mdc-button--unelevated",
)

#: Left-nav expand trigger if the sidebar is collapsed.
SIDEBAR_EXPAND_SELECTORS: tuple[str, ...] = (
    "button:has(mat-icon:text-is('left_panel_open'))",
    "button:has(i.google-symbols:text-is('left_panel_open'))",
)

#: Structural navigation anchors to open the project's Trash view.
TRASH_NAV_SELECTORS: tuple[str, ...] = (
    "a[href$='/trash']",
    "a[href*='/trash?']",
    "nav a:has(mat-icon:text-is('delete'))",
    "nav a:has(mat-icon:text-is('delete_outline'))",
    "aside a:has(mat-icon:text-is('delete'))",
    "aside a:has(mat-icon:text-is('delete_outline'))",
    "[role='navigation'] a:has(mat-icon:text-is('delete'))",
    "[role='navigation'] a:has(i.google-symbols:text-is('delete'))",
    "nav button:has(mat-icon:text-is('delete'))",
    "aside button:has(mat-icon:text-is('delete'))",
    "[role='navigation'] button:has(mat-icon:text-is('delete'))",
    "[role='navigation'] button:has(i.google-symbols:text-is('delete'))",
    "[role='tab']:has(mat-icon:text-is('delete'))",
    "[role='tab']:has(i.google-symbols:text-is('delete'))",
)

#: Restore action inside an open menu overlay in the Trash view.
RESTORE_MENUITEM_SELECTORS: tuple[str, ...] = (
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('restore_from_trash'))",
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('restore'))",
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('undo'))",
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('history'))",
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('replay'))",
    ".cdk-overlay-pane [role='menuitem']:has(i.google-symbols:text-is('restore_from_trash'))",
    ".cdk-overlay-pane [role='menuitem']:has(i.google-symbols:text-is('restore'))",
    ".cdk-overlay-pane [role='menuitem']:has(i.google-symbols:text-is('undo'))",
    "[role='menu'] [role='menuitem']:has(mat-icon:text-is('restore_from_trash'))",
    "[role='menu'] [role='menuitem']:has(mat-icon:text-is('restore'))",
    "[role='menu'] [role='menuitem']:has(mat-icon:text-is('undo'))",
)

#: Direct Restore button on a tile or toolbar in the Trash view.
RESTORE_BUTTON_SELECTORS: tuple[str, ...] = (
    "button:has(mat-icon:text-is('restore_from_trash'))",
    "button:has(mat-icon:text-is('restore'))",
    "button:has(mat-icon:text-is('undo'))",
    "button:has(mat-icon:text-is('history'))",
    "button:has(mat-icon:text-is('replay'))",
    "button:has(i.google-symbols:text-is('restore_from_trash'))",
    "button:has(i.google-symbols:text-is('restore'))",
    "button:has(i.google-symbols:text-is('undo'))",
)

#: Structural navigation anchors back to the main project grid from Trash.
GRID_NAV_SELECTORS: tuple[str, ...] = (
    "nav a:has(mat-icon:text-is('dashboard'))",
    "aside a:has(mat-icon:text-is('dashboard'))",
    "[role='navigation'] a:has(mat-icon:text-is('dashboard'))",
    "[role='navigation'] a:has(i.google-symbols:text-is('dashboard'))",
    "nav a:has(mat-icon:text-is('grid_view'))",
    "aside a:has(mat-icon:text-is('grid_view'))",
    "nav button:has(mat-icon:text-is('dashboard'))",
    "aside button:has(mat-icon:text-is('dashboard'))",
    "button:has(mat-icon:text-is('arrow_back'))",
    "a:has(mat-icon:text-is('arrow_back'))",
)


# ---------------------------------------------------------------------------
# Pure helpers (offline self-tested via --self-test)
# ---------------------------------------------------------------------------


def _replace_known_ids(value: str, targets: dict[str, str]) -> str | None:
    """If *value* equals or embeds a target id, return its tagged placeholder."""
    for label, target_id in targets.items():
        if not target_id:
            continue
        if value == target_id:
            return f"<{label}>"
        if value == f"projects/{target_id}":
            return f"projects/<{label}>"
        if value == f"tools/PINHOLE/projects/{target_id}":
            return f"tools/PINHOLE/projects/<{label}>"
    return None


def shape(node: Any, targets: dict[str, str] | None = None, depth: int = 0) -> Any:
    """Redacted skeleton of a decoded payload (adapted from ``spike_rpc_map_1226.py``).

    Preserves structure, enum tokens, and small integers while redacting user strings,
    signed URLs, and tokens. Target IDs (``media_id``, ``project_id``, ``workflow_id``)
    are replaced with ``<media_id>``, ``<project_id>``, ``<workflow_id>`` so their exact
    slot is visible without recording raw UUIDs.
    """
    active_targets = targets or {}
    if depth > 9:
        return "…"
    if isinstance(node, list):
        items = [shape(n, active_targets, depth + 1) for n in node[:12]]
        return [*items, f"…+{len(node) - 12}"] if len(node) > 12 else items
    if isinstance(node, dict):
        return {k: shape(v, active_targets, depth + 1) for k, v in list(node.items())[:8]}
    if isinstance(node, str):
        if node.startswith(("[", "{")):
            with contextlib.suppress(ValueError):
                return {"json": shape(json.loads(node), active_targets, depth + 1)}
        tagged = _replace_known_ids(node, active_targets)
        if tagged is not None:
            return tagged
        if UUID_RE.fullmatch(node):
            return "<uuid>"
        if UUID_RE.search(node):
            replaced = node
            for label, target_id in active_targets.items():
                if target_id and target_id in replaced:
                    replaced = replaced.replace(target_id, f"<{label}>")
            replaced = UUID_RE.sub("<uuid>", replaced)
            return replaced if len(node) < 80 else "<s+uuid>"
        if node in ("projects/*", "tools/PINHOLE"):
            return node
        if node.startswith("http"):
            return "<url>"
        if len(node) > 60:
            kind = "png-b64" if B64_PNG in node[:80] else "tok"
            return f"<{kind}:{len(node)}>"
        return node if ENUM_RE.fullmatch(node) else f"<s:{len(node)}>"
    if isinstance(node, (int, float)) and not isinstance(node, bool) and abs(node) >= 10_000:
        return "<n>"
    return node


def find_id_positions(
    node: Any,
    targets: dict[str, str],
    path: tuple[int | str, ...] = (),
) -> dict[str, list[list[int | str]]]:
    """Return every nested list/dict path where a target ID appears in *node*.

    Also descends into JSON-encoded strings (marked with a ``"json"`` step in the path)
    and matches resource-prefixed forms such as ``projects/<project_id>``.
    """
    found: dict[str, list[list[int | str]]] = {k: [] for k in targets if targets[k]}

    def _walk(current: Any, cur_path: tuple[int | str, ...], depth: int) -> None:
        if depth > 12:
            return
        if isinstance(current, list):
            for idx, item in enumerate(current):
                _walk(item, (*cur_path, idx), depth + 1)
            return
        if isinstance(current, dict):
            for key, val in current.items():
                _walk(val, (*cur_path, str(key)), depth + 1)
            return
        if isinstance(current, str):
            if current.startswith(("[", "{")):
                try:
                    decoded = json.loads(current)
                except ValueError:
                    decoded = None
                if decoded is not None:
                    _walk(decoded, (*cur_path, "json"), depth + 1)
                    return
            for label, target_id in targets.items():
                if not target_id:
                    continue
                if current == target_id or current in (
                    f"projects/{target_id}",
                    f"tools/PINHOLE/projects/{target_id}",
                ):
                    found[label].append(list(cur_path))

    _walk(node, path, 0)
    return found


def has_token_slot(shaped_node: Any) -> bool:
    """True when a shaped payload contains an opaque ``<tok:N>`` string slot."""
    if isinstance(shaped_node, str):
        return bool(TOK_SLOT_RE.fullmatch(shaped_node))
    if isinstance(shaped_node, list):
        return any(has_token_slot(item) for item in shaped_node)
    if isinstance(shaped_node, dict):
        return any(has_token_slot(val) for val in shaped_node.values())
    return False


def extract_pgcyoe_masks(inner: Any) -> list[str]:
    """Extract literal field mask string(s) from a pGCYOe inner payload.

    A pGCYOe payload typically has the shape ``[[[media_id, ...]], [[mask_1, mask_2, ...]]]``,
    where the second element contains a list of field mask names.
    """
    masks: list[str] = []
    if isinstance(inner, str) and inner.startswith(("[", "{")):
        with contextlib.suppress(ValueError):
            inner = json.loads(inner)
    if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], list):
        for sub in inner[1]:
            if isinstance(sub, list):
                for val in sub:
                    if isinstance(val, str) and val:
                        masks.append(val)
    return masks


def should_abort_batchexecute(ids: list[str], *, strict: bool = False) -> bool:
    """True when a batchexecute request carries a billed or forbidden write RPC id."""
    if BILLED_RPCS.intersection(ids):
        return True
    if FORBIDDEN_WRITE_RPCS.intersection(ids):
        return True
    return bool(strict and ids)


def should_abort_aisandbox(url: str) -> bool:
    """True when an aisandbox REST URL targets a billed generation or upscale endpoint."""
    lower = url.lower()
    return any(pat.lower() in lower for pat in BILLED_REST_PATTERNS)


def redact_url_path(url: str, targets: dict[str, str] | None = None) -> str:
    """Redact UUIDs and long tokens from a URL path while tagging known target IDs."""
    active_targets = targets or {}
    path = urlsplit(url).path or "/"
    for label, target_id in active_targets.items():
        if target_id and target_id in path:
            path = path.replace(target_id, f"<{label}>")
    path = UUID_RE.sub("<uuid>", path)
    return re.sub(r"[A-Za-z0-9_\-]{40,}", "<tok>", path)[:140]


def attribute_window_rpcs(action_ids: list[str], idle_ids: set[str]) -> list[str]:
    """Ordered unique RPC ids seen in an action window that were NOT in the idle window."""
    return [rid for rid in dict.fromkeys(action_ids) if rid not in idle_ids]


def extract_asb_token(src: str | None) -> str:
    """Extract the ``/asb/<token>`` media key from an ``lh3``/Flow thumbnail ``src``."""
    if not src:
        return ""
    match = re.search(r"/asb/([A-Za-z0-9_-]+)", src)
    return match.group(1) if match else ""


def filter_ligatures_for_kind(kind: str | None) -> tuple[str, ...]:
    """Left-nav media-type filter ligature(s) for a catalog asset kind (or both if unknown)."""
    normalized = (kind or "").strip().lower()
    if normalized == "image":
        return ("image",)
    if normalized == "video":
        return ("videocam",)
    return ("image", "videocam")


def left_nav_filter_selectors(ligature: str) -> tuple[str, ...]:
    """Structural selectors for a left-nav media-type filter button by icon ligature.

    Scoped to the left navigation list / list-item classes so they never match the
    ``button[aria-haspopup='true']:has(mat-icon:text-is('image'))`` badges inside grid tiles.
    """
    return (
        f"flow-project-nav-list button:has(mat-icon:text-is('{ligature}'))",
        f"mat-nav-list button:has(mat-icon:text-is('{ligature}'))",
        f"mat-sidenav button:has(mat-icon:text-is('{ligature}'))",
        f"[role='navigation'] button.mat-mdc-list-item:has(mat-icon:text-is('{ligature}'))",
        f"nav button.mat-mdc-list-item:has(mat-icon:text-is('{ligature}'))",
        f"aside button.mat-mdc-list-item:has(mat-icon:text-is('{ligature}'))",
        f"button.mat-mdc-list-item:has(mat-icon:text-is('{ligature}'))",
        f"button.mdc-list-item:has(mat-icon:text-is('{ligature}'))",
        f"flow-project-nav-list button:has(i.google-symbols:text-is('{ligature}'))",
        f"mat-nav-list button:has(i.google-symbols:text-is('{ligature}'))",
        f"button.mat-mdc-list-item:has(i.google-symbols:text-is('{ligature}'))",
    )


def tile_scoped_selectors(
    tile_id: str | None = None,
    inner_selectors: tuple[str, ...] = (),
    *,
    media_id: str | None = None,
) -> tuple[str, ...]:
    """Scope each selector in *inner_selectors* to the tile container for *tile_id*."""
    target_id = tile_id or media_id or ""
    media_attr = f'[data-media-id="{target_id}"]'
    scoped: list[str] = []
    for tag in TILE_CONTAINER_TAGS:
        for sel in inner_selectors:
            scoped.append(f"{tag}:has({media_attr}) {sel}")
    return tuple(scoped)


@dataclass(frozen=True, slots=True)
class TileResolution:
    media_id: str
    tile_id: str
    tile_id_source: str


def extract_listing_id_pairs(
    listings: list[Any],
) -> tuple[dict[str, str], dict[str, str]]:
    """Extract media_id <-> workflow_id mappings from Zzl0ze listings.

    Returns (media_to_wf, wf_to_media).
    """
    media_to_wf: dict[str, str] = {}
    wf_to_media: dict[str, str] = {}

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            if (
                len(node) >= 6
                and isinstance(node[5], list)
                and all(
                    isinstance(node[i], str) and bool(UUID_RE.fullmatch(node[i])) for i in (0, 1, 2)
                )
            ):
                wf = str(node[0])
                m = str(node[2])
                media_to_wf[m] = wf
                wf_to_media[wf] = m
            for child in node:
                _walk(child)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, str):
            if node.startswith(("[", "{")):
                with contextlib.suppress(Exception):
                    _walk(json.loads(node))

    for listing in listings:
        _walk(listing)
        if isinstance(listing, list) and len(listing) > 1 and isinstance(listing[1], list):
            for entry in listing[1]:
                m_id: str | None = None
                wf_id: str | None = None
                if (
                    isinstance(entry, list)
                    and len(entry) > 0
                    and isinstance(entry[0], str)
                    and UUID_RE.fullmatch(entry[0])
                ):
                    m_id = str(entry[0])
                if (
                    isinstance(entry, list)
                    and len(entry) > 3
                    and isinstance(entry[3], list)
                    and len(entry[3]) > 4
                    and isinstance(entry[3][4], str)
                    and UUID_RE.fullmatch(entry[3][4])
                ):
                    wf_id = str(entry[3][4])
                if m_id and wf_id:
                    media_to_wf[m_id] = wf_id
                    wf_to_media[wf_id] = m_id

    return media_to_wf, wf_to_media


def resolve_tile_id(
    target_id: str,
    listings: list[Any],
    catalog_workflow_id: str | None = None,
    *,
    already_matches_tile: bool = False,
) -> TileResolution | None:
    """Resolve (media_id, tile_id, tile_id_source) before any DOM tile lookup.

    1. Search Zzl0ze listings captured on project open:
       - If target_id equals an entry's media_id, take its workflow_id (source='listing').
       - If target_id equals an entry's workflow_id, take target_id as tile_id (source='listing').
    2. If already_matches_tile is True, target_id already matches a tile in DOM (source='direct').
    3. Fall back to catalog_workflow_id for that asset if provided (source='catalog').
    4. If neither exists, return None.
    """
    media_to_wf, wf_to_media = extract_listing_id_pairs(listings)

    if target_id in media_to_wf:
        return TileResolution(
            media_id=target_id,
            tile_id=media_to_wf[target_id],
            tile_id_source="listing",
        )

    if target_id in wf_to_media:
        return TileResolution(
            media_id=wf_to_media[target_id],
            tile_id=target_id,
            tile_id_source="listing",
        )

    if already_matches_tile:
        return TileResolution(
            media_id=target_id,
            tile_id=target_id,
            tile_id_source="direct",
        )

    cat_wf = (catalog_workflow_id or "").strip()
    if cat_wf:
        return TileResolution(
            media_id=target_id,
            tile_id=cat_wf,
            tile_id_source="catalog",
        )

    return None


def extract_listing_ids(listings: list[Any]) -> dict[str, set[str]]:
    """Extract media_ids, workflow_ids, project_ids, and other UUIDs from Zzl0ze listings."""
    media_ids: set[str] = set()
    workflow_ids: set[str] = set()
    project_ids: set[str] = set()
    all_uuids: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            if (
                len(node) >= 6
                and isinstance(node[5], list)
                and all(
                    isinstance(node[i], str) and bool(UUID_RE.fullmatch(node[i])) for i in (0, 1, 2)
                )
            ):
                workflow_ids.add(str(node[0]))
                project_ids.add(str(node[1]))
                media_ids.add(str(node[2]))
            for child in node:
                _walk(child)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, str):
            if node.startswith(("[", "{")):
                with contextlib.suppress(Exception):
                    _walk(json.loads(node))
            for m in UUID_RE.finditer(node):
                all_uuids.add(m.group(0))

    for listing in listings:
        _walk(listing)
        if isinstance(listing, list) and len(listing) > 1 and isinstance(listing[1], list):
            for entry in listing[1]:
                if (
                    isinstance(entry, list)
                    and len(entry) > 0
                    and isinstance(entry[0], str)
                    and UUID_RE.fullmatch(entry[0])
                ):
                    media_ids.add(entry[0])
                if (
                    isinstance(entry, list)
                    and len(entry) > 3
                    and isinstance(entry[3], list)
                    and len(entry[3]) > 4
                    and isinstance(entry[3][4], str)
                    and UUID_RE.fullmatch(entry[3][4])
                ):
                    workflow_ids.add(entry[3][4])

    other_uuids = all_uuids - media_ids - workflow_ids - project_ids
    return {
        "media_ids": media_ids,
        "workflow_ids": workflow_ids,
        "project_ids": project_ids,
        "other": other_uuids,
    }


def classify_uuid(u: str, id_maps: dict[str, set[str]]) -> str | None:
    """Classify *u* as 'media id', 'workflow id', or 'other' based on *id_maps*."""
    if u in id_maps.get("media_ids", set()):
        return "media id"
    if u in id_maps.get("workflow_ids", set()):
        return "workflow id"
    if u in id_maps.get("other", set()) or u in id_maps.get("project_ids", set()):
        return "other"
    return None


def sanitize_url_path(raw_url: str | None) -> str | None:
    """Return host + path with UUIDs replaced by <uuid> (query/fragment stripped)."""
    if not raw_url:
        return None
    try:
        parts = urlsplit(raw_url)
        path = UUID_RE.sub("<uuid>", parts.path or "")
        host = parts.netloc
        if host:
            return f"{host}{path}"
        return path or None
    except Exception:
        return UUID_RE.sub("<uuid>", str(raw_url))


def print_inventory_table(
    *,
    project_id: str,
    selected_filter: str | None,
    scroller_wheel_test: dict[str, Any],
    tiles: list[dict[str, Any]],
) -> None:
    """Print a short formatted summary table of visible grid tiles and scroller test."""
    print(f"\n=== Flow Project Grid Tile Inventory (project: {project_id[:8]}…) ===")
    print(f"Selected filter: {selected_filter or 'none'}")
    sw = scroller_wheel_test
    print(
        f"Wheel scroll: scrollTop {sw.get('before_scroll_top')} -> {sw.get('after_scroll_top')} "
        f"(changed={sw.get('scroll_top_changed')}), "
        f"new tiles attached={sw.get('new_tiles_attached')} (+{sw.get('new_tiles_count', 0)})\n"
    )
    header = (
        f"{'#':<3} {'Tag':<7} {'Role':<10} {'data-* / attrs':<28} "
        f"{'Matched Field':<15} {'Nearest Ancestor':<25}"
    )
    print(header)
    print("-" * len(header))
    for idx, t in enumerate(tiles[:40], 1):
        tag = t.get("tag", "")
        role = t.get("role") or "-"
        data_keys = list(t.get("data_attributes", {}).keys())
        if data_keys:
            attrs_str = (",".join(data_keys))[:27]
        else:
            attrs_str = (",".join(t.get("attribute_names", [])[:3]))[:27]
        matched = t.get("matched_listing_field") or "-"
        ancestors = t.get("ancestors", [])
        ancestor = (ancestors[0] if ancestors else "-")[:24]
        print(f"{idx:<3} {tag:<7} {role:<10} {attrs_str:<28} {matched:<15} {ancestor:<25}")
    if len(tiles) > 40:
        print(f"... (+{len(tiles) - 40} more tiles recorded in summary.json)")
    print()


# ---------------------------------------------------------------------------
# Offline self-test (--self-test)
# ---------------------------------------------------------------------------


def run_self_test() -> int:
    media_id = "11111111-2222-3333-4444-555555555555"
    project_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    workflow_id = "99999999-8888-7777-6666-555555555555"
    other_uuid = "abcdef01-2345-6789-abcd-ef0123456789"
    targets = {
        "media_id": media_id,
        "project_id": project_id,
        "workflow_id": workflow_id,
    }

    # 1. pGCYOe candidate payload shape + id-position matching
    pgcyoe_inner = [[[media_id, None, None, [None, None, 1], project_id]], [["trash_mask_token"]]]
    shaped_pgcyoe = shape(pgcyoe_inner, targets)
    assert shaped_pgcyoe == [
        [["<media_id>", None, None, [None, None, 1], "<project_id>"]],
        [["trash_mask_token"]],
    ], f"unexpected shape: {shaped_pgcyoe}"
    positions = find_id_positions(pgcyoe_inner, targets)
    assert positions["media_id"] == [[0, 0, 0]], f"unexpected media_id pos: {positions}"
    assert positions["project_id"] == [[0, 0, 4]], f"unexpected project_id pos: {positions}"
    assert positions["workflow_id"] == [], f"unexpected workflow_id pos: {positions}"
    assert not has_token_slot(shaped_pgcyoe)

    # 2. o8DA4 thumbnail-reset payload shape + prefixed project_id matching
    o8da4_inner = [
        f"projects/{project_id}",
        [None],
        [["thumbnail_media_key", "is_thumbnail_set_by_user"]],
        [None, 22],
    ]
    shaped_o8da4 = shape(o8da4_inner, targets)
    assert shaped_o8da4[0] == "projects/<project_id>"
    assert shaped_o8da4[2] == [["thumbnail_media_key", "is_thumbnail_set_by_user"]]
    o8da4_pos = find_id_positions(o8da4_inner, targets)
    assert o8da4_pos["project_id"] == [[0]]

    # 3. Token slot detection and PII/token redaction
    fake_token = "T" * 120
    with_token = shape(
        [media_id, other_uuid, "https://flow.google.com/signed", fake_token], targets
    )
    assert with_token == ["<media_id>", "<uuid>", "<url>", "<tok:120>"]
    assert has_token_slot(with_token)
    serialized = json.dumps(with_token)
    for secret in (media_id, project_id, workflow_id, other_uuid, fake_token, "signed"):
        assert secret not in serialized

    # 4. Nested JSON string decoding inside a frame
    nested_json = json.dumps([workflow_id, {"project": f"projects/{project_id}"}])
    nested_pos = find_id_positions([nested_json], targets)
    assert nested_pos["workflow_id"] == [[0, "json", 0]]
    assert nested_pos["project_id"] == [[0, "json", 1, "project"]]

    # 5. Route guard predicates
    assert should_abort_batchexecute(["YhhmEf"])
    assert should_abort_batchexecute(["pGCYOe", "ogiZ0b"])
    assert should_abort_batchexecute(["QI2zvc"])
    assert not should_abort_batchexecute(["pGCYOe", "o8DA4"])
    assert should_abort_batchexecute(["pGCYOe"], strict=True)
    assert should_abort_aisandbox(
        "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideo"
    )
    assert not should_abort_aisandbox(
        "https://aisandbox-pa.googleapis.com/v1/flow:batchDeleteAssets"
    )

    # 6. Attribution window filtering and URL redaction
    assert attribute_window_rpcs(["jwpduf", "pGCYOe", "o8DA4", "pGCYOe"], {"jwpduf", "DTaVef"}) == [
        "pGCYOe",
        "o8DA4",
    ]
    assert (
        redact_url_path(f"https://flow.google.com/project/{project_id}/trash", targets)
        == "/project/<project_id>/trash"
    )
    assert (
        extract_asb_token("https://lh3.googleusercontent.com/asb/AbCdEf_12-34=w512")
        == "AbCdEf_12-34"
    )

    # 7. Filter ligature mapping, left-nav scoping, tile-scoped selectors, and --dry-run parser
    assert filter_ligatures_for_kind("image") == ("image",)
    assert filter_ligatures_for_kind("video") == ("videocam",)
    assert filter_ligatures_for_kind("unknown") == ("image", "videocam")
    assert filter_ligatures_for_kind(None) == ("image", "videocam")
    img_nav_sels = left_nav_filter_selectors("image")
    assert img_nav_sels[0] == "flow-project-nav-list button:has(mat-icon:text-is('image'))"
    assert all("button:has(" != s[:11] for s in img_nav_sels)
    scoped_sels = tile_scoped_selectors(media_id, ("button:has(mat-icon:text-is('more_vert'))",))
    assert (
        scoped_sels[0] == f'flow-grid-tile-container:has([data-media-id="{media_id}"]) '
        "button:has(mat-icon:text-is('more_vert'))"
    )
    parsed_dry = build_parser().parse_args(
        ["--media-id", media_id, "--yes-trash-this", "--dry-run"]
    )
    assert parsed_dry.dry_run is True
    assert parsed_dry.yes_trash_this is True

    # 8. Listing ID extraction, UUID classification, URL sanitizing, and --inventory parser
    mock_listing = [
        [
            workflow_id,
            project_id,
            media_id,
            None,
            None,
            ["details"],
        ],
        [
            [media_id, None, None, [None, None, None, None, workflow_id]],
        ],
        f"prefix-{other_uuid}-suffix",
    ]
    extracted = extract_listing_ids([mock_listing])
    assert media_id in extracted["media_ids"], f"media_id not extracted: {extracted}"
    assert workflow_id in extracted["workflow_ids"], f"workflow_id not extracted: {extracted}"
    assert project_id in extracted["project_ids"], f"project_id not extracted: {extracted}"
    assert other_uuid in extracted["other"], f"other_uuid not extracted: {extracted}"
    assert classify_uuid(media_id, extracted) == "media id"
    assert classify_uuid(workflow_id, extracted) == "workflow id"
    assert classify_uuid(project_id, extracted) == "other"
    assert classify_uuid(other_uuid, extracted) == "other"
    assert classify_uuid("00000000-0000-0000-0000-000000000000", extracted) is None

    assert (
        sanitize_url_path(f"https://flow.google.com/project/{project_id}/view?query=1")
        == "flow.google.com/project/<uuid>/view"
    )
    assert (
        sanitize_url_path(f"https://lh3.googleusercontent.com/asb/{other_uuid}=w512")
        == "lh3.googleusercontent.com/asb/<uuid>=w512"
    )

    parsed_inv = build_parser().parse_args(["--project", project_id, "--inventory"])
    assert parsed_inv.inventory is True
    assert parsed_inv.project == project_id
    assert parsed_inv.media_id is None
    assert parsed_inv.yes_trash_this is False

    print_inventory_table(
        project_id=project_id,
        selected_filter="image",
        scroller_wheel_test={
            "before_scroll_top": 0,
            "after_scroll_top": 600,
            "scroll_top_changed": True,
            "new_tiles_attached": True,
            "new_tiles_count": 2,
        },
        tiles=[
            {
                "tag": "img",
                "role": None,
                "data_attributes": {"data-media-id": media_id},
                "attribute_names": ["src", "data-media-id"],
                "matched_listing_field": "media id",
                "ancestors": ["flow-image-tile.mat-mdc-card"],
            }
        ],
    )

    # 9. Tile ID resolution (listing hit, catalog fallback, none, either id accepted)
    # 9a. Listing hit where target_id equals media_id -> takes workflow_id
    res_listing = resolve_tile_id(media_id, [mock_listing], catalog_workflow_id=None)
    assert res_listing is not None, "expected listing hit"
    assert res_listing.media_id == media_id
    assert res_listing.tile_id == workflow_id
    assert res_listing.tile_id_source == "listing"

    # 9b. Listing hit where target_id equals workflow_id (either id accepted)
    res_wf = resolve_tile_id(workflow_id, [mock_listing], catalog_workflow_id=None)
    assert res_wf is not None, "expected workflow id listing hit"
    assert res_wf.media_id == media_id
    assert res_wf.tile_id == workflow_id
    assert res_wf.tile_id_source == "listing"

    # 9c. Catalog fallback when listing does not contain target_id
    cat_fallback_wf = "77777777-6666-5555-4444-333333333333"
    res_cat = resolve_tile_id(media_id, [], catalog_workflow_id=cat_fallback_wf)
    assert res_cat is not None, "expected catalog fallback"
    assert res_cat.media_id == media_id
    assert res_cat.tile_id == cat_fallback_wf
    assert res_cat.tile_id_source == "catalog"

    # 9d. None when neither listing nor catalog can resolve it
    res_none = resolve_tile_id(
        "00000000-0000-0000-0000-000000000000", [mock_listing], catalog_workflow_id=None
    )
    assert res_none is None, f"expected None, got {res_none}"
    res_none_empty = resolve_tile_id(media_id, [], catalog_workflow_id="")
    assert res_none_empty is None, f"expected None for empty catalog wf, got {res_none_empty}"

    # 9e. Direct DOM match when already matching tile
    res_direct = resolve_tile_id(
        workflow_id, [], catalog_workflow_id=None, already_matches_tile=True
    )
    assert res_direct is not None, "expected direct match"
    assert res_direct.tile_id == workflow_id
    assert res_direct.tile_id_source == "direct"

    # 10. pGCYOe field mask extraction for --show-mask
    sample_trash_inner = [
        [[media_id, None, None, [None, None, 1], project_id]],
        [["mask_field_name_17"]],
    ]
    sample_restore_inner = [
        [[media_id, "", None, [], project_id]],
        [["mask_field_name_17", "collection_id"]],
    ]
    assert extract_pgcyoe_masks(sample_trash_inner) == ["mask_field_name_17"]
    assert extract_pgcyoe_masks(sample_restore_inner) == [
        "mask_field_name_17",
        "collection_id",
    ]
    parsed_mask = build_parser().parse_args(["--show-mask"])
    assert parsed_mask.show_mask is True

    print("self-test: ok (10 helper suites passed)", flush=True)
    return 0


# ---------------------------------------------------------------------------
# DOM inspection helpers (closed structural vocabulary, zero PII)
# ---------------------------------------------------------------------------

_STRUCTURAL_INVENTORY_JS = r"""
() => {
  const ligatureOf = (el) => {
    const icons = [...el.querySelectorAll("mat-icon, i.google-symbols, i[class*='symbols']")];
    return icons.map(i => (i.textContent || "").trim()).filter(Boolean).join(",");
  };
  const structClasses = (el) =>
    [...(el.classList || [])]
      .filter(c => /^(cdk|mat|mdc|flow|glue)-/.test(c))
      .slice(0, 4)
      .join(".");
  const visible = (el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== "none" && cs.visibility !== "hidden" && r.width > 0 && r.height > 0;
  };
  const summarize = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role"),
    classes: structClasses(el),
    ligature: ligatureOf(el),
    haspopup: el.getAttribute("aria-haspopup"),
    disabled: el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true",
    action: el.getAttribute("data-mdc-dialog-action"),
    href: (el.getAttribute("href") || "").replace(
      /[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/g,
      "<uuid>"
    ).slice(0, 80) || null,
  });

  const menuSel = "[role='menuitem'], [role='menu'] button, .cdk-overlay-pane [role='option']";
  const menuItems = [...document.querySelectorAll(menuSel)]
    .filter(visible).slice(0, 30).map(summarize);
  const dialogSel =
    "[role='dialog'] button, [role='alertdialog'] button, mat-dialog-container button";
  const dialogButtons = [...document.querySelectorAll(dialogSel)]
    .filter(visible).slice(0, 20).map(summarize);
  const navSel =
    "nav a, nav button, aside a, aside button, [role='navigation'] a, [role='navigation'] button";
  const navLinks = [...document.querySelectorAll(navSel)]
    .filter(visible).slice(0, 30).map(summarize);
  const overlaySel = [
    ".cdk-overlay-pane button",
    ".cdk-overlay-pane [role='menuitem']",
    ".cdk-overlay-pane [role='option']",
  ].join(", ");
  const overlayButtons = [...document.querySelectorAll(overlaySel)]
    .filter(visible).slice(0, 30).map(summarize);
  const pageButtons = [...document.querySelectorAll("button, a[href]")]
    .filter(visible).slice(0, 60).map(summarize);
  const customTags = [...new Set(
    [...document.querySelectorAll("*")]
      .filter(visible)
      .map(e => e.tagName.toLowerCase())
      .filter(t => t.includes("-"))
  )].slice(0, 50);

  return {
    menuItems,
    dialogButtons,
    navLinks,
    overlayButtons,
    pageButtons,
    customTags,
  };
}
"""

_TILE_CONTEXT_JS = r"""
(mediaId) => {
  const sel = `img[data-media-id="${mediaId}"], [data-media-id="${mediaId}"]`;
  const el = document.querySelector(sel);
  if (!el) return { found: false };
  const visible = (node) => {
    const cs = getComputedStyle(node);
    const r = node.getBoundingClientRect();
    return cs.display !== "none" && cs.visibility !== "hidden" && r.width > 0 && r.height > 0;
  };
  const ligatureOf = (node) =>
    [...node.querySelectorAll("mat-icon, i.google-symbols, i[class*='symbols']")]
      .map(i => (i.textContent || "").trim()).filter(Boolean).join(",");
  const structClasses = (node) =>
    [...(node.classList || [])]
      .filter(c => !/^ng-(tns|star|trigger)/.test(c))
      .slice(0, 5)
      .join(".");
  const ancestors = [];
  let cur = el.parentElement;
  for (let depth = 0; cur && cur !== document.body && depth < 6; depth++, cur = cur.parentElement) {
    const btnSel = "button, [role='button'], [role='menuitem'], [role='checkbox'], a[href]";
    const btnNodes = [...cur.querySelectorAll(btnSel)].slice(0, 15);
    const buttons = btnNodes.map(b => ({
      tag: b.tagName.toLowerCase(),
      role: b.getAttribute("role"),
      classes: structClasses(b),
      ligature: ligatureOf(b),
      haspopup: b.getAttribute("aria-haspopup"),
      disabled: b.hasAttribute("disabled") || b.getAttribute("aria-disabled") === "true",
      visible: visible(b),
    }));
    ancestors.push({
      tag: cur.tagName.toLowerCase(),
      role: cur.getAttribute("role"),
      classes: structClasses(cur),
      buttons,
    });
  }
  return {
    found: true,
    tag: el.tagName.toLowerCase(),
    visible: visible(el),
    ancestors,
  };
}
"""

_SCROLL_GRID_STEP_JS = r"""
(arg) => {
  const { reset } = arg || {};
  const firstImg = document.querySelector("img[data-media-id], [data-media-id]");
  const isScrollable = (node) => {
    if (!node || node === document.body || node === document.documentElement) return false;
    const tag = node.tagName ? node.tagName.toLowerCase() : "";
    if (tag === "cdk-virtual-scroll-viewport") return true;
    const cs = getComputedStyle(node);
    const oy = cs.overflowY || cs.overflow || "";
    return /(auto|scroll|overlay)/.test(oy) && node.scrollHeight > node.clientHeight + 2;
  };
  let container = null;
  for (let cur = firstImg ? firstImg.parentElement : null; cur; cur = cur.parentElement) {
    if (isScrollable(cur)) {
      container = cur;
      break;
    }
  }
  if (!container) {
    container =
      document.querySelector("cdk-virtual-scroll-viewport") ||
      document.querySelector("mat-sidenav-content") ||
      document.scrollingElement ||
      document.documentElement;
  }
  if (!container) {
    return {
      found_container: false,
      moved: false,
      before_top: 0,
      after_top: 0,
      scroll_height: 0,
      client_height: 0,
      container_tag: null,
    };
  }
  const tag = container.tagName ? container.tagName.toLowerCase() : "document";
  if (reset) {
    const before = container.scrollTop || 0;
    container.scrollTop = 0;
    container.dispatchEvent(new Event("scroll", { bubbles: true }));
    return {
      found_container: true,
      moved: before !== 0,
      before_top: Math.round(before),
      after_top: Math.round(container.scrollTop || 0),
      scroll_height: Math.round(container.scrollHeight || 0),
      client_height: Math.round(container.clientHeight || 0),
      container_tag: tag,
    };
  }
  const before = container.scrollTop || 0;
  const ch = container.clientHeight || 600;
  const delta = Math.max(250, Math.floor(ch * 0.65));
  if (typeof container.scrollBy === "function") {
    container.scrollBy({ top: delta, behavior: "instant" });
  } else {
    container.scrollTop = before + delta;
  }
  container.dispatchEvent(new Event("scroll", { bubbles: true }));
  const after = container.scrollTop || 0;
  return {
    found_container: true,
    moved: after > before + 0.5,
    before_top: Math.round(before),
    after_top: Math.round(after),
    scroll_height: Math.round(container.scrollHeight || 0),
    client_height: Math.round(ch),
    container_tag: tag,
  };
}
"""

_COLLECT_TILE_INVENTORY_JS = r"""
() => {
  const uuidRegex = /[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/g;
  const isUuid = (s) => /[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/.test(s);

  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== "none" && cs.visibility !== "hidden" && r.width > 0 && r.height > 0;
  };

  const sanitizeUrl = (raw) => {
    if (!raw) return null;
    try {
      const u = new URL(raw, window.location.origin);
      const path = u.pathname.replace(uuidRegex, "<uuid>");
      return (u.host ? u.host : "") + path;
    } catch (e) {
      return String(raw).replace(uuidRegex, "<uuid>");
    }
  };

  const inHeaderOrNav = (el) => {
    return Boolean(
      el.closest(
        "flow-navigation-header, flow-tile-view-header, flow-project-nav-list, " +
        "mat-sidenav, nav, aside, [role='navigation'], flow-header-user-icon"
      )
    );
  };

  const mainRoot =
    document.querySelector("#main-content") ||
    document.querySelector("cdk-virtual-scroll-viewport") ||
    document.querySelector("mat-sidenav-content") ||
    document.querySelector("flow-project-page") ||
    document.querySelector("[role='main']") ||
    document.querySelector("main") ||
    document.body;

  const elements = [...mainRoot.querySelectorAll("*")];
  const matched = [];

  for (const el of elements) {
    if (!visible(el)) continue;
    if (inHeaderOrNav(el)) continue;
    const tag = el.tagName.toLowerCase();
    const isMediaTag = tag === "img" || tag === "video" || tag === "canvas";
    let hasUuidAttr = false;
    for (let i = 0; i < el.attributes.length; i++) {
      if (isUuid(el.attributes[i].value)) {
        hasUuidAttr = true;
        break;
      }
    }
    if (!isMediaTag && !hasUuidAttr) continue;

    const attrNames = [];
    const dataAttrs = {};
    const ariaAttrs = {};
    const foundUuids = [];

    for (let i = 0; i < el.attributes.length; i++) {
      const a = el.attributes[i];
      attrNames.push(a.name);
      if (a.name.startsWith("data-")) {
        dataAttrs[a.name] = a.value;
      }
      if (a.name.startsWith("aria-")) {
        ariaAttrs[a.name] = a.value;
      }
      const matches = a.value.match(uuidRegex);
      if (matches) {
        for (const m of matches) foundUuids.push(m);
      }
    }

    const srcVal = el.getAttribute("src");
    const hrefVal = el.getAttribute("href");
    if (srcVal) {
      const matches = srcVal.match(uuidRegex);
      if (matches) {
        for (const m of matches) foundUuids.push(m);
      }
    }
    if (hrefVal) {
      const matches = hrefVal.match(uuidRegex);
      if (matches) {
        for (const m of matches) foundUuids.push(m);
      }
    }

    const ancestors = [];
    let cur = el.parentElement;
    for (
      let depth = 0;
      cur && cur !== document.body && cur !== document.documentElement && depth < 4;
      depth++, cur = cur.parentElement
    ) {
      const pTag = cur.tagName.toLowerCase();
      const pCls =
        cur.className && typeof cur.className === "string" ? cur.className.trim() : "";
      ancestors.push(`${pTag}${pCls ? "." + pCls.split(/\s+/).filter(Boolean).join(".") : ""}`);
    }

    matched.push({
      tag,
      attribute_names: attrNames,
      data_attributes: dataAttrs,
      aria_attributes: ariaAttrs,
      role: el.getAttribute("role"),
      class: el.getAttribute("class") || "",
      src: sanitizeUrl(srcVal),
      href: sanitizeUrl(hrefVal),
      ancestors,
      found_uuids: [...new Set(foundUuids)],
    });

    if (matched.length >= 40) break;
  }

  return matched;
}
"""

_COLLECT_LEFT_NAV_FILTERS_JS = r"""
() => {
  const container = document.querySelector(
    "flow-project-nav-list, mat-nav-list, mat-sidenav, [role='navigation'], nav, aside"
  );
  if (!container) return { filters: [], selected: null };

  const ligatureOf = (el) => {
    const icons = [...el.querySelectorAll("mat-icon, i.google-symbols, i[class*='symbols']")];
    return icons.map(i => (i.textContent || "").trim()).filter(Boolean).join(",");
  };

  const btnSel = "button, a[href], [role='tab'], [role='button'], .mat-mdc-list-item";
  const buttons = [...container.querySelectorAll(btnSel)];
  const filters = [];
  let selected = null;

  for (const b of buttons) {
    const cs = getComputedStyle(b);
    const r = b.getBoundingClientRect();
    if (cs.display === "none" || cs.visibility === "hidden" || r.width === 0 || r.height === 0) {
      continue;
    }
    const cls = b.getAttribute("class") || "";
    const ariaSelected = b.getAttribute("aria-selected");
    const ariaCurrent = b.getAttribute("aria-current");
    const role = b.getAttribute("role");
    const ligature = ligatureOf(b);
    const isSelected =
      ariaSelected === "true" ||
      ariaCurrent === "page" ||
      ariaCurrent === "true" ||
      /(selected|active|activated|mat-mdc-list-item-activated)/.test(cls);

    const info = {
      tag: b.tagName.toLowerCase(),
      role,
      class: cls,
      ligature: ligature || null,
      aria_selected: ariaSelected,
      aria_current: ariaCurrent,
      is_selected: isSelected,
      text: (b.textContent || "").trim().slice(0, 40),
    };
    filters.push(info);
    if (isSelected && !selected) {
      selected = ligature || info.text || info.tag;
    }
  }

  return { filters, selected };
}
"""


# ---------------------------------------------------------------------------
# Wire recorder (batchexecute + aisandbox-pa.googleapis.com)
# ---------------------------------------------------------------------------


@dataclass
class WireRecorder:
    out_dir: Path
    targets: dict[str, str]
    step_name: str = "boot"
    events: list[dict[str, Any]] = field(default_factory=list)
    listings: list[Any] = field(default_factory=list)
    show_mask: bool = False

    def set_step(self, name: str) -> None:
        self.step_name = name
        self._append({"kind": "step", "step": name})

    def _append(self, payload: dict[str, Any]) -> None:
        entry = {"t": round(time.time(), 3), "step": self.step_name, **payload}
        self.events.append(entry)
        with (self.out_dir / "wire.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    async def on_batchexecute_route(self, route: Any, request: Any) -> None:
        body = mc.request_body(request)
        url = str(request.url)
        ids = mx.batch_rpcids(mx.url_rpcids(url), body)
        abort = should_abort_batchexecute(ids)
        frames_summary: list[dict[str, Any]] = []
        for raw_frame in mx._frames(body):  # noqa: SLF001
            rpcid = str(mx._at(raw_frame, 0))  # noqa: SLF001
            inner = mx._at(raw_frame, 1)  # noqa: SLF001
            if self.show_mask and rpcid == "pGCYOe":
                for mask_str in extract_pgcyoe_masks(inner):
                    print(mask_str, flush=True)
            shaped = shape(inner, self.targets)
            frames_summary.append(
                {
                    "rpcid": rpcid,
                    "shape": shaped,
                    "id_positions": find_id_positions(inner, self.targets),
                    "has_token_slot": has_token_slot(shaped),
                }
            )
        query = parse_qs(urlsplit(url).query)
        src = redact_url_path(query.get("source-path", [""])[0], self.targets)
        self._append(
            {
                "kind": "req",
                "ids": ids,
                "aborted": abort,
                "qkeys": sorted(query),
                "src": src,
                "frames": frames_summary,
            }
        )
        if abort:
            await route.abort()
            return
        await route.fallback()

    async def on_aisandbox_route(self, route: Any, request: Any) -> None:
        url = str(request.url)
        abort = should_abort_aisandbox(url)
        body_text = mc.request_body(request)
        parsed_body: Any = None
        if body_text:
            with contextlib.suppress(ValueError):
                parsed_body = json.loads(body_text)
        shaped = shape(parsed_body, self.targets) if parsed_body is not None else None
        positions = find_id_positions(parsed_body, self.targets) if parsed_body is not None else {}
        self._append(
            {
                "kind": "aisandbox_req",
                "method": request.method,
                "path": redact_url_path(url, self.targets),
                "aborted": abort,
                "shape": shaped,
                "id_positions": positions,
                "has_token_slot": has_token_slot(shaped),
            }
        )
        if abort:
            await route.abort()
            return
        await route.fallback()

    async def on_response(self, response: Any) -> None:
        url = str(response.url)
        if "batchexecute" in url:
            try:
                text = await response.text()
            except Exception as exc:  # noqa: BLE001
                self._append(
                    {
                        "kind": "resp_unreadable",
                        "ids": mx.url_rpcids(url),
                        "error": type(exc).__name__,
                    }
                )
                return
            resp_frames: list[dict[str, Any]] = []
            for rid, payload in parse_frames(text):
                if rid == mc.LISTING_RPC:
                    self.listings.append(payload)
                shaped = shape(payload, self.targets)
                resp_frames.append(
                    {
                        "rpcid": rid,
                        "shape": shaped,
                        "id_positions": find_id_positions(payload, self.targets),
                    }
                )
            self._append(
                {
                    "kind": "resp",
                    "status": response.status,
                    "ids": mx.url_rpcids(url),
                    "frames": resp_frames,
                    "errors": [
                        {"rpcid": e.rpcid, "code": e.code, "reasons": list(e.reasons)}
                        for e in rpc_errors(text)
                    ],
                }
            )
            return
        if "aisandbox-pa.googleapis.com" in url:
            parsed: Any = None
            with contextlib.suppress(Exception):
                parsed = json.loads(await response.text())
            self._append(
                {
                    "kind": "aisandbox_resp",
                    "status": response.status,
                    "path": redact_url_path(url, self.targets),
                    "shape": shape(parsed, self.targets) if parsed is not None else None,
                    "id_positions": (
                        find_id_positions(parsed, self.targets) if parsed is not None else {}
                    ),
                }
            )

    def step_summary(self, step_label: str, idle_ids: set[str]) -> dict[str, Any]:
        reqs = [e for e in self.events if e.get("step") == step_label and e.get("kind") == "req"]
        resps = [e for e in self.events if e.get("step") == step_label and e.get("kind") == "resp"]
        ais_reqs = [
            e
            for e in self.events
            if e.get("step") == step_label and e.get("kind") == "aisandbox_req"
        ]
        sent_raw = [rid for r in reqs if not r.get("aborted") for rid in r.get("ids", [])]
        aborted_raw = [rid for r in reqs if r.get("aborted") for rid in r.get("ids", [])]
        return {
            "rpc_ids_sent_all": list(dict.fromkeys(sent_raw)),
            "rpc_ids_sent_attributed": attribute_window_rpcs(sent_raw, idle_ids),
            "rpc_ids_aborted": list(dict.fromkeys(aborted_raw)),
            "request_shapes": [f for r in reqs for f in r.get("frames", [])],
            "response_shapes": [
                {
                    "status": resp.get("status"),
                    "frames": resp.get("frames", []),
                    "errors": resp.get("errors", []),
                }
                for resp in resps
            ],
            "aisandbox_requests": [
                {
                    "method": a.get("method"),
                    "path": a.get("path"),
                    "aborted": a.get("aborted"),
                    "shape": a.get("shape"),
                    "id_positions": a.get("id_positions"),
                }
                for a in ais_reqs
            ],
        }

    def idle_rpc_ids(self, step_label: str) -> set[str]:
        return {
            rid
            for e in self.events
            if e.get("step") == step_label and e.get("kind") == "req"
            for rid in e.get("ids", [])
        }


# ---------------------------------------------------------------------------
# Structural UI driving helpers
# ---------------------------------------------------------------------------


def _tile_selector(tile_id: str, asb_token: str = "") -> str:
    parts = [f'img[data-media-id="{tile_id}"]', f'[data-media-id="{tile_id}"]']
    if asb_token:
        parts.append(f'img[src*="/asb/{asb_token}"]')
    return ", ".join(parts)


async def _is_media_visible_in_dom(page: Any, tile_id: str, asb_token: str = "") -> bool:
    loc = page.locator(_tile_selector(tile_id, asb_token)).first
    try:
        return await loc.is_visible()
    except Exception:  # noqa: BLE001
        return False


async def _is_media_attached_and_visible(page: Any, tile_id: str, asb_token: str = "") -> bool:
    """True when ``img[data-media-id="<id>"]`` is attached and visible."""
    loc = page.locator(_tile_selector(tile_id, asb_token)).first
    try:
        if await loc.count() == 0:
            return False
        if not await loc.is_visible():
            with contextlib.suppress(Exception):
                await loc.scroll_into_view_if_needed(timeout=2_500)
        return await loc.is_visible()
    except Exception:  # noqa: BLE001
        return False


async def _is_disabled(loc: Any) -> bool:
    try:
        return bool(await loc.is_disabled() or await loc.get_attribute("aria-disabled") == "true")
    except Exception:  # noqa: BLE001
        return False


async def _first_visible_match(
    page: Any,
    selectors: tuple[str, ...],
    *,
    tried: list[str],
    timeout_ms: int = 800,
    allow_disabled: bool = False,
) -> tuple[str | None, Any | None]:
    """Try each structural selector in order; return ``(selector, locator)`` on first match."""
    for idx, sel in enumerate(selectors):
        tried.append(sel)
        wait_ms = timeout_ms if idx == 0 else min(timeout_ms, 150)
        loc = await wait_for_or_none(page, sel, wait_ms)
        if loc is not None and await loc.is_visible():
            if not allow_disabled and await _is_disabled(loc):
                continue
            return sel, loc
    return None, None


async def _expand_sidebar_if_collapsed(page: Any, tried: list[str]) -> None:
    for exp_sel in SIDEBAR_EXPAND_SELECTORS:
        exp_btn = await wait_for_or_none(page, exp_sel, 300)
        if exp_btn is not None and await exp_btn.is_visible():
            tried.append(f"expand:{exp_sel}")
            await exp_btn.click()
            await page.wait_for_timeout(450)
            break


async def _filter_and_scroll_to_tile(
    page: Any,
    *,
    tile_id: str | None = None,
    media_id: str | None = None,
    asb_token: str = "",
    media_kind: str = "image",
    max_scroll_steps: int = 80,
) -> dict[str, Any]:
    """Click the left-nav media-type filter button and scroll the grid container to the tile.

    1. Wait for the page shell to hydrate.
    2. Expand the left sidebar if collapsed, then click the left-nav filter button whose
       ligature matches *media_kind* (``image`` for images, ``videocam`` for videos, or try
       both if unknown).
    3. Scroll the scrollable ancestor of the first ``img[data-media-id]`` (typically
       ``cdk-virtual-scroll-viewport``) in overlapping viewport steps until
       ``img[data-media-id="<id>"]`` is attached and visible, or the scroll stops moving.
    """
    target_id = tile_id or media_id or ""
    await wait_for_or_none(page, SHELL_READY_SELECTOR, 12_000)

    ligatures_to_try = filter_ligatures_for_kind(media_kind)
    ligatures_tried: list[str] = []
    filter_selectors_tried: list[str] = []
    clicked_ligature: str | None = None
    matched_filter_sel: str | None = None
    total_scroll_steps = 0
    scroll_stopped = False
    container_tag: str | None = None
    last_scroll_top = 0
    last_scroll_height = 0
    tile_found = False

    await _expand_sidebar_if_collapsed(page, filter_selectors_tried)

    for ligature in ligatures_to_try:
        ligatures_tried.append(ligature)
        nav_sels = left_nav_filter_selectors(ligature)
        sel, btn = await _first_visible_match(
            page,
            nav_sels,
            tried=filter_selectors_tried,
            timeout_ms=1_500,
        )
        if btn is not None:
            clicked_ligature = ligature
            matched_filter_sel = sel
            await btn.click()
            await page.wait_for_timeout(1_500)

        # Wait briefly for the grid's initial tiles to render after any filter transition.
        await wait_for_or_none(page, "img[data-media-id], [data-media-id]", 4_000)

        if await _is_media_attached_and_visible(page, target_id, asb_token):
            tile_found = True
            meta = await page.evaluate(_SCROLL_GRID_STEP_JS, {"reset": False})
            container_tag = meta.get("container_tag")
            last_scroll_top = int(meta.get("before_top") or 0)
            last_scroll_height = int(meta.get("scroll_height") or 0)
            break

        # Reset scroll container to top before scanning downwards in steps.
        reset_info = await page.evaluate(_SCROLL_GRID_STEP_JS, {"reset": True})
        container_tag = reset_info.get("container_tag")
        last_scroll_top = int(reset_info.get("after_top") or 0)
        last_scroll_height = int(reset_info.get("scroll_height") or 0)
        if reset_info.get("moved"):
            await page.wait_for_timeout(350)
            if await _is_media_attached_and_visible(page, target_id, asb_token):
                tile_found = True
                break

        scroll_stopped = False
        for _step_idx in range(1, max_scroll_steps + 1):
            step_info = await page.evaluate(_SCROLL_GRID_STEP_JS, {"reset": False})
            total_scroll_steps += 1
            container_tag = step_info.get("container_tag") or container_tag
            last_scroll_top = int(step_info.get("after_top") or 0)
            last_scroll_height = int(step_info.get("scroll_height") or 0)
            await page.wait_for_timeout(400)

            if await _is_media_attached_and_visible(page, target_id, asb_token):
                tile_found = True
                break

            if not step_info.get("moved"):
                # Allow one extra beat at the bottom in case lazy-load extends scrollHeight.
                await page.wait_for_timeout(600)
                if await _is_media_attached_and_visible(page, target_id, asb_token):
                    tile_found = True
                    break
                retry_info = await page.evaluate(_SCROLL_GRID_STEP_JS, {"reset": False})
                if not retry_info.get("moved"):
                    scroll_stopped = True
                    break
                total_scroll_steps += 1
                last_scroll_top = int(retry_info.get("after_top") or 0)
                last_scroll_height = int(retry_info.get("scroll_height") or 0)
                await page.wait_for_timeout(400)
                if await _is_media_attached_and_visible(page, target_id, asb_token):
                    tile_found = True
                    break

        if tile_found:
            break

    return {
        "media_kind": media_kind,
        "filter_ligatures_tried": ligatures_tried,
        "filter_selectors_tried": filter_selectors_tried,
        "filter_ligature_clicked": clicked_ligature,
        "filter_selector_matched": matched_filter_sel,
        "scroll_container_tag": container_tag,
        "scroll_steps": total_scroll_steps,
        "scroll_stopped": scroll_stopped,
        "last_scroll_top": last_scroll_top,
        "scroll_height": last_scroll_height,
        "tile_found": tile_found,
    }


async def _is_menu_open(page: Any) -> bool:
    menu_loc = page.locator(
        ".cdk-overlay-pane [role='menuitem'], [role='menu'] [role='menuitem'], "
        ".cdk-overlay-pane [role='menu']"
    ).first
    try:
        return await menu_loc.count() > 0 and await menu_loc.is_visible()
    except Exception:  # noqa: BLE001
        return False


async def _open_tile_overflow_or_context_menu(
    page: Any,
    *,
    tile: Any,
    tile_id: str | None = None,
    media_id: str | None = None,
    tried_triggers: list[str],
) -> tuple[str | None, bool]:
    """Open the hovered tile's overflow menu (or right-click context menu).

    Returns ``(matched_trigger_selector, menu_opened)``.
    """
    target_id = tile_id or media_id or ""
    overflow_candidates = (
        *tile_scoped_selectors(target_id, TILE_OVERFLOW_INNER_SELECTORS),
        *NON_HEADER_OVERFLOW_SELECTORS,
    )
    trig_sel, trig_loc = await _first_visible_match(
        page, overflow_candidates, tried=tried_triggers, timeout_ms=800
    )
    if trig_loc is not None:
        await trig_loc.click()
        await page.wait_for_timeout(600)
        if await _is_menu_open(page):
            return trig_sel, True
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)

    tried_triggers.append("tile.click(button='right')")
    await tile.click(button="right")
    await page.wait_for_timeout(600)
    if await _is_menu_open(page):
        return "tile.click(button='right')", True
    return trig_sel, False


async def _probe_tile_menu_dry_run(
    page: Any,
    *,
    tile_id: str | None = None,
    media_id: str | None = None,
    asb_token: str,
) -> dict[str, Any]:
    """Hover the tile, open its overflow/context menu, record menu DOM inventory, and Escape."""
    target_id = tile_id or media_id or ""
    tried_triggers: list[str] = []
    tried_actions: list[str] = []
    tile_sel = _tile_selector(target_id, asb_token)
    tile = await wait_for_or_none(page, tile_sel, 5_000)
    if tile is None:
        return {
            "tile_found": False,
            "tile_context": await page.evaluate(_TILE_CONTEXT_JS, target_id),
            "menu_opened": False,
            "trigger_tried": tried_triggers,
            "trigger_matched": None,
            "direct_trash_button_matched": None,
            "candidate_trash_menuitem_matched": None,
            "menu_items": [],
            "overlay_buttons": [],
            "dom_inventory": await page.evaluate(_STRUCTURAL_INVENTORY_JS),
            "escaped": False,
        }

    await tile.scroll_into_view_if_needed(timeout=5_000)
    await tile.hover(timeout=5_000)
    await page.wait_for_timeout(500)
    tile_context = await page.evaluate(_TILE_CONTEXT_JS, target_id)

    scoped_direct = tile_scoped_selectors(target_id, TRASH_BUTTON_SELECTORS)
    direct_trash_sel, _ = await _first_visible_match(
        page, scoped_direct, tried=tried_actions, timeout_ms=400
    )

    matched_trigger, menu_opened = await _open_tile_overflow_or_context_menu(
        page, tile=tile, tile_id=target_id, tried_triggers=tried_triggers
    )
    dom_inventory = await page.evaluate(_STRUCTURAL_INVENTORY_JS)
    candidate_trash_sel, _ = await _first_visible_match(
        page,
        TRASH_MENUITEM_SELECTORS,
        tried=tried_actions,
        timeout_ms=400,
        allow_disabled=True,
    )

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)

    return {
        "tile_found": True,
        "tile_context": tile_context,
        "menu_opened": menu_opened,
        "trigger_tried": tried_triggers,
        "trigger_matched": matched_trigger,
        "direct_trash_button_matched": direct_trash_sel,
        "candidate_trash_menuitem_matched": candidate_trash_sel,
        "menu_items": dom_inventory.get("menuItems", []),
        "overlay_buttons": dom_inventory.get("overlayButtons", []),
        "dom_inventory": dom_inventory,
        "escaped": True,
    }


async def _confirm_dialog_if_present(
    page: Any,
    *,
    tried: list[str],
) -> tuple[str | None, dict[str, Any] | None]:
    """If a confirmation dialog opened, record its structural buttons and click primary."""
    dialog = await wait_for_or_none(page, DIALOG_CONTAINER_SELECTOR, 2_500)
    if dialog is None or not await dialog.is_visible():
        return None, None
    inventory = await page.evaluate(_STRUCTURAL_INVENTORY_JS)
    matched_sel, btn = await _first_visible_match(
        page, DIALOG_CONFIRM_SELECTORS, tried=tried, timeout_ms=1_000
    )
    if btn is not None:
        await btn.click()
        await page.wait_for_timeout(1_500)
    return matched_sel, {"dialog_buttons": inventory.get("dialogButtons", [])}


async def _trigger_tile_action(
    page: Any,
    *,
    tile_id: str | None = None,
    media_id: str | None = None,
    asb_token: str,
    menuitem_selectors: tuple[str, ...],
    direct_button_selectors: tuple[str, ...],
) -> dict[str, Any]:
    """Open the item's menu or toolbar structurally and click the target action (Trash/Restore)."""
    target_id = tile_id or media_id or ""
    tried_triggers: list[str] = []
    tried_actions: list[str] = []
    tried_dialogs: list[str] = []
    matched: dict[str, str | None] = {
        "trigger": None,
        "action": None,
        "confirm_dialog": None,
    }
    tile_sel = _tile_selector(target_id, asb_token)
    tile = await wait_for_or_none(page, tile_sel, 5_000)
    if tile is None:
        return {
            "tile_found": False,
            "tile_context": await page.evaluate(_TILE_CONTEXT_JS, target_id),
            "anchors_tried": {
                "trigger": tried_triggers,
                "action": tried_actions,
                "confirm_dialog": tried_dialogs,
            },
            "anchors_matched": matched,
            "dom_inventory": await page.evaluate(_STRUCTURAL_INVENTORY_JS),
        }

    await tile.scroll_into_view_if_needed(timeout=5_000)
    await tile.hover(timeout=5_000)
    await page.wait_for_timeout(500)
    tile_context = await page.evaluate(_TILE_CONTEXT_JS, target_id)

    # Strategy 1: direct action button visible inside the hovered tile card.
    scoped_direct = tile_scoped_selectors(target_id, direct_button_selectors)
    action_sel, action_loc = await _first_visible_match(
        page, scoped_direct, tried=tried_actions, timeout_ms=500
    )
    if action_loc is not None:
        matched["trigger"] = "hover_tile_direct"
        matched["action"] = action_sel

    # Strategy 2: tile-scoped (or non-header) overflow menu button, then menuitem with ligature.
    if action_loc is None:
        overflow_candidates = (
            *tile_scoped_selectors(target_id, TILE_OVERFLOW_INNER_SELECTORS),
            *NON_HEADER_OVERFLOW_SELECTORS,
        )
        trig_sel, trig_loc = await _first_visible_match(
            page, overflow_candidates, tried=tried_triggers, timeout_ms=800
        )
        if trig_loc is not None:
            await trig_loc.click()
            await page.wait_for_timeout(600)
            action_sel, action_loc = await _first_visible_match(
                page, menuitem_selectors, tried=tried_actions, timeout_ms=1_200
            )
            if action_loc is not None:
                matched["trigger"] = trig_sel
                matched["action"] = action_sel
            else:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)

    # Strategy 3: right-click context menu on the tile, then menuitem with ligature.
    if action_loc is None:
        tried_triggers.append("tile.click(button='right')")
        await tile.click(button="right")
        await page.wait_for_timeout(600)
        action_sel, action_loc = await _first_visible_match(
            page, menuitem_selectors, tried=tried_actions, timeout_ms=1_200
        )
        if action_loc is not None:
            matched["trigger"] = "tile.click(button='right')"
            matched["action"] = action_sel
        else:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)

    # Strategy 4: click the tile (opens detail view in project grid, or selects tile in Trash
    # view enabling flow-trash-action-bar's undo button), then try enabled direct button or menu.
    if action_loc is None:
        tried_triggers.append("tile.click() -> detail_or_select")
        await tile.click()
        await page.wait_for_timeout(1_200)
        action_sel, action_loc = await _first_visible_match(
            page, direct_button_selectors, tried=tried_actions, timeout_ms=1_200
        )
        if action_loc is not None:
            matched["trigger"] = "tile.click() -> direct_button"
            matched["action"] = action_sel
        else:
            trig_sel, trig_loc = await _first_visible_match(
                page, NON_HEADER_OVERFLOW_SELECTORS, tried=tried_triggers, timeout_ms=1_000
            )
            if trig_loc is not None:
                await trig_loc.click()
                await page.wait_for_timeout(600)
                action_sel, action_loc = await _first_visible_match(
                    page, menuitem_selectors, tried=tried_actions, timeout_ms=1_200
                )
                if action_loc is not None:
                    matched["trigger"] = f"detail_view -> {trig_sel}"
                    matched["action"] = action_sel

    pre_click_inventory = await page.evaluate(_STRUCTURAL_INVENTORY_JS)
    dialog_meta: dict[str, Any] | None = None
    if action_loc is not None:
        await action_loc.click()
        await page.wait_for_timeout(1_200)
        dialog_sel, dialog_meta = await _confirm_dialog_if_present(page, tried=tried_dialogs)
        matched["confirm_dialog"] = dialog_sel
        await page.wait_for_timeout(2_000)

    return {
        "tile_found": True,
        "tile_context": tile_context,
        "anchors_tried": {
            "trigger": tried_triggers,
            "action": tried_actions,
            "confirm_dialog": tried_dialogs,
        },
        "anchors_matched": matched,
        "dialog_observation": dialog_meta,
        "dom_inventory": pre_click_inventory,
    }


async def _navigate_to_trash_view(
    page: Any,
    *,
    project_id: str,
    targets: dict[str, str],
) -> dict[str, Any]:
    """Navigate to the project's Trash view via structural nav anchor (or URL fallback)."""
    tried: list[str] = []
    await _expand_sidebar_if_collapsed(page, tried)

    matched_sel, nav_loc = await _first_visible_match(
        page, TRASH_NAV_SELECTORS, tried=tried, timeout_ms=1_000
    )
    if nav_loc is not None:
        await nav_loc.click()
        await page.wait_for_timeout(2_500)
    else:
        trash_url = f"{mc.MIGRATED_PROJECT_URL.format(project_id=project_id)}/trash"
        tried.append("goto:/project/<project_id>/trash")
        await page.goto(trash_url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3_000)
        matched_sel = "goto:/project/<project_id>/trash"

    return {
        "anchors_tried": tried,
        "anchor_matched": matched_sel,
        "url_reached": redact_url_path(page.url, targets),
        "dom_inventory": await page.evaluate(_STRUCTURAL_INVENTORY_JS),
    }


async def _navigate_to_project_grid(
    page: Any,
    *,
    project_id: str,
    targets: dict[str, str],
) -> dict[str, Any]:
    """Return from Trash view to the main project grid."""
    tried: list[str] = []
    matched_sel, nav_loc = await _first_visible_match(
        page, GRID_NAV_SELECTORS, tried=tried, timeout_ms=1_000
    )
    if nav_loc is not None:
        await nav_loc.click()
        await page.wait_for_timeout(2_500)
    if "/trash" in page.url or f"/project/{project_id}" not in page.url:
        tried.append("goto:/project/<project_id>")
        await page.goto(
            mc.MIGRATED_PROJECT_URL.format(project_id=project_id),
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        await page.wait_for_timeout(3_000)
        matched_sel = "goto:/project/<project_id>"
    return {
        "anchors_tried": tried,
        "anchor_matched": matched_sel,
        "url_reached": redact_url_path(page.url, targets),
    }


# ---------------------------------------------------------------------------
# Catalog listing & pre-flight safety checks
# ---------------------------------------------------------------------------


def _list_candidate_images(profile: str | None, project_id: str | None) -> list[ImageRow]:
    db_path = get_settings().resolved_db_path()
    rows = list_images(db_path=db_path, profile=profile, limit=50, offset=0)
    if project_id:
        rows = [r for r in rows if r.project_id == project_id]
    return rows


def _print_candidates_and_refuse(
    *,
    profile: str | None,
    project_id: str | None,
    reason: str,
) -> int:
    print(f"[spike-1363] REFUSED: {reason}", file=sys.stderr, flush=True)
    try:
        rows = _list_candidate_images(profile, project_id)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[spike-1363] Could not query local catalog: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2

    if not rows:
        scope = f"profile={profile!r} project={project_id!r}"
        print(
            f"[spike-1363] No cataloged images found for {scope}. "
            "Pass --profile <name> for a profile that has cataloged images.",
            file=sys.stderr,
            flush=True,
        )
        return 2

    print(
        "\nNewest cataloged IMAGE candidates (0 credits; newest first):",
        file=sys.stderr,
        flush=True,
    )
    for idx, r in enumerate(rows[:10], 1):
        marker = " <-- newest disposable image" if idx == 1 else ""
        created = r.created_at.strftime("%Y-%m-%d %H:%M")
        print(
            f"  {idx:2d}. media_id={r.media_id}  project_id={r.project_id}  "
            f"profile={r.profile}  created={created}  model={r.model}{marker}",
            file=sys.stderr,
            flush=True,
        )
    newest = rows[0]
    print(
        "\nRe-run with --media-id and --yes-trash-this (add --dry-run to probe menu first):\n"
        f"  uv run python scripts/dev/spike_1363_trash.py --profile {newest.profile} "
        f"--project {newest.project_id} --media-id {newest.media_id} --yes-trash-this --dry-run",
        file=sys.stderr,
        flush=True,
    )
    return 2


# ---------------------------------------------------------------------------
# Main live spike execution
# ---------------------------------------------------------------------------


async def _extract_tile_asb_token(page: Any, tile_id: str) -> str:
    tile_loc = page.locator(_tile_selector(tile_id)).first
    if await tile_loc.count() == 0:
        return ""
    src_attr = await tile_loc.get_attribute("src")
    if not src_attr:
        img_child = tile_loc.locator("img").first
        if await img_child.count() > 0:
            src_attr = await img_child.get_attribute("src")
    return extract_asb_token(src_attr)


async def _run_live(
    *,
    profile: str,
    project_id: str,
    media_id: str,
    workflow_id: str,
    media_kind: str,
    out_dir: Path,
    dry_run: bool = False,
    show_mask: bool = False,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = resolve_profile_dir(profile)
    targets = {
        "media_id": media_id,
        "project_id": project_id,
        "workflow_id": workflow_id,
    }
    recorder = WireRecorder(out_dir=out_dir, targets=targets, show_mask=show_mask)
    timings_ms: dict[str, float] = {}
    t_run_start = time.monotonic()

    item_may_be_trashed = False
    restored_cleanly = False
    trash_url = f"https://flow.google.com/project/{project_id}/trash"
    host_served = "unknown"
    asb_token = ""
    resolved_kind = media_kind
    resolved_media_id = media_id
    tile_id = workflow_id or ""
    tile_id_source: str | None = None

    project_filter_scroll: dict[str, Any] = {}
    trash_filter_scroll: dict[str, Any] = {}
    verify_filter_scroll: dict[str, Any] = {}
    dry_run_menu_info: dict[str, Any] = {}
    trash_action_info: dict[str, Any] = {}
    trash_nav_info: dict[str, Any] = {}
    restore_action_info: dict[str, Any] = {}
    grid_return_info: dict[str, Any] = {}
    observed_in_unfiltered_top_grid = False
    observed_in_initial_grid = False
    observed_gone_from_grid = False
    observed_in_trash_view = False
    observed_gone_from_trash = False
    observed_back_in_grid = False
    run_error: str | None = None

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        ctx = page.context
        await ctx.route("**/batchexecute**", recorder.on_batchexecute_route)
        await ctx.route("https://aisandbox-pa.googleapis.com/**", recorder.on_aisandbox_route)
        ctx.on("response", recorder.on_response)

        try:
            # 1. Open the project, apply left-nav media-type filter, and scroll grid to the tile
            t0 = time.monotonic()
            recorder.set_step("project_open")
            step("1", f"Opening project {project_id[:8]}…", prefix="1363")
            await page.goto(
                mc.MIGRATED_PROJECT_URL.format(project_id=project_id),
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await raise_if_known_landing(page, requested=f"project {project_id}", at="spike_1363")
            host_served = urlsplit(page.url).netloc or "unknown"
            await wait_for_or_none(page, SHELL_READY_SELECTOR, 12_000)
            await page.wait_for_timeout(1_500)

            # Resolve the tile id before any DOM lookup
            tile_already_visible = await _is_media_visible_in_dom(page, media_id)
            resolution = resolve_tile_id(
                media_id,
                recorder.listings,
                catalog_workflow_id=workflow_id or None,
                already_matches_tile=tile_already_visible,
            )
            if resolution is None:
                msg = (
                    f"[spike-1363] REFUSED: could not resolve tile id for --media-id {media_id}: "
                    "not found in Zzl0ze listing and no catalog flow_workflow_id (nothing clicked)."
                )
                print(msg, file=sys.stderr, flush=True)
                run_error = msg
                timings_ms["project_open"] = round((time.monotonic() - t0) * 1000, 1)
                timings_ms["total"] = round((time.monotonic() - t_run_start) * 1000, 1)
                summary_doc = {
                    "spike": "1363-media-trash-and-restore",
                    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "host_served": host_served,
                    "dry_run": dry_run,
                    "media_kind": resolved_kind,
                    "media_id": media_id,
                    "tile_id": None,
                    "tile_id_source": None,
                    "positive_control": {
                        "zzl0ze_seen": bool(recorder.listings),
                        "media_in_initial_listing": False,
                        "media_in_unfiltered_top_grid": False,
                        "media_in_initial_grid": False,
                        "filter_ligature_clicked": None,
                        "scroll_steps": 0,
                        "filter_scroll": {},
                    },
                    "timings_ms": timings_ms,
                    "restored_cleanly": False,
                    "manual_restore_needed": False,
                    "error": run_error,
                }
                summary_path = out_dir / "summary.json"
                summary_path.write_text(
                    json.dumps(summary_doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                step("out", f"summary -> {summary_path}", prefix="1363")
                return 1

            resolved_media_id = resolution.media_id
            tile_id = resolution.tile_id
            tile_id_source = resolution.tile_id_source
            targets["media_id"] = resolved_media_id
            targets["workflow_id"] = tile_id
            recorder.targets = targets

            step(
                "1a",
                f"Resolved tile id {tile_id[:8]}… from {tile_id_source} "
                f"(media_id: {resolved_media_id[:8]}…)",
                prefix="1363",
            )

            observed_in_unfiltered_top_grid = await _is_media_visible_in_dom(page, tile_id)
            if resolved_kind == "unknown" and recorder.listings:
                clip = mx.source_record(recorder.listings[0], resolved_media_id)
                if clip is not None and clip.kind in ("image", "video"):
                    resolved_kind = clip.kind

            step(
                "1b",
                f"Filtering left-nav ({resolved_kind}) and scrolling grid to {tile_id[:8]}…",
                prefix="1363",
            )
            project_filter_scroll = await _filter_and_scroll_to_tile(
                page,
                tile_id=tile_id,
                asb_token=asb_token,
                media_kind=resolved_kind,
            )
            if not project_filter_scroll.get("tile_found"):
                await page.reload(wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(1_500)
                project_filter_scroll = await _filter_and_scroll_to_tile(
                    page,
                    tile_id=tile_id,
                    asb_token=asb_token,
                    media_kind=resolved_kind,
                )

            observed_in_initial_grid = bool(project_filter_scroll.get("tile_found"))
            if observed_in_initial_grid:
                asb_token = await _extract_tile_asb_token(page, tile_id)
            timings_ms["project_open"] = round((time.monotonic() - t0) * 1000, 1)

            if dry_run:
                t0 = time.monotonic()
                recorder.set_step("dry_run_menu")
                step(
                    "2",
                    f"Dry-run: opening menu for tile {tile_id[:8]}… (no Trash click)",
                    prefix="1363",
                )
                dry_run_menu_info = await _probe_tile_menu_dry_run(
                    page,
                    tile_id=tile_id,
                    asb_token=asb_token,
                )
                timings_ms["dry_run_menu"] = round((time.monotonic() - t0) * 1000, 1)
            else:
                # 2. Idle baseline window before Trash
                t0 = time.monotonic()
                recorder.set_step("idle_before_trash")
                await page.wait_for_timeout(3_000)
                timings_ms["idle_before_trash"] = round((time.monotonic() - t0) * 1000, 1)

                # 3. Step (a) & (b): Open menu/toolbar on the item in grid, click Trash, confirm
                t0 = time.monotonic()
                recorder.set_step("trash")
                step(
                    "2",
                    f"Moving media {resolved_media_id[:8]}… (tile {tile_id[:8]}…) to Trash",
                    prefix="1363",
                )
                if not await _is_media_attached_and_visible(page, tile_id, asb_token):
                    project_filter_scroll = await _filter_and_scroll_to_tile(
                        page,
                        tile_id=tile_id,
                        asb_token=asb_token,
                        media_kind=resolved_kind,
                    )
                item_may_be_trashed = True
                trash_action_info = await _trigger_tile_action(
                    page,
                    tile_id=tile_id,
                    asb_token=asb_token,
                    menuitem_selectors=TRASH_MENUITEM_SELECTORS,
                    direct_button_selectors=TRASH_BUTTON_SELECTORS,
                )
                trash_action_info["filter_scroll"] = project_filter_scroll
                trash_action_info["scroll_steps"] = project_filter_scroll.get("scroll_steps", 0)
                timings_ms["trash"] = round((time.monotonic() - t0) * 1000, 1)

                # If we clicked from inside the detail view, return to the project grid to observe
                matched_trigger = str(
                    (trash_action_info.get("anchors_matched") or {}).get("trigger") or ""
                )
                if "detail" in matched_trigger and "/edit/" in page.url:
                    await page.goto(
                        mc.MIGRATED_PROJECT_URL.format(project_id=project_id),
                        wait_until="domcontentloaded",
                        timeout=45_000,
                    )
                    await page.wait_for_timeout(2_500)

                observed_gone_from_grid = not await _is_media_visible_in_dom(
                    page, tile_id, asb_token
                )
                if trash_action_info.get("anchors_matched", {}).get("action") is None:
                    # No trash action was clicked at all, so the item was not moved.
                    item_may_be_trashed = False

                # 4. Step (c): Navigate to Trash view, filter + scroll, and observe item present
                t0 = time.monotonic()
                recorder.set_step("trash_view_nav")
                step("3", "Navigating to Trash view and filtering/scrolling to tile", prefix="1363")
                trash_nav_info = await _navigate_to_trash_view(
                    page, project_id=project_id, targets=targets
                )
                trash_filter_scroll = await _filter_and_scroll_to_tile(
                    page,
                    tile_id=tile_id,
                    asb_token=asb_token,
                    media_kind=resolved_kind,
                )
                trash_nav_info["filter_scroll"] = trash_filter_scroll
                trash_nav_info["scroll_steps"] = trash_filter_scroll.get("scroll_steps", 0)
                observed_in_trash_view = bool(trash_filter_scroll.get("tile_found"))
                timings_ms["trash_view_nav"] = round((time.monotonic() - t0) * 1000, 1)

                # 5. Idle baseline window in Trash view before Restore
                t0 = time.monotonic()
                recorder.set_step("idle_before_restore")
                await page.wait_for_timeout(2_000)
                timings_ms["idle_before_restore"] = round((time.monotonic() - t0) * 1000, 1)

                # 6. Step (d): Restore the item from the Trash view
                t0 = time.monotonic()
                recorder.set_step("restore")
                step(
                    "4",
                    f"Restoring media {resolved_media_id[:8]}… (tile {tile_id[:8]}…) from Trash",
                    prefix="1363",
                )
                if not await _is_media_attached_and_visible(page, tile_id, asb_token):
                    trash_filter_scroll = await _filter_and_scroll_to_tile(
                        page,
                        tile_id=tile_id,
                        asb_token=asb_token,
                        media_kind=resolved_kind,
                    )
                restore_action_info = await _trigger_tile_action(
                    page,
                    tile_id=tile_id,
                    asb_token=asb_token,
                    menuitem_selectors=RESTORE_MENUITEM_SELECTORS,
                    direct_button_selectors=RESTORE_BUTTON_SELECTORS,
                )
                restore_action_info["filter_scroll"] = trash_filter_scroll
                restore_action_info["scroll_steps"] = trash_filter_scroll.get("scroll_steps", 0)
                observed_gone_from_trash = not await _is_media_visible_in_dom(
                    page, tile_id, asb_token
                )
                timings_ms["restore"] = round((time.monotonic() - t0) * 1000, 1)

                # 7. Step (e): Return to project grid, filter + scroll, and observe item back
                t0 = time.monotonic()
                recorder.set_step("verify_back_in_grid")
                step("5", "Verifying media returned to the project grid", prefix="1363")
                grid_return_info = await _navigate_to_project_grid(
                    page, project_id=project_id, targets=targets
                )
                verify_filter_scroll = await _filter_and_scroll_to_tile(
                    page,
                    tile_id=tile_id,
                    asb_token=asb_token,
                    media_kind=resolved_kind,
                )
                if not verify_filter_scroll.get("tile_found"):
                    await page.reload(wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(1_500)
                    verify_filter_scroll = await _filter_and_scroll_to_tile(
                        page,
                        tile_id=tile_id,
                        asb_token=asb_token,
                        media_kind=resolved_kind,
                    )
                grid_return_info["filter_scroll"] = verify_filter_scroll
                grid_return_info["scroll_steps"] = verify_filter_scroll.get("scroll_steps", 0)
                observed_back_in_grid = bool(verify_filter_scroll.get("tile_found"))
                if observed_back_in_grid:
                    item_may_be_trashed = False
                    restored_cleanly = True
                timings_ms["verify_back_in_grid"] = round((time.monotonic() - t0) * 1000, 1)

        except BaseException as exc:
            run_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            raise
        finally:
            # Emergency restore safeguard if an exception occurred or restore did not confirm
            if item_may_be_trashed:
                recorder.set_step("emergency_restore")
                step("!", "Running emergency restore safeguard…", prefix="1363")
                with contextlib.suppress(BaseException):
                    if "/trash" not in page.url:
                        await page.goto(trash_url, wait_until="domcontentloaded", timeout=30_000)
                        await page.wait_for_timeout(2_500)
                    await _filter_and_scroll_to_tile(
                        page,
                        tile_id=tile_id,
                        asb_token=asb_token,
                        media_kind=resolved_kind,
                    )
                    await _trigger_tile_action(
                        page,
                        tile_id=tile_id,
                        asb_token=asb_token,
                        menuitem_selectors=RESTORE_MENUITEM_SELECTORS,
                        direct_button_selectors=RESTORE_BUTTON_SELECTORS,
                    )
                    await page.goto(
                        mc.MIGRATED_PROJECT_URL.format(project_id=project_id),
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    em_verify = await _filter_and_scroll_to_tile(
                        page,
                        tile_id=tile_id,
                        asb_token=asb_token,
                        media_kind=resolved_kind,
                    )
                    if em_verify.get("tile_found"):
                        item_may_be_trashed = False
                        restored_cleanly = True
                        observed_back_in_grid = True

            with contextlib.suppress(BaseException):
                await ctx.unroute("**/batchexecute**", recorder.on_batchexecute_route)
                await ctx.unroute(
                    "https://aisandbox-pa.googleapis.com/**", recorder.on_aisandbox_route
                )
                ctx.remove_listener("response", recorder.on_response)
            client._checkin_page(page)  # noqa: SLF001

            timings_ms["total"] = round((time.monotonic() - t_run_start) * 1000, 1)
            idle_trash = recorder.idle_rpc_ids("idle_before_trash")
            idle_restore = recorder.idle_rpc_ids("idle_before_restore") | idle_trash
            initial_listing_matches = any(
                bool(find_id_positions(listing, {"media_id": resolved_media_id}).get("media_id"))
                or bool(find_id_positions(listing, {"workflow_id": tile_id}).get("workflow_id"))
                for listing in recorder.listings[:1]
            )
            summary_doc: dict[str, Any] = {
                "spike": "1363-media-trash-and-restore",
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "host_served": host_served,
                "dry_run": dry_run,
                "media_kind": resolved_kind,
                "media_id": resolved_media_id,
                "tile_id": tile_id,
                "tile_id_source": tile_id_source,
                "positive_control": {
                    "zzl0ze_seen": bool(recorder.listings),
                    "media_in_initial_listing": initial_listing_matches,
                    "media_in_unfiltered_top_grid": observed_in_unfiltered_top_grid,
                    "media_in_initial_grid": observed_in_initial_grid,
                    "filter_ligature_clicked": project_filter_scroll.get("filter_ligature_clicked"),
                    "scroll_steps": project_filter_scroll.get("scroll_steps", 0),
                    "filter_scroll": project_filter_scroll,
                },
            }
            if dry_run:
                summary_doc["dry_run_menu"] = {
                    **recorder.step_summary("dry_run_menu", set()),
                    **dry_run_menu_info,
                    "filter_scroll": project_filter_scroll,
                    "scroll_steps": project_filter_scroll.get("scroll_steps", 0),
                }
            else:
                summary_doc["trash"] = {
                    **recorder.step_summary("trash", idle_trash),
                    **trash_action_info,
                    "observed_gone_from_grid": observed_gone_from_grid,
                    "observed_in_trash_view": observed_in_trash_view,
                }
                summary_doc["trash_view_nav"] = {
                    **recorder.step_summary("trash_view_nav", idle_trash),
                    **trash_nav_info,
                }
                summary_doc["restore"] = {
                    **recorder.step_summary("restore", idle_restore),
                    **restore_action_info,
                    "observed_gone_from_trash": observed_gone_from_trash,
                    "observed_back_in_grid": observed_back_in_grid,
                }
                summary_doc["verify_back_in_grid"] = {
                    **recorder.step_summary("verify_back_in_grid", idle_restore),
                    **grid_return_info,
                    "observed_back_in_grid": observed_back_in_grid,
                }
            summary_doc["timings_ms"] = timings_ms
            summary_doc["restored_cleanly"] = restored_cleanly
            summary_doc["manual_restore_needed"] = item_may_be_trashed
            summary_doc["error"] = run_error

            summary_path = out_dir / "summary.json"
            summary_path.write_text(
                json.dumps(summary_doc, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            step("out", f"summary -> {summary_path}", prefix="1363")
            if item_may_be_trashed:
                print(
                    f"\n[spike-1363] MANUAL RESTORE REQUIRED: media_id={resolved_media_id} "
                    f"(tile_id={tile_id}) may still be in Trash.\n"
                    f"[spike-1363] Open Trash view and restore by hand: {trash_url}",
                    file=sys.stderr,
                    flush=True,
                )

    if dry_run:
        return 0 if dry_run_menu_info.get("tile_found") else 1
    return 0 if restored_cleanly else 1


async def _run_inventory(
    *,
    profile: str,
    project_id: str,
    out_dir: Path,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = resolve_profile_dir(profile)
    targets = {
        "media_id": "",
        "project_id": project_id,
        "workflow_id": "",
    }
    recorder = WireRecorder(out_dir=out_dir, targets=targets)
    timings_ms: dict[str, float] = {}
    t_run_start = time.monotonic()
    host_served = "unknown"
    run_error: str | None = None

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        ctx = page.context
        await ctx.route("**/batchexecute**", recorder.on_batchexecute_route)
        await ctx.route("https://aisandbox-pa.googleapis.com/**", recorder.on_aisandbox_route)
        ctx.on("response", recorder.on_response)

        try:
            t0 = time.monotonic()
            recorder.set_step("project_open")
            step("1", f"Opening project {project_id[:8]}… (inventory mode)", prefix="1363")
            await page.goto(
                mc.MIGRATED_PROJECT_URL.format(project_id=project_id),
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await raise_if_known_landing(page, requested=f"project {project_id}", at="spike_1363")
            host_served = urlsplit(page.url).netloc or "unknown"
            await wait_for_or_none(page, SHELL_READY_SELECTOR, 12_000)
            await wait_for_or_none(
                page,
                (
                    "cdk-virtual-scroll-viewport, flow-grid-tile-container, "
                    "flow-image-tile, img, video, canvas"
                ),
                8_000,
            )
            await page.wait_for_timeout(1_500)
            timings_ms["project_open"] = round((time.monotonic() - t0) * 1000, 1)

            t0 = time.monotonic()
            recorder.set_step("inventory_inspection")
            step(
                "2",
                "Inspecting visible tiles, left-nav filters, and virtual scroller…",
                prefix="1363",
            )

            nav_filters_data: dict[str, Any] = await page.evaluate(_COLLECT_LEFT_NAV_FILTERS_JS)
            raw_initial_tiles: list[dict[str, Any]] = await page.evaluate(
                _COLLECT_TILE_INVENTORY_JS
            )

            listing_id_maps = extract_listing_ids(recorder.listings)
            initial_tiles: list[dict[str, Any]] = []
            for tile in raw_initial_tiles:
                found_uuids = tile.pop("found_uuids", [])
                matches: list[dict[str, str]] = []
                matched_field: str | None = None
                for u in found_uuids:
                    field_name = classify_uuid(u, listing_id_maps)
                    if field_name:
                        matches.append({"uuid": u, "field": field_name})
                        if not matched_field or field_name == "media id":
                            matched_field = field_name
                tile["src"] = sanitize_url_path(tile.get("src"))
                tile["href"] = sanitize_url_path(tile.get("href"))
                tile["listing_matches"] = matches
                tile["matched_listing_field"] = matched_field
                initial_tiles.append(tile)

            viewport_loc = page.locator("cdk-virtual-scroll-viewport").first
            has_viewport = await viewport_loc.count() > 0 and await viewport_loc.is_visible()
            scroller_loc = (
                viewport_loc
                if has_viewport
                else page.locator("mat-sidenav-content, #main-content, main, [role='main']").first
            )
            box = await scroller_loc.bounding_box()
            before_scroll_top = int(
                await scroller_loc.evaluate("el => Math.round(el.scrollTop || 0)")
            )
            if box:
                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

            wheel_steps = 3
            delta_y = 500
            for _ in range(wheel_steps):
                await page.mouse.wheel(0, delta_y)
                await page.wait_for_timeout(600)

            after_scroll_top = int(
                await scroller_loc.evaluate("el => Math.round(el.scrollTop || 0)")
            )
            scroll_top_changed = after_scroll_top != before_scroll_top
            scroll_delta = after_scroll_top - before_scroll_top

            raw_after_tiles: list[dict[str, Any]] = await page.evaluate(_COLLECT_TILE_INVENTORY_JS)

            def _tile_fingerprint(t: dict[str, Any]) -> str:
                tag = t.get("tag", "")
                src = t.get("src") or ""
                attrs = tuple(sorted(t.get("data_attributes", {}).items()))
                return f"{tag}:{src}:{attrs}"

            before_fps = {_tile_fingerprint(t) for t in raw_initial_tiles}
            after_fps = {_tile_fingerprint(t) for t in raw_after_tiles}
            new_fps = after_fps - before_fps
            new_tiles_attached = len(new_fps) > 0

            scroller_wheel_test = {
                "viewport_found": bool(has_viewport),
                "viewport_tag": (
                    await scroller_loc.evaluate("el => el.tagName.toLowerCase()")
                    if await scroller_loc.count() > 0
                    else "unknown"
                ),
                "steps": wheel_steps,
                "delta_y_per_step": delta_y,
                "before_scroll_top": before_scroll_top,
                "after_scroll_top": after_scroll_top,
                "scroll_top_changed": scroll_top_changed,
                "scroll_delta": scroll_delta,
                "before_tiles_count": len(raw_initial_tiles),
                "after_tiles_count": len(raw_after_tiles),
                "new_tiles_attached": new_tiles_attached,
                "new_tiles_count": len(new_fps),
            }
            timings_ms["inventory_inspection"] = round((time.monotonic() - t0) * 1000, 1)

            timings_ms["total"] = round((time.monotonic() - t_run_start) * 1000, 1)
            summary_doc: dict[str, Any] = {
                "spike": "1363-media-trash-and-restore",
                "mode": "inventory",
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "host_served": host_served,
                "project_id": project_id,
                "profile": profile,
                "positive_control": {
                    "zzl0ze_seen": bool(recorder.listings),
                    "listings_count": len(recorder.listings),
                    "total_listing_media_ids": len(listing_id_maps["media_ids"]),
                    "total_listing_workflow_ids": len(listing_id_maps["workflow_ids"]),
                },
                "inventory": {
                    "tiles": initial_tiles,
                    "total_tiles_recorded": len(initial_tiles),
                    "left_nav_filters": nav_filters_data.get("filters", []),
                    "selected_filter": nav_filters_data.get("selected"),
                    "scroller_wheel_test": scroller_wheel_test,
                },
                "timings_ms": timings_ms,
                "error": None,
            }
            summary_path = out_dir / "summary.json"
            summary_path.write_text(
                json.dumps(summary_doc, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            step("out", f"summary -> {summary_path}", prefix="1363")

            print_inventory_table(
                project_id=project_id,
                selected_filter=nav_filters_data.get("selected"),
                scroller_wheel_test=scroller_wheel_test,
                tiles=initial_tiles,
            )

        except BaseException as exc:
            run_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            raise
        finally:
            with contextlib.suppress(BaseException):
                await ctx.unroute("**/batchexecute**", recorder.on_batchexecute_route)
                await ctx.unroute(
                    "https://aisandbox-pa.googleapis.com/**", recorder.on_aisandbox_route
                )
                ctx.remove_listener("response", recorder.on_response)
            client._checkin_page(page)  # noqa: SLF001

    return 0 if run_error is None else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Issue #1363 Phase 0 spike ($0, 0 generations): record batchexecute/REST shapes "
            "and structural DOM anchors for moving a cataloged image to Flow's Trash and "
            "restoring it."
        )
    )
    parser.add_argument(
        "--self-test",
        "--selftest",
        dest="self_test",
        action="store_true",
        help="Run offline self-tests for payload-shape, id-position, and route-guard helpers.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="gflow profile name (resolved from catalog when --media-id is unambiguous).",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Flow project UUID (resolved from catalog when --media-id is in the catalog).",
    )
    parser.add_argument(
        "--media-id",
        dest="media_id",
        default=None,
        help="Disposable catalog IMAGE media UUID to move to Trash and restore.",
    )
    parser.add_argument(
        "--yes-trash-this",
        dest="yes_trash_this",
        action="store_true",
        help="Confirm moving --media-id to Flow's Trash and restoring it in the same run.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=(
            "Open the project, apply the left-nav media filter, scroll to the tile, open its "
            "overflow/context menu, record the menu's DOM inventory, press Escape, and exit "
            "WITHOUT clicking Trash."
        ),
    )
    parser.add_argument(
        "--allow-non-image",
        dest="allow_non_image",
        action="store_true",
        help="Allow running on a non-image catalog asset (default refuses non-image rows).",
    )
    parser.add_argument(
        "--show-mask",
        dest="show_mask",
        action="store_true",
        help="Print only the literal unredacted pGCYOe field mask string to stdout when captured.",
    )
    parser.add_argument(
        "--inventory",
        dest="inventory",
        action="store_true",
        help=(
            "Open the project grid, record the first 40 visible tile elements and their "
            "structural attributes, inspect left-nav filters, test virtual scroller wheel "
            "movement (3 steps), write inventory to summary.json, and print a summary table "
            "(changes nothing, needs --project, no --media-id)."
        ),
    )
    parser.add_argument(
        "--out-dir",
        dest="out_dir",
        type=Path,
        default=None,
        help="Override evidence output dir (default: _spike_out/spike_1363_<ts>/).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()

    if args.inventory:
        if not args.project:
            print(
                "[spike-1363] REFUSED: --project <uuid> is required for --inventory mode.",
                file=sys.stderr,
                flush=True,
            )
            return 2

        profile = args.profile
        if not profile:
            with contextlib.suppress(Exception):
                db_path = get_settings().resolved_db_path()
                rows = [
                    r
                    for r in list_images(db_path=db_path, profile=None, limit=50, offset=0)
                    if r.project_id == args.project
                ]
                if rows:
                    profile = rows[0].profile
        if not profile:
            with contextlib.suppress(Exception):
                from gflow_cli.profile_store import resolve_profile

                profile = resolve_profile(None)
        if not profile:
            print(
                "[spike-1363] REFUSED: could not resolve profile for project. "
                "Pass --profile explicitly.",
                file=sys.stderr,
                flush=True,
            )
            return 2

        ts = time.strftime("%Y%m%d_%H%M%S")
        out_dir = args.out_dir or (OUT_ROOT / f"spike_1363_{ts}")
        return asyncio.run(
            _run_inventory(
                profile=profile,
                project_id=args.project,
                out_dir=out_dir,
            )
        )

    if not args.media_id:
        return _print_candidates_and_refuse(
            profile=args.profile,
            project_id=args.project,
            reason="--media-id is required (this spike never picks or trashes an item implicitly).",
        )

    if not args.yes_trash_this:
        return _print_candidates_and_refuse(
            profile=args.profile,
            project_id=args.project,
            reason="--yes-trash-this is required alongside --media-id.",
        )

    profile = args.profile
    project_id = args.project
    workflow_id = ""
    media_kind = "unknown"
    try:
        asset = resolve_asset(args.media_id, profile=profile, route="spike.1363")
    except DataStoreError as exc:
        asset = None
        if profile is not None:
            with contextlib.suppress(Exception):
                with DataStore.open(get_settings().resolved_db_path()) as store:
                    asset = DataRepository(store).get_asset_by_any_id(profile, args.media_id)
        if asset is None and profile is None:
            with contextlib.suppress(Exception):
                with DataStore.open(get_settings().resolved_db_path()) as store:
                    rows = store.conn.execute(
                        "SELECT id, profile_name FROM assets WHERE flow_workflow_id = ? LIMIT 2",
                        (args.media_id,),
                    ).fetchall()
                    if len(rows) == 1:
                        asset = DataRepository(store).get_asset_by_any_id(
                            str(rows[0]["profile_name"]), args.media_id
                        )
        if asset is None and not (profile and project_id):
            print(
                f"[spike-1363] Catalog lookup failed ({exc.detail}); pass both --profile and "
                "--project if the item is not in the local catalog.",
                file=sys.stderr,
                flush=True,
            )
            return 2

    if asset is not None:
        if asset.kind is not AssetKind.IMAGE and not args.allow_non_image:
            print(
                f"[spike-1363] REFUSED: catalog row {args.media_id} has kind={asset.kind.value!r}, "
                "not 'image'. Use a zero-credit image candidate (or pass --allow-non-image).",
                file=sys.stderr,
                flush=True,
            )
            return 2
        profile = profile or asset.profile_name
        project_id = project_id or asset.flow_project_id
        workflow_id = asset.flow_workflow_id or ""
        media_kind = asset.kind.value

    if not profile or not project_id:
        print(
            "[spike-1363] REFUSED: could not resolve both profile and project_id for "
            f"media_id={args.media_id!r}. Pass --profile and --project explicitly.",
            file=sys.stderr,
            flush=True,
        )
        return 2

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir or (OUT_ROOT / f"spike_1363_{ts}")
    return asyncio.run(
        _run_live(
            profile=profile,
            project_id=project_id,
            media_id=args.media_id,
            workflow_id=workflow_id,
            media_kind=media_kind,
            out_dir=out_dir,
            dry_run=bool(args.dry_run),
            show_mask=bool(args.show_mask),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
