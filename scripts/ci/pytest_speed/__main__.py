"""Command line: `python -m pytest_speed check <junit-folder> --config <toml>`."""

import argparse
import json
import shlex
import sys
import tomllib
from pathlib import Path

from .ab import LeaseRefused, RunFailed, run_ab_noise
from .check import check, load_exceptions, render_markdown, summary_json
from .compare import (
    first_test_cost,
    median_compare,
    noise,
    rank,
    render_compare,
    render_first_cost,
    render_rank,
)
from .durations import merge, write_baseline
from .junit import file_totals, read_run
from .vendor import DirtyTreeError, vendor


def _load(path: Path, loader):
    try:
        return loader(path)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as error:  # ValueError covers JSON, TOML
        print(f"{path}: {error}", file=sys.stderr)
        raise SystemExit(2) from None


def _read_baseline(path: Path) -> dict[str, float] | None:
    if not path.is_file():
        return None
    return {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def _read_config(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)["pytest-speed"]


def _check_command(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    cfg = _load(config_path, _read_config)
    here = config_path.parent
    names = _load(
        config_path, lambda _: [here / cfg[k] for k in ("baseline", "exceptions", "root")]
    )
    approvers = cfg.get("approvers", [])
    baseline = _load(names[0], _read_baseline)
    exceptions = _load(names[1], lambda p: load_exceptions(p, approvers))
    run = read_run(Path(args.folder), names[2])
    ratio, min_rise = cfg.get("ratio", 2.0), cfg.get("min_rise", 10.0)
    report = check(run, baseline, exceptions, ratio, min_rise, cfg.get("new_file_limit"))
    text = render_markdown(report)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(summary_json(report), indent=2), encoding="utf-8")
    return 1 if cfg.get("block", False) and report.flagged else 0


def _refresh_command(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    cfg = _load(config_path, _read_config)
    here = config_path.parent
    root, baseline_path = _load(config_path, lambda _: [here / cfg["root"], here / cfg["baseline"]])
    run = read_run(Path(args.folder), root)
    if not run.tests:
        print(f"{args.folder}: no mapped test cases; baseline left unchanged", file=sys.stderr)
        return 2
    old = _load(baseline_path, _read_baseline) or {}
    merged = merge(old, file_totals(run), root)
    write_baseline(baseline_path, merged, args.source, args.commit)
    print(f"{baseline_path}: {len(merged)} files")
    return 0


def _vendor_command(args: argparse.Namespace) -> int:
    try:
        vendor(Path(args.dest))
    except (DirtyTreeError, OSError) as error:
        print(f"vendor: {error}", file=sys.stderr)
        return 2
    return 0


def _missing_dir(paths: list[str]) -> bool:
    for path in paths:
        if not Path(path).is_dir():
            print(f"{path}: not a directory", file=sys.stderr)
            return True
    return False


def _compare_command(args: argparse.Namespace) -> int:
    if _missing_dir([*args.base, *args.new]):
        return 2
    root = Path(args.root)
    exclude = set(args.exclude)
    try:
        base, new = (
            [file_totals(read_run(Path(d), root)) for d in dirs] for dirs in (args.base, args.new)
        )
    except (OSError, ValueError) as error:
        print(f"compare: {error}", file=sys.stderr)
        return 2
    print(render_compare(median_compare(base, new, exclude)))
    print(f"\nbase noise (median spread): {noise(base, exclude):.1f}s")
    return 0


def _first_cost_command(args: argparse.Namespace) -> int:
    if _missing_dir([args.folder]):
        return 2
    try:
        rows = first_test_cost(Path(args.folder), Path(args.root))
    except (OSError, ValueError) as error:
        print(f"{args.folder}: {error}", file=sys.stderr)
        return 2
    print(render_first_cost(rows))
    return 0


def _rank_command(args: argparse.Namespace) -> int:
    if _missing_dir([args.folder]):
        return 2
    try:
        rows = rank(read_run(Path(args.folder), Path(args.root)))
    except (OSError, ValueError) as error:
        print(f"{args.folder}: {error}", file=sys.stderr)
        return 2
    print(render_rank(rows))
    return 0


def _ab_command(args: argparse.Namespace) -> int:
    if args.lease_cmd and not args.release_cmd:
        print("ab: --lease-cmd needs --release-cmd, or the lease would leak", file=sys.stderr)
        return 2
    config_path = Path(args.config)
    cfg = _load(config_path, _read_config)
    run_cmd = _load(config_path, lambda _: shlex.split(cfg["run"]))
    extra = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    if _missing_dir([args.base_dir, args.change_dir]):
        return 2
    try:
        rows, spread = run_ab_noise(
            Path(args.base_dir),
            Path(args.change_dir),
            args.rounds,
            [*run_cmd, *extra],
            Path(args.out),
            args.lease_cmd,
            args.release_cmd,
        )
    except (LeaseRefused, RunFailed, OSError, ValueError) as error:
        print(f"ab: {error}", file=sys.stderr)
        return 2
    print(render_compare(rows))
    print(f"\nbase noise (median spread): {spread:.1f}s")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pytest_speed")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("check", help="report test files that got much slower")
    cmd.add_argument("folder")
    cmd.add_argument("--config", required=True)
    cmd.add_argument("--out")
    cmd.add_argument("--json")
    cmd.set_defaults(func=_check_command)
    refresh = sub.add_parser("refresh", help="merge a run into the baseline")
    refresh.add_argument("folder")
    refresh.add_argument("--config", required=True)
    refresh.add_argument("--source", choices=["fleet", "ci"], required=True)
    refresh.add_argument("--commit", required=True)
    refresh.set_defaults(func=_refresh_command)
    vendor_cmd = sub.add_parser("vendor", help="copy the package into another repository")
    vendor_cmd.add_argument("dest")
    vendor_cmd.set_defaults(func=_vendor_command)
    cmp = sub.add_parser("compare", help="median per-file times of two sets of runs")
    cmp.add_argument("--base", nargs="+", required=True)
    cmp.add_argument("--new", nargs="+", required=True)
    cmp.add_argument("--root", required=True)
    cmp.add_argument("--exclude", nargs="*", default=[])
    cmp.set_defaults(func=_compare_command)
    first = sub.add_parser("first-cost", help="files whose first test is far slower than the rest")
    first.add_argument("folder")
    first.add_argument("--root", required=True)
    first.set_defaults(func=_first_cost_command)
    rank_cmd = sub.add_parser("rank", help="files ranked by their share of their box")
    rank_cmd.add_argument("folder")
    rank_cmd.add_argument("--root", required=True)
    rank_cmd.set_defaults(func=_rank_command)
    ab = sub.add_parser("ab", help="interleaved A/B runs of two checkouts under a lease")
    ab.add_argument("--config", required=True)
    ab.add_argument("--base-dir", required=True)
    ab.add_argument("--change-dir", required=True)
    ab.add_argument("--rounds", type=int, default=3)
    ab.add_argument("--out", required=True)
    ab.add_argument("--lease-cmd")
    ab.add_argument("--release-cmd")
    ab.add_argument("pytest_args", nargs=argparse.REMAINDER)
    ab.set_defaults(func=_ab_command)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
