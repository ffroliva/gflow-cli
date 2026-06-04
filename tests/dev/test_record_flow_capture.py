"""Wiring tests for the dev-scoped ``record_flow_capture`` driver.

These exercise the driver's pure-Python wiring (the image-path guard, the
no-ffmpeg transcode fallback, and argparse dispatch) WITHOUT launching a real
browser or generating anything — the genuine Flow interaction is validated by
the gated live run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_DEV = Path(__file__).resolve().parents[2] / "scripts" / "dev"
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))

import record_flow_capture as rfc  # noqa: E402


def test_verify_image_path_accepts_completed_face_run() -> None:
    events = {"character_create.completed", "character_create.face_done"}
    assert rfc._verify_image_path(events) is None


def test_verify_image_path_rejects_failure() -> None:
    err = rfc._verify_image_path(
        {"character_create.failed", "character_create.completed", "character_create.face_done"}
    )
    assert err is not None and "failure" in err


def test_verify_image_path_rejects_incomplete() -> None:
    assert rfc._verify_image_path({"character_create.face_done"}) is not None


def test_verify_image_path_rejects_no_face() -> None:
    # Completed but the image path never ran (no face event) -> reject.
    assert rfc._verify_image_path({"character_create.completed"}) is not None


def test_transcode_falls_back_to_webm_without_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rfc.shutil, "which", lambda _: None)
    src = tmp_path / "rec.webm"
    src.write_bytes(b"fake-webm-bytes")
    dst = tmp_path / "out" / "flow-create.mp4"
    rfc._transcode(src, dst)
    fallback = dst.with_suffix(".webm")
    assert fallback.exists()
    assert fallback.read_bytes() == b"fake-webm-bytes"
    assert not dst.exists()  # no ffmpeg -> no mp4


def test_main_dispatches_run_with_parsed_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_run(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(rfc, "_run", fake_run)
    monkeypatch.setattr(rfc, "resolve_profile_dir", lambda p: tmp_path / "profiles" / p)
    out = tmp_path / "x.mp4"
    rc = rfc.main(["--profile", "promo", "--out", str(out)])
    assert rc == 0
    assert captured["profile"] == "promo"
    assert captured["locale"] == "en-US"  # genuine default; normalized by #153 fix
    assert captured["out_path"] == out
    assert captured["profile_dir"] == tmp_path / "profiles" / "promo"
