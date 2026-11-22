"""Report scrubber to conceal environment secrets in pytest failure and collection reports (#1382).

Masks secret-looking environment variable values and credentialed URL passwords in
pytest test reports (tracebacks, ``-l`` / ``--showlocals`` local variables, and
captured sections such as stdout, stderr, and logs) with ``'***'``.

Guarantees and limits:
- Covered: failure reports, passed reports with captured sections (e.g. ``-rP``,
  ``-rA``, or JUnit XML system-out/err), and collection error reports.
- Not covered: live uncaptured console output (such as ``-s`` / ``--capture=no``
  or ``log_cli``) and the pytest warnings summary.
- Targeted names: repo secret variables and variables matching anchored secret
  naming patterns, plus passwords inside credentialed URLs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pytest import CallInfo, Collector, CollectReport, Item, TestReport

_MIN_SECRET_LENGTH = 8
_CHUNK_SIZE = 16
_MIN_CHUNK_SECRET_LENGTH = 32

# Allowlist of real secret variables defined or read across gflow-cli
_EXPLICIT_SECRETS: frozenset[str] = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GEMINI_API_KEY",
        "GFLOW_CI_SESSION_TOKEN",
        "GFLOW_CLI_DAEMON_TOKEN",
        "GFLOW_CLI_GEMINI_API_KEY",
        "GFLOW_CLI_GEMINI_TTS_API_KEY",
        "GFLOW_CLI_LLM_API_KEY",
        "GFLOW_DAEMON_TOKEN",
        "GH_COMMENT_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GOOGLE_API_KEY",
        "MINIO_ROOT_PASSWORD",
        "OPENAI_API_KEY",
        "PYPI_API_TOKEN",
        "RESEND_API_KEY",
        "RUSTFS_ACCESS_KEY",
        "RUSTFS_SECRET_KEY",
        "SONAR_TOKEN",
        "SPONSORS_READ_TOKEN",
        "TWINE_PASSWORD",
    }
)

# Segment-anchored pattern: must end with one of these segments preceded by start or underscore.
# Excludes substrings like AUTHOR, KEYRING, *_SESSION_ID, GFLOW_CLI_AUTH_BROWSER.
_SECRET_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|_)(?:API_KEY|APIKEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|COOKIE|CREDENTIALS?|CREDS|DSN|PAT)$",
    re.IGNORECASE,
)

# Extracts password from scheme://user:password@host
_CREDENTIAL_URL_PATTERN: re.Pattern[str] = re.compile(
    r"[a-zA-Z][a-zA-Z0-9+.-]*://[^:@/\s]+:([^@/\s]+)@"
)

# Snapshot of secret values collected at session start
_SESSION_SECRETS: set[str] = set()

# Stash key to persist secrets seen during setup and call for teardown reports (#1382)
_ITEM_SECRETS_KEY: pytest.StashKey[set[str]] = pytest.StashKey()


def is_secret_name(name: str) -> bool:
    """Return True if an environment variable name matches secret naming patterns."""
    upper = name.upper()
    return upper in _EXPLICIT_SECRETS or bool(_SECRET_NAME_PATTERN.search(upper))


def _is_existing_path(val: str) -> bool:
    """Return True if val is an existing absolute filesystem path."""
    try:
        return os.path.isabs(val) and os.path.exists(val)
    except (ValueError, OSError):
        return False


def _add_chunks(secret: str, target: set[str]) -> None:
    """Add 16-character chunks for secrets longer than 32 characters to defeat truncation."""
    if len(secret) > _MIN_CHUNK_SECRET_LENGTH:
        for i in range(0, len(secret) - (_CHUNK_SIZE - 1), _CHUNK_SIZE):
            target.add(secret[i : i + _CHUNK_SIZE])
        if len(secret) % _CHUNK_SIZE != 0:
            target.add(secret[-_CHUNK_SIZE:])


def collect_secret_values(
    env: Mapping[str, str] | None = None,
    extra_secrets: Iterable[str] | None = None,
) -> set[str]:
    """Collect secret values and credential passwords from an environment mapping."""
    target_env = os.environ if env is None else env
    secrets: set[str] = set()

    for extra in extra_secrets or ():
        if len(extra) >= _MIN_SECRET_LENGTH:
            secrets.add(extra)
            _add_chunks(extra, secrets)

    for k, v in target_env.items():
        if not isinstance(v, str):
            continue

        # Check for credentialed URL scheme://user:password@host
        for m in _CREDENTIAL_URL_PATTERN.finditer(v):
            pwd = m.group(1)
            if len(pwd) >= _MIN_SECRET_LENGTH:
                secrets.add(pwd)
                _add_chunks(pwd, secrets)

        if is_secret_name(k) and len(v) >= _MIN_SECRET_LENGTH:
            if _is_existing_path(v):
                continue
            secrets.add(v)
            _add_chunks(v, secrets)

    return secrets


def _is_inside_quote(line: str, pos: int) -> bool:
    """Return True if pos in line is inside a quoted string fragment."""
    in_quote = None
    escaped = False
    quote_start = -1
    for i, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch in ("'", '"'):
            if in_quote is None:
                in_quote = ch
                quote_start = i
            elif in_quote == ch:
                if quote_start < pos < i:
                    return True
                in_quote = None
                quote_start = -1
    return False


def _has_truncated_secret(text: str, secrets: Iterable[str]) -> bool:
    """Return True if text has a truncated secret fragment near '...' in a quoted repr."""
    if "..." not in text or not secrets:
        return False

    valid_secrets = [s for s in secrets if len(s) >= _MIN_SECRET_LENGTH]
    if not valid_secrets:
        return False

    max_sec_len = max(len(s) for s in valid_secrets)
    prefixes = {s[:length] for s in valid_secrets for length in range(4, len(s) + 1)}
    suffixes = {s[-length:] for s in valid_secrets for length in range(4, len(s) + 1)}

    ellipses = [m.start() for m in re.finditer(r"\.\.\.", text)]
    for p in ellipses:
        line_start = text.rfind("\n", 0, p) + 1
        line_end = text.find("\n", p)
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        pos_in_line = p - line_start

        if not _is_inside_quote(line, pos_in_line):
            continue

        q = p + 3
        for m in range(min(len(text) - q, max_sec_len), 3, -1):
            if text[q : q + m] in suffixes:
                return True

        for k in range(min(p, max_sec_len), 3, -1):
            if text[p - k : p] in prefixes:
                return True

    return False


def _mask_truncated_secrets(text: str, secrets: Iterable[str]) -> str:
    """Mask tails before '...' that are prefixes and heads after '...' in quoted reprs."""
    if "..." not in text or not secrets:
        return text

    valid_secrets = [s for s in secrets if len(s) >= _MIN_SECRET_LENGTH]
    if not valid_secrets:
        return text

    max_sec_len = max(len(s) for s in valid_secrets)
    prefixes = {s[:length] for s in valid_secrets for length in range(4, len(s) + 1)}
    suffixes = {s[-length:] for s in valid_secrets for length in range(4, len(s) + 1)}

    ellipses = [m.start() for m in re.finditer(r"\.\.\.", text)]
    for p in reversed(ellipses):
        line_start = text.rfind("\n", 0, p) + 1
        line_end = text.find("\n", p)
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        pos_in_line = p - line_start

        if not _is_inside_quote(line, pos_in_line):
            continue

        q = p + 3
        # Head after '...'
        for m in range(min(len(text) - q, max_sec_len), 3, -1):
            if text[q : q + m] in suffixes:
                text = text[:q] + "***" + text[q + m :]
                break

        # Tail before '...'
        for k in range(min(p, max_sec_len), 3, -1):
            if text[p - k : p] in prefixes:
                text = text[: p - k] + "***" + text[p:]
                break

    return text


def scrub_text(text: str, secrets: Iterable[str], *, mask_truncations: bool = False) -> str:
    """Replace all occurrences of secret values in text with '***'."""
    if not text or not secrets:
        return text
    # Replace longest secrets first so partial chunks don't fragment longer tokens
    for secret in sorted(secrets, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, "***")
    if mask_truncations and "..." in text:
        text = _mask_truncated_secrets(text, secrets)
    return text


def _scrub_entry(entry: Any, secrets: Iterable[str]) -> None:
    """Scrub a traceback entry in place."""
    if hasattr(entry, "lines") and isinstance(entry.lines, list):
        entry.lines = [scrub_text(line, secrets, mask_truncations=True) for line in entry.lines]
    if hasattr(entry, "reprlocals") and entry.reprlocals and hasattr(entry.reprlocals, "lines"):
        if isinstance(entry.reprlocals.lines, list):
            entry.reprlocals.lines = [
                scrub_text(line, secrets, mask_truncations=True) for line in entry.reprlocals.lines
            ]
    if (
        hasattr(entry, "reprfileloc")
        and entry.reprfileloc
        and hasattr(entry.reprfileloc, "message")
    ):
        entry.reprfileloc.message = scrub_text(
            entry.reprfileloc.message, secrets, mask_truncations=True
        )
    if (
        hasattr(entry, "reprfuncargs")
        and entry.reprfuncargs
        and hasattr(entry.reprfuncargs, "args")
    ):
        if isinstance(entry.reprfuncargs.args, (list, tuple)):
            entry.reprfuncargs.args = [
                (str(name), scrub_text(str(val), secrets, mask_truncations=True))
                for name, val in entry.reprfuncargs.args
            ]


def _scrub_longrepr_in_place(longrepr: Any, secrets: Iterable[str]) -> None:
    """Scrub longrepr in place where possible, preserving object structure and types."""
    # 1. Update reprcrash message (drives summary lines in -ra)
    if hasattr(longrepr, "reprcrash") and longrepr.reprcrash:
        if hasattr(longrepr.reprcrash, "message"):
            longrepr.reprcrash.message = scrub_text(
                longrepr.reprcrash.message, secrets, mask_truncations=True
            )

    # 2. Update reprtraceback reprentries
    if hasattr(longrepr, "reprtraceback") and longrepr.reprtraceback:
        if hasattr(longrepr.reprtraceback, "reprentries"):
            for entry in longrepr.reprtraceback.reprentries:
                _scrub_entry(entry, secrets)

    # 3. Update ExceptionChainRepr chain: list of (reprtraceback, reprcrash, descr)
    if hasattr(longrepr, "chain") and isinstance(longrepr.chain, list):
        for item in longrepr.chain:
            if isinstance(item, tuple) and len(item) >= 2:
                tb, crash = item[0], item[1]
                if crash and hasattr(crash, "message"):
                    crash.message = scrub_text(crash.message, secrets, mask_truncations=True)
                if tb and hasattr(tb, "reprentries"):
                    for entry in tb.reprentries:
                        _scrub_entry(entry, secrets)

    # 4. Update custom sections if present on ExceptionRepr
    if hasattr(longrepr, "sections") and isinstance(longrepr.sections, list):
        longrepr.sections = [
            (title, scrub_text(content, secrets, mask_truncations=False), sep)
            for title, content, sep in longrepr.sections
        ]

    # 5. Update CollectErrorRepr (holds collection failure message in longrepr attribute)
    if hasattr(longrepr, "longrepr") and isinstance(longrepr.longrepr, str):
        longrepr.longrepr = scrub_text(longrepr.longrepr, secrets, mask_truncations=True)


def _scrub_longrepr(report: Any, secrets: Iterable[str]) -> None:
    """Scrub report.longrepr without degrading structured reports that hold no secret."""
    if report.longrepr is None:
        return

    # Check if any secret is present in the longrepr representation
    longrepr_text = getattr(report, "longreprtext", None) or str(report.longrepr)
    if not any(secret in longrepr_text for secret in secrets) and not _has_truncated_secret(
        longrepr_text, secrets
    ):
        # Report holds no secret: leave longrepr completely untouched
        return

    # Handle skip/xfail tuples: (path, lineno, reason)
    if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
        report.longrepr = (
            report.longrepr[0],
            report.longrepr[1],
            scrub_text(str(report.longrepr[2]), secrets, mask_truncations=False),
        )
        return

    # A secret is present: try in-place modification first to preserve reprcrash and structure
    if not isinstance(report.longrepr, str):
        try:
            _scrub_longrepr_in_place(report.longrepr, secrets)
        except Exception:
            pass

    # Verify and fall back: re-render and, if any secret is still present,
    # replace report.longrepr with the scrubbed string.
    rendered_text = getattr(report, "longreprtext", None) or str(report.longrepr)
    if any(secret in rendered_text for secret in secrets) or _has_truncated_secret(
        rendered_text, secrets
    ):
        report.longrepr = scrub_text(rendered_text, secrets, mask_truncations=True)


def scrub_report(report: Any, secrets: Iterable[str]) -> None:
    """Scrub secrets from a test or collect report."""
    if not secrets:
        return

    # Scrub longrepr (tracebacks, locals, failure/error messages)
    if getattr(report, "longrepr", None) is not None:
        _scrub_longrepr(report, secrets)

    # Scrub wasxfail (string explanation for xfail)
    if getattr(report, "wasxfail", None) is not None:
        report.wasxfail = scrub_text(str(report.wasxfail), secrets, mask_truncations=False)

    # Scrub captured sections (stdout, stderr, log) for all reports (passed, failed, skipped)
    if getattr(report, "sections", None):
        report.sections = [
            (name, scrub_text(str(content), secrets, mask_truncations=False))
            for name, content in report.sections
        ]


def pytest_sessionstart(session: pytest.Session) -> None:  # noqa: ARG001
    """Snapshot secret values at session start."""
    global _SESSION_SECRETS
    _SESSION_SECRETS = collect_secret_values(os.environ)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Item, call: CallInfo[None]) -> Iterator[None]:  # noqa: ARG001
    """Scrub secrets from test reports (failure longrepr and all captured sections)."""
    outcome = yield
    report: TestReport = outcome.get_result()
    accumulated: set[str] = (
        item.stash.get(_ITEM_SECRETS_KEY, set()) if hasattr(item, "stash") else set()
    )
    current_secrets = collect_secret_values(
        os.environ, extra_secrets=_SESSION_SECRETS | accumulated
    )
    accumulated |= current_secrets
    if hasattr(item, "stash"):
        item.stash[_ITEM_SECRETS_KEY] = accumulated
    scrub_report(report, accumulated)


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector: Collector) -> Iterator[None]:  # noqa: ARG001
    """Scrub secrets from collection reports (import errors, collection failures)."""
    outcome = yield
    report: CollectReport = outcome.get_result()
    secrets = collect_secret_values(os.environ, extra_secrets=_SESSION_SECRETS)
    scrub_report(report, secrets)
