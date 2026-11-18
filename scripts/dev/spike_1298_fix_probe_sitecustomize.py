# One-shot sitecustomize probe for #1298 section 9: copy into a pip venv's site-packages,
# then run `GFLOW_SPIKE_1298=1 Scripts/gflow.exe --version`. Stdlib only; spike evidence, not product code.
import os

if os.environ.pop("GFLOW_SPIKE_1298", None):  # pop: no child ever re-arms the hook
    import atexit

    def _late() -> None:
        import gc
        import importlib
        import importlib.metadata as md
        import subprocess
        import sys

        try:
            os.remove(__file__)  # one-shot
        except OSError as exc:
            print("PROBE could not delete hook:", exc)
        files = md.distribution("gflow-cli").files or []
        launchers = [str(f.locate()) for f in files if str(f).lower().endswith(".exe")]
        print("PROBE launchers:", [os.path.basename(p) for p in launchers])
        importlib.invalidate_caches()
        gc.collect()
        moved = []
        for p in launchers:
            old = f"{p}.gflow-update-{os.getpid()}.old"
            try:
                os.replace(p, old)
                moved.append((p, old))
                print("PROBE renamed", os.path.basename(p))
            except OSError as exc:
                print("PROBE rename FAILED", os.path.basename(p), exc)
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli"],
            capture_output=True, text=True, timeout=600,
        )
        print("PROBE pip rc:", r.returncode)
        print("PROBE pip tail:", "\n".join((r.stdout + r.stderr).splitlines()[-6:]))
        for p, old in moved:
            print("PROBE", os.path.basename(p), "exists after pip:", os.path.exists(p))
            if not os.path.exists(p):
                os.replace(old, p)
                print("PROBE restored", os.path.basename(p))
        chk = subprocess.run(
            [sys.executable, "-I", "-c", "import gflow_cli.cli, gflow_cli; print(gflow_cli.__version__)"],
            capture_output=True, text=True,
        )
        print("PROBE import rc:", chk.returncode, chk.stdout.strip(), chk.stderr.strip()[-300:])

    atexit.register(_late)
