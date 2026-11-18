"""End-to-end tests for `gflow update` through real installers in tmp venvs (#1298).

Validates that `gflow update` inside real installer environments (pip venv,
pip --user, uv tool, pipx) either succeeds and leaves a working installation,
or refuses before pip runs when another process holds a launcher.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

# needs_network: gflow's dependencies come from the index; only gflow-cli itself is local.
pytestmark = [pytest.mark.e2e, pytest.mark.e2e_update, pytest.mark.needs_network]


@pytest.fixture(scope="session", autouse=True)
def _private_installer_caches(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Keep pip's and uv's caches under tmp, so a run writes nothing outside it."""
    caches = tmp_path_factory.mktemp("installer_caches")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PIP_CACHE_DIR", str(caches / "pip"))
        mp.setenv("UV_CACHE_DIR", str(caches / "uv"))
        yield


_PROBE_DIST = "gflow-e2e-extra-probe"
#: The `e2e-probe` extra pins the probe at this version in LOW and HIGH (#1322). Local
#: version labels cannot be uploaded to PyPI, so no index copy can stand in for a wheel.
_PROBE_PINS = {"0.0.1": "1.0+e2e", "9999.0.0": "2.0+e2e"}


def _build_probe_wheels(wheels_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Two empty wheels of `_PROBE_DIST`, one per pinned version."""
    for ver in _PROBE_PINS.values():
        src = tmp_path_factory.mktemp(f"probe_{ver.replace('.', '_').replace('+', '_')}")
        (src / "pyproject.toml").write_text(
            f'[project]\nname = "{_PROBE_DIST}"\nversion = "{ver}"\n'
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
            "[tool.hatch.build.targets.wheel]\nbypass-selection = true\n",
            encoding="utf-8",
        )
        res = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(wheels_dir), str(src)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"probe build failed for {ver}:\n{res.stderr}"


@pytest.fixture(scope="session")
def update_wheels(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """Build LOW (0.0.1) and HIGH (9999.0.0) wheels from the current working tree.

    Each carries an `e2e-probe` extra whose pin moves between them (`_PROBE_PINS`)."""
    worktree_root = Path(__file__).resolve().parent.parent.parent
    wheels_dir = tmp_path_factory.mktemp("update_wheels")
    _build_probe_wheels(wheels_dir, tmp_path_factory)

    for ver in ("0.0.1", "9999.0.0"):
        src_tmp = tmp_path_factory.mktemp(f"src_{ver.replace('.', '_')}")
        shutil.copy(worktree_root / "pyproject.toml", src_tmp)
        shutil.copy(worktree_root / "README.md", src_tmp)
        shutil.copy(worktree_root / "LICENSE", src_tmp)
        shutil.copy(worktree_root / "hatch_build.py", src_tmp)
        shutil.copytree(worktree_root / "src", src_tmp / "src")
        shutil.copytree(worktree_root / "docs", src_tmp / "docs")

        pyproj = src_tmp / "pyproject.toml"
        pyproj_content = pyproj.read_text(encoding="utf-8")
        pyproj_content = re.sub(
            r'version\s*=\s*"[^"]+"', f'version = "{ver}"', pyproj_content, count=1
        )
        pyproj_content = pyproj_content.replace(
            "[project.optional-dependencies]\n",
            f'[project.optional-dependencies]\ne2e-probe = ["{_PROBE_DIST}=={_PROBE_PINS[ver]}"]\n',
            1,
        )
        pyproj.write_text(pyproj_content, encoding="utf-8", newline="\n")

        init_py = src_tmp / "src" / "gflow_cli" / "__init__.py"
        init_content = init_py.read_text(encoding="utf-8")
        init_content = re.sub(
            r'__version__\s*=\s*"[^"]+"', f'__version__ = "{ver}"', init_content, count=1
        )
        init_py.write_text(init_content, encoding="utf-8", newline="\n")

        env = {**os.environ, "UV_LINK_MODE": "copy", "PYTHONUTF8": "1"}
        build_res = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(wheels_dir), str(src_tmp)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert build_res.returncode == 0, f"uv build failed for {ver}:\n{build_res.stderr}"

    low_candidates = list(wheels_dir.glob("gflow_cli-0.0.1-*.whl"))
    high_candidates = list(wheels_dir.glob("gflow_cli-9999.0.0-*.whl"))
    assert len(low_candidates) == 1, f"Expected 1 low wheel, found: {low_candidates}"
    assert len(high_candidates) == 1, f"Expected 1 high wheel, found: {high_candidates}"
    return wheels_dir, low_candidates[0], high_candidates[0]


def _make_pip_venv(
    venv_dir: Path, wheels_dir: Path, spec: str = "gflow-cli==0.0.1"
) -> tuple[Path, Path]:
    """Create a clean venv with pip and install LOW 0.0.1 (`spec`) from wheels_dir."""
    # Use Python's stdlib venv (py -3.13 or sys._base_executable)
    # to match the Windows pip install environment from the spike
    venv_cmd = ["py", "-3.13", "-m", "venv", str(venv_dir)]
    try:
        res = subprocess.run(venv_cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            res = subprocess.run(
                [sys._base_executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
    except FileNotFoundError:
        res = subprocess.run(
            [sys._base_executable, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
    assert res.returncode == 0, f"venv creation failed:\n{res.stderr}"

    if sys.platform == "win32":
        python_exe = venv_dir / "Scripts" / "python.exe"
        gflow_exe = venv_dir / "Scripts" / "gflow.exe"
    else:
        python_exe = venv_dir / "bin" / "python"
        gflow_exe = venv_dir / "bin" / "gflow"

    assert python_exe.exists(), f"python missing at {python_exe}"

    install_res = subprocess.run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--find-links",
            wheels_dir.as_uri(),
            spec,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert install_res.returncode == 0, f"pip install failed:\n{install_res.stderr}"
    assert gflow_exe.exists(), f"gflow launcher missing at {gflow_exe}"

    # Ensure no direct_url.json so it is treated as an index install
    for p in venv_dir.glob("**/gflow_cli-*.dist-info/direct_url.json"):
        p.unlink(missing_ok=True)

    return python_exe, gflow_exe


def test_pip_venv_update_keeps_a_working_install(
    tmp_path: Path, update_wheels: tuple[Path, Path, Path]
) -> None:
    """Case A: pip venv uncontested upgrade from 0.0.1 to 9999.0.0."""
    wheels_dir, _, _ = update_wheels
    venv_dir = tmp_path / "venv_a"
    home_dir = tmp_path / "home_a"
    python_exe, gflow_exe = _make_pip_venv(venv_dir, wheels_dir)

    env = {
        **os.environ,
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": wheels_dir.as_uri(),
        "GFLOW_CLI_HOME": str(home_dir),
        "GFLOW_CLI_UPDATE_CHECK": "0",
        "GFLOW_CLI_TELEMETRY": "0",
        "UV_LINK_MODE": "copy",
        "PYTHONUTF8": "1",
    }

    # Execute update via the launcher
    up_res = subprocess.run(
        [str(gflow_exe), "update"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert up_res.returncode == 0, (
        f"gflow update failed (exit {up_res.returncode}):\n{up_res.stdout}\n{up_res.stderr}"
    )

    # Verify import reports 9999.0.0
    probe = subprocess.run(
        [
            str(python_exe),
            "-I",
            "-c",
            "import gflow_cli.cli, gflow_cli; print(gflow_cli.__version__)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, f"Import probe failed:\n{probe.stderr}"
    assert probe.stdout.strip() == "9999.0.0"

    # The new gflow --version runs
    ver_res = subprocess.run(
        [str(gflow_exe), "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert ver_res.returncode == 0
    assert "9999.0.0" in ver_res.stdout

    # After a second gflow --version and update --check, no *.gflow-update-*.old is left
    subprocess.run([str(gflow_exe), "--version"], capture_output=True, env=env, check=True)
    subprocess.run([str(gflow_exe), "update", "--check"], capture_output=True, env=env, check=True)

    scripts_dir = gflow_exe.parent
    old_files = list(scripts_dir.glob("*.gflow-update-*.old"))
    assert old_files == [], f"Unexpected .old files remain: {old_files}"


def test_pip_update_moves_an_installed_extras_pin(
    tmp_path: Path, update_wheels: tuple[Path, Path, Path]
) -> None:
    """#1322: an extra installed with LOW is upgraded to HIGH's pin, not left behind."""
    wheels_dir, _, _ = update_wheels
    python_exe, gflow_exe = _make_pip_venv(
        tmp_path / "venv_x", wheels_dir, "gflow-cli[e2e-probe]==0.0.1"
    )
    env = {
        **os.environ,
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": wheels_dir.as_uri(),
        "GFLOW_CLI_HOME": str(tmp_path / "home_x"),
        "GFLOW_CLI_UPDATE_CHECK": "0",
        "GFLOW_CLI_TELEMETRY": "0",
        "PYTHONUTF8": "1",
    }
    up_res = subprocess.run(
        [str(gflow_exe), "update"], capture_output=True, text=True, env=env, check=False
    )
    assert up_res.returncode == 0, f"gflow update failed:\n{up_res.stdout}\n{up_res.stderr}"
    probe = subprocess.run(
        [
            str(python_exe),
            "-I",
            "-c",
            "import importlib.metadata as m;"
            f"print(m.version('gflow-cli'), m.version('{_PROBE_DIST}'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.stdout.split() == ["9999.0.0", "2.0+e2e"], probe.stderr


def _wait_until_mcp_answers(child: subprocess.Popen[str], timeout: float = 120) -> None:
    """Send MCP `initialize` and wait for its reply: by then the server has imported
    gflow and holds its launcher, which a fixed sleep could not guarantee."""
    assert child.stdin is not None and child.stdout is not None
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "e2e-update", "version": "0"},
        },
    }
    child.stdin.write(json.dumps(request) + "\n")
    child.stdin.flush()
    reply: list[str] = []
    reader = threading.Thread(target=lambda: reply.append(child.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    assert reply and '"id"' in reply[0], f"mcp run did not answer initialize: {reply}"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific launcher lock test")
def test_pip_update_refuses_untouched_while_another_gflow_runs(
    tmp_path: Path, update_wheels: tuple[Path, Path, Path]
) -> None:
    """Case B: Windows only: live gflow.exe mcp run in the same venv refuses before pip."""
    wheels_dir, _, _ = update_wheels
    venv_dir = tmp_path / "venv_b"
    home_dir = tmp_path / "home_b"
    python_exe, gflow_exe = _make_pip_venv(venv_dir, wheels_dir)

    env = {
        **os.environ,
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": wheels_dir.as_uri(),
        "GFLOW_CLI_HOME": str(home_dir),
        "GFLOW_CLI_UPDATE_CHECK": "0",
        "GFLOW_CLI_TELEMETRY": "0",
        "UV_LINK_MODE": "copy",
        "PYTHONUTF8": "1",
    }

    # Start child: gflow.exe mcp run with stdin=PIPE
    child = subprocess.Popen(
        [str(gflow_exe), "mcp", "run"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        _wait_until_mcp_answers(child)  # a fixed sleep raced a loaded machine (review)
        assert child.poll() is None, "Child mcp run died prematurely"

        up_res = subprocess.run(
            [str(gflow_exe), "update"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert up_res.returncode == 11, (
            f"Expected exit 11, got {up_res.returncode}:\n{up_res.stdout}\n{up_res.stderr}"
        )

        # The refusal names a launcher and says it is in use
        combined_err = " ".join((up_res.stdout + up_res.stderr).split())  # undo line wraps
        assert "is in use by another program" in combined_err, combined_err
        assert ".exe" in combined_err, combined_err

        # The version is still 0.0.1 and the import works
        probe = subprocess.run(
            [
                str(python_exe),
                "-I",
                "-c",
                "import gflow_cli.cli, gflow_cli; print(gflow_cli.__version__)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert probe.returncode == 0, f"Import probe failed after refusal: {probe.stderr}"
        assert probe.stdout.strip() == "0.0.1"

        # Every launcher is present, and there is no .old
        scripts_dir = gflow_exe.parent
        assert (scripts_dir / "gflow.exe").exists()
        assert (scripts_dir / "flow.exe").exists()
        assert (scripts_dir / "gflow-cli.exe").exists()
        assert list(scripts_dir.glob("*.gflow-update-*.old")) == []
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific user install test")
def test_pip_user_update_keeps_a_working_install(
    tmp_path: Path, update_wheels: tuple[Path, Path, Path]
) -> None:
    """Case C: Windows only: a --user install with PYTHONUSERBASE under tmp."""
    wheels_dir, _, _ = update_wheels
    user_base = tmp_path / "userbase"
    home_dir = tmp_path / "home_c"

    install_env = {
        **os.environ,
        "PYTHONUSERBASE": str(user_base),
        "PIP_BREAK_SYSTEM_PACKAGES": "1",
        "UV_LINK_MODE": "copy",
        "PYTHONUTF8": "1",
    }

    # Install LOW to user site using sys._base_executable (deps from PyPI)
    install_res = subprocess.run(
        [
            sys._base_executable,
            "-m",
            "pip",
            "install",
            "--user",
            "--no-warn-script-location",
            "--find-links",
            wheels_dir.as_uri(),
            "gflow-cli==0.0.1",
        ],
        capture_output=True,
        text=True,
        env=install_env,
        check=False,
    )
    assert install_res.returncode == 0, f"user pip install failed:\n{install_res.stderr}"

    for p in user_base.glob("**/gflow_cli-*.dist-info/direct_url.json"):
        p.unlink(missing_ok=True)

    launchers = list(user_base.glob("**/Scripts/gflow.exe")) + list(user_base.glob("**/gflow.exe"))
    assert len(launchers) >= 1, f"gflow.exe not found under {user_base}"
    gflow_exe = launchers[0]

    update_env = {
        **os.environ,
        "PYTHONUSERBASE": str(user_base),
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": wheels_dir.as_uri(),
        "PIP_BREAK_SYSTEM_PACKAGES": "1",
        "GFLOW_CLI_HOME": str(home_dir),
        "GFLOW_CLI_UPDATE_CHECK": "0",
        "GFLOW_CLI_TELEMETRY": "0",
        "UV_LINK_MODE": "copy",
        "PYTHONUTF8": "1",
    }

    up_res = subprocess.run(
        [str(gflow_exe), "update"],
        capture_output=True,
        text=True,
        env=update_env,
        check=False,
    )
    assert up_res.returncode == 0, (
        f"gflow update failed (exit {up_res.returncode}):\n{up_res.stdout}\n{up_res.stderr}"
    )

    probe = subprocess.run(
        [
            sys._base_executable,
            "-c",
            "import gflow_cli.cli, gflow_cli; print(gflow_cli.__version__)",
        ],
        capture_output=True,
        text=True,
        env=update_env,
        check=False,
    )
    assert probe.returncode == 0, f"Import probe failed:\n{probe.stderr}"
    assert probe.stdout.strip() == "9999.0.0"

    ver_res = subprocess.run(
        [str(gflow_exe), "--version"],
        capture_output=True,
        text=True,
        env=update_env,
        check=False,
    )
    assert ver_res.returncode == 0
    assert "9999.0.0" in ver_res.stdout

    # Clean sweep verification
    subprocess.run([str(gflow_exe), "--version"], capture_output=True, env=update_env, check=True)
    subprocess.run(
        [str(gflow_exe), "update", "--check"], capture_output=True, env=update_env, check=True
    )
    scripts_dir = gflow_exe.parent
    old_files = list(scripts_dir.glob("*.gflow-update-*.old"))
    assert old_files == [], f"Unexpected .old files remain: {old_files}"


def test_uv_tool_update_still_works(tmp_path: Path, update_wheels: tuple[Path, Path, Path]) -> None:
    """Case D: uv tool upgrade still works and leaves a working installation."""
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")

    wheels_dir, _, _ = update_wheels
    tool_dir = tmp_path / "uv_tools"
    bin_dir = tmp_path / "uv_bin"
    home_dir = tmp_path / "home_d"

    env = {
        **os.environ,
        "UV_TOOL_DIR": str(tool_dir),
        "UV_TOOL_BIN_DIR": str(bin_dir),
        "UV_LINK_MODE": "copy",
        "UV_FIND_LINKS": wheels_dir.as_uri(),
        "UV_NO_INDEX": "1",
        "GFLOW_CLI_HOME": str(home_dir),
        "GFLOW_CLI_UPDATE_CHECK": "0",
        "GFLOW_CLI_TELEMETRY": "0",
        "PYTHONUTF8": "1",
    }

    # Install LOW 0.0.1 via uv tool install
    install_res = subprocess.run(
        ["uv", "tool", "install", "--find-links", wheels_dir.as_uri(), "gflow-cli==0.0.1"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert install_res.returncode == 0, f"uv tool install failed:\n{install_res.stderr}"

    # Simulate index install by removing specifier pin from uv-receipt.toml
    # and stripping direct_url.json
    receipt_file = tool_dir / "gflow-cli" / "uv-receipt.toml"
    if receipt_file.exists():
        content = receipt_file.read_text(encoding="utf-8")
        content = re.sub(r',\s*specifier\s*=\s*"==0\.0\.1"', "", content)
        content = re.sub(r'specifier\s*=\s*"==0\.0\.1",?\s*', "", content)
        receipt_file.write_text(content, encoding="utf-8")
    for p in tool_dir.glob("**/gflow_cli-*.dist-info/direct_url.json"):
        p.unlink(missing_ok=True)

    gflow_bin = bin_dir / ("gflow.exe" if sys.platform == "win32" else "gflow")
    assert gflow_bin.exists(), f"Launcher missing at {gflow_bin}"

    up_res = subprocess.run(
        [str(gflow_bin), "update"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert up_res.returncode == 0, f"uv tool gflow update failed:\n{up_res.stdout}\n{up_res.stderr}"

    ver_res = subprocess.run(
        [str(gflow_bin), "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert ver_res.returncode == 0
    assert "9999.0.0" in ver_res.stdout


def test_pipx_update_still_works(tmp_path: Path, update_wheels: tuple[Path, Path, Path]) -> None:
    """Case E: pipx update still works and leaves a working installation."""
    if shutil.which("pipx") is None:
        pytest.skip("pipx is not installed")

    wheels_dir, _, _ = update_wheels
    pipx_home = tmp_path / "pipx_home"
    bin_dir = tmp_path / "pipx_bin"
    home_dir = tmp_path / "home_e"

    env = {
        **os.environ,
        "PIPX_HOME": str(pipx_home),
        "PIPX_BIN_DIR": str(bin_dir),
        "PIP_FIND_LINKS": wheels_dir.as_uri(),
        "PIP_NO_INDEX": "1",
        "GFLOW_CLI_HOME": str(home_dir),
        "GFLOW_CLI_UPDATE_CHECK": "0",
        "GFLOW_CLI_TELEMETRY": "0",
        "UV_LINK_MODE": "copy",
        "PYTHONUTF8": "1",
    }

    # The install needs the index (gflow's dependencies, and pipx's own shared pip);
    # only the update must resolve gflow-cli from the local wheels.
    install_env = {k: v for k, v in env.items() if k != "PIP_NO_INDEX"}
    install_res = subprocess.run(
        [
            "pipx",
            "install",
            "--pip-args",
            f"--find-links {wheels_dir.as_uri()}",
            "gflow-cli==0.0.1",
        ],
        capture_output=True,
        text=True,
        env=install_env,
        check=False,
    )
    assert install_res.returncode == 0, f"pipx install failed:\n{install_res.stderr}"

    for p in pipx_home.glob("**/gflow_cli-*.dist-info/direct_url.json"):
        p.unlink(missing_ok=True)
    meta_file = pipx_home / "venvs" / "gflow-cli" / "pipx_metadata.json"
    if meta_file.exists():
        content = meta_file.read_text(encoding="utf-8")
        content = content.replace("gflow-cli==0.0.1", "gflow-cli")
        meta_file.write_text(content, encoding="utf-8")

    gflow_bin = bin_dir / ("gflow.exe" if sys.platform == "win32" else "gflow")
    assert gflow_bin.exists(), f"Launcher missing at {gflow_bin}"

    up_res = subprocess.run(
        [str(gflow_bin), "update"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert up_res.returncode == 0, f"pipx gflow update failed:\n{up_res.stdout}\n{up_res.stderr}"

    ver_res = subprocess.run(
        [str(gflow_bin), "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert ver_res.returncode == 0
    assert "9999.0.0" in ver_res.stdout
