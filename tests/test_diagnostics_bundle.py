"""Unit tests for the incident bundle filesystem layer (Task 4 —
S26/S27/S28/S38/S40): root containment, exclusive creation, pending marker
locking, and atomic manifest-last finalization."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gflow_cli.diagnostics import (
    BundleDir,
    validated_incidents_root,
)

_NOW = datetime(2026, 7, 22, 21, 30, 0, tzinfo=UTC)


def _root(base: Path) -> Path:
    root = validated_incidents_root(base)
    assert root is not None
    return root


def _link_dir(target: Path, link: Path) -> None:
    """Directory symlink on POSIX; junction on Windows (no admin needed)."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


class TestValidatedIncidentsRoot:
    def test_creates_incidents_root_under_home(self, tmp_path: Path) -> None:
        root = _root(tmp_path)
        assert root == tmp_path / "incidents"
        assert root.is_dir()

    def test_symlink_and_reparse_roots_refused(self, tmp_path: Path) -> None:
        """S27: a linked incidents root writes nothing — never chase the link."""
        outside = tmp_path / "outside-user-dir"
        outside.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        _link_dir(outside, home / "incidents")
        assert validated_incidents_root(home) is None
        assert list(outside.iterdir()) == []


class TestBundleDir:
    def test_bundle_paths_with_spaces_and_unicode(self, tmp_path: Path) -> None:
        """S26: spaces + non-ASCII home components round-trip fine."""
        home = tmp_path / "gflow höme with spaces"
        home.mkdir()
        bundle = BundleDir.create_exclusive(_root(home), "corr123-fp456", now=_NOW)
        bundle.write_artifact("ui.json", json.dumps({"ok": True}).encode())
        bundle.write_artifact("sensitive/screenshot.png", b"\x89PNG\r\n\x1a\nfake")
        bundle.finalize({"schema": "gflow-incident-v1", "incident_id": "corr123-fp456"})
        manifest = json.loads((bundle.path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["incident_id"] == "corr123-fp456"
        assert (bundle.path / "sensitive" / "screenshot.png").exists()
        assert bundle.path.is_relative_to(home / "incidents")
        assert "2026-07-22" in str(bundle.path)

    def test_exclusive_creation_survives_clock_rollback_collision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S40: identical timestamp+id+suffix must not overwrite — retry with
        fresh randomness instead."""
        import gflow_cli.diagnostics as diag

        suffixes = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
        monkeypatch.setattr(diag.secrets, "token_hex", lambda _n: next(suffixes))
        root = _root(tmp_path)
        first = BundleDir.create_exclusive(root, "corr-fp", now=_NOW)
        first.write_artifact("ui.json", b"{}")
        second = BundleDir.create_exclusive(root, "corr-fp", now=_NOW)
        assert first.path != second.path
        assert (first.path / "ui.json").exists()  # original untouched

    def test_pending_marker_locked_until_finalize(self, tmp_path: Path) -> None:
        """S38: while staged, the marker's advisory lock is held; finalize
        writes manifest.json last, releases the lock, removes the marker."""
        bundle = BundleDir.create_exclusive(_root(tmp_path), "corr-fp", now=_NOW)
        marker = bundle.path / ".pending"
        assert marker.exists()
        assert marker.stat().st_size <= 4096
        # A contender must NOT be able to lock the live marker.
        assert _try_lock(marker) is False
        assert not (bundle.path / "manifest.json").exists()
        bundle.finalize({"schema": "gflow-incident-v1"})
        assert (bundle.path / "manifest.json").exists()
        assert not marker.exists()

    def test_atomic_manifest_last_no_manifest_before_finalize(self, tmp_path: Path) -> None:
        bundle = BundleDir.create_exclusive(_root(tmp_path), "corr-fp", now=_NOW)
        bundle.write_artifact("network.json", b"[]")
        names = {p.name for p in bundle.path.iterdir()}
        assert names == {".pending", "network.json"}

    def test_artifact_names_cannot_escape_bundle(self, tmp_path: Path) -> None:
        bundle = BundleDir.create_exclusive(_root(tmp_path), "corr-fp", now=_NOW)
        for hostile in ("../escape.json", "..\\escape.json", "/abs.json", "a/../../b.json"):
            with pytest.raises(ValueError):
                bundle.write_artifact(hostile, b"x")

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
    def test_posix_modes_0700_0600_from_creation(self, tmp_path: Path) -> None:
        """S28: restrictive from first creation, not via post-write chmod.

        Windows has no equivalent test: protection relies on inherited
        per-user ACLs (documented in docs/SECURITY.md) — asserting chmod
        bits there would be a false claim.
        """
        root = _root(tmp_path)
        bundle = BundleDir.create_exclusive(root, "corr-fp", now=_NOW)
        bundle.write_artifact("ui.json", b"{}")
        bundle.finalize({"schema": "gflow-incident-v1"})
        assert os.stat(root).st_mode & 0o777 == 0o700
        assert os.stat(bundle.path).st_mode & 0o777 == 0o700
        assert os.stat(bundle.path / "ui.json").st_mode & 0o777 == 0o600
        assert os.stat(bundle.path / "manifest.json").st_mode & 0o777 == 0o600


def _try_lock(path: Path) -> bool:
    """Try to grab the marker's advisory lock; True if acquired (then released)."""
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return True
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)
