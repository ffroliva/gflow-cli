#!/usr/bin/env python3
"""Size gate for src/gflow_cli: file and class length, function complexity (#1249).

docs/ARCHITECTURE.md says an oversized module should be split. Nothing enforced it, and four
classes grew past 2,600 lines. A class that large carries several responsibilities.

Metrics, for every .py file under src/gflow_cli:
    file:<rel>                  lines in the file           <= LIMIT_FILE
    class-lines:<rel>::<Class>  lines in the class          <= LIMIT_CLASS
    C901:<rel>::<qualname>      cyclomatic complexity       ruff, threshold in pyproject.toml
    PLR0915:<rel>::<qualname>   statements in a function    ruff, threshold in pyproject.toml
    PLR0904:<rel>::<Class>      public methods in a class   ruff, threshold in pyproject.toml

Existing offenders are recorded in scripts/ci/size_baseline.json. That baseline is a RATCHET:
an offender may stay at its recorded value or shrink, never grow, and anything not in the
baseline must be within its limit. `--update-baseline` can only lower or drop entries, so the
baseline cannot be used to wave a new violation through.

Usage:
    check_size.py                    check the tree (CI, pre-commit, /gflow:check)
    check_size.py --update-baseline  lower or drop entries that improved
Exit 0 clean, 1 violation.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "gflow_cli"
BASELINE = Path(__file__).with_name("size_baseline.json")
PYPROJECT = REPO / "pyproject.toml"

# Deliberately loose to start: a tight limit forces thin wrappers and many tiny files (extra hops)
# just to satisfy a number. These catch only the worst offenders; tighten a step at a time, once the
# baseline is empty at the current step (next candidates: class 600, file 1000).
LIMIT_FILE = 2000
LIMIT_CLASS = 1000
RUFF_RULES = "C901,PLR0904,PLR0915"  # PLR0904 is a preview rule in ruff 0.17
_RUFF_NUMBERS = re.compile(r"\((\d+) > (\d+)\)")


class Measure(NamedTuple):
    value: int
    limit: int


def _qualnames(tree: ast.Module) -> dict[int, str]:
    """Line of each def/class -> dotted name (``Class.method``, ``outer.inner``)."""
    out: dict[int, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = f"{prefix}{child.name}"
                out[child.lineno] = name
                visit(child, f"{name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def _parse(path: Path) -> tuple[str, ast.Module | None]:
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        return src, ast.parse(src)
    except SyntaxError:
        return src, None  # a file that does not parse is ruff's and pytest's problem


def measure_lines(path: Path, root: Path = REPO) -> dict[str, Measure]:
    """``file:<rel>`` and ``class-lines:<rel>::<Class>`` for one file."""
    rel = path.relative_to(root).as_posix()
    src, tree = _parse(path)
    out = {f"file:{rel}": Measure(src.count("\n") + 1, LIMIT_FILE)}
    for node in ast.walk(tree) if tree else ():
        if isinstance(node, ast.ClassDef):
            key = f"class-lines:{rel}::{node.name}"
            span = (node.end_lineno or node.lineno) - node.lineno + 1
            out[key] = Measure(max(out[key].value if key in out else 0, span), LIMIT_CLASS)
    return out


def measure_ruff(src: Path, root: Path = REPO) -> dict[str, Measure]:
    """Ruff's C901/PLR0915/PLR0904 hits under ``src``. Ruff reports only what is over its
    threshold, and its message carries both numbers: ``... (13 > 10)``."""
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter, no shell
        [sys.executable, "-m", "ruff", "check", "--preview", "--select", RUFF_RULES,
         "--config", str(PYPROJECT), "--output-format", "json", "--exit-zero", "--no-cache",
         str(src)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )  # fmt: skip
    out: dict[str, Measure] = {}
    names: dict[Path, dict[int, str]] = {}
    for diag in json.loads(proc.stdout):
        path = Path(diag["filename"]).resolve()
        numbers = _RUFF_NUMBERS.search(diag["message"])
        if numbers is None:
            raise RuntimeError(f"unexpected ruff message: {diag['message']!r}")
        if path not in names:
            tree = _parse(path)[1]
            names[path] = _qualnames(tree) if tree else {}
        row = diag["location"]["row"]
        key = f"{diag['code']}:{path.relative_to(root).as_posix()}::{names[path].get(row, row)}"
        value, limit = int(numbers[1]), int(numbers[2])
        out[key] = Measure(max(out[key].value if key in out else 0, value), limit)
    return out


def measure_all(src: Path = SRC, root: Path = REPO) -> dict[str, Measure]:
    out: dict[str, Measure] = {}
    for p in sorted(src.rglob("*.py")):
        if "__pycache__" not in p.parts:
            out.update(measure_lines(p, root))
    out.update(measure_ruff(src, root))
    return out


def load_baseline(path: Path = BASELINE) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def violations(current: dict[str, Measure], baseline: dict[str, int]) -> list[str]:
    bad: list[str] = []
    for key, (value, limit) in sorted(current.items()):
        if value <= limit:
            continue
        allowed = baseline.get(key)
        if allowed is None:
            bad.append(f"{key}: {value} > {limit} (new; not in the baseline)")
        elif value > allowed:
            bad.append(
                f"{key}: grew {allowed} -> {value} (limit {limit}; baseline may only shrink)"
            )
    return bad


def ratcheted(current: dict[str, Measure], baseline: dict[str, int]) -> dict[str, int]:
    """Baseline after a ratchet step: keep entries still over the limit, at the lower value."""
    return {
        k: min(v, current[k].value)
        for k, v in sorted(baseline.items())
        if k in current and current[k].value > current[k].limit
    }


def update_baseline(src: Path = SRC, root: Path = REPO, path: Path = BASELINE) -> int:
    current = measure_all(src, root)
    baseline = load_baseline(path)
    bad = violations(current, baseline)
    if bad:
        print("\n".join(["cannot update the baseline while there are violations:", *bad]))
        return 1
    path.write_text(json.dumps(ratcheted(current, baseline), indent=1) + "\n", encoding="utf-8")
    print(f"baseline lowered: {path.name}")
    return 0


ADVICE = (
    "Split the unit instead of growing it (one responsibility per class or function; extract a "
    "sub-package). Do not raise the baseline: `--update-baseline` only lowers it. "
    "See docs/ARCHITECTURE.md and #1249."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--update-baseline", action="store_true")
    if ap.parse_args(argv).update_baseline:
        return update_baseline()
    bad = violations(measure_all(), load_baseline())
    if bad:
        print("\n".join(["SIZE GATE FAILED:", *bad, ADVICE]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
