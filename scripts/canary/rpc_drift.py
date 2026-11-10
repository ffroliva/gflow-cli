"""RPC-DRIFT: are the catalogued batchexecute ids still in the JavaScript Flow serves? (#1218)

Method from the #1217 spike (``scripts/dev/spike_rpc_ids_in_bundle.py::construct_arm``):
fetch the public flow.google.com shell with NO cookies, take its ``/_/js/`` script URL, swap
the ``m=`` segment for the four feature modules, and look for each id as a quoted literal.
No browser, no profile, no RPC call: billed submits are covered without being sent. $0.

Five outcomes. Like every canary state they gate nothing:

    OK               every catalogued id is in the bundle (an id found only in the shell's
                     base bundle is listed as "not in the feature modules", not as gone)
    ID-GONE          the bundle parsed (it has descriptors) and catalogued ids are missing
    MODULES-CHANGED  the feature-module bundle answered 404: a module name moved, so this
                     run says nothing about any single id
    PARSE-CHANGED    the bundle answered 200 with no ``{<key>:"…",`` descriptor: the bundle
                     format changed, so the ids cannot be judged
    INCONCLUSIVE     nothing was measured (offline, an HTTP error other than a bundle 404,
                     a redirect to a page without the app, no build label, a bad catalog,
                     or the time budget ran out)

Only ``live`` rows of the table are checked; a table that loses rows fails loudly.

The real nightly (``write=True``) archives each new build's full descriptor inventory, one
JSON per build label, under ``tmp/rpc-inventory/`` (gitignored, local, never committed),
with the ids added and removed against the previously first-seen build: the data spike S3
(#1223) reads. A ``--dry-run`` or a standalone run of this file shows the same diff but writes
nothing, so it cannot use up a new build's diff before a published comment reports it. A
hand-run ``run_canary.py --issue N`` is a real run: it archives and publishes the diff itself.

Publishable: the build label, ids, counts, and our own reasons. Nothing else from the shell
or the bundle; an exception is reported by its class name only (httpx messages carry URLs).

Usage (the live arm, $0, writes nothing): ``uv run python scripts/canary/rpc_drift.py``
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "docs" / "FLOW_WIRE_RPCS.md"
ARCHIVE_DIR = REPO_ROOT / "tmp" / "rpc-inventory"
SHELL = "https://flow.google.com/"
FEATURE_MODULES = ("XRV0Af", "wO1vlb", "wj3Fuc", "zxMy9c")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
#: Whole-check wall clock: a slow 7 MB download must not push the canary past its own cap.
BUDGET_S = 120.0

OK, ID_GONE, MODULES_CHANGED, INCONCLUSIVE = "OK", "ID-GONE", "MODULES-CHANGED", "INCONCLUSIVE"
PARSE_CHANGED = "PARSE-CHANGED"

#: ``{<key>:"<id>",`` — the key is minified and was renamed Xa -> Wa by one build (#1224).
_DESC_RE = re.compile(r'\{([A-Za-z$_]{1,3}):"([A-Za-z0-9]{4,7})",')
_SCRIPT_RE = re.compile(r'<script[^>]+src="([^"]*/_/js/[^"]*)"')
_BL_RE = re.compile(r'"cfb2h":"([^"]*)"')
#: The row pattern `tests/test_flow_wire_rpcs_catalog.py` counts; every such row is parsed.
_ROW_RE = re.compile(r"^\| `([A-Za-z0-9]{5,6})` \|", re.MULTILINE)
#: Most ids a comment lists before "and N more"; the archive keeps the full list.
_LIST_CAP = 20

#: url -> (HTTP status, body, final URL after redirects)
Getter = Callable[[str], tuple[int, str, str]]


class CatalogError(ValueError):
    """The wire table did not parse into the ids it holds. The message is ours, publishable."""


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
    moved: tuple[str, ...] = ()


def catalog_ids(text: str) -> tuple[str, ...]:
    """The id of every ``live`` row of the table in ``docs/FLOW_WIRE_RPCS.md``.

    A row marked ``gone`` or ``renamed -> X`` stays in the table and is not checked again.
    Raises CatalogError rather than return a short list: an id dropped silently is an id
    never checked.
    """
    status_col = None
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if status_col is None and "Status" in cells:
            status_col = cells.index("Status")
        m = _ROW_RE.match(line)
        if m and status_col is not None and len(cells) > status_col:
            rows.append((m.group(1), cells[status_col].lower()))
    expected = len(_ROW_RE.findall(text))
    if len(rows) != expected:
        raise CatalogError(f"catalog parsed {len(rows)} of {expected} rows")
    live = tuple(rpc for rpc, status in rows if status == "live")
    if not live:
        raise CatalogError("catalog has no live rows")
    return live


def bundle_url(shell_html: str, base: str = SHELL) -> str | None:
    """The shell's own script URL with ``m=`` swapped for the feature modules."""
    src = _script_url(shell_html, base)
    if src is None:
        return None
    kept = "/".join(s for s in src.split("/") if not s.startswith("m="))
    return f"{kept}/m={','.join(FEATURE_MODULES)}"


def _script_url(shell_html: str, base: str) -> str | None:
    m = _SCRIPT_RE.search(shell_html)
    return urljoin(base, m.group(1).replace("&amp;", "&")) if m else None


def descriptor_ids(text: str, ids: tuple[str, ...]) -> tuple[str, ...]:
    """Every id held under the descriptor key, which is learned rather than hard-coded.

    The key is the one the catalogued ids sit under; if none of them is present, the key
    most often seen in that shape (measured 2026-11-10: 267 hits against 24 for the next).
    """
    pairs = _DESC_RE.findall(text)
    if not pairs:
        return ()
    keys = Counter(k for k, i in pairs if i in ids) or Counter(k for k, _ in pairs)
    key = keys.most_common(1)[0][0]
    return tuple(sorted({i for k, i in pairs if k == key}))


def _has(rpc: str, text: str) -> bool:
    return re.search(rf"""["']{re.escape(rpc)}["']""", text) is not None


def assess(  # noqa: PLR0911 - one return per outcome reads better than a ladder of flags
    ids: tuple[str, ...],
    shell_html: str,
    bundle_status: int,
    bundle_text: str,
    *,
    shell_status: int = 200,
    shell_url: str = SHELL,
    base_text: str = "",
) -> Drift:
    """Pure: decide the outcome from what was fetched."""
    if not ids:
        return Drift(INCONCLUSIVE, note="no catalogued ids parsed")
    if shell_status != 200:
        return Drift(INCONCLUSIVE, note=f"shell HTTP {shell_status}")
    bl = _BL_RE.search(shell_html)
    build = bl.group(1) if bl else None
    if bundle_url(shell_html, shell_url) is None:
        host = urlsplit(shell_url).netloc
        return Drift(INCONCLUSIVE, build, note=f"shell served by {host} has no /_/js/ script")
    if build is None:
        return Drift(INCONCLUSIVE, note="shell has no build label")
    if bundle_status == 404:
        # Measured 2026-11-10: one unknown module name 404s the whole bundle.
        return Drift(MODULES_CHANGED, build, missing=ids, note="bundle HTTP 404")
    if bundle_status != 200:
        return Drift(INCONCLUSIVE, build, note=f"bundle HTTP {bundle_status}")
    inventory = descriptor_ids(bundle_text, ids)
    if not inventory:
        return Drift(PARSE_CHANGED, build, note='bundle has no {<key>:"<id>",...} descriptors')
    found = tuple(i for i in ids if _has(i, bundle_text))
    absent = [i for i in ids if i not in found]
    moved = tuple(i for i in absent if _has(i, base_text))
    missing = tuple(i for i in absent if i not in moved)
    return Drift(OK if not missing else ID_GONE, build, found, missing, inventory, moved=moved)


def _load(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def archive(drift: Drift, root: Path, now: str, write: bool = True) -> Drift:
    """Diff a NEW build against the last archived one and, when ``write``, archive it.

    Only OK and ID-GONE runs count: both have a parsed, non-empty inventory. A build already
    archived is never overwritten, so a wrong inventory would otherwise be fixed for good.
    """
    if drift.state not in (OK, ID_GONE) or not drift.build:
        return drift
    target = root / f"{re.sub(r'[^A-Za-z0-9._-]', '_', drift.build)}.json"
    if target.exists():
        return drift
    # A hand-edited or truncated file is skipped, never allowed to break tonight's report.
    builds = [
        d
        for d in map(_load, root.glob("*.json") if root.is_dir() else [])
        if d and d.get("first_seen") and isinstance(d.get("ids"), list)
    ]
    prev = max(builds, key=lambda d: str(d["first_seen"]), default=None)
    previous_build, added, removed = None, (), ()
    if prev is not None:
        prior = {str(i) for i in prev["ids"]}  # pyright: ignore[reportGeneralTypeIssues]
        previous_build = str(prev.get("build"))
        added = tuple(sorted(set(drift.inventory) - prior))
        removed = tuple(sorted(prior - set(drift.inventory)))
    drift = replace(drift, previous_build=previous_build, added=added, removed=removed)
    if not write:
        return drift
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
    except OSError as exc:
        return replace(drift, note=f"archive not written: {type(exc).__name__}")
    return drift


def _http_get(url: str) -> tuple[int, str, str]:
    import httpx  # noqa: PLC0415 - only the live arm needs it

    # No cookies, ever: the check must say nothing about any account.
    with httpx.Client(timeout=60, follow_redirects=True, headers={"user-agent": UA}) as client:
        r = client.get(url)
    return r.status_code, r.text, str(r.url)


def _measure(ids: tuple[str, ...], fetch: Getter) -> Drift:
    shell_status, shell, shell_url = fetch(SHELL)
    url = bundle_url(shell, shell_url) if shell_status == 200 else None
    status, text, _ = fetch(url) if url else (0, "", "")
    base_text = ""
    if status == 200 and not all(_has(i, text) for i in ids):
        # Only when an id is missing: is it still served, just outside the feature modules?
        base = _script_url(shell, shell_url)
        base_status, base_body, _ = fetch(base) if base else (0, "", "")
        base_text = base_body if base_status == 200 else ""
    return assess(
        ids, shell, status, text, shell_status=shell_status, shell_url=shell_url,
        base_text=base_text,
    )  # fmt: skip


def check(
    root: Path = ARCHIVE_DIR,
    get: Getter | None = None,
    ids: tuple[str, ...] | None = None,
    *,
    write: bool = False,
    budget_s: float = BUDGET_S,
) -> Drift:
    """Fetch, assess and diff; archive only when ``write`` (the real nightly). Never raises."""
    try:
        catalogued = ids if ids is not None else catalog_ids(CATALOG.read_text(encoding="utf-8"))
    except CatalogError as exc:
        return Drift(INCONCLUSIVE, note=str(exc))
    except Exception as exc:  # noqa: BLE001 - class name only
        return Drift(INCONCLUSIVE, note=type(exc).__name__)
    box: list[Drift] = []

    def run() -> None:
        try:
            box.append(_measure(catalogued, get or _http_get))
        except Exception as exc:  # noqa: BLE001 - reported by class name, never by message
            box.append(Drift(INCONCLUSIVE, note=type(exc).__name__))

    # A daemon thread: if the budget runs out, the process can still publish and exit.
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(budget_s)
    if not box:
        return Drift(INCONCLUSIVE, note=f"check exceeded its {budget_s:g} s budget")
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return archive(box[0], root, now, write=write)


def _ids(ids: tuple[str, ...]) -> str:
    shown = ", ".join(f"`{i}`" for i in ids[:_LIST_CAP])
    return shown + (f" and {len(ids) - _LIST_CAP} more" if len(ids) > _LIST_CAP else "")


def render(drift: Drift) -> str:
    headline = {
        OK: "Every catalogued RPC id is in the served JavaScript.",
        ID_GONE: "Catalogued RPC ids are missing from the served JavaScript: renamed or removed.",
        MODULES_CHANGED: (
            "The feature-module bundle answered 404: a module name moved. Says nothing about "
            "any single id."
        ),
        PARSE_CHANGED: (
            "The bundle loaded but holds no RPC descriptors: its format changed. Says nothing "
            "about any single id."
        ),
        INCONCLUSIVE: "Nothing was measured. Says nothing about Flow.",
    }[drift.state]
    total = len(drift.found) + len(drift.missing) + len(drift.moved)
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
    if drift.moved:
        lines += ["", f"**Served, but not in the feature modules:** {_ids(drift.moved)}"]
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
