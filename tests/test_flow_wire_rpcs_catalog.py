"""Every batchexecute rpc id constant in src has a row in docs/FLOW_WIRE_RPCS.md (#1216)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CATALOG = _ROOT / "docs" / "FLOW_WIRE_RPCS.md"
_NAME = re.compile(r"_RPC(ID)?S?$")


def _src_rpc_ids() -> set[str]:
    """String values of module-level `*_RPC`, `*_RPCID`, `*_RPCS`, `*_RPCIDS` assignments."""
    ids: set[str] = set()
    for path in (_ROOT / "src" / "gflow_cli").rglob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            elif isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            else:
                continue
            if not any(isinstance(t, ast.Name) and _NAME.search(t.id) for t in targets):
                continue
            elts = value.elts if isinstance(value, ast.Tuple) else [value]
            ids |= {
                e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    return ids


def _catalog_ids() -> set[str]:
    """The first cell of every table row: `| `id` | ...`."""
    text = _CATALOG.read_text(encoding="utf-8")
    return set(re.findall(r"^\| `([A-Za-z0-9]{5,6})` \|", text, flags=re.MULTILINE))


def test_every_rpc_id_constant_has_a_catalog_row() -> None:
    ids = _src_rpc_ids()
    assert ids, "collector found no rpc id constants; the name pattern is broken"
    missing = sorted(ids - _catalog_ids())
    assert not missing, f"rpc ids in src with no row in {_CATALOG.name}: {missing}"
