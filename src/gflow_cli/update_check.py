"""Once-a-day PyPI update notice (#479).

Best-effort by contract: every failure path — unreadable cache, offline host,
broken settings, PyPI shape change — resolves to "no notice", never an
exception, and nothing here ever blocks the command. The notice is always
served from the on-disk cache; a stale cache triggers a daemon-thread refresh
whose result feeds the NEXT invocation (a very short command may exit before
the refresh lands — it retries next run, still capped at one poll per day).

Skipped entirely when: ``GFLOW_CLI_UPDATE_CHECK=0``, the ``CI`` env var is
set, or gflow-cli is not an index-installed wheel (editable and local-source
installs per PEP 610 ``direct_url.json`` — "upgrade" advice is wrong there).
"""

from __future__ import annotations

import json
import os
import threading
import time
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import cast

import httpx
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
    editable (PEP 660) and local/direct installs do (a pure source run has no
    distribution at all)."""
    try:
        direct_url = distribution("gflow-cli").read_text("direct_url.json")
    except PackageNotFoundError:
        return False
    if direct_url is None:
        return True
    try:
        info = json.loads(direct_url)
    except ValueError:
        return False
    if info.get("dir_info", {}).get("editable"):
        return False
    return not str(info.get("url", "")).startswith("file://")


def _is_newer(latest: str, installed: str) -> bool:
    def parse(version: str) -> tuple[int, ...] | None:
        try:
            return tuple(int(part) for part in version.split("."))
        except ValueError:
            return None

    latest_parts, installed_parts = parse(latest), parse(installed)
    if latest_parts is None or installed_parts is None:
        return False
    return latest_parts > installed_parts


def _refresh_cache(cache_path: Path) -> None:
    """Fetch the latest version from PyPI and rewrite the cache. Always stamps
    ``checked_at`` — even on failure — so an offline host polls at most once a
    day instead of on every invocation; a previously fetched ``latest``
    survives a failed poll."""
    latest: str | None = None
    try:
        response = httpx.get(_PYPI_JSON_URL, timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        latest = str(response.json()["info"]["version"])
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        log.debug("update_check.fetch_failed", error=type(exc).__name__)
    try:
        if latest is None:
            cached = _read_cache(cache_path).get("latest")
            latest = cached if isinstance(cached, str) else None
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"checked_at": time.time(), "latest": latest}), encoding="utf-8"
        )
    except OSError as exc:
        log.debug("update_check.cache_write_failed", error=type(exc).__name__)


def _read_cache(cache_path: Path) -> dict[str, object]:
    try:
        parsed: object = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return cast("dict[str, object]", parsed)


def maybe_notify_update() -> str | None:
    """The one-line update notice to show the user, or None. Never raises."""
    try:
        settings = get_settings()
        if not settings.update_check or os.environ.get("CI"):
            return None
        if not _installed_from_index():
            return None
        cache_path = Path(settings.home) / "update_check.json"
        cache = _read_cache(cache_path)
        checked_at = cache.get("checked_at")
        stale = (
            not isinstance(checked_at, (int, float))
            or time.time() - checked_at > _CHECK_INTERVAL_SECONDS
        )
        if stale:
            threading.Thread(target=_refresh_cache, args=(cache_path,), daemon=True).start()
        latest = cache.get("latest")
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
