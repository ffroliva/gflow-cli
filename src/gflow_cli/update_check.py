"""Once-a-day PyPI update notice (#479).

Best-effort by contract: every failure path — unreadable cache, offline host,
broken settings, PyPI shape change — resolves to "no notice", never an
exception, and nothing here ever blocks the command. The notice is always
served from the on-disk cache; a stale cache stamps ``checked_at``
SYNCHRONOUSLY (atomic tmp+replace, so the once-a-day cap holds even when the
fetch thread dies with a fast command) and then refreshes ``latest`` on a
daemon thread whose result feeds the NEXT invocation.

Skipped entirely when: ``GFLOW_CLI_UPDATE_CHECK=0``, a CI environment is
detected (``CI`` set to anything but ``0``/``false``), or gflow-cli is not an
index-installed wheel (PEP 610 ``direct_url.json`` present — editable, local
source, VCS, and direct-URL installs must not get index "upgrade" advice).
"""

from __future__ import annotations

import json
import os
import re
import time
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from threading import Thread
from typing import cast

import structlog

from gflow_cli import __version__
from gflow_cli.config import get_settings

log = structlog.get_logger(__name__)

_CHECK_INTERVAL_SECONDS = 86_400.0
_PYPI_JSON_URL = "https://pypi.org/pypi/gflow-cli/json"
_FETCH_TIMEOUT_SECONDS = 3.0


def _installed_from_index() -> bool:
    """True only for an index-installed wheel — the case where "upgrade"
    advice applies. PEP 610: index installs write no ``direct_url.json``;
    its mere presence (editable, local source, VCS, direct wheel URL) means
    ``pip install -U`` would silently replace a deliberate install. A pure
    source run has no distribution at all."""
    try:
        return distribution("gflow-cli").read_text("direct_url.json") is None
    except PackageNotFoundError:
        return False


def _in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() not in ("", "0", "false")


def _is_newer(latest: str, installed: str) -> bool:
    # ponytail: base-version compare only — each dot part contributes its
    # leading digits and parsing stops at the first non-numeric part, so
    # "0.56.0rc1" -> (0, 56, 0) and "0.55.1.post1" -> (0, 55, 1). Ceiling: a
    # post/rc release of the SAME base version is not reported as newer.
    # Upgrade path: packaging.version.Version (new runtime dep) if that bites.
    def parse(version: str) -> tuple[int, ...] | None:
        parts: list[int] = []
        for part in version.split("."):
            match = re.match(r"\d+", part)
            if match is None:
                break
            parts.append(int(match.group()))
        return tuple(parts) or None

    latest_parts, installed_parts = parse(latest), parse(installed)
    if latest_parts is None or installed_parts is None:
        return False
    return latest_parts > installed_parts


def _read_cache(cache_path: Path) -> dict[str, object]:
    try:
        parsed: object = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return cast("dict[str, object]", parsed)


def _write_cache(cache_path: Path, payload: dict[str, object]) -> None:
    """Atomic tmp+replace (house pattern — a bare write_text can be torn by
    a daemon thread frozen at interpreter exit or a concurrent process)."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, cache_path)
    except OSError as exc:
        log.debug("update_check.cache_write_failed", error=type(exc).__name__)


def _refresh_cache(cache_path: Path) -> None:
    """Fetch the latest version from PyPI and rewrite the cache. The caller
    already stamped ``checked_at``, so a failed fetch writes nothing and the
    once-a-day cap still holds."""
    try:
        import httpx  # lazy — keeps CLI startup free of the httpx import chain

        response = httpx.get(_PYPI_JSON_URL, timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        latest = str(response.json()["info"]["version"])
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        log.debug("update_check.fetch_failed", error=type(exc).__name__)
        return
    _write_cache(cache_path, {"checked_at": time.time(), "latest": latest})


def maybe_notify_update() -> str | None:
    """The one-line update notice to show the user, or None. Never raises."""
    try:
        settings = get_settings()
        if not settings.update_check or _in_ci():
            return None
        if not _installed_from_index():
            return None
        cache_path = Path(settings.home) / "update_check.json"
        cache = _read_cache(cache_path)
        checked_at = cache.get("checked_at")
        latest = cache.get("latest")
        now = time.time()
        # A future checked_at (clock skew, restored VM/backup) must count as
        # stale, or the cache stays "fresh" until the wall clock catches up.
        fresh = isinstance(checked_at, (int, float)) and 0 <= now - checked_at <= (
            _CHECK_INTERVAL_SECONDS
        )
        if not fresh:
            # Stamp checked_at SYNCHRONOUSLY before spawning the fetch: the
            # daemon thread dies with fast commands, and an unstamped cache
            # would fire a doomed PyPI request on every invocation.
            _write_cache(
                cache_path,
                {"checked_at": now, "latest": latest if isinstance(latest, str) else None},
            )
            Thread(target=_refresh_cache, args=(cache_path,), daemon=True).start()
        if isinstance(latest, str) and _is_newer(latest, __version__):
            return (
                f"A newer gflow-cli is available: {latest} (installed {__version__}). "
                "Upgrade: `uv tool upgrade gflow-cli` (or `pipx upgrade gflow-cli` / "
                "`pip install -U gflow-cli`). Set GFLOW_CLI_UPDATE_CHECK=0 to silence."
            )
        return None
    except Exception as exc:  # noqa: BLE001 — the notice must never break a command
        log.debug("update_check.skipped", error=type(exc).__name__)
        return None
