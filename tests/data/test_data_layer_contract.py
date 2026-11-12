"""Characterization (golden master) of ``DataRepository`` and ``OperationRecorder`` (#1249 wave 2).

Pinned BEFORE either class is split, so the split can prove it changed nothing a caller sees:

* the member set of both classes, each member's kind and signature (inherited members count, so a
  mixin split that keeps the API passes);
* the names other code imports from ``gflow_cli.data.repository`` and ``gflow_cli.data.recorder``;
* the database every existing data test leaves behind. Each test in ``SCENARIO_FILES`` that needs
  only ``tmp_path`` is run against a fresh directory, and every SQLite file it writes is dumped,
  table by table, with timestamps, UUIDs and the temp path normalised. The rows a test writes are
  the observable outcome of the repository and recorder methods it calls.

The expected values live in ``fixtures/data_layer_contract.json``, recorded on develop. A change
to that file is a behaviour change and must be justified. Re-record it with:

    RECORD_DATA_CONTRACT=1 uv run pytest tests/data/test_data_layer_contract.py -k record
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import gflow_cli.data.recorder as recorder_mod
import gflow_cli.data.repository as repository_mod
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository

FIXTURE = Path(__file__).parent / "fixtures" / "data_layer_contract.json"
SCENARIO_FILES = (
    "tests.data.test_repository",
    "tests.data.test_recorder",
    "tests.data.test_recorder_character",
    "tests.data.test_failure_recording",
    "tests.data.test_scene_persistence",
    "tests.data.test_find_incomplete_character",
    "tests.data.test_chain_repo",
    "tests.data.test_null_columns_stay_null",
)
_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)?")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def member_contract(cls: type) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in sorted(set(dir(cls)) - set(dir(object))):
        if name.startswith("__") and name != "__init__":
            continue  # class bookkeeping (__dict__, __module__, __firstlineno__), not API
        raw = inspect.getattr_static(cls, name)
        kind = type(raw).__name__
        value = getattr(cls, name)
        out[name] = f"{kind} {inspect.signature(value)}" if callable(value) else kind
    return out


def imported_names() -> dict[str, list[str]]:
    """Every name another file imports from the two modules (parsed: multi-line imports count)."""
    root = Path(__file__).resolve().parents[2]
    found: dict[str, set[str]] = {"repository": set(), "recorder": set()}
    for path in [
        *root.glob("src/**/*.py"),
        *root.glob("tests/**/*.py"),
        *root.glob("scripts/**/*.py"),
    ]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in (
                "gflow_cli.data.repository",
                "gflow_cli.data.recorder",
            ):
                found[node.module.rsplit(".", 1)[1]].update(a.name for a in node.names)
    return {mod: sorted(names) for mod, names in found.items()}


def names_contract() -> dict[str, list[str]]:
    """The imported names, and whether each still resolves at the old path (all must)."""
    modules = {"repository": repository_mod, "recorder": recorder_mod}
    return {
        mod: [f"{n} {'ok' if hasattr(modules[mod], n) else 'MISSING'}" for n in names]
        for mod, names in imported_names().items()
    }


def _normalise(value: Any, tmp: str) -> Any:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if not isinstance(value, str):
        return value
    value = value.replace(tmp, "<tmp>").replace(tmp.replace("\\", "/"), "<tmp>")
    value = value.replace(json.dumps(tmp)[1:-1], "<tmp>")  # JSON-escaped paths in metadata
    return _UUID.sub("<uuid>", _TS.sub("<ts>", value))


def dump_databases(root: Path) -> dict[str, dict[str, list[list[Any]]]]:
    out: dict[str, dict[str, list[list[Any]]]] = {}
    for db in sorted(root.rglob("*.db")):
        con = sqlite3.connect(db)
        try:
            tables = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            dumped: dict[str, list[list[Any]]] = {}
            for table in tables:
                rows = [
                    [_normalise(v, str(root)) for v in row]
                    for row in con.execute(f'SELECT * FROM "{table}"')
                ]  # noqa: S608
                if rows:
                    dumped[table] = sorted(rows, key=repr)
            out[db.relative_to(root).as_posix()] = dumped
        finally:
            con.close()
    return out


def _scenarios() -> list[tuple[str, Any]]:
    """``(id, callable(tmp_path))`` for every sync test that takes only ``tmp_path``."""
    found: list[tuple[str, Any]] = []
    for mod_name in SCENARIO_FILES:
        mod = importlib.import_module(mod_name)
        holders: list[tuple[str, Any]] = [("", mod)]
        holders += [
            (f"{n}.", c)
            for n, c in vars(mod).items()
            if inspect.isclass(c) and n.startswith("Test")
        ]
        for prefix, holder in holders:
            for name, fn in vars(holder).items():
                if not name.startswith("test_") or not inspect.isfunction(fn):
                    continue
                if inspect.iscoroutinefunction(fn) or hasattr(fn, "pytestmark"):
                    continue
                params = [p for p in inspect.signature(fn).parameters if p != "self"]
                if params != ["tmp_path"]:
                    continue
                if holder is mod:
                    found.append((f"{mod_name}::{name}", fn))
                else:
                    found.append(
                        (f"{mod_name}::{prefix}{name}", lambda t, h=holder, f=fn: f(h(), t))
                    )
    return found


def outcome_contract(tmp_path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i, (scenario_id, run) in enumerate(_scenarios()):
        work = tmp_path / f"s{i}"
        work.mkdir()
        run(work)
        out[scenario_id] = dump_databases(work)
    return out


def snapshot(tmp_path: Path) -> dict[str, Any]:
    return {
        "members": {
            "DataRepository": member_contract(DataRepository),
            "OperationRecorder": member_contract(OperationRecorder),
        },
        "module_names": names_contract(),
        "outcomes": outcome_contract(tmp_path),
    }


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_members_and_signatures_are_unchanged(expected: dict[str, Any]) -> None:
    assert member_contract(DataRepository) == expected["members"]["DataRepository"]
    assert member_contract(OperationRecorder) == expected["members"]["OperationRecorder"]


def test_open_stays_a_classmethod_on_the_final_class() -> None:
    """Five CLI tests patch ``OperationRecorder.open`` through their import sites."""
    assert isinstance(inspect.getattr_static(OperationRecorder, "open"), classmethod)
    assert "open" in vars(OperationRecorder)


def test_every_imported_name_is_still_there(expected: dict[str, Any]) -> None:
    assert names_contract() == expected["module_names"]


def test_the_data_tests_leave_the_same_rows(expected: dict[str, Any], tmp_path: Path) -> None:
    got = outcome_contract(tmp_path)
    assert sorted(got) == sorted(expected["outcomes"])
    for key in got:
        assert got[key] == expected["outcomes"][key], key


@pytest.mark.skipif(not os.environ.get("RECORD_DATA_CONTRACT"), reason="re-records on request")
def test_record_the_fixture(tmp_path: Path) -> None:
    FIXTURE.parent.mkdir(exist_ok=True)
    text = json.dumps(snapshot(tmp_path), indent=1, sort_keys=True, ensure_ascii=False, default=str)
    FIXTURE.write_text(text + "\n", encoding="utf-8", newline="\n")
