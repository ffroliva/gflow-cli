"""Report scrubber to conceal environment secrets in pytest failure reports (#1382).

Masks secret-looking environment variable values in pytest failure reports
(tracebacks, ``-l`` / ``--showlocals`` local variables, and captured sections
such as stdout, stderr, and logs) with ``'***'``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pytest import CallInfo, Item, TestReport

# Match secret variable names case-insensitively
_SECRET_NAME_PATTERN = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|COOKIE|CREDENTIAL|AUTH|SESSION|PRIVATE|_PAT$)",
    re.IGNORECASE,
)
_MIN_SECRET_LENGTH = 8

# Snapshot of secret values collected at session start
_SESSION_SECRETS: set[str] = set()


def _is_path(val: str) -> bool:
    try:
        return os.path.exists(val)
    except (ValueError, OSError):
        return False


def is_secret_name(name: str) -> bool:
    """Return True if an environment variable name matches secret naming patterns."""
    return bool(_SECRET_NAME_PATTERN.search(name))


def collect_secret_values(
    env: Mapping[str, str] | None = None,
    extra_secrets: Iterable[str] | None = None,
) -> set[str]:
    """Collect secret-looking values from an environment mapping.

    Excludes values shorter than 8 characters and paths that exist on disk.
    """
    target_env = os.environ if env is None else env
    secrets = set(extra_secrets or ())
    for k, v in target_env.items():
        if len(v) < _MIN_SECRET_LENGTH:
            continue
        if _is_path(v):
            continue
        if is_secret_name(k):
            secrets.add(v)
    return secrets


def scrub_text(text: str, secrets: Iterable[str]) -> str:
    """Replace all occurrences of secret values in text with '***'."""
    if not text or not secrets:
        return text
    # Replace longest secrets first so partial prefixes don't corrupt substrings
    for secret in sorted(secrets, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, "***")
    return text


def scrub_report(report: TestReport, secrets: Iterable[str]) -> None:
    """Mask secret values in a failed report's longrepr and captured sections."""
    if not report.failed or not secrets:
        return

    # Scrub longrepr (tracebacks, locals from -l, failure messages)
    if report.longrepr is not None:
        longrepr_text = getattr(report, "longreprtext", None) or str(report.longrepr)
        report.longrepr = scrub_text(longrepr_text, secrets)

    # Scrub captured sections (stdout, stderr, log)
    if report.sections:
        report.sections = [
            (name, scrub_text(str(content), secrets)) for name, content in report.sections
        ]


def pytest_sessionstart(session: pytest.Session) -> None:  # noqa: ARG001
    """Snapshot secret values at session start."""
    global _SESSION_SECRETS
    _SESSION_SECRETS = collect_secret_values(os.environ)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Item, call: CallInfo[None]) -> Iterator[None]:  # noqa: ARG001
    """Scrub secrets from failure reports on test failure."""
    outcome = yield
    report: TestReport = outcome.get_result()
    if report.failed:
        secrets = collect_secret_values(os.environ, extra_secrets=_SESSION_SECRETS)
        scrub_report(report, secrets)
