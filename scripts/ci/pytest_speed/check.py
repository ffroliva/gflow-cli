"""Flag test files that got much slower than their baseline."""

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .junit import Run, file_totals


@dataclass(frozen=True)
class Exception_:  # noqa: N801 - name fixed by the interface
    ceiling_seconds: float
    reason: str
    approved_by: str
    date: str


def load_exceptions(path: Path, approvers: list[str]) -> dict[str, Exception_]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = {
        name: Exception_(**{**fields, "ceiling_seconds": float(fields["ceiling_seconds"])})
        for name, fields in raw.items()
    }
    return {
        n: e
        for n, e in entries.items()
        if e.approved_by in approvers and e.approved_by != "PENDING"
    }


def box_ratio(
    totals: dict[str, float],
    baseline: dict[str, float],
    min_baseline: float = 1.0,
    min_files: int = 5,
) -> float:
    ratios = [
        seconds / baseline[name]
        for name, seconds in totals.items()
        if baseline.get(name, 0.0) >= min_baseline
    ]
    return median(ratios) if len(ratios) >= min_files else 1.0


@dataclass(frozen=True)
class Row:
    file: str
    group: str
    baseline: float | None
    measured: float
    tests: int
    ratio: float | None
    box_ratio: float
    verdict: str

    @property
    def per_test(self) -> float:
        return self.measured / self.tests if self.tests else 0.0


@dataclass(frozen=True)
class Report:
    rows: list[Row]
    not_measured: int
    unmapped: int
    unreadable: int
    measured_any: bool
    has_baseline: bool

    @property
    def flagged(self) -> list[Row]:
        return [r for r in self.rows if r.verdict in ("flagged", "over-exception")]


def _main_group(run: Run, name: str) -> str:
    spent = {g: files[name] for g, files in run.groups.items() if name in files}
    return min(spent, key=lambda g: (-spent[g], g))


def _group_ratios(
    assigned: dict[str, str], totals: dict[str, float], baseline: dict[str, float]
) -> dict[str, float]:
    members: dict[str, dict[str, float]] = {}
    for name, group in assigned.items():
        members.setdefault(group, {})[name] = totals[name]
    return {g: box_ratio(t, baseline) for g, t in members.items()}


def _verdict(
    measured: float,
    base: float | None,
    box: float,
    exception: Exception_ | None,
    ratio: float,
    min_rise: float,
    new_file_limit: float | None,
) -> str:
    if base is None:
        over = new_file_limit is not None and measured > new_file_limit
        return "flagged" if over else "new"
    if exception is not None:
        return "excepted" if measured <= exception.ceiling_seconds else "over-exception"
    if measured - base < min_rise:
        return "ok"
    return "flagged" if base == 0.0 or measured / base / box >= ratio else "ok"


def check(
    run: Run,
    baseline: dict[str, float] | None,
    exceptions: dict[str, Exception_],
    ratio: float = 2.0,
    min_rise: float = 10.0,
    new_file_limit: float | None = None,
) -> Report:
    totals = file_totals(run)
    base = baseline or {}
    assigned = {name: _main_group(run, name) for name in totals}
    boxes = _group_ratios(assigned, totals, base)
    rows = []
    for name, measured in sorted(totals.items()):
        b = base.get(name)
        box = boxes[assigned[name]]
        exception = exceptions.get(name)
        verdict = _verdict(measured, b, box, exception, ratio, min_rise, new_file_limit)
        file_ratio = measured / b if b else None
        tests = run.tests.get(name, 0)
        rows.append(Row(name, assigned[name], b, measured, tests, file_ratio, box, verdict))
    return Report(
        rows,
        sum(1 for name in base if name not in totals),
        run.unmapped,
        run.unreadable,
        bool(run.tests),
        baseline is not None,
    )


def _escape(path: str) -> str:
    return path.replace("|", "\\|").replace("`", "\\`").replace("\r", " ").replace("\n", " ")


def _rise(row: Row) -> float:
    return row.measured - (row.baseline or 0.0)


def _ordered(report: Report) -> list[Row]:
    new = [r for r in report.rows if r.verdict == "new"]
    rest = [r for r in report.rows if r.verdict in ("ok", "excepted")]
    return report.flagged + new + sorted(rest, key=_rise, reverse=True)[:10]


def _line(row: Row) -> str:
    base = "-" if row.baseline is None else f"{row.baseline:.1f}"
    ratio = "-" if row.ratio is None else f"{row.ratio:.2f}"
    return (
        f"| {_escape(row.file)} | {row.group} | {base} | {row.measured:.1f} | {row.tests} "
        f"| {row.per_test:.2f} | {ratio} | {row.box_ratio:.2f} | {row.verdict} |"
    )


def render_markdown(report: Report, limit: int = 30) -> str:
    new = sum(1 for r in report.rows if r.verdict == "new")
    head = (
        f"measured: {len(report.rows)} · flagged: {len(report.flagged)} · new: {new} · "
        f"not measured: {report.not_measured} · unmapped: {report.unmapped} · "
        f"unreadable: {report.unreadable}"
    )
    if not report.has_baseline:
        head += " · no baseline"
    if not report.measured_any:
        return f"{head}\n\nno measurement: the folder held no mapped test cases\n"
    table = [
        "| file | group | baseline s | measured s | tests | s/test | ratio | box | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
        *[_line(r) for r in _ordered(report)[:limit]],
    ]
    return head + "\n\n" + "\n".join(table) + "\n"


def summary_json(report: Report) -> dict:
    return {
        "measured": len(report.rows),
        "flagged": len(report.flagged),
        "new": sum(1 for r in report.rows if r.verdict == "new"),
        "not_measured": report.not_measured,
        "unmapped": report.unmapped,
        "unreadable": report.unreadable,
        "has_baseline": report.has_baseline,
        "flagged_files": [r.file for r in report.flagged],
    }
