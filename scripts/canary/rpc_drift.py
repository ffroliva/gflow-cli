"""RPC-DRIFT: are the catalogued batchexecute ids still in the JavaScript Flow serves? (#1218)

Method from the #1217 spike (``scripts/dev/spike_rpc_ids_in_bundle.py::construct_arm``):
fetch the public flow.google.com shell with NO cookies, take its ``/_/js/`` script URL, swap
the ``m=`` segment for the four feature modules, and look for each id as a quoted literal.
No browser, no profile, no RPC call: billed submits are covered without being sent. $0.

Four outcomes. Like every canary state they gate nothing:

    OK               every catalogued id is in the bundle
    ID-GONE          some ids are missing while others are present: a rename or a removal
    MODULES-CHANGED  the bundle fetch failed, or every id is missing: the feature module
                     names moved, so this run says nothing about any single id
    INCONCLUSIVE     nothing was measured (offline, no script URL, no build label, no catalog)

Each new build's full ``{Xa:"<id>"}`` descriptor inventory is archived, one JSON per build
label, under ``tmp/rpc-inventory/`` (gitignored, local, never committed). When a build is
new, the archive and the report also carry the ids added and removed against the previously
first-seen build. That diff is the data spike S3 (#1223) reads.

Publishable: the build label, ids and counts. Nothing else from the shell or the bundle,
and an error is reported by its class name only (httpx messages carry the full URL).

Usage (the live arm, $0): ``uv run python scripts/canary/rpc_drift.py``
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "docs" / "FLOW_WIRE_RPCS.md"
ARCHIVE_DIR = REPO_ROOT / "tmp" / "rpc-inventory"
SHELL = "https://flow.google.com/"
FEATURE_MODULES = ("XRV0Af", "wO1vlb", "wj3Fuc", "zxMy9c")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

OK, ID_GONE, MODULES_CHANGED, INCONCLUSIVE = "OK", "ID-GONE", "MODULES-CHANGED", "INCONCLUSIVE"

_XA_RE = re.compile(r'\{Xa:"([A-Za-z0-9]{4,7})"')
_SCRIPT_RE = re.compile(r'<script[^>]+src="([^"]*/_/js/[^"]*)"')
_BL_RE = re.compile(r'"cfb2h":"([^"]*)"')
#: Most ids a comment lists before "and N more"; the archive keeps the full list.
_LIST_CAP = 20

Getter = Callable[[str], tuple[int, str]]


@dataclass(frozen=True)
class Drift:
    state: str
    build: str | None = None
    found: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    inventory: tuple[str, ...] = ()
    previous_build: str | None = None
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    note: str = ""


def catalog_ids(text: str) -> tuple[str, ...]:
    """The first cell of every row of the table in ``docs/FLOW_WIRE_RPCS.md``.

    Same pattern as ``tests/test_flow_wire_rpcs_catalog.py``: the doc is the one list.
    """
    return tuple(re.findall(r"^\| `([A-Za-z0-9]{5,6})` \|", text, flags=re.MULTILINE))


def bundle_url(shell_html: str) -> str | None:
    m = _SCRIPT_RE.search(shell_html)
    if not m:
        return None
    segs = urljoin(SHELL, m.group(1).replace("&amp;", "&")).split("/")
    base = "/".join(s for s in segs if not s.startswith("m="))
    return f"{base}/m={','.join(FEATURE_MODULES)}"


def assess(ids: tuple[str, ...], shell_html: str, bundle_status: int, bundle_text: str) -> Drift:
    """Pure: decide the outcome from what was fetched."""
    bl = _BL_RE.search(shell_html)
    build = bl.group(1) if bl else None
    if not ids:
        return Drift(INCONCLUSIVE, build, note="no catalogued ids parsed")
    if build is None or bundle_url(shell_html) is None:
        return Drift(INCONCLUSIVE, build, note="shell has no build label or no /_/js/ script")
    if bundle_status != 200:
        return Drift(MODULES_CHANGED, build, missing=ids, note=f"bundle HTTP {bundle_status}")
    found = tuple(i for i in ids if re.search(rf"""["']{re.escape(i)}["']""", bundle_text))
    missing = tuple(i for i in ids if i not in found)
    inventory = tuple(sorted(set(_XA_RE.findall(bundle_text))))
    state = OK if not missing else MODULES_CHANGED if not found else ID_GONE
    return Drift(state, build, found, missing, inventory)


def _load(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def archive(drift: Drift, root: Path, now: str) -> Drift:
    """Write a NEW build's inventory and return the drift with its diff against the last build.

    Never overwrites a build already archived, and never writes an empty inventory: a renamed
    descriptor key would otherwise read as every id removed.
    """
    if not drift.build or not drift.inventory:
        return drift
    target = root / f"{re.sub(r'[^A-Za-z0-9._-]', '_', drift.build)}.json"
    if target.exists():
        return drift
    builds = [d for d in map(_load, root.glob("*.json")) if d and d.get("first_seen")]
    prev = max(builds, key=lambda d: str(d["first_seen"]), default=None)
    previous_build, added, removed = None, (), ()
    if prev is not None:
        prior = {str(i) for i in prev.get("ids", [])}  # type: ignore[union-attr]
        previous_build = str(prev.get("build"))
        added = tuple(sorted(set(drift.inventory) - prior))
        removed = tuple(sorted(prior - set(drift.inventory)))
    record = {
        "build": drift.build,
        "first_seen": now,
        "modules": list(FEATURE_MODULES),
        "ids": list(drift.inventory),
        "catalog_missing": list(drift.missing),
        "previous_build": previous_build,
        "added": list(added),
        "removed": list(removed),
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, indent=1), encoding="utf-8")
    except OSError:
        pass  # local and best-effort, like the canary's state file
    return replace(drift, previous_build=previous_build, added=added, removed=removed)


def _http_get(url: str) -> tuple[int, str]:
    import httpx  # noqa: PLC0415 - only the live arm needs it

    # No cookies, ever: the check must say nothing about any account.
    with httpx.Client(timeout=60, follow_redirects=True, headers={"user-agent": UA}) as client:
        r = client.get(url)
    return r.status_code, r.text


def check(
    root: Path = ARCHIVE_DIR, get: Getter | None = None, ids: tuple[str, ...] | None = None
) -> Drift:
    """Fetch, assess and archive. Never raises: a broken check is INCONCLUSIVE, not a crash."""
    fetch = get or _http_get
    try:
        if ids is None:
            ids = catalog_ids(CATALOG.read_text(encoding="utf-8"))
        _, shell = fetch(SHELL)
        url = bundle_url(shell)
        status, text = fetch(url) if url else (0, "")
        drift = assess(ids, shell, status, text)
    except Exception as exc:  # noqa: BLE001 - reported by class name, never by message
        return Drift(INCONCLUSIVE, note=type(exc).__name__)
    return archive(drift, root, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _ids(ids: tuple[str, ...]) -> str:
    shown = ", ".join(f"`{i}`" for i in ids[:_LIST_CAP])
    return shown + (f" and {len(ids) - _LIST_CAP} more" if len(ids) > _LIST_CAP else "")


def render(drift: Drift) -> str:
    headline = {
        OK: "Every catalogued RPC id is in the served JavaScript.",
        ID_GONE: "Catalogued RPC ids are missing from the served JavaScript: renamed or removed.",
        MODULES_CHANGED: (
            "The feature-module bundle failed or held none of the ids: the module names "
            "moved. Says nothing about any single id."
        ),
        INCONCLUSIVE: "Nothing was measured. Says nothing about Flow.",
    }[drift.state]
    total = len(drift.found) + len(drift.missing)
    lines = [
        f"#### RPC-DRIFT {drift.state}",
        "",
        headline,
        "",
        f"| build | `{drift.build or 'unknown'}` |",
        "| --- | --- |",
        f"| catalogued ids found | {len(drift.found)}/{total} |",
        f"| descriptors | {len(drift.inventory)} |",
    ]
    if drift.note:
        lines += ["", f"**Reason:** {drift.note}"]
    if drift.state == ID_GONE:
        lines += ["", f"**Missing:** {_ids(drift.missing)}"]
    if drift.previous_build:
        lines += [
            "",
            f"**New build** since `{drift.previous_build}`: "
            f"{len(drift.added)} descriptor ids added, {len(drift.removed)} removed.",
        ]
        if drift.added:
            lines.append(f"- added: {_ids(drift.added)}")
        if drift.removed:
            lines.append(f"- removed: {_ids(drift.removed)}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(check()))
