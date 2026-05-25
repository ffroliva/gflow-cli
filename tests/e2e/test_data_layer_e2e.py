"""Live E2E for the SQLite data layer.

Drives `gflow image t2i` and `gflow video t2v` end-to-end via subprocess
against a real Pro/Ultra Google Flow account, then asserts the
``OperationRecorder`` actually persisted:

  - the active ``profile`` row
  - the Flow ``project`` row (source=generated)
  - the Flow ``asset`` row (image / video, kind + dimensions + media IDs)
  - the ``operation`` row (t2i / t2v, status=succeeded, prompt fields)
  - the ``operation_assets`` link (output, position 0)
  - the ``local_files`` row (resolved absolute path, sha256, bytes)

Also drives ``gflow data media <media_id>`` and asserts the persisted row
round-trips through the read CLI.

Spec: ``docs/superpowers/specs/2026-05-24-data-layer-design.md``
Plan: ``docs/superpowers/plans/2026-05-24-data-layer.md``
Doc:  ``docs/DATA_LAYER.md``

# Opt-in gates

  - ``GFLOW_CLI_E2E_PROFILE``    master gate; Chrome-strategy profile name
  - ``GFLOW_CLI_E2E_RUN_VIDEO``  default "1"; set to "0" to skip the Veo step
                                  (only spends an Imagen credit then)

# Spending

  - t2i: ~1 Imagen credit
  - t2v: 1 Veo credit (omni-flash, 4s, count=1 — cheapest configuration)

Per the project's 5-layer verification ledger ([[verification-ledger-5-layer]])
plus a 6th layer: data-layer row presence.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"
_E2E_RUN_VIDEO_ENV = "GFLOW_CLI_E2E_RUN_VIDEO"

_IMAGE_PROMPT = "a single red apple on a wooden table, soft daylight"
_VIDEO_PROMPT = "a single red apple on a wooden table, soft daylight"

# Generous because real Flow latency: image ~30-60s; video ~60-180s.
_IMAGE_TIMEOUT_S = 240
_VIDEO_TIMEOUT_S = 600


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_name() -> str:
    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(
            f"E2E data-layer test requires {_E2E_PROFILE_ENV} — "
            "set it to a logged-in Chromium profile name and re-run with -m e2e"
        )
    return name


def _run_video_enabled() -> bool:
    return os.environ.get(_E2E_RUN_VIDEO_ENV, "1").strip() != "0"


def _run_gflow(
    args: list[str], env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run ``gflow`` in a subprocess and capture stdout/stderr/exit-code."""
    cmd = [sys.executable, "-m", "gflow_cli", *args]
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture
def e2e_env(tmp_path: Path) -> Iterator[dict[str, str]]:
    """Build an isolated subprocess env: tmp DB + tmp output dir + active profile."""
    profile = _profile_name()
    db = tmp_path / "gflow.db"
    out = tmp_path / "out"
    out.mkdir()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["GFLOW_CLI_DB_PATH"] = str(db)
    env["GFLOW_CLI_OUTPUT_DIR"] = str(out)
    env["GFLOW_CLI_PROFILE"] = profile
    env["GFLOW_CLI_HISTORY_PROMPTS"] = "store"  # keep full prompt for assertion
    yield env


def _open_db(env: dict[str, str]) -> sqlite3.Connection:
    db_path = Path(env["GFLOW_CLI_DB_PATH"])
    assert db_path.exists(), f"data layer never wrote to {db_path}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _image_magic(path: Path) -> str | None:
    head = path.read_bytes()[:12]
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    return None


def _is_mp4(path: Path) -> bool:
    # ISO BMFF container; bytes 4..8 are 'ftyp' for valid mp4/mov.
    return path.read_bytes()[4:8] == b"ftyp"


# ---------------------------------------------------------------------------
# t2i: image generation records every layer
# ---------------------------------------------------------------------------


def test_t2i_records_full_provenance(e2e_env: dict[str, str]) -> None:
    """Live t2i: ~1 Imagen credit. Asserts file lands AND DB carries the full
    profile→project→asset→operation→operation_assets→local_files chain."""
    profile = e2e_env["GFLOW_CLI_PROFILE"]
    out_dir = Path(e2e_env["GFLOW_CLI_OUTPUT_DIR"])

    result = _run_gflow(
        ["image", "t2i", _IMAGE_PROMPT, "--aspect", "1:1", "--profile", profile],
        env=e2e_env,
        timeout=_IMAGE_TIMEOUT_S,
    )

    assert result.returncode == 0, (
        f"gflow image t2i exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    # ---- 5-layer file ledger ----
    images = [
        p
        for p in sorted(out_dir.rglob("*"))
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    ]
    assert len(images) >= 1, f"no image files landed in {out_dir}"
    image = images[0]
    assert image.stat().st_size > 1024, f"suspiciously small image: {image.stat().st_size} bytes"
    assert _image_magic(image) is not None, f"bad magic bytes: {image.read_bytes()[:12]!r}"

    # Pillow check (dimensions are a strong "real image" signal).
    from PIL import Image

    with Image.open(image) as im:
        width, height = im.size
        assert width > 0 and height > 0, f"zero-dimension image: {im.size}"
        # 1:1 aspect — allow ±2% slack for H.264-style alignment.
        ratio = width / height
        assert abs(ratio - 1.0) <= 0.02, f"aspect mismatch on 1:1: {im.size}"

    # ---- data-layer row ledger ----
    conn = _open_db(e2e_env)
    try:
        # profile
        profile_rows = conn.execute("SELECT name FROM profiles").fetchall()
        assert profile in [r["name"] for r in profile_rows], (
            f"profile {profile!r} not persisted; rows: {[dict(r) for r in profile_rows]}"
        )

        # operation: exactly one t2i operation, succeeded, prompt persisted
        op_rows = conn.execute(
            "SELECT mode, status, prompt, prompt_hash, prompt_redacted, model, aspect_ratio, "
            "started_at, completed_at FROM operations WHERE mode='t2i'"
        ).fetchall()
        assert len(op_rows) == 1, f"expected 1 t2i operation, got {len(op_rows)}"
        op = op_rows[0]
        assert op["status"] == "succeeded", f"expected status=succeeded, got {op['status']!r}"
        assert op["prompt"] == _IMAGE_PROMPT, (
            f"prompt round-trip failed: persisted={op['prompt']!r} expected={_IMAGE_PROMPT!r}"
        )
        assert op["prompt_hash"] and len(op["prompt_hash"]) == 64, (
            f"prompt_hash should be SHA-256 hex (64 chars), got {op['prompt_hash']!r}"
        )
        assert op["prompt_redacted"] == 0, "store mode must keep prompt_redacted=0"
        assert op["started_at"] is not None and op["completed_at"] is not None

        # asset: at least one image asset with a Flow media ID
        asset_rows = conn.execute(
            "SELECT id, flow_media_id, flow_project_id, kind, status, width, height "
            "FROM assets WHERE kind='image'"
        ).fetchall()
        assert len(asset_rows) >= 1, "no image asset row persisted"
        asset = asset_rows[0]
        assert asset["flow_media_id"], f"asset has empty flow_media_id: {dict(asset)}"
        assert asset["flow_project_id"], (
            f"asset has empty flow_project_id (would break I2V seed resolution): {dict(asset)}"
        )

        # project: source='generated' for t2i
        project_rows = conn.execute("SELECT flow_project_id, source FROM projects").fetchall()
        assert any(p["source"] == "generated" for p in project_rows), (
            f"no generated project persisted: {[dict(p) for p in project_rows]}"
        )

        # operation_assets: link as output position 0
        link_rows = conn.execute("SELECT role, position FROM operation_assets").fetchall()
        assert any(r["role"] == "output" and r["position"] == 0 for r in link_rows), (
            f"no output position-0 link persisted: {[dict(r) for r in link_rows]}"
        )

        # local_files: resolved absolute path, sha256, bytes
        file_rows = conn.execute(
            "SELECT path, sha256, bytes, media_type FROM local_files"
        ).fetchall()
        assert len(file_rows) >= 1, "no local_files row persisted"
        file_row = file_rows[0]
        assert Path(file_row["path"]).is_absolute(), (
            f"local_files.path must be absolute: {file_row['path']}"
        )
        assert file_row["sha256"] and len(file_row["sha256"]) == 64, (
            f"sha256 should be hex (64 chars): {file_row['sha256']!r}"
        )
        assert file_row["bytes"] and file_row["bytes"] > 1024, (
            f"local_files.bytes too small: {file_row['bytes']}"
        )

        flow_media_id = asset["flow_media_id"]
    finally:
        conn.close()

    # ---- read CLI round-trip ----
    lookup = _run_gflow(
        ["data", "media", flow_media_id, "--profile", profile],
        env=e2e_env,
        timeout=30,
    )
    assert lookup.returncode == 0, (
        f"gflow data media exited {lookup.returncode}\n"
        f"STDOUT:\n{lookup.stdout}\nSTDERR:\n{lookup.stderr}"
    )
    assert flow_media_id in lookup.stdout, (
        f"flow_media_id {flow_media_id!r} not in CLI output:\n{lookup.stdout}"
    )
    assert "image" in lookup.stdout, f"kind 'image' not in CLI output:\n{lookup.stdout}"


# ---------------------------------------------------------------------------
# t2v: video generation records started + completed lifecycle
# ---------------------------------------------------------------------------


def test_t2v_records_started_and_completed_lifecycle(e2e_env: dict[str, str]) -> None:
    """Live t2v: 1 Veo credit (omni-flash, 4s, count=1). Asserts video lands
    AND DB shows the operation went through started → succeeded plus a
    video-kind asset and a local_files row.

    Skipped when ``GFLOW_CLI_E2E_RUN_VIDEO=0`` (cost-control)."""
    if not _run_video_enabled():
        pytest.skip(f"{_E2E_RUN_VIDEO_ENV}=0 — skipping live Veo run")

    profile = e2e_env["GFLOW_CLI_PROFILE"]
    out_dir = Path(e2e_env["GFLOW_CLI_OUTPUT_DIR"])

    # gflow video t2v does NOT honor GFLOW_CLI_OUTPUT_DIR (defaults to ./tmp);
    # pass --out-dir explicitly so the test's tmp_path captures the mp4.
    result = _run_gflow(
        [
            "video",
            "t2v",
            _VIDEO_PROMPT,
            "--aspect",
            "16:9",
            "--model",
            "omni-flash",
            "--duration",
            "4",
            "--count",
            "1",
            "--profile",
            profile,
            "--out-dir",
            str(out_dir),
        ],
        env=e2e_env,
        timeout=_VIDEO_TIMEOUT_S,
    )

    assert result.returncode == 0, (
        f"gflow video t2v exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    # ---- file ledger ----
    videos = [p for p in sorted(out_dir.rglob("*")) if p.is_file() and p.suffix.lower() == ".mp4"]
    assert len(videos) >= 1, f"no .mp4 files landed in {out_dir}"
    video = videos[0]
    assert video.stat().st_size > 100 * 1024, (
        f"suspiciously small video: {video.stat().st_size} bytes"
    )
    assert _is_mp4(video), f"bad mp4 magic bytes: {video.read_bytes()[:12]!r}"

    # ---- data-layer row ledger ----
    conn = _open_db(e2e_env)
    try:
        # asset FIRST so we can cross-check flow_operation_id below.
        asset_rows = conn.execute(
            "SELECT flow_media_id, flow_project_id, kind, status FROM assets WHERE kind='video'"
        ).fetchall()
        assert len(asset_rows) == 1, (
            f"expected exactly 1 video asset, got {[dict(r) for r in asset_rows]}"
        )
        asset = asset_rows[0]

        # operation: t2v, terminal status, prompt + completed_at populated
        op_rows = conn.execute(
            "SELECT id, status, completed_at, prompt, prompt_hash, "
            "flow_operation_id FROM operations WHERE mode='t2v'"
        ).fetchall()
        assert len(op_rows) >= 1, "no t2v operation persisted"
        # We expect exactly one row in v1 — record_completed_video updates the
        # pending row via update_operation_status, it does not insert a second.
        terminal = [r for r in op_rows if r["status"] == "succeeded"]
        assert len(terminal) == 1, (
            f"expected exactly 1 succeeded t2v operation, got {[dict(r) for r in op_rows]}"
        )
        op = terminal[0]
        assert op["prompt"] == _VIDEO_PROMPT
        assert op["prompt_hash"] and len(op["prompt_hash"]) == 64
        assert op["completed_at"] is not None
        # flow_operation_id is opportunistic — Flow's omni-flash response does
        # not always carry operations[0].operation.name. Spec: store SEPARATELY
        # from flow_media_id when present. If present, it currently equals
        # flow_media_id (observation; subject to Flow API drift).
        if op["flow_operation_id"]:
            assert op["flow_operation_id"] == asset["flow_media_id"], (
                "When flow_operation_id is present, current Flow API surfaces "
                f"it as the media id; got op={op['flow_operation_id']!r}, "
                f"media={asset['flow_media_id']!r}"
            )
        assert asset["flow_media_id"], "video asset has empty flow_media_id"
        assert asset["flow_project_id"], "video asset has empty flow_project_id"
        assert "SUCCESSFUL" in (asset["status"] or "").upper(), (
            f"video asset status not terminal SUCCESSFUL: {asset['status']!r}"
        )

        # local_files: at least one row for the video
        file_rows = conn.execute(
            "SELECT path, bytes FROM local_files WHERE path LIKE '%.mp4'"
        ).fetchall()
        assert len(file_rows) >= 1, "no video local_files row persisted"

        flow_media_id = asset["flow_media_id"]
    finally:
        conn.close()

    # ---- read CLI round-trip ----
    lookup = _run_gflow(
        ["data", "media", flow_media_id, "--profile", profile],
        env=e2e_env,
        timeout=30,
    )
    assert lookup.returncode == 0, (
        f"gflow data media exited {lookup.returncode}\n"
        f"STDOUT:\n{lookup.stdout}\nSTDERR:\n{lookup.stderr}"
    )
    assert flow_media_id in lookup.stdout
    assert "video" in lookup.stdout


# ---------------------------------------------------------------------------
# data CLI: not-found path
# ---------------------------------------------------------------------------


def test_data_media_unknown_id_exits_non_zero(e2e_env: dict[str, str]) -> None:
    """Sanity: an unknown media ID against a freshly-created DB exits non-zero
    via DataStoreError (exit 16). This does NOT spend credits."""
    profile = e2e_env["GFLOW_CLI_PROFILE"]
    # Touch the DB so it exists (open + migrate happens on first command).
    _run_gflow(["data", "media", "ignored", "--profile", profile], env=e2e_env, timeout=30)
    # Now query for a media ID we KNOW isn't there.
    result = _run_gflow(
        ["data", "media", "does-not-exist", "--profile", profile],
        env=e2e_env,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected non-zero for unknown media ID, got 0\nSTDOUT:\n{result.stdout}"
    )
    assert result.returncode == 16, f"expected exit 16 (DataStoreError), got {result.returncode}"


# ---------------------------------------------------------------------------
# Evidence dump — call from a single test to write a JSON snapshot for the
# LIVE_VERIFICATION_data_layer.md doc.
# ---------------------------------------------------------------------------


def dump_db_evidence(db_path: Path, dest: Path) -> None:
    """Snapshot every data-layer table from ``db_path`` to ``dest`` (JSON).

    Helper used to capture verification evidence after a live run — call from
    the shell with the post-run DB path produced by pytest --basetemp.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        snapshot: dict[str, object] = {}
        for tbl in (
            "profiles",
            "projects",
            "assets",
            "operations",
            "operation_assets",
            "local_files",
            "schema_migrations",
        ):
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {tbl}").fetchall()]
            snapshot[tbl] = rows
        dest.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    finally:
        conn.close()
