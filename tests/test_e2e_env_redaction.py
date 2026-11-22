"""Regression test for #1382: e2e environment mapping and failure report redaction."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from tests.conftest import pytest_plugins as repo_plugins
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


def test_repo_conftest_declares_secret_scrub_plugin() -> None:
    assert "tests._secret_scrub" in repo_plugins


def test_failing_report_without_secret_keeps_reprcrash_summary_line(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
def test_clean():
    assert False, "custom assertion failure message"
""")
    result = pytester.runpytest_subprocess("-ra")
    output = result.stdout.str()
    assert (
        "FAILED test_failing_report_without_secret_keeps_reprcrash_summary_line.py::test_clean"
        " - AssertionError: custom assertion failure message" in output
    )


def test_non_secret_env_vars_and_credentialed_urls(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
def test_secrets_and_non_secrets(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "RealAuthorName")
    monkeypatch.setenv("PIP_KEYRING_PROVIDER", "subprocess_provider")
    monkeypatch.setenv("GFLOW_CLI_AUTH_BROWSER", "chromium_browser")
    monkeypatch.setenv("MY_API_KEY", "secret_api_key_12345678")
    monkeypatch.setenv("SMTP_PASS", "secret_smtp_password_12345678")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://dbuser:super_secret_url_pass_12345678@localhost:5432/testdb",
    )
    author = "RealAuthorName"
    keyring = "subprocess_provider"
    browser = "chromium_browser"
    api_key = "secret_api_key_12345678"
    smtp_pass = "secret_smtp_password_12345678"
    url = "postgres://dbuser:super_secret_url_pass_12345678@localhost:5432/testdb"
    assert False
""")
    result = pytester.runpytest_subprocess("-l")
    output = result.stdout.str()
    # Non-secrets must not be masked
    assert "RealAuthorName" in output
    assert "subprocess_provider" in output
    assert "chromium_browser" in output
    # Secrets must be masked
    assert "secret_api_key_12345678" not in output
    assert "secret_smtp_password_12345678" not in output
    assert "super_secret_url_pass_12345678" not in output
    assert "postgres://dbuser:***@localhost:5432/testdb" in output


def test_truncated_secret_repr_masks_chunks_under_showlocals(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    token_404 = "".join(f"{i:04d}" for i in range(101))
    assert len(token_404) == 404
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile(f"""
def test_long_token(monkeypatch):
    token = {token_404!r}
    monkeypatch.setenv("MY_API_KEY", token)
    val = token
    assert False
""")
    result = pytester.runpytest_subprocess("-l")
    output = result.stdout.str()
    assert token_404[:16] not in output
    assert token_404[-16:] not in output
    assert "***" in output


def test_passing_test_masks_secrets_in_captured_sections(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
def test_pass_print(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "super_secret_passing_token_12345")
    print("OUTPUT: super_secret_passing_token_12345")
""")
    result = pytester.runpytest_subprocess("-rA")
    output = result.stdout.str()
    assert "super_secret_passing_token_12345" not in output
    assert "OUTPUT: ***" in output


def test_collection_error_masks_secrets(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
import os
os.environ["MY_API_KEY"] = "super_secret_import_token_12345"
raise RuntimeError("failed to import module with super_secret_import_token_12345")
""")
    result = pytester.runpytest_subprocess()
    output = result.stdout.str()
    assert "super_secret_import_token_12345" not in output
    assert "***" in output


def test_pytest_report_scrubber_masks_env_secrets_in_showlocals(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
def test_leak(monkeypatch):
    monkeypatch.setenv("GFLOW_TEST_API_KEY", "sk-fake-1234567890abcd")
    import os
    env = {**os.environ}
    print("captured secret: sk-fake-1234567890abcd")
    assert False
""")
    result = pytester.runpytest_subprocess("-l")
    output = result.stdout.str()
    assert "sk-fake-1234567890abcd" not in output
    assert "***" in output


def test_pytest_report_scrubber_masks_subprocess_env_list_in_showlocals(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
import subprocess

def test_subprocess_leak(monkeypatch):
    monkeypatch.setenv("GFLOW_TEST_API_KEY", "sk-fake-1234567890abcd")
    import os
    subprocess.run(["definitely-not-a-program"], env=dict(os.environ))
""")
    result = pytester.runpytest_subprocess("-l")
    output = result.stdout.str()
    assert "sk-fake-1234567890abcd" not in output
    assert "***" in output


def test_secret_scrub_plugin_is_active(request: pytest.FixtureRequest) -> None:
    assert request.config.pluginmanager.has_plugin("tests._secret_scrub")


def test_collection_import_error_masks_secrets(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("MY_API_KEY", "super_secret_import_token_12345")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
import os
raise ImportError("driver failed for " + os.environ["MY_API_KEY"])
""")
    result = pytester.runpytest_subprocess()
    output = result.stdout.str()
    assert "super_secret_import_token_12345" not in output
    assert "***" in output


def test_skip_reason_masks_secrets_under_rs(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("MY_API_KEY", "secret_skip_token_12345678")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
import os, pytest
def test_s():
    pytest.skip("backend unreachable with key " + os.environ["MY_API_KEY"])
""")
    result = pytester.runpytest_subprocess("-rs")
    output = result.stdout.str()
    assert "secret_skip_token_12345678" not in output
    assert "***" in output


def test_funcargs_masks_secrets_without_showlocals(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("MY_API_KEY", "super_secret_funcarg_token_12345")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
import os
def helper(env):
    raise RuntimeError("boom")
def test_a():
    helper({"PATH": "x", "MY_API_KEY": os.environ["MY_API_KEY"]})
""")
    result = pytester.runpytest_subprocess()
    output = result.stdout.str()
    assert "super_secret_funcarg_token_12345" not in output
    assert "***" in output


def test_truncated_gh_token_under_showlocals_shows_no_fragment(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    token = "ghp_" + "1234567890" * 3 + "abcdef"
    assert len(token) == 40
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile(f"""
def test_trunc(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", {token!r})
    d = {{'a' * 88: '1', 'tok': {token!r}, 'b' * 112: '2'}}
    assert False
""")
    result = pytester.runpytest_subprocess("-l")
    output = result.stdout.str()
    for i in range(len(token) - 3):
        assert token[i : i + 4] not in output


def test_late_secrets_at_teardown_masked_in_junitxml(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
def test_j(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "late_secret_value_0987654321")
    print("token=late_secret_value_0987654321")
""")
    xml_path = pytester.path / "junit.xml"
    result = pytester.runpytest_subprocess(
        f"--junitxml={xml_path}", "-o", "junit_logging=system-out"
    )
    assert result.ret == 0
    xml_content = xml_path.read_text(encoding="utf-8")
    assert "late_secret_value_0987654321" not in xml_content
    assert "token=***" in xml_content


def test_google_application_credentials_path_not_masked(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    creds_file = tmp_path / "creds.json"
    creds_file.write_text('{"type": "service_account"}', encoding="utf-8")
    creds_path_str = str(creds_file)

    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile(f"""
import os
def test_creds_path(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", {creds_path_str!r})
    p = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    assert False, "missing file under " + p
""")
    result = pytester.runpytest_subprocess()
    output = result.stdout.str()
    assert creds_path_str in output


def test_truncated_short_secret_repr_masks_prefix_under_showlocals(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    key = "AIzaSyQ1w2e3r4t5y6u7"
    monkeypatch.setenv("GEMINI_API_KEY", key)
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
import os
KEY = os.environ["GEMINI_API_KEY"]

def test_trunc():
    out = "A" * 100 + "token=" + KEY + " " + "B" * 300
    assert "zzz" in out
""")
    xml_path = pytester.path / "junit.xml"
    result = pytester.runpytest_subprocess("-l", "-rA", f"--junitxml={xml_path}")
    output = result.stdout.str()
    xml_content = xml_path.read_text(encoding="utf-8")
    for i in range(len(key) - 3):
        frag = key[i : i + 4]
        assert frag not in output
        assert frag not in xml_content


def test_wasxfail_masks_secrets_under_rx_and_junitxml(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    key = "AIzaSyQ1w2e3r4t5y6u7"
    monkeypatch.setenv("GEMINI_API_KEY", key)
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
import os, pytest
def test_xf():
    KEY = os.environ["GEMINI_API_KEY"]
    pytest.xfail(f"server said key {KEY}")
""")
    xml_path = pytester.path / "junit.xml"
    result = pytester.runpytest_subprocess("-rx", f"--junitxml={xml_path}")
    output = result.stdout.str()
    xml_content = xml_path.read_text(encoding="utf-8")
    assert key not in output
    assert "server said key ***" in output
    assert key not in xml_content
    assert "server said key ***" in xml_content


def test_passing_test_prints_ellipsis_without_false_positive_masking(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "minioadmin")
    pytester.syspathinsert()
    pytester.makeconftest(f"pytest_plugins = {repo_plugins!r}\n")
    pytester.makepyfile("""
def test_pass_minio():
    print("Connecting to minio...admin console ready")
""")
    result = pytester.runpytest_subprocess("-rA")
    output = result.stdout.str()
    assert "Connecting to minio...admin console ready" in output
