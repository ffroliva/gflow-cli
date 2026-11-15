"""Tests for ``scripts/dev/spike_1298_pip_launcher.py`` path safety."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_DEV = Path(__file__).resolve().parents[2] / "scripts" / "dev"
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))

import spike_1298_pip_launcher as spike  # noqa: E402


@pytest.mark.parametrize("bad_name", [".", "..", "../victim", ""])
def test_cmd_venv_refuses_name_outside_spiceout_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_name: str
) -> None:
    root = tmp_path / "spiceout"
    root.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    monkeypatch.setattr(spike, "SPICEOUT_ROOT", root)
    monkeypatch.setattr(
        spike.shutil,
        "rmtree",
        lambda p: pytest.fail(f"shutil.rmtree called on {p}"),
    )

    with pytest.raises(ValueError, match="strictly inside"):
        spike.cmd_venv(argparse.Namespace(name=bad_name))


def test_cmd_venv_and_delete_commands_refuse_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "spiceout"
    root.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    monkeypatch.setattr(spike, "SPICEOUT_ROOT", root)
    monkeypatch.setattr(
        spike.shutil,
        "rmtree",
        lambda p: pytest.fail(f"shutil.rmtree called on {p}"),
    )

    for fn in (spike.cmd_venv, spike.cmd_locks, spike.cmd_renamed):
        with pytest.raises(ValueError, match="strictly inside"):
            fn(argparse.Namespace(name=str(victim)))
