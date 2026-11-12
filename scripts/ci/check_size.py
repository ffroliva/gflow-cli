#!/usr/bin/env python3
"""Size gate for src/gflow_cli: file and class length, function complexity (#1249).

docs/ARCHITECTURE.md says an oversized module should be split. Nothing enforced it, and four
classes grew past 2,600 lines. A class that large carries several responsibilities.

Metrics, for every .py file under src/gflow_cli:
    file:<rel>                     lines in the file           <= LIMIT_FILE
    class-lines:<rel>::<qualname>  lines in the class          <= LIMIT_CLASS
    C901:<rel>::<qualname>         cyclomatic complexity       ruff, threshold in pyproject.toml
    PLR0915:<rel>::<qualname>      statements in a function    ruff, threshold in pyproject.toml
    PLR0904:<rel>::<qualname>      public methods in a class   ruff, threshold in pyproject.toml

Existing offenders are recorded in scripts/ci/size_baseline.json. That baseline is a RATCHET:
an offender may stay at its recorded value or shrink, never grow, and anything not in the
baseline must be within its limit. `# noqa` does not exempt anything here.

A pure move keeps its entry: when an entry's key disappears and an offender with the same
rule and name appears in another file at the same value or lower, the entry follows it.

`--update-baseline` only lowers, drops or re-keys (moves) entries. `--base-ref REF` (CI) also
fails if the committed baseline adds an entry or raises a value compared with REF's copy, so
the file cannot be hand-edited to wave a violation through.

Usage:
    check_size.py                    check the tree (pre-commit, /gflow:check)
    check_size.py --base-ref REF     the same, plus the hand-edit check against REF (CI)
    check_size.py --update-baseline  lower, drop or re-key entries that improved or moved
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


def _put(out: dict[str, Measure], key: str, value: int, limit: int) -> None:
    out[key] = Measure(max(out[key].value if key in out else 0, value), limit)


def measure_lines(path: Path, root: Path = REPO) -> dict[str, Measure]:
    """``file:<rel>`` and ``class-lines:<rel>::<qualname>`` for one file."""
    rel = path.relative_to(root).as_posix()
    src, tree = _parse(path)
    out = {f"file:{rel}": Measure(len(src.splitlines()), LIMIT_FILE)}
    if tree is None:
        return out
    names = _qualnames(tree)
    for qual in names.values():  # existence only: tells a moved unit from a shrunk one
        out[f"unit:{rel}::{qual}"] = Measure(0, 0)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            span = (node.end_lineno or node.lineno) - node.lineno + 1
            _put(out, f"class-lines:{rel}::{names[node.lineno]}", span, LIMIT_CLASS)
    return out


def measure_ruff(src: Path, root: Path = REPO) -> dict[str, Measure]:
    """Ruff's C901/PLR0915/PLR0904 hits under ``src``. Ruff reports only what is over its
    threshold, and its message carries both numbers: ``... (13 > 10)``."""
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter, no shell
        [sys.executable, "-m", "ruff", "check", "--preview", "--select", RUFF_RULES,
         "--ignore-noqa", "--config", str(PYPROJECT), "--output-format", "json",
         "--exit-zero", "--no-cache", str(src)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        raise RuntimeError(f"ruff failed ({proc.returncode}): {proc.stderr.strip()}")
    out: dict[str, Measure] = {}
    names: dict[Path, dict[int, str]] = {}
    for diag in json.loads(proc.stdout):
        if not diag.get("code"):
            continue  # a syntax error: not a size metric, and ruff check reports it anyway
        numbers = _RUFF_NUMBERS.search(diag["message"])
        if numbers is None:
            raise RuntimeError(f"unexpected ruff message: {diag['message']!r}")
        path = Path(diag["filename"]).resolve()
        if path not in names:
            tree = _parse(path)[1]
            names[path] = _qualnames(tree) if tree else {}
        row = diag["location"]["row"]
        key = f"{diag['code']}:{path.relative_to(root).as_posix()}::{names[path].get(row, row)}"
        _put(out, key, int(numbers[1]), int(numbers[2]))
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


def _identity(key: str) -> tuple[str, str] | None:
    """(rule, last name segment) for a moved-entry match; file entries never move."""
    kind, _, rest = key.partition(":")
    _, sep, qual = rest.partition("::")
    return (kind, qual.rsplit(".", 1)[-1]) if sep else None


def _unit(key: str) -> str:
    return "unit:" + key.partition(":")[2]


def _moves(new: dict[str, int], old: dict[str, int], present: set[str]) -> dict[str, str]:
    """new key -> old key, for an old entry whose unit is gone from ``present`` and that
    reappears under another path with the same rule and name, at the old value or lower.
    A move needs exactly one candidate on each side; anything ambiguous does not move."""
    gone: dict[tuple[str, str], list[str]] = {}
    for k in old:
        ident = _identity(k)
        if ident and k not in present:
            gone.setdefault(ident, []).append(k)
    claims: dict[str, list[str]] = {}
    for k, v in new.items():
        ident = _identity(k)
        candidates = gone.get(ident, []) if ident and k not in old else []
        if len(candidates) == 1 and v <= old[candidates[0]]:
            claims.setdefault(candidates[0], []).append(k)
    return {news[0]: o for o, news in claims.items() if len(news) == 1}


def _carried(current: dict[str, Measure], baseline: dict[str, int]) -> dict[str, int]:
    """The baseline with moved entries re-keyed to where their offender now lives."""
    over = {k: m.value for k, m in current.items() if m.value > m.limit}
    present = {k for k in baseline if _unit(k) in current}
    out = dict(baseline)
    for new, old in _moves(over, baseline, present).items():
        out[new] = out.pop(old)
    return out


def violations(current: dict[str, Measure], baseline: dict[str, int]) -> list[str]:
    baseline = _carried(current, baseline)
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
        for k, v in sorted(_carried(current, baseline).items())
        if k in current and current[k].value > current[k].limit
    }


def escalations(head: dict[str, int], base: dict[str, int]) -> list[str]:
    """Entries the committed baseline adds or raises compared with ``base`` (moves excepted)."""
    moved = _moves(head, base, set(head))
    bad: list[str] = []
    for key, value in sorted(head.items()):
        if key in base and value > base[key]:
            bad.append(f"{key}: raised {base[key]} -> {value} by hand")
        elif key not in base and key not in moved:
            bad.append(f"{key}: added by hand")
    return bad


def base_baseline(ref: str) -> dict[str, int] | None:
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],  # noqa: S607 - git from PATH, as every CI step uses it
            capture_output=True, text=True, encoding="utf-8", check=False, cwd=REPO,
        )  # fmt: skip

    if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
        raise SystemExit(f"--base-ref {ref!r} is not a commit here (fetch it first)")
    proc = git("show", f"{ref}:{BASELINE.relative_to(REPO).as_posix()}")
    if proc.returncode != 0:
        return None  # no baseline on the base yet: the PR that introduces it
    return json.loads(proc.stdout)


def update_baseline(src: Path = SRC, root: Path = REPO, path: Path = BASELINE) -> int:
    current = measure_all(src, root)
    baseline = load_baseline(path)
    bad = violations(current, baseline)
    if bad:
        print("\n".join(["cannot update the baseline while there are violations:", *bad]))
        return 1
    path.write_text(
        json.dumps(ratcheted(current, baseline), indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"baseline lowered: {path.name}")
    return 0


ADVICE = (
    "Split the unit instead of growing it (one responsibility per class or function; extract a "
    "sub-package). Do not raise the baseline: `--update-baseline` only lowers it. "
    "See docs/ARCHITECTURE.md and #1249."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--update-baseline", action="store_true")
    mode.add_argument("--base-ref", metavar="REF")
    args = ap.parse_args(argv)
    if args.update_baseline:
        return update_baseline()
    baseline = load_baseline()
    bad = violations(measure_all(), baseline)
    base = base_baseline(args.base_ref) if args.base_ref else None
    if base is not None:
        bad += escalations(baseline, base)
    if bad:
        print("\n".join(["SIZE GATE FAILED:", *bad, ADVICE]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
