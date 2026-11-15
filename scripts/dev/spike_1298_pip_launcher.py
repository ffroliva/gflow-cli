#!/usr/bin/env python3
# ruff: noqa: S603, S607
"""Spike: #1298 pip update vs the running gflow.exe launcher on Windows.

Measures on Windows:
1. `build`: Archives HEAD, sets version to 0.98.99, builds wheel with uv.
2. `venv NAME`: Creates fresh venv, installs gflow-cli 0.98.99 via pip with --find-links.
3. `reproduce NAME [--tmp-dir DIR]`: Runs `gflow.exe update` (no flags) from launcher.
4. `locks NAME`: Measures Windows file lock semantics on running gflow.exe & loaded .pyd.
5. `renamed NAME`: Tests if renaming running launcher aside allows clean pip upgrade.
6. `pymodule NAME`: Control run using `python -m gflow_cli update`.
7. `pipdry NAME`: Traces pip upgrade execution order (uninstall vs install) via pip -v.
8. `check-temp NAME`: Compares tempdir volume vs venv drive and tests custom TMP/TEMP.

Stdlib only. Run with `py -3.13`.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SPICEOUT_ROOT = REPO_ROOT / "scripts" / "dev" / "_spike_out" / "1298"
WHEELS_DIR = SPICEOUT_ROOT / "wheels"
SRC_DIR = SPICEOUT_ROOT / "src"


def _venv_dir(name: str) -> pathlib.Path:
    root = SPICEOUT_ROOT.resolve()
    venv_dir = (root / name).resolve()
    if pathlib.Path(name).is_absolute() or venv_dir == root or root not in venv_dir.parents:
        raise ValueError(f"venv name must resolve strictly inside {root}: {name!r}")
    return venv_dir


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(val)))
            else:
                col_widths.append(len(str(val)))

    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "-|-".join("-" * col_widths[i] for i in range(len(headers)))
    print(f"\n| {header_line} |")
    print(f"| {sep_line} |")
    for row in rows:
        row_line = " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row))
        print(f"| {row_line} |")
    print()


def cmd_build(args: argparse.Namespace) -> int:
    print("=== Subcommand: build ===")
    SPICEOUT_ROOT.mkdir(parents=True, exist_ok=True)
    WHEELS_DIR.mkdir(parents=True, exist_ok=True)

    if SRC_DIR.exists():
        shutil.rmtree(SRC_DIR)
    SRC_DIR.mkdir(parents=True, exist_ok=True)

    tar_path = SPICEOUT_ROOT / "src.tar"
    print(f"[build] Creating git archive at {tar_path}...")
    subprocess.run(
        ["git", "archive", "HEAD", "--format=tar", "-o", str(tar_path)],
        check=True,
        cwd=REPO_ROOT,
    )

    print(f"[build] Extracting archive to {SRC_DIR}...")
    with tarfile.open(tar_path) as tar:
        tar.extractall(SRC_DIR, filter="data")
    if tar_path.exists():
        tar_path.unlink()

    # Modify pyproject.toml
    pyproject_file = SRC_DIR / "pyproject.toml"
    content = pyproject_file.read_text(encoding="utf-8")
    assert 'version = "0.99.0"' in content, "pyproject.toml does not contain version 0.99.0"
    content = content.replace('version = "0.99.0"', 'version = "0.98.99"')
    pyproject_file.write_text(content, encoding="utf-8")

    # Modify src/gflow_cli/__init__.py
    init_file = SRC_DIR / "src" / "gflow_cli" / "__init__.py"
    init_content = init_file.read_text(encoding="utf-8")
    assert '__version__ = "0.99.0"' in init_content, "__init__.py does not contain version 0.99.0"
    init_content = init_content.replace('__version__ = "0.99.0"', '__version__ = "0.98.99"')
    init_file.write_text(init_content, encoding="utf-8")

    # Clean existing wheels
    for old_whl in WHEELS_DIR.glob("*.whl"):
        old_whl.unlink()

    print("[build] Building wheel with uv...")
    env = {**os.environ, "UV_LINK_MODE": "copy"}
    res = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(WHEELS_DIR), str(SRC_DIR)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if res.returncode != 0:
        print(f"[build] uv build failed (exit {res.returncode}):\n{res.stderr}")
        return res.returncode

    wheels = list(WHEELS_DIR.glob("*.whl"))
    rows = []
    for whl in wheels:
        size = f"{whl.stat().st_size:,} bytes"
        rows.append([whl.name, "0.98.99", size, "SUCCESS"])

    _print_table(["Wheel", "Version", "Size", "Status"], rows)
    return 0


def cmd_venv(args: argparse.Namespace) -> int:
    name = args.name
    print(f"=== Subcommand: venv {name} ===")
    venv_dir = _venv_dir(name)
    if venv_dir.exists():
        print(f"[venv] Removing existing {venv_dir}...")
        shutil.rmtree(venv_dir)

    print(f"[venv] Creating venv with py -3.13 at {venv_dir}...")
    subprocess.run(["py", "-3.13", "-m", "venv", str(venv_dir)], check=True)

    python_exe = venv_dir / "Scripts" / "python.exe"
    assert python_exe.exists(), f"Python not found at {python_exe}"

    print("[venv] Installing gflow-cli==0.98.99 with pip...")
    res = subprocess.run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--find-links",
            str(WHEELS_DIR),
            "gflow-cli==0.98.99",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        print(f"[venv] pip install failed (exit {res.returncode}):\n{res.stderr}")
        return res.returncode

    # Probe version
    probe = subprocess.run(
        [str(python_exe), "-I", "-c", "import gflow_cli; print(gflow_cli.__version__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    ver = probe.stdout.strip()
    gflow_exe = venv_dir / "Scripts" / "gflow.exe"

    rows = [[name, str(venv_dir), ver, str(gflow_exe.exists()), f"exit {res.returncode}"]]
    _print_table(["Venv", "Path", "Installed Version", "gflow.exe exists", "pip status"], rows)
    return 0


def cmd_reproduce(args: argparse.Namespace) -> int:
    name = args.name
    print(f"=== Subcommand: reproduce {name} ===")
    venv_dir = _venv_dir(name)
    gflow_exe = venv_dir / "Scripts" / "gflow.exe"
    python_exe = venv_dir / "Scripts" / "python.exe"

    if not gflow_exe.exists():
        print(f"Error: {gflow_exe} does not exist. Run 'venv {name}' first.")
        return 1

    env = os.environ.copy()
    if args.tmp_dir:
        tmp_p = pathlib.Path(args.tmp_dir).resolve()
        tmp_p.mkdir(parents=True, exist_ok=True)
        env["TMP"] = str(tmp_p)
        env["TEMP"] = str(tmp_p)
        print(f"[reproduce] Overriding TMP/TEMP to {tmp_p}")

    print(f"[reproduce] Running {gflow_exe} update...")
    update_res = subprocess.run(
        [str(gflow_exe), "update"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    log_path = venv_dir / "reproduce.log"
    log_content = (
        f"=== STDOUT ===\n{update_res.stdout}\n"
        f"=== STDERR ===\n{update_res.stderr}\n"
        f"=== EXIT CODE: {update_res.returncode} ===\n"
    )
    log_path.write_text(log_content, encoding="utf-8")
    print(f"[reproduce] Output saved to {log_path}")

    # Search for WinError 32
    has_winerror_32 = "WinError 32" in update_res.stderr or "WinError 32" in update_res.stdout

    # Probe import
    probe = subprocess.run(
        [str(python_exe), "-I", "-c", "import gflow_cli, sys; print(gflow_cli.__version__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    import_ok = probe.returncode == 0
    err_lines = probe.stderr.strip().splitlines()
    last_err = err_lines[-1] if err_lines else "no err"
    import_report = (
        probe.stdout.strip() if import_ok else f"FAILED (exit {probe.returncode}: {last_err})"
    )

    # List gflow*.exe*
    gflow_files = list((venv_dir / "Scripts").glob("gflow*.exe*"))
    file_summary = ", ".join(f.name for f in gflow_files) if gflow_files else "NONE"

    # pip list
    pip_list = subprocess.run(
        [str(python_exe), "-m", "pip", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    gflow_in_pip = "NOT LISTED"
    for line in pip_list.stdout.splitlines():
        if line.lower().startswith("gflow-cli"):
            gflow_in_pip = line.strip()

    rows = [
        [
            name,
            f"exit {update_res.returncode}",
            f"{has_winerror_32}",
            import_report,
            file_summary,
            gflow_in_pip,
        ]
    ]
    _print_table(
        ["Venv", "update Exit", "WinError 32", "Import Probe", "gflow*.exe* files", "pip list"],
        rows,
    )
    return 0


def cmd_locks(args: argparse.Namespace) -> int:
    name = args.name
    print(f"=== Subcommand: locks {name} ===")
    venv_dir = _venv_dir(name)
    gflow_exe = venv_dir / "Scripts" / "gflow.exe"

    if not gflow_exe.exists():
        print(f"Error: {gflow_exe} does not exist.")
        return 1

    # Start child: gflow.exe mcp run
    print(f"[locks] Starting child: {gflow_exe} mcp run...")
    proc = subprocess.Popen(
        [str(gflow_exe), "mcp", "run"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = proc.pid
    print(f"[locks] Child started with PID {child_pid}. Waiting 2s...")
    time.sleep(2)

    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        print(f"[locks] Child exited prematurely with {proc.returncode}!\nStderr:\n{stderr}")
        return 1

    # Find a loaded .pyd
    site_packages = venv_dir / "Lib" / "site-packages"
    pyd_files = list(site_packages.glob("pydantic_core/_pydantic_core*.pyd"))
    pyd_target = pyd_files[0] if pyd_files else None

    targets = [("gflow.exe", gflow_exe)]
    if pyd_target:
        targets.append((pyd_target.name, pyd_target))
    else:
        print("[locks] Warning: no pydantic_core .pyd found!")

    results_table = []

    for label, target_path in targets:
        p = str(target_path)
        # 1. open(p, "r+b")
        try:
            with open(p, "r+b") as f:
                pass
            res_open = "SUCCESS"
        except Exception as e:
            res_open = f"{type(e).__name__}: {e}"

        # 2. os.remove(p)
        try:
            os.remove(p)
            res_remove = "SUCCESS"
        except Exception as e:
            res_remove = f"{type(e).__name__}: {e}"

        # 3. os.replace(p, p + ".old") followed by write new and restore
        old_p = p + f".{child_pid}.old"
        try:
            os.replace(p, old_p)
            write_status = "write failed"
            try:
                with open(p, "wb") as f:
                    f.write(b"test_payload")
                write_status = "write ok"
                os.remove(p)
            except Exception as we:
                write_status = f"write err: {we}"
            # restore
            os.replace(old_p, p)
            res_replace = f"SUCCESS (replace ok, {write_status}, restore ok)"
        except Exception as e:
            res_replace = f"{type(e).__name__}: {e}"
            if os.path.exists(old_p):
                os.replace(old_p, p)

        # 4. os.rename into %TEMP%
        temp_dir = tempfile.gettempdir()
        temp_dest = os.path.join(temp_dir, os.path.basename(p) + f".{child_pid}.tmp")
        try:
            os.rename(p, temp_dest)
            os.rename(temp_dest, p)
            res_rename_temp = "SUCCESS (rename & restore ok)"
        except Exception as e:
            res_rename_temp = f"{type(e).__name__}: {e}"
            if os.path.exists(temp_dest):
                os.rename(temp_dest, p)

        results_table.append([label, res_open, res_remove, res_replace, res_rename_temp])

    # Terminate child
    print(f"[locks] Terminating child PID {child_pid}...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print(f"[locks] Child did not terminate, killing PID {child_pid}...")
        proc.kill()
        proc.wait()

    _print_table(
        ["Target", "open(r+b)", "os.remove", "os.replace + new file", "os.rename into %TEMP%"],
        results_table,
    )
    return 0


def cmd_renamed(args: argparse.Namespace) -> int:
    name = args.name
    print(f"=== Subcommand: renamed {name} ===")
    venv_dir = _venv_dir(name)
    gflow_exe = venv_dir / "Scripts" / "gflow.exe"
    python_exe = venv_dir / "Scripts" / "python.exe"

    if not gflow_exe.exists():
        print(f"Error: {gflow_exe} does not exist.")
        return 1

    # Start child: gflow.exe mcp run
    print(f"[renamed] Starting child: {gflow_exe} mcp run...")
    proc = subprocess.Popen(
        [str(gflow_exe), "mcp", "run"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = proc.pid
    print(f"[renamed] Child PID {child_pid}. Waiting 2s...")
    time.sleep(2)

    if proc.poll() is not None:
        _, stderr = proc.communicate()
        print(f"[renamed] Child died early:\n{stderr}")
        return 1

    # Rename gflow.exe to gflow.exe.<pid>.old
    old_exe = gflow_exe.with_name(f"gflow.exe.{child_pid}.old")
    print(f"[renamed] Renaming {gflow_exe} -> {old_exe}...")
    rename_ok = False
    try:
        os.rename(gflow_exe, old_exe)
        rename_ok = True
        print("[renamed] Launcher rename succeeded!")
    except Exception as e:
        print(f"[renamed] Launcher rename failed: {e}")

    # Run pip upgrade
    print("[renamed] Running python -m pip install --upgrade --find-links ... gflow-cli")
    pip_res = subprocess.run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--find-links",
            str(WHEELS_DIR),
            "gflow-cli",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    # Check new gflow.exe
    new_exe_exists = gflow_exe.is_file()

    # Import probe
    probe = subprocess.run(
        [str(python_exe), "-I", "-c", "import gflow_cli; print(gflow_cli.__version__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    reported_ver = (
        probe.stdout.strip() if probe.returncode == 0 else f"FAIL ({probe.stderr.strip()})"
    )

    # Can old file be deleted while child runs?
    try:
        os.remove(old_exe)
        del_while_running = "SUCCESS"
    except Exception as e:
        del_while_running = f"{type(e).__name__}: {e}"

    # Terminate child
    print(f"[renamed] Terminating child PID {child_pid}...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # Can old file be deleted after child stops?
    del_after_stopped = "N/A (already deleted)"
    if old_exe.exists():
        try:
            os.remove(old_exe)
            del_after_stopped = "SUCCESS"
        except Exception as e:
            del_after_stopped = f"{type(e).__name__}: {e}"

    rows = [
        [
            name,
            f"{rename_ok}",
            f"exit {pip_res.returncode}",
            f"{new_exe_exists}",
            reported_ver,
            del_while_running,
            del_after_stopped,
        ]
    ]
    _print_table(
        [
            "Venv",
            "Rename Aside",
            "pip Exit",
            "New gflow.exe",
            "Import Version",
            "Del Old (Running)",
            "Del Old (Stopped)",
        ],
        rows,
    )
    return 0


def cmd_pymodule(args: argparse.Namespace) -> int:
    name = args.name
    print(f"=== Subcommand: pymodule {name} ===")
    venv_dir = _venv_dir(name)
    python_exe = venv_dir / "Scripts" / "python.exe"

    if not python_exe.exists():
        print(f"Error: {python_exe} does not exist.")
        return 1

    print(f"[pymodule] Running {python_exe} -m gflow_cli update...")
    update_res = subprocess.run(
        [str(python_exe), "-m", "gflow_cli", "update"],
        capture_output=True,
        text=True,
        check=False,
    )

    log_path = venv_dir / "pymodule.log"
    log_content = (
        f"=== STDOUT ===\n{update_res.stdout}\n"
        f"=== STDERR ===\n{update_res.stderr}\n"
        f"=== EXIT CODE: {update_res.returncode} ===\n"
    )
    log_path.write_text(log_content, encoding="utf-8")

    probe = subprocess.run(
        [str(python_exe), "-I", "-c", "import gflow_cli, sys; print(gflow_cli.__version__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    import_report = (
        probe.stdout.strip() if probe.returncode == 0 else f"FAILED: {probe.stderr.strip()}"
    )

    rows = [[name, f"exit {update_res.returncode}", import_report]]
    _print_table(["Venv", "update Exit", "Import Version Afterwards"], rows)
    return 0


def cmd_pipdry(args: argparse.Namespace) -> int:
    name = args.name
    print(f"=== Subcommand: pipdry {name} ===")
    venv_dir = _venv_dir(name)
    python_exe = venv_dir / "Scripts" / "python.exe"
    gflow_exe = venv_dir / "Scripts" / "gflow.exe"

    if not python_exe.exists():
        print(f"Error: {python_exe} does not exist.")
        return 1

    mtime_before = gflow_exe.stat().st_mtime if gflow_exe.exists() else 0

    print("[pipdry] Running pip install -v --upgrade gflow-cli...")
    pip_res = subprocess.run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "-v",
            "--upgrade",
            "--find-links",
            str(WHEELS_DIR),
            "gflow-cli",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    log_path = venv_dir / "pipdry.log"
    log_path.write_text(f"{pip_res.stdout}\n{pip_res.stderr}", encoding="utf-8")

    # Extract key lines in order
    milestones = []
    for line in pip_res.stdout.splitlines():
        line_clean = line.strip()
        if any(
            kw in line_clean
            for kw in [
                "Found existing installation",
                "Uninstalling gflow-cli",
                "Successfully uninstalled",
                "Rolling back uninstall",
                "Installing collected packages",
                "Successfully installed",
                "Created temporary directory",
                "Moving to new path",
            ]
        ):
            milestones.append(line_clean)

    print("\n--- pip execution sequence ---")
    for m in milestones:
        print(f"  -> {m}")

    mtime_after = gflow_exe.stat().st_mtime if gflow_exe.exists() else 0
    rows = [
        [
            name,
            f"exit {pip_res.returncode}",
            str(mtime_before),
            str(mtime_after),
            f"{len(milestones)} milestones recorded",
        ]
    ]
    _print_table(["Venv", "pip Exit", "mtime before", "mtime after", "Order analysis"], rows)
    return 0


def cmd_check_temp(args: argparse.Namespace) -> int:
    name = args.name
    print(f"=== Subcommand: check-temp {name} ===")
    temp_dir = tempfile.gettempdir()
    temp_drive = pathlib.Path(temp_dir).drive
    venv_dir = _venv_dir(name)
    venv_drive = venv_dir.resolve().drive
    same_volume = temp_drive.upper() == venv_drive.upper()

    rows = [
        ["tempfile.gettempdir()", temp_dir, temp_drive],
        [f"venv {name}", str(venv_dir), venv_drive],
        ["Same Volume?", str(same_volume), "Same" if same_volume else "Different"],
    ]
    _print_table(["Item", "Path", "Drive / Volume"], rows)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Spike #1298: pip update vs running launcher on Windows"
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # build
    subparsers.add_parser("build", help="Build lowered wheel (0.98.99)")

    # venv
    parser_venv = subparsers.add_parser("venv", help="Create venv and install 0.98.99")
    parser_venv.add_argument("name", help="Name of venv (e.g. a, b, c, d)")

    # reproduce
    parser_rep = subparsers.add_parser("reproduce", help="Run gflow.exe update and measure failure")
    parser_rep.add_argument("name", help="Name of venv")
    parser_rep.add_argument("--tmp-dir", default=None, help="Optional custom TMP/TEMP directory")

    # locks
    parser_locks = subparsers.add_parser(
        "locks", help="Measure Windows lock semantics on running launcher"
    )
    parser_locks.add_argument("name", help="Name of venv")

    # renamed
    parser_renamed = subparsers.add_parser(
        "renamed", help="Test renaming launcher aside before pip install"
    )
    parser_renamed.add_argument("name", help="Name of venv")

    # pymodule
    parser_pymod = subparsers.add_parser("pymodule", help="Control run: python -m gflow_cli update")
    parser_pymod.add_argument("name", help="Name of venv")

    # pipdry
    parser_dry = subparsers.add_parser(
        "pipdry", help="Trace pip upgrade order (uninstall vs install)"
    )
    parser_dry.add_argument("name", help="Name of venv")

    # check-temp
    parser_tmp = subparsers.add_parser("check-temp", help="Check tempdir volume vs venv volume")
    parser_tmp.add_argument("name", help="Name of venv")

    args = parser.parse_args()

    commands = {
        "build": cmd_build,
        "venv": cmd_venv,
        "reproduce": cmd_reproduce,
        "locks": cmd_locks,
        "renamed": cmd_renamed,
        "pymodule": cmd_pymodule,
        "pipdry": cmd_pipdry,
        "check-temp": cmd_check_temp,
    }

    return commands[args.subcommand](args)


if __name__ == "__main__":
    sys.exit(main())
