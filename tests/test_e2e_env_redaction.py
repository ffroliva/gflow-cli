"""Regression test for #1382: e2e environment mapping conceals secret values."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from tests.e2e import conftest as e2e_conftest
from tests.e2e.conftest import RedactedEnv

_DUMMY_SECRET_NAME = "GFLOW_TEST_SECRET"
_DUMMY_SECRET_VAL = "sk-fake-1234567890"


def _build_fixture_env(tmp_path: Path) -> RedactedEnv:
    gen = cast(Any, e2e_conftest.e2e_env).__wrapped__(tmp_path)
    env = next(gen)
    gen.close()
    return env


def test_redacted_env_conceals_values_in_repr_and_str() -> None:
    mapping = RedactedEnv({_DUMMY_SECRET_NAME: _DUMMY_SECRET_VAL, "SAFE_KEY": "safe_val"})

    assert isinstance(mapping, dict)
    assert mapping[_DUMMY_SECRET_NAME] == _DUMMY_SECRET_VAL
    assert mapping.get(_DUMMY_SECRET_NAME) == _DUMMY_SECRET_VAL
    assert dict(mapping.items())[_DUMMY_SECRET_NAME] == _DUMMY_SECRET_VAL

    # Values must be concealed with '***' in repr and str
    assert _DUMMY_SECRET_VAL not in repr(mapping)
    assert _DUMMY_SECRET_VAL not in str(mapping)
    assert repr(mapping) == repr({_DUMMY_SECRET_NAME: "***", "SAFE_KEY": "***"})
    assert str(mapping) == str({_DUMMY_SECRET_NAME: "***", "SAFE_KEY": "***"})

    # Key names remain visible
    assert _DUMMY_SECRET_NAME in repr(mapping)
    assert "SAFE_KEY" in repr(mapping)


def test_redacted_env_copy_preserves_redaction() -> None:
    original = RedactedEnv({_DUMMY_SECRET_NAME: _DUMMY_SECRET_VAL})

    copied = original.copy()
    assert isinstance(copied, RedactedEnv)
    assert _DUMMY_SECRET_VAL not in repr(copied)
    assert _DUMMY_SECRET_VAL not in str(copied)
    assert copied[_DUMMY_SECRET_NAME] == _DUMMY_SECRET_VAL


def test_redacted_env_union_preserves_redaction() -> None:
    original = RedactedEnv({_DUMMY_SECRET_NAME: _DUMMY_SECRET_VAL})
    extra_secret = "new-secret-xyz"

    # __or__: RedactedEnv | dict
    merged = original | {"EXTRA_KEY": extra_secret}
    assert isinstance(merged, RedactedEnv)
    assert merged[_DUMMY_SECRET_NAME] == _DUMMY_SECRET_VAL
    assert merged["EXTRA_KEY"] == extra_secret
    assert _DUMMY_SECRET_VAL not in repr(merged)
    assert extra_secret not in repr(merged)
    assert _DUMMY_SECRET_VAL not in str(merged)
    assert extra_secret not in str(merged)

    # __ror__: dict | RedactedEnv
    rmerged = {"EXTRA_KEY": extra_secret} | original
    assert isinstance(rmerged, RedactedEnv)
    assert rmerged[_DUMMY_SECRET_NAME] == _DUMMY_SECRET_VAL
    assert rmerged["EXTRA_KEY"] == extra_secret
    assert _DUMMY_SECRET_VAL not in repr(rmerged)
    assert extra_secret not in repr(rmerged)
    assert _DUMMY_SECRET_VAL not in str(rmerged)
    assert extra_secret not in str(rmerged)

    # __ior__: RedactedEnv |= dict
    mutable = RedactedEnv({_DUMMY_SECRET_NAME: _DUMMY_SECRET_VAL})
    mutable |= {"EXTRA_KEY": extra_secret}
    assert isinstance(mutable, RedactedEnv)
    assert mutable[_DUMMY_SECRET_NAME] == _DUMMY_SECRET_VAL
    assert mutable["EXTRA_KEY"] == extra_secret
    assert _DUMMY_SECRET_VAL not in repr(mutable)
    assert extra_secret not in repr(mutable)


def test_no_unredacted_e2e_env_rebuilds_in_e2e_tests() -> None:
    e2e_dir = Path(__file__).parent / "e2e"
    violations: list[str] = []

    for py_path in sorted(e2e_dir.rglob("*.py")):
        if py_path.name in ("conftest.py", "_redacted_env.py"):
            continue
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))

        lines = source.splitlines()
        for idx, line in enumerate(lines, 1):
            if "dict(e2e_env" in line:
                violations.append(
                    f"{py_path.name}:{idx}: dict(e2e_env) rebuilds a plain dict; use e2e_env.copy()"
                )
            if "{**e2e_env" in line:
                violations.append(
                    f"{py_path.name}:{idx}: {{**e2e_env}} rebuilds a plain dict; "
                    "use e2e_env | {...}"
                )

        # Detect comprehensions over e2e_env.items() not wrapped in RedactedEnv(...)
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.DictComp):
                    for gen in child.generators:
                        if (
                            isinstance(gen.iter, ast.Call)
                            and isinstance(gen.iter.func, ast.Attribute)
                            and gen.iter.func.attr == "items"
                            and isinstance(gen.iter.func.value, ast.Name)
                            and gen.iter.func.value.id == "e2e_env"
                        ):
                            is_wrapped = (
                                isinstance(node, ast.Call)
                                and isinstance(node.func, ast.Name)
                                and node.func.id == "RedactedEnv"
                            )
                            if not is_wrapped:
                                violations.append(
                                    f"{py_path.name}:{child.lineno}: unredacted comprehension over "
                                    "e2e_env.items(); wrap in RedactedEnv({...})"
                                )

    assert not violations, (
        f"Found {len(violations)} site(s) rebuilding a plain dict from e2e_env, which leaks "
        f"secrets in pytest locals:\n" + "\n".join(f"  - {v}" for v in violations)
    )


def test_e2e_env_fixture_redacts_secrets_in_repr_and_str(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GFLOW_CLI_E2E_PROFILE", "test-profile")
    monkeypatch.setenv(_DUMMY_SECRET_NAME, _DUMMY_SECRET_VAL)

    env = _build_fixture_env(tmp_path)

    assert isinstance(env, RedactedEnv)
    assert env[_DUMMY_SECRET_NAME] == _DUMMY_SECRET_VAL
    assert _DUMMY_SECRET_VAL not in repr(env)
    assert _DUMMY_SECRET_VAL not in str(env)
    assert _DUMMY_SECRET_NAME in repr(env)
    assert _DUMMY_SECRET_NAME in str(env)


def test_e2e_env_works_as_subprocess_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GFLOW_CLI_E2E_PROFILE", "test-profile")
    monkeypatch.setenv(_DUMMY_SECRET_NAME, _DUMMY_SECRET_VAL)

    env = _build_fixture_env(tmp_path)

    proc = subprocess.run(
        [sys.executable, "-c", f"import os; print(os.environ['{_DUMMY_SECRET_NAME}'])"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == _DUMMY_SECRET_VAL
