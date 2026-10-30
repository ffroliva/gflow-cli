"""Merge a run's per-file times into the stored baseline."""

import json
from pathlib import Path


def merge(old: dict[str, float], measured: dict[str, float], root: Path) -> dict[str, float]:
    """New times win, absent files keep their old time, vanished files are dropped."""
    combined = {**old, **{name: round(seconds, 1) for name, seconds in measured.items()}}
    return {name: combined[name] for name in sorted(combined) if (root / name).is_file()}


def write_baseline(path: Path, data: dict[str, float], source: str, commit: str) -> None:
    body = json.dumps(data, indent=1) + "\n"
    path.write_bytes(body.encode("utf-8"))
    meta = json.dumps({"source": source, "commit": commit}, indent=1) + "\n"
    path.with_name(f"{path.stem}.meta.json").write_bytes(meta.encode("utf-8"))
