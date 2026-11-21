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
    python scripts/dev/spike_1363_trash.py --profile <profile> [--project <project-uuid>]
    python scripts/dev/spike_1363_trash.py --profile <profile> --project <project-uuid> \\
        --media-id <media-uuid> --yes-trash-this
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

    print("self-test: ok (6 helper suites passed)", flush=True)
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

  const menuItems = [...document.querySelectorAll("[role='menuitem']")]
    .filter(visible).slice(0, 30).map(summarize);
  const dialogSel =
    "[role='dialog'] button, [role='alertdialog'] button, mat-dialog-container button";
  const dialogButtons = [...document.querySelectorAll(dialogSel)]
    .filter(visible).slice(0, 20).map(summarize);
  const navSel =
    "nav a, nav button, aside a, aside button, [role='navigation'] a, [role='navigation'] button";
  const navLinks = [...document.querySelectorAll(navSel)]
    .filter(visible).slice(0, 30).map(summarize);
  const overlaySel = ".cdk-overlay-pane button, .cdk-overlay-pane [role='menuitem']";
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
  const ligatureOf = (node) =>
    [...node.querySelectorAll("mat-icon, i.google-symbols, i[class*='symbols']")]
      .map(i => (i.textContent || "").trim()).filter(Boolean).join(",");
  const structClasses = (node) =>
    [...(node.classList || [])]
      .filter(c => /^(cdk|mat|mdc|flow|glue)-/.test(c))
      .slice(0, 4)
      .join(".");
  const ancestors = [];
  let cur = el.parentElement;
  for (let depth = 0; cur && cur !== document.body && depth < 5; depth++, cur = cur.parentElement) {
    const btnNodes = [...cur.querySelectorAll("button, [role='button'], a[href]")].slice(0, 15);
    const buttons = btnNodes.map(b => ({
      tag: b.tagName.toLowerCase(),
      role: b.getAttribute("role"),
      classes: structClasses(b),
      ligature: ligatureOf(b),
      haspopup: b.getAttribute("aria-haspopup"),
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
    ancestors,
  };
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


def _tile_selector(media_id: str, asb_token: str = "") -> str:
    parts = [f'img[data-media-id="{media_id}"]', f'[data-media-id="{media_id}"]']
    if asb_token:
        parts.append(f'img[src*="/asb/{asb_token}"]')
    return ", ".join(parts)


async def _is_media_visible_in_dom(page: Any, media_id: str, asb_token: str = "") -> bool:
    loc = page.locator(_tile_selector(media_id, asb_token)).first
    try:
        return await loc.is_visible()
    except Exception:  # noqa: BLE001
        return False


async def _first_visible_match(
    page: Any,
    selectors: tuple[str, ...],
    *,
    tried: list[str],
    timeout_ms: int = 1_200,
) -> tuple[str | None, Any | None]:
    """Try each structural selector in order; return ``(selector, locator)`` on first match."""
    for sel in selectors:
        tried.append(sel)
        loc = await wait_for_or_none(page, sel, timeout_ms)
        if loc is not None and await loc.is_visible():
            return sel, loc
    return None, None


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
    media_id: str,
    asb_token: str,
    menuitem_selectors: tuple[str, ...],
    direct_button_selectors: tuple[str, ...],
) -> dict[str, Any]:
    """Open the item's menu or toolbar structurally and click the target action (Trash/Restore)."""
    tried_triggers: list[str] = []
    tried_actions: list[str] = []
    tried_dialogs: list[str] = []
    matched: dict[str, str | None] = {
        "trigger": None,
        "action": None,
        "confirm_dialog": None,
    }
    tile_sel = _tile_selector(media_id, asb_token)
    tile = await wait_for_or_none(page, tile_sel, 10_000)
    tile_context = await page.evaluate(_TILE_CONTEXT_JS, media_id)
    if tile is None:
        return {
            "tile_found": False,
            "tile_context": tile_context,
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
    await page.wait_for_timeout(400)

    # Strategy 1: direct action button visible on the hovered tile or current toolbar.
    action_sel, action_loc = await _first_visible_match(
        page, direct_button_selectors, tried=tried_actions, timeout_ms=800
    )
    if action_loc is not None:
        matched["trigger"] = "hover_direct"
        matched["action"] = action_sel

    # Strategy 2: overflow menu button (`more_vert` / `more_horiz`), then menuitem with ligature.
    if action_loc is None:
        trig_sel, trig_loc = await _first_visible_match(
            page, OVERFLOW_TRIGGER_SELECTORS, tried=tried_triggers, timeout_ms=1_000
        )
        if trig_loc is not None:
            await trig_loc.click()
            await page.wait_for_timeout(600)
            action_sel, action_loc = await _first_visible_match(
                page, menuitem_selectors, tried=tried_actions, timeout_ms=1_500
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
            page, menuitem_selectors, tried=tried_actions, timeout_ms=1_500
        )
        if action_loc is not None:
            matched["trigger"] = "tile.click(button='right')"
            matched["action"] = action_sel
        else:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)

    # Strategy 4: click the tile to open its detail view, then try direct button or overflow menu.
    if action_loc is None:
        tried_triggers.append("tile.click() -> detail_view")
        await tile.click()
        await page.wait_for_timeout(1_200)
        action_sel, action_loc = await _first_visible_match(
            page, direct_button_selectors, tried=tried_actions, timeout_ms=1_500
        )
        if action_loc is not None:
            matched["trigger"] = "tile.click() -> detail_view_direct"
            matched["action"] = action_sel
        else:
            trig_sel, trig_loc = await _first_visible_match(
                page, OVERFLOW_TRIGGER_SELECTORS, tried=tried_triggers, timeout_ms=1_200
            )
            if trig_loc is not None:
                await trig_loc.click()
                await page.wait_for_timeout(600)
                action_sel, action_loc = await _first_visible_match(
                    page, menuitem_selectors, tried=tried_actions, timeout_ms=1_500
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
    # Expand sidebar if collapsed.
    for exp_sel in SIDEBAR_EXPAND_SELECTORS:
        exp_btn = await wait_for_or_none(page, exp_sel, 600)
        if exp_btn is not None and await exp_btn.is_visible():
            tried.append(f"expand:{exp_sel}")
            await exp_btn.click()
            await page.wait_for_timeout(500)
            break

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
        "\nRe-run with an explicit --media-id and --yes-trash-this:\n"
        f"  uv run python scripts/dev/spike_1363_trash.py --profile {newest.profile} "
        f"--project {newest.project_id} --media-id {newest.media_id} --yes-trash-this",
        file=sys.stderr,
        flush=True,
    )
    return 2


# ---------------------------------------------------------------------------
# Main live spike execution
# ---------------------------------------------------------------------------


async def _run_live(
    *,
    profile: str,
    project_id: str,
    media_id: str,
    workflow_id: str,
    out_dir: Path,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = resolve_profile_dir(profile)
    targets = {
        "media_id": media_id,
        "project_id": project_id,
        "workflow_id": workflow_id,
    }
    recorder = WireRecorder(out_dir=out_dir, targets=targets)
    timings_ms: dict[str, float] = {}
    t_run_start = time.monotonic()

    item_may_be_trashed = False
    restored_cleanly = False
    trash_url = f"https://flow.google.com/project/{project_id}/trash"
    host_served = "unknown"
    asb_token = ""

    trash_action_info: dict[str, Any] = {}
    trash_nav_info: dict[str, Any] = {}
    restore_action_info: dict[str, Any] = {}
    grid_return_info: dict[str, Any] = {}
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
            # 1. Open the project using gflow's own MigratedComposer / project route
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
            # Wait for the target tile to render in the grid (reloading once if needed).
            tile_loc = await wait_for_or_none(page, _tile_selector(media_id), 15_000)
            if tile_loc is None:
                await page.reload(wait_until="domcontentloaded", timeout=60_000)
                tile_loc = await wait_for_or_none(page, _tile_selector(media_id), 15_000)
            observed_in_initial_grid = tile_loc is not None and await tile_loc.is_visible()
            if observed_in_initial_grid and tile_loc is not None:
                src_attr = await tile_loc.get_attribute("src")
                if not src_attr:
                    img_child = tile_loc.locator("img").first
                    if await img_child.count() > 0:
                        src_attr = await img_child.get_attribute("src")
                asb_token = extract_asb_token(src_attr)
            timings_ms["project_open"] = round((time.monotonic() - t0) * 1000, 1)

            # 2. Idle baseline window before Trash
            t0 = time.monotonic()
            recorder.set_step("idle_before_trash")
            await page.wait_for_timeout(3_000)
            timings_ms["idle_before_trash"] = round((time.monotonic() - t0) * 1000, 1)

            # 3. Step (a) & (b): Open menu / toolbar on the item in the grid, click Trash, confirm
            t0 = time.monotonic()
            recorder.set_step("trash")
            step("2", f"Moving media {media_id[:8]}… to Trash", prefix="1363")
            item_may_be_trashed = True
            trash_action_info = await _trigger_tile_action(
                page,
                media_id=media_id,
                asb_token=asb_token,
                menuitem_selectors=TRASH_MENUITEM_SELECTORS,
                direct_button_selectors=TRASH_BUTTON_SELECTORS,
            )
            timings_ms["trash"] = round((time.monotonic() - t0) * 1000, 1)

            # If we clicked from inside the detail view, return to the project grid to observe
            matched_trigger = str(
                (trash_action_info.get("anchors_matched") or {}).get("trigger") or ""
            )
            if "detail_view" in matched_trigger and "/edit/" in page.url:
                await page.goto(
                    mc.MIGRATED_PROJECT_URL.format(project_id=project_id),
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                await page.wait_for_timeout(2_500)

            observed_gone_from_grid = not await _is_media_visible_in_dom(page, media_id, asb_token)
            if trash_action_info.get("anchors_matched", {}).get("action") is None:
                # No trash action was clicked at all, so the item was not moved.
                item_may_be_trashed = False

            # 4. Step (c): Navigate to Trash view via structural nav link and observe item present
            t0 = time.monotonic()
            recorder.set_step("trash_view_nav")
            step("3", "Navigating to Trash view", prefix="1363")
            trash_nav_info = await _navigate_to_trash_view(
                page, project_id=project_id, targets=targets
            )
            observed_in_trash_view = await _is_media_visible_in_dom(page, media_id, asb_token)
            timings_ms["trash_view_nav"] = round((time.monotonic() - t0) * 1000, 1)

            # 5. Idle baseline window in Trash view before Restore
            t0 = time.monotonic()
            recorder.set_step("idle_before_restore")
            await page.wait_for_timeout(2_000)
            timings_ms["idle_before_restore"] = round((time.monotonic() - t0) * 1000, 1)

            # 6. Step (d): Restore the item from the Trash view
            t0 = time.monotonic()
            recorder.set_step("restore")
            step("4", f"Restoring media {media_id[:8]}… from Trash", prefix="1363")
            restore_action_info = await _trigger_tile_action(
                page,
                media_id=media_id,
                asb_token=asb_token,
                menuitem_selectors=RESTORE_MENUITEM_SELECTORS,
                direct_button_selectors=RESTORE_BUTTON_SELECTORS,
            )
            observed_gone_from_trash = not await _is_media_visible_in_dom(page, media_id, asb_token)
            timings_ms["restore"] = round((time.monotonic() - t0) * 1000, 1)

            # 7. Step (e): Return to project grid and observe item back
            t0 = time.monotonic()
            recorder.set_step("verify_back_in_grid")
            step("5", "Verifying media returned to the project grid", prefix="1363")
            grid_return_info = await _navigate_to_project_grid(
                page, project_id=project_id, targets=targets
            )
            tile_back = await wait_for_or_none(page, _tile_selector(media_id, asb_token), 12_000)
            if tile_back is None:
                await page.reload(wait_until="domcontentloaded", timeout=45_000)
                tile_back = await wait_for_or_none(
                    page, _tile_selector(media_id, asb_token), 12_000
                )
            observed_back_in_grid = tile_back is not None and await tile_back.is_visible()
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
                    await _trigger_tile_action(
                        page,
                        media_id=media_id,
                        asb_token=asb_token,
                        menuitem_selectors=RESTORE_MENUITEM_SELECTORS,
                        direct_button_selectors=RESTORE_BUTTON_SELECTORS,
                    )
                    await page.goto(
                        mc.MIGRATED_PROJECT_URL.format(project_id=project_id),
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    back_loc = await wait_for_or_none(
                        page, _tile_selector(media_id, asb_token), 10_000
                    )
                    if back_loc is not None and await back_loc.is_visible():
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
                bool(find_id_positions(listing, {"media_id": media_id}).get("media_id"))
                for listing in recorder.listings[:1]
            )
            summary_doc = {
                "spike": "1363-media-trash-and-restore",
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "host_served": host_served,
                "positive_control": {
                    "zzl0ze_seen": bool(recorder.listings),
                    "media_in_initial_listing": initial_listing_matches,
                    "media_in_initial_grid": observed_in_initial_grid,
                },
                "trash": {
                    **recorder.step_summary("trash", idle_trash),
                    **trash_action_info,
                    "observed_gone_from_grid": observed_gone_from_grid,
                    "observed_in_trash_view": observed_in_trash_view,
                },
                "trash_view_nav": {
                    **recorder.step_summary("trash_view_nav", idle_trash),
                    **trash_nav_info,
                },
                "restore": {
                    **recorder.step_summary("restore", idle_restore),
                    **restore_action_info,
                    "observed_gone_from_trash": observed_gone_from_trash,
                    "observed_back_in_grid": observed_back_in_grid,
                },
                "verify_back_in_grid": {
                    **recorder.step_summary("verify_back_in_grid", idle_restore),
                    **grid_return_info,
                    "observed_back_in_grid": observed_back_in_grid,
                },
                "timings_ms": timings_ms,
                "restored_cleanly": restored_cleanly,
                "manual_restore_needed": item_may_be_trashed,
                "error": run_error,
            }
            summary_path = out_dir / "summary.json"
            summary_path.write_text(
                json.dumps(summary_doc, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            step("out", f"summary -> {summary_path}", prefix="1363")
            if item_may_be_trashed:
                print(
                    f"\n[spike-1363] MANUAL RESTORE REQUIRED: media_id={media_id} "
                    f"may still be in Trash.\n"
                    f"[spike-1363] Open Trash view and restore by hand: {trash_url}",
                    file=sys.stderr,
                    flush=True,
                )

    return 0 if restored_cleanly else 1


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
        "--allow-non-image",
        dest="allow_non_image",
        action="store_true",
        help="Allow running on a non-image catalog asset (default refuses non-image rows).",
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
    try:
        asset = resolve_asset(args.media_id, profile=profile, route="spike.1363")
    except DataStoreError as exc:
        if not (profile and project_id):
            print(
                f"[spike-1363] Catalog lookup failed ({exc.detail}); pass both --profile and "
                "--project if the item is not in the local catalog.",
                file=sys.stderr,
                flush=True,
            )
            return 2
        asset = None

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
            out_dir=out_dir,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
