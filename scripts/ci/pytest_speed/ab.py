"""Paired A/B runs: alternate two checkouts in ABBA order, each pytest call under a lease."""

import shlex
import subprocess
from pathlib import Path

from .compare import median_compare, noise
from .junit import file_totals, read_run


class LeaseRefused(Exception):  # noqa: N818
    """The lease command exited non-zero, so no pytest call is made."""


class RunFailed(Exception):  # noqa: N818
    """pytest ended with something other than pass or test failures."""


def plan_rounds(rounds: int) -> list[str]:
    order: list[str] = []
    for n in range(rounds):
        order += ["base", "change"] if n % 2 == 0 else ["change", "base"]
    return order


def _changed_files(base_dir: Path, change_dir: Path, runner) -> set[str]:
    head = runner(["git", "-C", str(base_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode != 0:
        return set()
    diff = runner(
        ["git", "-C", str(change_dir), "diff", "--name-only", head.stdout.strip()],
        capture_output=True,
        text=True,
    )
    return set(diff.stdout.split()) if diff.returncode == 0 else set()


def _one_round(side_dir: Path, run_cmd, xml: Path, lease_cmd, release_cmd, runner) -> None:
    if lease_cmd and runner(shlex.split(lease_cmd), cwd=side_dir).returncode != 0:
        raise LeaseRefused(f"lease refused: {lease_cmd}")
    release = None
    try:
        code = runner([*run_cmd, f"--junitxml={xml}"], cwd=side_dir).returncode
    finally:
        if release_cmd:
            release = runner(shlex.split(release_cmd), cwd=side_dir)
    if release is not None and release.returncode != 0:
        raise LeaseRefused(f"release failed, the lease may still be held: {release_cmd}")
    if code not in (0, 1):  # 1 = tests failed, which is a normal round
        raise RunFailed(f"pytest exited {code} in {side_dir}")


def run_ab_noise(
    base_dir: Path,
    change_dir: Path,
    rounds: int,
    run_cmd: list[str],
    out_dir: Path,
    lease_cmd: str | None,
    release_cmd: str | None,
    runner=subprocess.run,
) -> tuple[list[tuple[str, float, float, float]], float]:
    out_dir = out_dir.resolve()
    dirs = {"base": base_dir, "change": change_dir}
    seen = {"base": 0, "change": 0}
    runs: dict[str, list[dict[str, float]]] = {"base": [], "change": []}
    for side in plan_rounds(rounds):
        seen[side] += 1
        # One folder per round, because read_run reads a folder as one run.
        folder = out_dir / f"{side}-{seen[side]}"
        folder.mkdir(parents=True, exist_ok=True)
        _one_round(dirs[side], run_cmd, folder / "report.xml", lease_cmd, release_cmd, runner)
        runs[side].append(file_totals(read_run(folder, dirs[side])))
    exclude = _changed_files(base_dir, change_dir, runner)
    rows = median_compare(runs["base"], runs["change"])
    return rows, noise(runs["base"], exclude)


def run_ab(*args, **kwargs) -> list[tuple[str, float, float, float]]:
    return run_ab_noise(*args, **kwargs)[0]
