"""Live E2E for `gflow image i2i --ref <UUID>` reference binding (#393).

The #393 field report was that a UUID `--ref` silently generated WITHOUT the
reference. Live investigation on 2026-07-27 against real Flow established the
contract these tests pin:

1. **Never silent.** A UUID that cannot be bound aborts the generation with a
   non-zero exit — it must never produce an image that quietly lacks the
   reference, because that failure mode is invisible in the output and
   corrupts downstream pipelines (the reporter's set plate).
2. **Rescued when we own the bytes.** Flow's picker search does not index
   UUIDs, and its media library is per-project, so a ref pointing at an asset
   in another project is unreachable in the picker. When the local catalog has
   that asset's file, the transport uploads it instead of failing the run.

Scenario 1 costs no credits (it aborts before submission). Scenario 2 costs
2 image generations, zero credits (a seed image plus the referencing generation).

Opt in with::

    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e_image -v \\
        tests/e2e/test_image_uuid_ref_e2e.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.e2e

# Well-formed UUID that is not (and will not be) a real Flow media id, so the
# picker lookup is guaranteed to miss and the catalog is guaranteed to hold no
# fallback file for it.
_UNRESOLVABLE_UUID = "11111111-2222-3333-4444-555555555555"

_UPLOAD_FALLBACK_EVENT = "ui_automation_video.image_ref_upload_fallback"
_PICKER_MISS_EVENT = "ui_automation_video.existing_asset_not_found"
_SELECTED_EXISTING_EVENT = "ui_automation_video.image_ref_selected_existing"


def _run_gflow(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run `gflow <args>` with JSON structlog on stderr so events are assertable.

    ``GFLOW_CLI_HOME`` is dropped so the child resolves the REAL platformdirs
    home where `gflow auth login` planted the live session (the autouse
    ``_isolate_settings`` fixture points it at a temp dir, which has no
    profiles). ``e2e_env``'s isolated ``GFLOW_CLI_DB_PATH`` is kept: the
    catalog these tests read is the one they populate themselves.
    """
    child_env = {k: v for k, v in env.items() if k != "GFLOW_CLI_HOME"}
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**child_env, "GFLOW_CLI_LOG_FORMAT": "json"},
    )


def _events(stderr: str) -> list[str]:
    names: list[str] = []
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            names.append(str(json.loads(stripped).get("event", "")))
        except json.JSONDecodeError:
            continue
    return names


def _json_payload(stdout: str) -> dict[str, Any]:
    """Parse the `--json` document emitted last on stdout.

    ``json_output.emit`` pretty-prints with ``indent=2``, so the payload spans
    many lines and only its opening brace sits at column 0 — a per-line scan
    would pick up a nested fragment.
    """
    lines = stdout.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("{")]
    if not starts:
        msg = f"no JSON object on stdout; got: {stdout[-400:]!r}"
        raise AssertionError(msg)
    return dict(json.loads("\n".join(lines[starts[-1] :])))


@pytest.mark.e2e_image
def test_e2e_unresolvable_uuid_ref_fails_loud(e2e_env: dict[str, str], tmp_path: Path) -> None:
    """A UUID ref that cannot be bound must abort, not generate without it (#393).

    This is the anti-silence guard: the reporter's pipeline lost a set plate
    because a reference it believed was attached never was. Costs no credits —
    the run aborts at reference-attach time, before any generation is
    submitted.
    """
    # `e2e_env` already created this and pointed GFLOW_CLI_OUTPUT_DIR at it.
    out = tmp_path / "out"

    proc = _run_gflow(
        [
            "image",
            "i2i",
            "a plain wooden shelf",
            "--ref",
            _UNRESOLVABLE_UUID,
            "--model",
            "nano2",
            "--out",
            str(out),
        ],
        e2e_env,
    )

    assert proc.returncode != 0, (
        "an unbindable UUID ref generated anyway — this is the #393 silent-skip "
        f"failure mode. stdout tail: {proc.stdout[-400:]!r}"
    )
    # #529: a UUID with no catalog display name never reaches the picker
    # search (no UUID-term typing, no grid scrolling), so the abort is the
    # typed no-fallback error rather than a picker-miss event. A cataloged
    # name that misses still emits _PICKER_MISS_EVENT (see the cross-project
    # test's fallback path).
    assert "could not be selected in the picker" in proc.stderr, (
        f"expected the typed no-fallback abort; stderr tail: {proc.stderr[-600:]!r}"
    )
    # `debug_picker_*.png` diagnostics are expected on a miss; only GENERATED
    # media must be absent.
    generated = [
        p for p in (*out.rglob("*.jpg"), *out.rglob("*.png")) if not p.name.startswith("debug_")
    ]
    assert not generated, (
        f"an image was written despite the reference never being attached: {generated}"
    )


@pytest.mark.e2e_image
def test_e2e_same_project_uuid_ref_selected_in_picker(
    e2e_env: dict[str, str], tmp_path: Path
) -> None:
    """#529 happy path: a same-project UUID ref is selected in the picker.

    The catalog resolves the UUID to Flow's ``displayName``, the picker is
    searched by that name only, and the exact UUID tile is attached — no
    duplicate upload, no grid scrolling, no UUID-fragment searches. The
    ``image_ref_selected_existing`` event (``resolved_by=display_name``) is the
    contract; the upload fallback firing instead means the picker path
    regressed. Runs 2 image generations (zero credits): one seed image, one referencing
    generation.
    """
    # `e2e_env` already created this and pointed GFLOW_CLI_OUTPUT_DIR at it.
    out = tmp_path / "out"

    seed = _run_gflow(
        [
            "image",
            "t2i",
            "a ceramic teapot with a cobalt glaze on a plain shelf",
            "--model",
            "nano2",
            "--out",
            str(out),
            "--json",
        ],
        e2e_env,
    )
    assert seed.returncode == 0, f"seed generation failed; stderr tail: {seed.stderr[-400:]!r}"
    seed_payload = _json_payload(seed.stdout)
    images = seed_payload.get("images") or []
    assert images, f"seed run returned no images: {seed_payload}"
    seed_media_id = str(images[0]["media_name"])
    project_id = str(seed_payload.get("project_id") or "")
    assert project_id, f"seed payload carried no project_id: {seed_payload}"

    # SAME project: the seed's tile is reachable in this picker's library view,
    # so the display-name search must find it and no upload may happen.
    ref_run = _run_gflow(
        [
            "image",
            "i2i",
            "the same teapot, warmer light",
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
        f"same-project UUID ref failed; stderr tail: {ref_run.stderr[-600:]!r}"
    )
    events = _events(ref_run.stderr)
    assert _SELECTED_EXISTING_EVENT in events, (
        f"expected the picker to select the existing asset ({_SELECTED_EXISTING_EVENT}); "
        f"observed events: {sorted(set(events))}"
    )
    assert _UPLOAD_FALLBACK_EVENT not in events, (
        "a same-project ref must not duplicate-upload — the picker path regressed"
    )
    ref_payload = _json_payload(ref_run.stdout)
    assert ref_payload.get("ref_count") == 1, (
        f"the reference was not counted on the generation: {ref_payload}"
    )


@pytest.mark.e2e_image
def test_e2e_cross_project_uuid_ref_falls_back_to_upload(
    e2e_env: dict[str, str], tmp_path: Path
) -> None:
    """A cataloged ref whose tile the picker cannot reach is uploaded, not failed.

    Flow's media picker is scoped to the active project, so a ref generated in
    project A is unreachable while generating in a fresh project B. Before the
    #393 fix the CLI handed the transport a bare UUID with no fallback and the
    whole run died (verified live: exit 9). With the catalog's recorded file
    attached to the ref, the transport uploads those exact bytes instead.

    Runs 2 image generations (zero credits): one seed image, one referencing generation.
    """
    # `e2e_env` already created this and pointed GFLOW_CLI_OUTPUT_DIR at it.
    out = tmp_path / "out"

    seed = _run_gflow(
        [
            "image",
            "t2i",
            "a wooden spool of crimson ribbon on a plain shelf",
            "--model",
            "nano2",
            "--out",
            str(out),
            "--json",
        ],
        e2e_env,
    )
    assert seed.returncode == 0, f"seed generation failed; stderr tail: {seed.stderr[-400:]!r}"
    seed_payload = _json_payload(seed.stdout)
    images = seed_payload.get("images") or []
    assert images, f"seed run returned no images: {seed_payload}"
    # `media_name` IS the Flow media UUID — the same value `--ref` accepts.
    seed_media_id = str(images[0]["media_name"])

    # No --project: i2i creates a FRESH scratch project, so the seed asset lives
    # in a different project and is absent from this picker's library view.
    ref_run = _run_gflow(
        [
            "image",
            "i2i",
            "the same ribbon spool, softer light",
            "--ref",
            seed_media_id,
            "--model",
            "nano2",
            "--out",
            str(out),
            "--json",
        ],
        e2e_env,
    )

    assert ref_run.returncode == 0, (
        f"cross-project UUID ref failed instead of falling back to upload; "
        f"stderr tail: {ref_run.stderr[-600:]!r}"
    )
    events = _events(ref_run.stderr)
    assert _UPLOAD_FALLBACK_EVENT in events, (
        "expected the catalog's local file to rescue an unreachable picker tile "
        f"({_UPLOAD_FALLBACK_EVENT}); observed events: {sorted(set(events))}"
    )
    ref_payload = _json_payload(ref_run.stdout)
    assert ref_payload.get("ref_count") == 1, (
        f"the reference was not counted on the generation: {ref_payload}"
    )
