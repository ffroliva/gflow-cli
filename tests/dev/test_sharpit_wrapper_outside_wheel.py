"""The sharpit dev wrapper must never ship (sharpit 0.3.0 spec, hardening row A1)."""

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_wheel_packages_only_the_gflow_cli_package() -> None:
    wheel: dict[str, Any] = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/gflow_cli"]


def test_wrapper_lives_outside_the_package() -> None:
    wrapper = ROOT / "dev" / "sharpit_app.py"
    assert wrapper.is_file()
    assert ROOT / "src" / "gflow_cli" not in wrapper.parents


def test_sharpit_is_not_a_runtime_dependency() -> None:
    runtime: list[str] = _pyproject()["project"]["dependencies"]
    assert not [d for d in runtime if d.lower().startswith("sharpit")]


def test_shipped_code_never_imports_sharpit() -> None:
    offenders = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "src" / "gflow_cli").rglob("*.py")
        if "import sharpit" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
