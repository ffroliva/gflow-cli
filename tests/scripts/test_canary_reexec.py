"""The canary must run the version of itself that it just pulled.

`run_canary.py --pull` updates its own source and then keeps executing the copy
Python loaded at startup, so every runner change is silently one night late.

That is not theoretical: #572 added `-o junit_logging=all` so a preserved RED
would carry the structlog line that decides #561. It merged 2026-08-25 12:29 UTC,
the 2026-08-26 02:00 run pulled it, and that RED still had zero log output —
because the pre-pull copy (`230200b`, no flag) was the one running. Three REDs are
untriageable as a result, and the failure reads as "the fix did not work", which
would prompt rewriting a fix that was already correct.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "canary" / "run_canary.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("run_canary_under_test", _RUNNER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def canary() -> Any:
    return _load()


def test_digest_is_stable_and_content_sensitive(canary: Any, tmp_path: Path) -> None:
    f = tmp_path / "s.py"
    f.write_text("one", encoding="utf-8")
    first = canary._script_digest(f)
    assert first == canary._script_digest(f)
    f.write_text("two", encoding="utf-8")
    assert canary._script_digest(f) != first


def test_unreadable_script_does_not_trigger_a_rerun(canary: Any, tmp_path: Path) -> None:
    """Fail SAFE, not fail LOOP.

    A digest we cannot read must degrade to "unchanged" (update late) rather than
    "changed" (re-run, possibly forever).
    """
    assert canary._script_digest(tmp_path / "does-not-exist.py") == ""


def test_guard_env_var_prevents_a_second_rerun(
    canary: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard alone must stop a loop, even if the digest logic is wrong."""
    monkeypatch.setenv(canary._REEXEC_GUARD, "1")
    called: list[Any] = []
    monkeypatch.setattr(canary.subprocess, "run", lambda *a, **k: called.append(a))

    canary._maybe_rerun_after_pull("digest-before", digest_now="totally-different")

    assert called == []


def test_unchanged_digest_does_not_rerun(canary: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(canary._REEXEC_GUARD, raising=False)
    called: list[Any] = []
    monkeypatch.setattr(canary.subprocess, "run", lambda *a, **k: called.append(a))

    canary._maybe_rerun_after_pull("same", digest_now="same")

    assert called == []


def test_changed_digest_reruns_once_with_argv_preserved(
    canary: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(canary._REEXEC_GUARD, raising=False)
    monkeypatch.setattr(sys, "argv", ["run_canary.py", "--pull", "--profile", "denon82"])
    seen: dict[str, Any] = {}

    class _Proc:
        returncode = 7

    def _fake_run(cmd: list[str], **kwargs: Any) -> Any:
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(canary.subprocess, "run", _fake_run)

    with pytest.raises(SystemExit) as exc:
        canary._maybe_rerun_after_pull("before", digest_now="after")

    # exit code propagates so Task Scheduler still sees the real result
    assert exc.value.code == 7
    # the ORIGINAL flags must survive the re-run, or the second pass behaves
    # differently from the one the operator scheduled
    assert "--pull" in seen["cmd"]
    assert "--profile" in seen["cmd"] and "denon82" in seen["cmd"]
    # and the guard must be set for the child, or it re-runs forever
    assert seen["env"][canary._REEXEC_GUARD] == "1"


def test_rerun_uses_subprocess_not_execv(canary: Any) -> None:
    """Windows-specific, and load-bearing.

    `os.execv` on Windows does not replace the process image: the CRT spawns a new
    process and terminates this one, so the PID changes. Under Task Scheduler that
    can read as "the task finished". A supervising `subprocess.run` keeps one
    process and propagates the exit code.

    Checks for a CALL, not a mention — the module documents why execv is wrong,
    so a naive substring search matches its own explanation.
    """
    import ast

    tree = ast.parse(_RUNNER.read_text(encoding="utf-8"))
    calls = {
        f"{n.func.value.id}.{n.func.attr}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
    }
    assert not {c for c in calls if c.startswith("os.exec")}, calls
    assert "subprocess.run" in calls
