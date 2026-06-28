"""Live e2e for the Tools framework (`--tool creative-director`).

Verifies, against the real Flow API, that applying a tool before generation
(a) still produces real media and (b) records the applied-tool provenance in the
local catalog — `operations.prompt` = the user's ORIGINAL prompt,
`operations.expanded_prompt` = the submitted Gemini expansion, and
`operations.metadata_json.tool` = the `{name, version, model, params, config_hash}`
snapshot.

Gating (all opt-in):
- `GFLOW_CLI_E2E_PROFILE` — a logged-in Chrome-strategy profile (handled by the
  `e2e_env` fixture; the test is skipped without it).
- `GFLOW_CLI_GEMINI_API_KEY` — REQUIRED here: the tool is never-fatal and falls
  back to the original prompt without a key, in which case NO expansion happens
  and `metadata_json.tool` is (correctly) not written. So these tests skip
  without the key — otherwise they would assert on provenance that, by design,
  is only recorded when the prompt was actually rewritten.
- `GFLOW_CLI_E2E_RUN_VIDEO=1` — the video case additionally gates on this (Veo
  credits), mirroring `test_data_layer_e2e.py`.

Run:
    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_GEMINI_API_KEY=<key> \
        pytest -m e2e_image tests/e2e/test_tools_e2e.py
    # + GFLOW_CLI_E2E_RUN_VIDEO=1 and -m e2e_video for the video case.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_GEMINI_KEY_ENV = "GFLOW_CLI_GEMINI_API_KEY"
_E2E_RUN_VIDEO_ENV = "GFLOW_CLI_E2E_RUN_VIDEO"

# Terse prompts so the Creative Director has something to expand.
_IMAGE_PROMPT = "a red fox in snow"
_VIDEO_PROMPT = "a paper boat drifting downstream"
_TOOL = "creative-director"
_IMAGE_TIMEOUT_S = 240
_VIDEO_TIMEOUT_S = 600

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _run_gflow(
    args: list[str], *, env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run `python -m gflow_cli <args>` as an isolated subprocess (real CLI path)."""
    sub_env = dict(env)
    # The autouse `_isolate_settings` fixture points GFLOW_CLI_HOME at a per-test
    # tmp dir, which `e2e_env` copies — but the real authenticated profiles live
    # under the DEFAULT home. Drop it so the subprocess resolves the real profile;
    # the DB and output dir stay isolated via their own explicit env vars.
    sub_env.pop("GFLOW_CLI_HOME", None)
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=sub_env,
    )


def _open_db(env: dict[str, str]):  # noqa: ANN202 — sqlite3.Connection, imported lazily
    import sqlite3

    db_path = Path(env["GFLOW_CLI_DB_PATH"])
    assert db_path.exists(), f"data layer never wrote to {db_path}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _require_gemini_key() -> None:
    if not os.environ.get(_GEMINI_KEY_ENV, "").strip():
        pytest.skip(
            f"{_GEMINI_KEY_ENV} unset — the tool falls back to the original prompt "
            "and records no metadata_json.tool, so there is nothing to verify."
        )


def _assert_tool_metadata(meta_json: str | None, *, model: str = "gemini-2.5-flash") -> None:
    """Assert the operations.metadata_json.tool provenance shape."""
    assert meta_json, "metadata_json is empty — the tool snapshot was not recorded"
    meta = json.loads(meta_json)
    tool = meta.get("tool")
    assert isinstance(tool, dict), f"metadata_json.tool not a dict: {meta!r}"
    assert tool["name"] == _TOOL
    assert tool["model"] == model
    # config_hash is a sha256 hexdigest; version is the hand-bumped tool version.
    assert len(tool["config_hash"]) == 64
    assert tool["version"]


@pytest.mark.e2e_image
@pytest.mark.e2e_data
def test_t2i_with_tool_records_provenance(e2e_env: dict[str, str]) -> None:
    """`image t2i --tool creative-director` generates a real image AND records the
    original prompt, the expansion, and the tool snapshot."""
    _require_gemini_key()
    profile = e2e_env["GFLOW_CLI_PROFILE"]
    out_dir = Path(e2e_env["GFLOW_CLI_OUTPUT_DIR"])

    result = _run_gflow(
        ["image", "t2i", _IMAGE_PROMPT, "--aspect", "1:1", "--tool", _TOOL, "--profile", profile],
        env=e2e_env,
        timeout=_IMAGE_TIMEOUT_S,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    images = [
        p
        for p in sorted(out_dir.rglob("*"))
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    ]
    assert images, "no image written"
    assert images[0].stat().st_size > 1024
    assert images[0].read_bytes()[:8] == _PNG_MAGIC

    conn = _open_db(e2e_env)
    try:
        op = conn.execute(
            "SELECT prompt, expanded_prompt, prompt_redacted, metadata_json "
            "FROM operations WHERE mode='t2i'"
        ).fetchone()
        assert op is not None, "no t2i operation recorded"
        # prompt column = the user's ORIGINAL terse prompt; the expansion is stored
        # separately (history_prompts=store in the e2e_env fixture).
        assert op["prompt"] == _IMAGE_PROMPT
        assert op["prompt_redacted"] == 0
        assert op["expanded_prompt"], "expanded_prompt not recorded"
        assert op["expanded_prompt"] != _IMAGE_PROMPT, "prompt was not actually expanded"
        _assert_tool_metadata(op["metadata_json"])
    finally:
        conn.close()


@pytest.mark.e2e_video
@pytest.mark.e2e_data
def test_t2v_with_tool_records_provenance(e2e_env: dict[str, str]) -> None:
    """`video t2v --tool creative-director` generates a real video AND records the
    tool snapshot on the (started→succeeded) operation."""
    if os.environ.get(_E2E_RUN_VIDEO_ENV, "0").strip() != "1":
        pytest.skip(f"{_E2E_RUN_VIDEO_ENV}=0 — skipping live Veo run")
    _require_gemini_key()
    profile = e2e_env["GFLOW_CLI_PROFILE"]
    out_dir = Path(e2e_env["GFLOW_CLI_OUTPUT_DIR"])

    result = _run_gflow(
        # t2v ignores GFLOW_CLI_OUTPUT_DIR — pass --out-dir explicitly. Cheapest
        # config: omni-flash / 4s / 1 output.
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
            "--tool",
            _TOOL,
            "--profile",
            profile,
            "--out-dir",
            str(out_dir),
        ],
        env=e2e_env,
        timeout=_VIDEO_TIMEOUT_S,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    videos = [p for p in sorted(out_dir.rglob("*")) if p.is_file() and p.suffix.lower() == ".mp4"]
    assert videos, "no video written"
    assert videos[0].stat().st_size > 100 * 1024
    assert videos[0].read_bytes()[4:8] == b"ftyp"

    conn = _open_db(e2e_env)
    try:
        rows = conn.execute(
            "SELECT status, prompt, expanded_prompt, metadata_json FROM operations WHERE mode='t2v'"
        ).fetchall()
        succeeded = [r for r in rows if r["status"] == "succeeded"]
        assert succeeded, f"no succeeded t2v operation; rows={[dict(r) for r in rows]}"
        op = succeeded[0]
        assert op["prompt"] == _VIDEO_PROMPT
        assert op["expanded_prompt"] and op["expanded_prompt"] != _VIDEO_PROMPT
        _assert_tool_metadata(op["metadata_json"])
    finally:
        conn.close()
