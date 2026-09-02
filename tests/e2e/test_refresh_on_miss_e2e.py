"""Live E2E: a stale catalog name self-heals via refresh-on-miss (#546).

The #546 contract — cached name = optimization; listing = truth; UUID =
identity — proven live against real Flow:

1. Seed a t2i image; the recorder persists Flow's async caption as the
   catalog ``display_name`` (bounded retry: the caption is computed
   server-side and a fresh response sometimes lacks it — same pattern as the
   r2v uuid-name e2e).
2. CORRUPT the catalog name (simulating a user rename in the Flow UI): the
   recorded caption is overwritten with a deliberately wrong name, so the
   picker's name search is guaranteed to miss.
3. Generate ``i2i --ref <uuid> --project <pid>``: the picker search misses on
   the stale name, the transport consults the resolver (one free ~0.5s
   ``projectInitialData`` fetch), retries once with the fresh name, and
   attaches the existing tile — no duplicate upload. The catalog is healed
   with ``sync.source == "refresh"`` provenance.

Costs 2 image generations (zero credits) (seed + referencing generation).

Opt in with::

    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e_image -v \\
        tests/e2e/test_refresh_on_miss_e2e.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

from .test_image_uuid_ref_e2e import _events, _json_payload, _run_gflow

if TYPE_CHECKING:
    from gflow_cli.data.models import AssetLookup

pytestmark = pytest.mark.e2e

_STALE_NAME = "Deliberately stale wrong name"

_SELECTED_EXISTING_EVENT = "ui_automation_video.image_ref_selected_existing"
_UPLOAD_FALLBACK_EVENT = "ui_automation_video.image_ref_upload_fallback"
_RESOLVER_FAILED_EVENT = "ui_automation_video.name_resolver_failed"


def _catalog_asset(env: dict[str, str], media_id: str) -> AssetLookup | None:
    profile_name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    with DataStore.open(Path(env["GFLOW_CLI_DB_PATH"])) as store:
        return DataRepository(store).get_asset_by_flow_media_id(profile_name, media_id)


@pytest.mark.e2e_image
def test_e2e_stale_catalog_name_self_heals_on_picker_miss(
    e2e_env: dict[str, str], tmp_path: Path
) -> None:
    """A corrupted catalog name still attaches in the picker and is healed."""
    out = tmp_path / "out"
    profile_name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()

    # 1. Seed. Flow computes the caption asynchronously server-side, so a
    # fresh generation sometimes records no display_name — bounded retry.
    real_caption: str | None = None
    seed_media_id = ""
    project_id = ""
    for attempt in range(2):
        seed = _run_gflow(
            [
                "image",
                "t2i",
                f"a tin lantern with a candle on a plain shelf, take {attempt}",
                "--model",
                "nano2",
                "--out",
                str(out),
                "--json",
            ],
            e2e_env,
        )
        if seed.returncode != 0:
            continue  # transient seed failure — retry within the bounded loop
        seed_payload = _json_payload(seed.stdout)
        images = seed_payload.get("images") or []
        assert images, f"seed run returned no images: {seed_payload}"
        seed_media_id = str(images[0]["media_name"])
        project_id = str(seed_payload.get("project_id") or "")
        assert project_id, f"seed payload carried no project_id: {seed_payload}"

        asset = _catalog_asset(e2e_env, seed_media_id)
        assert asset is not None, f"seed asset {seed_media_id} missing from the catalog"
        name = asset.metadata_json.get("display_name")
        if isinstance(name, str) and name:
            real_caption = name
            break

    if real_caption is None:
        pytest.skip(
            "no usable seed with a recorded displayName after 2 attempts — the "
            "caption is computed asynchronously server-side (or the seed "
            "generation failed transiently); nothing to corrupt."
        )
    assert real_caption != _STALE_NAME

    # 2. Corrupt the catalog name — the exact stale-cache condition
    # refresh-on-miss heals (deterministic; no Flow-UI rename automation).
    with DataStore.open(Path(e2e_env["GFLOW_CLI_DB_PATH"])) as store:
        assert DataRepository(store).set_asset_display_name(
            profile_name, seed_media_id, _STALE_NAME, source="e2e_corruption"
        ), "failed to corrupt the catalog name"

    # 3. i2i in the SAME project: the picker search misses on the stale name,
    # the resolver supplies the current one, and the retry attaches the tile.
    ref_run = _run_gflow(
        [
            "image",
            "i2i",
            "the same lantern, warmer light",
            "--ref",
            seed_media_id,
            "--project",
            project_id,
            "--model",
            "nano2",
            "--out",
            str(out),
            "--json",
        ],
        e2e_env,
    )

    assert ref_run.returncode == 0, (
        f"stale-name i2i failed instead of self-healing; stderr tail: {ref_run.stderr[-600:]!r}"
    )
    events = _events(ref_run.stderr)
    assert _SELECTED_EXISTING_EVENT in events, (
        f"expected the resolver-healed picker attach ({_SELECTED_EXISTING_EVENT}); "
        f"observed events: {sorted(set(events))}"
    )
    assert _UPLOAD_FALLBACK_EVENT not in events, (
        "a same-project ref with a healable name must not duplicate-upload"
    )
    assert _RESOLVER_FAILED_EVENT not in events, (
        "the listing fetch raised inside the resolver — the bridge regressed"
    )
    ref_payload = _json_payload(ref_run.stdout)
    assert ref_payload.get("ref_count") == 1, (
        f"the reference was not counted on the generation: {ref_payload}"
    )

    # 4. Write-through: the catalog now carries the REAL Flow caption again,
    # with refresh provenance.
    healed = _catalog_asset(e2e_env, seed_media_id)
    assert healed is not None
    assert healed.metadata_json.get("display_name") == real_caption, (
        f"catalog not healed: {healed.metadata_json.get('display_name')!r} != {real_caption!r}"
    )
    sync = healed.metadata_json.get("sync") or {}
    assert isinstance(sync, dict)
    assert sync.get("source") == "refresh", f"expected refresh provenance, got: {sync}"
