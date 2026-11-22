"""Regression test for #1382: e2e environment mapping conceals secret values."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e import conftest as e2e_conftest
from tests.e2e.conftest import RedactedEnv

_DUMMY_SECRET_NAME = "GFLOW_TEST_SECRET"
_DUMMY_SECRET_VAL = "sk-fake-1234567890"


def _build_fixture_env(tmp_path: Path) -> RedactedEnv:
    fn = getattr(e2e_conftest.e2e_env, "__wrapped__", e2e_conftest.e2e_env)
    return next(fn(tmp_path))


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
