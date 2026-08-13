"""Unit + multiprocess tests for incident retention (Task 7 — S37/S38/S39).

Retention is a security boundary: it must delete ONLY validated recorder-owned
content and must never chase links or touch unknown directories.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gflow_cli.diagnostics import (
    BundleDir,
    run_retention,
    validated_incidents_root,
)

_NOW = datetime(2026, 7, 22, 21, 30, 0, tzinfo=UTC)


def _root(base: Path) -> Path:
    root = validated_incidents_root(base)
    assert root is not None
    return root


def _complete_bundle(
    root: Path, incident_id: str, *, now: datetime, payload: bytes = b"{}"
) -> Path:
    b = BundleDir.create_exclusive(root, incident_id, now=now)
    b.write_artifact("ui.json", payload)
    b.write_artifact("network.json", b"[]")
    b.finalize({"schema": "gflow-incident-v1", "incident_id": incident_id})
    return b.path


def _staged_bundle(root: Path, incident_id: str, *, now: datetime) -> BundleDir:
    b = BundleDir.create_exclusive(root, incident_id, now=now)
    b.write_artifact("ui.json", b"{}")
    return b


def _age_marker(bundle: Path, hours: float) -> None:
    old = (_NOW - timedelta(hours=hours)).timestamp()
    os.utime(bundle / ".pending", (old, old))


class TestRetentionSafety:
    def test_retention_never_deletes_unknown_or_escaping_content(self, tmp_path: Path) -> None:
        """S37: unknown dirs, invalid/oversized manifests, and linked children
        all survive; only valid oldest complete bundles are pruned."""
        root = _root(tmp_path)
        day = root / "2026-07-22"
        day.mkdir(exist_ok=True)
        # Unknown directory with no gflow marker.
        (day / "user-notes").mkdir()
        (day / "user-notes" / "notes.txt").write_text("keep me")
        # Wrong-schema manifest.
        wrong = day / "20260722T000000Z-wrong-aaaaaa"
        wrong.mkdir()
        (wrong / "manifest.json").write_text(json.dumps({"schema": "not-gflow"}))
        # Oversized manifest (> 64 KiB parse cap).
        huge = day / "20260722T000001Z-huge-aaaaaa"
        huge.mkdir()
        (huge / "manifest.json").write_text(
            '{"schema": "gflow-incident-v1", "pad": "' + "x" * 70_000 + '"}'
        )
        # Bundle containing an unknown artifact file.
        alien = day / "20260722T000002Z-alien-aaaaaa"
        alien.mkdir()
        (alien / "manifest.json").write_text(json.dumps({"schema": "gflow-incident-v1"}))
        (alien / "passwords.txt").write_text("keep")
        # Valid bundles beyond the count cap — only these are prunable.
        valid = [_complete_bundle(root, f"v{i}", now=_NOW.replace(minute=i)) for i in range(5)]
        run_retention(root, max_complete=2)
        assert (day / "user-notes" / "notes.txt").exists()
        assert (wrong / "manifest.json").exists()
        assert (huge / "manifest.json").exists()
        assert (alien / "passwords.txt").exists()
        survivors = [p for p in valid if p.exists()]
        assert len(survivors) == 2
        assert survivors == valid[-2:]  # oldest pruned first

    def test_symlinked_child_is_never_deleted_or_followed(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("keep")
        root = _root(tmp_path / "home2")
        day = root / "2026-07-22"
        day.mkdir(exist_ok=True)
        link = day / "20260722T000003Z-link-aaaaaa"
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
        # Give the linked dir a "valid" manifest so ONLY link-refusal saves it.
        (outside / "manifest.json").write_text(json.dumps({"schema": "gflow-incident-v1"}))
        run_retention(root, max_complete=0)
        assert (outside / "precious.txt").exists()
        assert (outside / "manifest.json").exists()

    def test_bundle_with_report_is_still_classified_and_prunable(self, tmp_path: Path) -> None:
        """report.md is recorder-owned (issue #476) — it must not flip a bundle
        to 'unknown', which would exempt it from pruning forever."""
        root = _root(tmp_path)
        bundles = []
        for i in range(3):
            b = BundleDir.create_exclusive(root, f"r{i}", now=_NOW.replace(minute=i))
            b.write_artifact("ui.json", b"{}")
            b.write_artifact("report.md", b"# report")
            b.finalize({"schema": "gflow-incident-v1", "incident_id": f"r{i}"})
            bundles.append(b.path)
        run_retention(root, max_complete=1)
        assert sum(p.exists() for p in bundles) == 1

    def test_count_and_byte_limits_enforced(self, tmp_path: Path) -> None:
        root = _root(tmp_path)
        for i in range(4):
            _complete_bundle(root, f"big{i}", now=_NOW.replace(minute=i), payload=b"x" * 10_000)
        run_retention(root, max_complete=10, max_complete_bytes=25_000)
        remaining = [
            b for day in root.iterdir() if day.is_dir() for b in day.iterdir() if b.is_dir()
        ]
        assert len(remaining) == 2  # byte cap trimmed the two oldest


class TestPendingRetention:
    def test_stale_pending_pruned_only_after_lock_and_age(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S38: locked marker → active, untouched. Unlocked + young → kept.
        Unlocked + >24h → pruned. Unlocked + valid manifest → marker removed,
        bundle kept (crash-left stale marker)."""
        import gflow_cli.diagnostics as diag

        monkeypatch.setattr(diag.time, "time", lambda: _NOW.timestamp())
        root = _root(tmp_path)
        active = _staged_bundle(root, "active", now=_NOW)  # lock still held
        young = _staged_bundle(root, "young", now=_NOW.replace(minute=1))
        young_path = young.path
        _release_marker(young)
        _age_marker(young_path, hours=1)
        stale = _staged_bundle(root, "stale", now=_NOW.replace(minute=2))
        stale_path = stale.path
        _release_marker(stale)
        _age_marker(stale_path, hours=30)
        crashed = _staged_bundle(root, "crashed", now=_NOW.replace(minute=3))
        crashed_path = crashed.path
        # Simulate manifest-written-then-crash-before-marker-removal.
        (crashed_path / "manifest.json").write_text(json.dumps({"schema": "gflow-incident-v1"}))
        _release_marker(crashed)
        _age_marker(crashed_path, hours=30)

        run_retention(root)

        assert active.path.exists() and (active.path / ".pending").exists()
        assert young_path.exists()
        assert not stale_path.exists()
        assert crashed_path.exists()
        assert not (crashed_path / ".pending").exists()  # stale marker cleaned

    def test_refused_deletion_does_not_condemn_healthy_bundles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review fix: a bundle the sweep cannot delete must not keep inflating
        the working byte total and take every healthy pending bundle with it."""
        import gflow_cli.diagnostics as diag

        monkeypatch.setattr(diag.time, "time", lambda: _NOW.timestamp())
        root = _root(tmp_path)
        big = BundleDir.create_exclusive(root, "big", now=_NOW)
        big.write_artifact("ui.json", b"x" * 5_000)  # dominates the byte total
        big_path = big.path
        _release_marker(big)
        _age_marker(big_path, hours=30)  # oldest — the byte loop pops it first
        healthy: list[Path] = []
        for i in range(3):
            b = _staged_bundle(root, f"ok{i}", now=_NOW.replace(minute=10 + i))
            healthy.append(b.path)
            _release_marker(b)
            _age_marker(b.path, hours=1)  # young: only the byte cap can touch them

        real_delete = diag._safe_delete_bundle

        def _refusing_delete(bundle: Path, root_resolved: Path) -> int:
            if bundle == big_path:
                return 0  # simulates a locked/reparse refusal
            return real_delete(bundle, root_resolved)

        monkeypatch.setattr(diag, "_safe_delete_bundle", _refusing_delete)
        # Total ≈ 5.3KB with a 6KB... no: cap 2KB < big alone, so the byte loop
        # engages, pops the refused big bundle first, and — with its size
        # subtracted on pop — the remaining total is under the cap, so the
        # healthy bundles survive. Pre-fix, the stuck 5KB total condemned all.
        run_retention(root, max_pending=10, max_pending_bytes=2_000)
        assert big_path.exists()  # refused, left as-is
        assert all(p.exists() for p in healthy)

    def test_retention_skips_when_lock_held(self, tmp_path: Path) -> None:
        root = _root(tmp_path)
        doomed = _complete_bundle(root, "doomed", now=_NOW)
        lock_fd = _hold_retention_lock(root)
        try:
            run_retention(root, max_complete=0)
            assert doomed.exists()  # another process owns retention — skipped
        finally:
            os.close(lock_fd)


class TestMultiprocess:
    def test_multiprocess_prune_is_race_safe(self, tmp_path: Path) -> None:
        """S39: two concurrent pruners — no crash, no double-delete error, and
        the active pending bundle survives."""
        root = _root(tmp_path)
        active = _staged_bundle(root, "active", now=_NOW)
        for i in range(6):
            _complete_bundle(root, f"c{i}", now=_NOW.replace(second=i))
        script = (
            "from pathlib import Path\n"
            "from gflow_cli.diagnostics import run_retention\n"
            f"run_retention(Path({str(root)!r}), max_complete=1)\n"
            "print('OK')\n"
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONUTF8": "1", "GFLOW_CLI_HOME": str(tmp_path)},
            )
            for _ in range(2)
        ]
        outs = [p.communicate(timeout=60) for p in procs]
        assert all(p.returncode == 0 for p in procs), outs
        assert active.path.exists() and (active.path / ".pending").exists()
        remaining = [
            b
            for day in root.iterdir()
            if day.is_dir()
            for b in day.iterdir()
            if b.is_dir() and (b / "manifest.json").exists()
        ]
        assert len(remaining) >= 1


def _release_marker(bundle: BundleDir) -> None:
    """Release the marker lock WITHOUT finalizing (simulates a dead process)."""
    import gflow_cli.diagnostics as diag

    fd = bundle._marker_fd  # noqa: SLF001 — test needs to simulate process death
    if fd >= 0:
        diag._kernel_unlock(fd)  # noqa: SLF001
        os.close(fd)
        bundle._marker_fd = -1  # noqa: SLF001


def _hold_retention_lock(root: Path) -> int:
    import gflow_cli.diagnostics as diag

    fd = os.open(root / ".retention", os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    os.write(fd, b"\0")
    os.lseek(fd, 0, os.SEEK_SET)
    diag._kernel_lock_nonblocking(fd)  # noqa: SLF001
    return fd
