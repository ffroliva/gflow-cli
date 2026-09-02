"""Live E2E for entity provenance recording (#402).

The unit tests in ``tests/data/test_recorder.py`` prove the recorder writes what
the *request* carried. They cannot prove the recorded set matches what Flow
actually **accepted** — that is the gap this file closes, and the reason #402's
fix shipped as ``Refs`` rather than ``Closes``.

Each test drives a real generation with ``--reference-entity`` against a live
Pro/Ultra account, then asserts ``operations.metadata_json`` carries the entity
that produced the output:

  - t2i + entity  → succeeded row carries ``entity_ids`` / ``entity_names``
  - i2i + entity  → same, on the surface where #402 lost 333 generations
  - bad entity id → FAILED row STILL carries ``entity_ids`` (intent recorded
    even when the attach is rejected — the negative case that made a
    silently-dropped reference indistinguishable from a good one)

The entity itself is minted with ``client.create_entity``, which is a FREE REST
call (no reCAPTCHA, no credit) — so the only spend here is the image generation.

# Opt-in gates

  - ``GFLOW_CLI_E2E_PROFILE``            master gate; Chrome-strategy profile name
  - ``GFLOW_CLI_E2E_RUN_ENTITY_PROV``   default "0"; set to "1" to run (spends
                                         image generations (zero credits))

# Spending

  - t2i + entity:  1 image generation (zero credits)
  - i2i + entity:  2 image generations, zero credits (one seed t2i, then the i2i itself)
  - bad entity id: 0 credits expected — the attach is refused before submit

Doc: ``docs/DATA_LAYER.md`` §"Operation ``metadata_json`` provenance"
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_RUN_ENTITY_PROV_ENV = "GFLOW_CLI_E2E_RUN_ENTITY_PROV"
_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"

_ENTITY_NAME = "e2e-provenance-character"
_T2I_PROMPT = "portrait of a calm woman with dark wavy hair, soft studio lighting"
_I2I_PROMPT = "the same woman, now standing on a windy beach at golden hour"

# Generous because real Flow latency: image ~30-60s, plus picker interaction.
_IMAGE_TIMEOUT_S = 300
# A rejected attach fails fast, but the picker cascade still exhausts its tiers.
_REJECT_TIMEOUT_S = 240


# ---------------------------------------------------------------------------
# Opt-in guard + helpers
# ---------------------------------------------------------------------------


def _require_entity_prov_optin() -> None:
    """Skip unless ``GFLOW_CLI_E2E_RUN_ENTITY_PROV=1`` (these spend credits)."""
    if os.environ.get(_RUN_ENTITY_PROV_ENV, "0").strip() != "1":
        pytest.skip(
            f"{_RUN_ENTITY_PROV_ENV} != 1 — entity-provenance e2e is opt-in "
            "because it drives a real browser image generation (zero credits; "
            "daily-capped). Set it to 1 to run the live "
            "verification gate for #402."
        )


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


def _open_db(env: dict[str, str]) -> sqlite3.Connection:
    db_path = Path(env["GFLOW_CLI_DB_PATH"])
    assert db_path.exists(), f"data layer never wrote to {db_path}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _operation_metadata(conn: sqlite3.Connection, mode: str) -> dict[str, object]:
    """Return the parsed ``metadata_json`` of the single operation for *mode*."""
    rows = conn.execute(
        "SELECT status, metadata_json FROM operations WHERE mode = ?", (mode,)
    ).fetchall()
    assert len(rows) == 1, f"expected exactly 1 {mode} operation, got {[dict(r) for r in rows]}"
    raw = rows[0]["metadata_json"]
    assert raw, (
        f"{mode} operation carries NULL metadata_json — this is the #402 "
        "regression: the entity attach left no trace in the catalog"
    )
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _fresh_project_and_entity() -> tuple[str, str]:
    """Mint a real Flow project + CHARACTER entity over REST. Free, no credits.

    Returns ``(project_id, entity_id)``. Skips (rather than fails) when the
    profile isn't usable, matching the other e2e modules' posture.
    """
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.config import Settings

    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(f"set {_E2E_PROFILE_ENV} to a logged-in profile name")

    old_home = os.environ.pop("GFLOW_CLI_HOME", None)
    try:
        profile_dir = Settings(_env_file=None).profile_subdir(name)  # pyright: ignore[reportCallIssue]
    finally:
        if old_home is not None:
            os.environ["GFLOW_CLI_HOME"] = old_home

    if not profile_dir.exists():
        pytest.skip(f"profile dir not found: {profile_dir}")

    async def _mint() -> tuple[str, str]:
        async with FlowApiClient(profile_dir=profile_dir) as client:
            project = await client.create_project(title="e2e-entity-provenance")
            entity_id = await client.create_entity(project.project_id)
            assert entity_id, "createEntity returned no entity id"
            return project.project_id, entity_id

    return asyncio.run(_mint())


# ---------------------------------------------------------------------------
# t2i — the cheapest surface that accepts --reference-entity
# ---------------------------------------------------------------------------


@pytest.mark.e2e_image
@pytest.mark.e2e_data
def test_t2i_entity_attach_records_provenance(e2e_env: dict[str, str]) -> None:
    """Live t2i with an attached entity: 1 image generation (zero credits).

    The gate for #402: after a generation Flow actually accepted, the catalog
    must be able to answer "which character produced this?".
    """
    _require_entity_prov_optin()
    project_id, entity_id = _fresh_project_and_entity()
    profile = e2e_env["GFLOW_CLI_PROFILE"]

    result = _run_gflow(
        [
            "image",
            "t2i",
            _T2I_PROMPT,
            "--project",
            project_id,
            "--reference-entity",
            entity_id,
            "--reference-entity-name",
            _ENTITY_NAME,
            "--profile",
            profile,
        ],
        env=e2e_env,
        timeout=_IMAGE_TIMEOUT_S,
    )
    assert result.returncode == 0, (
        f"gflow image t2i --reference-entity exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    conn = _open_db(e2e_env)
    try:
        meta = _operation_metadata(conn, "t2i")
        assert meta.get("entity_ids") == [entity_id], (
            f"entity_ids missing or wrong: {meta!r} (expected [{entity_id!r}])"
        )
        assert meta.get("entity_names") == [_ENTITY_NAME], (
            f"entity_names missing or wrong: {meta!r}"
        )
        status = conn.execute("SELECT status FROM operations WHERE mode='t2i'").fetchone()
        assert status["status"] == "succeeded"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# i2i — the surface where #402 lost 333 generations
# ---------------------------------------------------------------------------


@pytest.mark.e2e_image
@pytest.mark.e2e_data
def test_i2i_entity_attach_records_provenance(e2e_env: dict[str, str]) -> None:
    """Live i2i with an attached entity: 2 image generations, zero credits (seed t2i + i2i).

    i2i is the surface the issue measured at 0/333 coverage, so it gets its own
    gate rather than riding on the t2i result.
    """
    _require_entity_prov_optin()
    project_id, entity_id = _fresh_project_and_entity()
    profile = e2e_env["GFLOW_CLI_PROFILE"]
    out_dir = Path(e2e_env["GFLOW_CLI_OUTPUT_DIR"])

    # 1. Seed frame — a plain t2i in the same project (no entity attached).
    seed = _run_gflow(
        ["image", "t2i", _T2I_PROMPT, "--project", project_id, "--profile", profile],
        env=e2e_env,
        timeout=_IMAGE_TIMEOUT_S,
    )
    assert seed.returncode == 0, (
        f"seed t2i exited {seed.returncode}\nSTDOUT:\n{seed.stdout}\nSTDERR:\n{seed.stderr}"
    )
    seeds = [
        p
        for p in sorted(out_dir.rglob("*"))
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    ]
    assert seeds, f"seed t2i produced no image in {out_dir}"

    # 2. i2i off that frame, WITH the entity attached.
    result = _run_gflow(
        [
            "image",
            "i2i",
            _I2I_PROMPT,
            "--ref",
            str(seeds[0]),
            "--project",
            project_id,
            "--reference-entity",
            entity_id,
            "--reference-entity-name",
            _ENTITY_NAME,
            "--profile",
            profile,
        ],
        env=e2e_env,
        timeout=_IMAGE_TIMEOUT_S,
    )
    assert result.returncode == 0, (
        f"gflow image i2i --reference-entity exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    conn = _open_db(e2e_env)
    try:
        meta = _operation_metadata(conn, "i2i")
        assert meta.get("entity_ids") == [entity_id], (
            f"entity_ids missing or wrong on i2i: {meta!r} (expected [{entity_id!r}])"
        )
        assert meta.get("entity_names") == [_ENTITY_NAME], (
            f"entity_names missing or wrong on i2i: {meta!r}"
        )
        # The media ref must STILL be recorded alongside the entity — the fix
        # must not have regressed the provenance that already worked.
        links = conn.execute(
            "SELECT role, position FROM operation_assets WHERE role='input'"
        ).fetchall()
        assert links, "i2i input media ref no longer linked in operation_assets"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Negative case — a rejected attach must still be attributable
# ---------------------------------------------------------------------------


@pytest.mark.e2e_image
@pytest.mark.e2e_data
def test_rejected_entity_attach_still_records_provenance(e2e_env: dict[str, str]) -> None:
    """An entity id that does not exist in the target project must fail the run
    AND leave a FAILED row carrying the requested ``entity_ids``.

    This is the case that made #402 expensive to diagnose: before the fix a
    silently-dropped reference and a successful attach were indistinguishable
    after the fact. Expected to spend 0 credits — the attach is refused before
    submit — but it is marked ``e2e_image`` because that is not guaranteed
    against a live blackbox.
    """
    _require_entity_prov_optin()
    project_id, _real_entity = _fresh_project_and_entity()
    profile = e2e_env["GFLOW_CLI_PROFILE"]

    # Well-formed (passes --reference-entity's charset validation) but absent
    # from the project, so the picker can never stage it.
    bogus_entity = "e2e-nonexistent-entity-0000"

    result = _run_gflow(
        [
            "image",
            "t2i",
            _T2I_PROMPT,
            "--project",
            project_id,
            "--reference-entity",
            bogus_entity,
            "--profile",
            profile,
        ],
        env=e2e_env,
        timeout=_REJECT_TIMEOUT_S,
    )
    assert result.returncode != 0, (
        "a non-existent entity must NOT report success — a text-only generation "
        f"was reported as an entity-referenced one.\nSTDOUT:\n{result.stdout}"
    )

    conn = _open_db(e2e_env)
    try:
        rows = conn.execute(
            "SELECT status, error_type, metadata_json FROM operations WHERE mode='t2i'"
        ).fetchall()
        assert rows, "the failed run recorded no operation row at all"
        failed = [r for r in rows if r["status"] == "failed"]
        assert failed, f"expected a FAILED t2i row, got {[dict(r) for r in rows]}"
        raw = failed[0]["metadata_json"]
        assert raw, (
            "FAILED row carries NULL metadata_json — a rejected attach is still "
            "indistinguishable from a run that never requested an entity"
        )
        meta = json.loads(raw)
        assert meta.get("entity_ids") == [bogus_entity], (
            f"FAILED row lost the requested entity id: {meta!r}"
        )
    finally:
        conn.close()
