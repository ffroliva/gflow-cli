"""Regression test for #1382: e2e environment mapping and failure report redaction."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from tests.e2e import conftest as e2e_conftest
from tests.e2e.conftest import RedactedEnv

pytest_plugins = ["pytester"]

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


def test_redacted_env_union_operands_and_values() -> None:
    original = RedactedEnv({"KEY": "val"})

    # Non-mapping operands return NotImplemented / raise TypeError
    with pytest.raises(TypeError):
        _ = original | 123  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = 123 | original  # type: ignore[operator]
    with pytest.raises(TypeError):
        original |= 123  # type: ignore[operator]

    # Does not coerce values to str
    merged = original | {"INT_KEY": 12345}  # type: ignore[dict-item]
    assert merged["INT_KEY"] == 12345


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


def test_pytest_report_scrubber_masks_env_secrets_in_showlocals(
    pytester: pytest.Pytester,
) -> None:
    pytester.syspathinsert()
    pytester.makepyfile("""
import os

def test_leak():
    os.environ["GFLOW_TEST_API_KEY"] = "sk-fake-1234567890abcd"
    env = {**os.environ}
    print("captured secret: sk-fake-1234567890abcd")
    assert False
""")
    result = pytester.runpytest("-l", "-p", "tests._secret_scrub")
    output = result.stdout.str()
    assert "sk-fake-1234567890abcd" not in output
    assert "***" in output


def test_pytest_report_scrubber_masks_subprocess_env_list_in_showlocals(
    pytester: pytest.Pytester,
) -> None:
    pytester.syspathinsert()
    pytester.makepyfile("""
import os
import subprocess

def test_subprocess_leak():
    os.environ["GFLOW_TEST_API_KEY"] = "sk-fake-1234567890abcd"
    subprocess.run(["definitely-not-a-program"], env=dict(os.environ))
""")
    result = pytester.runpytest("-l", "-p", "tests._secret_scrub")
    output = result.stdout.str()
    assert "sk-fake-1234567890abcd" not in output
    assert "***" in output
