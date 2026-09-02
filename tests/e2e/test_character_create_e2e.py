"""Live E2E for ``gflow character create`` / ``gflow character show`` (issue #145, Phase-2 Task 12).

These tests CODIFY the formal Definition-of-Done for the Character-creation
feature. The live happy path was first verified MANUALLY on 2026-06-02 against
the ``denon82`` profile — entity ``951d2ce4`` ("Marina Côrtes") was created
end-to-end (face image-gen bound to the entity, entity patched, read-back
confirmed in the existing project). The tests below make that manual proof
*repeatable* so the feature stays covered.

# Cost & opt-in gates

``character create`` drives real image generation, zero credits (it runs a real face image
generation, optionally a body one). The tests are therefore **default-OFF** so
CI and ``/gflow:check`` never burn credits. Two gates must BOTH be satisfied:

  - ``GFLOW_CLI_E2E_PROFILE``        master gate; Chrome-strategy profile name
                                     (provided by the ``e2e_env`` fixture).
  - ``GFLOW_CLI_E2E_RUN_CHARACTER``  set to ``1`` to actually run these tests.

Env-parameterized (sensible defaults so a bare opt-in works on ``denon82``):

  - ``GFLOW_CLI_E2E_CHARACTER_PROJECT``  Flow project id the character is created
                                          IN (must be an EXISTING project — the
                                          read-back asserts no new project is
                                          spun up). REQUIRED when opted-in.
  - ``GFLOW_CLI_E2E_CHARACTER_LOCALE``   BCP-47 locale; default ``pt``.
  - ``GFLOW_CLI_E2E_CHARACTER_FACE``     face prompt; sensible default below.
  - ``GFLOW_CLI_E2E_CHARACTER_PERSONALITY`` personality notes; default below.

# Spending

  - each ``character create`` = 1 image generation, zero credits (face only; no ``--body-prompt``).
  - the partial-saga test runs ``create`` TWICE with the SAME name; the second
    run must RESUME (idempotent) and NOT create a second entity — so it should
    not double-spend. A true mid-saga kill is a MANUAL exercise; this test
    asserts the resume path observably (one ``character``-mode op row, not two).

Per the project's verification-ledger discipline ([[verification-ledger-5-layer]]):
assert the *observable* binding proof (entity has workflow ids + a thumbnail
media id, read-back lands in the SAME project) rather than re-deriving the
in-code ``parentEntityId == entityId`` invariant.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

# File-level e2e + cost sub-marker (registered in pyproject.toml).
pytestmark = [pytest.mark.e2e, pytest.mark.e2e_character]

_RUN_CHARACTER_ENV = "GFLOW_CLI_E2E_RUN_CHARACTER"
_PROJECT_ENV = "GFLOW_CLI_E2E_CHARACTER_PROJECT"
_LOCALE_ENV = "GFLOW_CLI_E2E_CHARACTER_LOCALE"
_FACE_ENV = "GFLOW_CLI_E2E_CHARACTER_FACE"
_PERSONALITY_ENV = "GFLOW_CLI_E2E_CHARACTER_PERSONALITY"

_DEFAULT_LOCALE = "pt"
_DEFAULT_FACE_PROMPT = (
    "portrait of a calm woman with dark wavy hair, soft studio lighting, "
    "neutral background, photorealistic"
)
# Accented on purpose (é, à, ç, em-dash) so the UTF-8 round-trip test has real
# multi-byte content to verify byte-for-byte.
_DEFAULT_PERSONALITY = "calma, atenciosa — fala pausado; née à Paris, criança do Açores"

# Character create runs a real image generation then patches the entity over
# REST. Generous because of real Flow latency (image-gen + entity PATCH).
_CREATE_TIMEOUT_S = 300
_SHOW_TIMEOUT_S = 60


# ---------------------------------------------------------------------------
# Opt-in guard + env parameterization
# ---------------------------------------------------------------------------


def _require_character_optin() -> None:
    """Skip unless ``GFLOW_CLI_E2E_RUN_CHARACTER=1`` (these tests spend credits)."""
    if os.environ.get(_RUN_CHARACTER_ENV, "0").strip() != "1":
        pytest.skip(
            f"{_RUN_CHARACTER_ENV} != 1 — character-create e2e is opt-in because "
            "it drives a real browser image generation (zero credits; daily-capped). "
            "Set GFLOW_CLI_E2E_RUN_CHARACTER=1 (and "
            f"{_PROJECT_ENV}) to run it."
        )


def _require_project() -> str:
    """Resolve the EXISTING Flow project id the character is created in."""
    project = os.environ.get(_PROJECT_ENV, "").strip()
    if not project:
        pytest.skip(
            f"{_PROJECT_ENV} is unset — set it to an EXISTING Flow project id "
            "so the read-back can assert no new project was created."
        )
    return project


def _character_env(e2e_env: dict[str, str]) -> dict[str, str]:
    """Overlay character-locale + UTF-8 onto the isolated ``e2e_env``.

    ``e2e_env`` already pins PYTHONUTF8=1, an isolated GFLOW_CLI_DB_PATH, an
    isolated output dir, and the active profile. The DB is isolated so the
    idempotency assertion counts only THIS test's rows.
    """
    env = dict(e2e_env)
    env["PYTHONUTF8"] = "1"  # explicit: accented names must round-trip byte-for-byte
    # The autouse `_isolate_settings` fixture points GFLOW_CLI_HOME at a temp
    # dir that holds no profiles, and `e2e_env` inherits it — a child process
    # would resolve "no session for profile <name>" and exit 2 before touching
    # Flow. Drop it so the subprocess finds the REAL platformdirs home where
    # `gflow auth login` planted the session. The isolated GFLOW_CLI_DB_PATH is
    # deliberately kept: only the catalog is isolated, not the session.
    env.pop("GFLOW_CLI_HOME", None)
    return env


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


def _parse_json_stdout(result: subprocess.CompletedProcess[str], what: str) -> dict[str, object]:
    """Parse the LAST JSON object on stdout (CLI may print log noise first)."""
    text = result.stdout.strip()
    assert text, f"{what}: empty stdout\nSTDERR:\n{result.stderr}"
    # The --json contract emits exactly one JSON document on stdout.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the last non-empty line in case of a trailing newline-split.
        last = text.splitlines()[-1]
        return json.loads(last)


def _open_db(env: dict[str, str]) -> sqlite3.Connection:
    db_path = Path(env["GFLOW_CLI_DB_PATH"])
    assert db_path.exists(), f"data layer never wrote to {db_path}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Scenario #1 / #5 — the generation BINDS to the parent entity
# ---------------------------------------------------------------------------


def test_character_create_binds_parent_entity(e2e_env: dict[str, str]) -> None:
    """Live ``character create`` (1 image generation, zero credits): the face generation must
    bind to the created entity, and the read-back must land in the SAME
    existing project.

    Observable proof of ``parentEntityId == entityId`` (enforced in-code):
      - create --json returns a non-empty ``entity_id`` + non-empty
        ``workflow_ids`` (the gen produced workflow(s) bound to the entity).
      - ``character show --json`` read-back returns the SAME entity in the SAME
        project (``project_id`` matches the requested project — NOT a fresh one)
        AND carries a ``thumbnail_media_id`` (the gen's image bound to the entity).
    """
    _require_character_optin()
    project_id = _require_project()
    env = _character_env(e2e_env)
    profile = env["GFLOW_CLI_PROFILE"]
    locale = os.environ.get(_LOCALE_ENV, _DEFAULT_LOCALE)
    face = os.environ.get(_FACE_ENV, _DEFAULT_FACE_PROMPT)
    # Unique name so the entity is unambiguous on read-back.
    name = f"e2e-bind-{uuid.uuid4().hex[:8]}"

    result = _run_gflow(
        [
            "character",
            "create",
            "--project",
            project_id,
            "--name",
            name,
            "--face-prompt",
            face,
            "--locale",
            locale,
            "--profile",
            profile,
            "--json",
        ],
        env=env,
        timeout=_CREATE_TIMEOUT_S,
    )
    assert result.returncode == 0, (
        f"character create exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    payload = _parse_json_stdout(result, "character create")
    assert payload.get("status") == "ok", f"unexpected create payload: {payload}"
    created = payload["character"]
    assert isinstance(created, dict)
    entity_id = created["entity_id"]
    assert entity_id, f"create returned empty entity_id: {created}"
    assert created["project_id"] == project_id, (
        f"create bound to a DIFFERENT project: requested {project_id!r}, "
        f"got {created['project_id']!r}"
    )
    workflow_ids = created["workflow_ids"]
    assert isinstance(workflow_ids, list) and workflow_ids, (
        f"create returned no workflow_ids — generation did not bind to the entity: {created}"
    )

    # ---- read-back: same entity, same project, thumbnail bound ----
    show = _run_gflow(
        [
            "character",
            "show",
            "--project",
            project_id,
            "--id",
            str(entity_id),
            "--profile",
            profile,
            "--json",
        ],
        env=env,
        timeout=_SHOW_TIMEOUT_S,
    )
    assert show.returncode == 0, (
        f"character show exited {show.returncode}\nSTDOUT:\n{show.stdout}\nSTDERR:\n{show.stderr}"
    )
    show_payload = _parse_json_stdout(show, "character show")
    shown = show_payload["character"]
    assert isinstance(shown, dict)
    assert shown["entity_id"] == entity_id, (
        f"read-back returned a different entity: {shown['entity_id']!r} != {entity_id!r}"
    )
    assert shown["project_id"] == project_id, (
        f"read-back project drift: {shown['project_id']!r} != requested {project_id!r} "
        "(a new project would mean the entity was NOT created in-place)"
    )
    assert shown.get("workflow_ids"), f"read-back entity has no workflow_ids: {shown}"
    assert shown.get("thumbnail_media_id"), (
        "read-back entity has no thumbnail_media_id — the generated image did "
        f"not bind to the entity: {shown}"
    )


# ---------------------------------------------------------------------------
# Scenario #3 / #4 — partial-saga is RESUMABLE / idempotent re-run
# ---------------------------------------------------------------------------


def test_character_create_partial_saga_recoverable(e2e_env: dict[str, str]) -> None:
    """Re-running ``character create`` with the SAME name must RESUME, not
    double-spend.

    A true mid-saga kill (SIGKILL between image-gen and entity PATCH) is a
    MANUAL exercise — it cannot be reliably injected from a black-box subprocess
    test. This test asserts the *resume* half of the saga contract observably:
    a second create with the same name does NOT create a second entity / second
    face workflow set.

    Verification ledger:
      - both runs exit 0.
      - the isolated data layer ends with EXACTLY ONE ``character``-mode
        operation row (the recorder did not insert a second STARTED entity).
      - both runs report the SAME entity_id and the workflow_ids count did not
        double on the second run.
    """
    _require_character_optin()
    project_id = _require_project()
    env = _character_env(e2e_env)
    profile = env["GFLOW_CLI_PROFILE"]
    locale = os.environ.get(_LOCALE_ENV, _DEFAULT_LOCALE)
    face = os.environ.get(_FACE_ENV, _DEFAULT_FACE_PROMPT)
    # FIXED name shared by both runs so the second run resumes the first.
    name = f"e2e-saga-{uuid.uuid4().hex[:8]}"

    create_args = [
        "character",
        "create",
        "--project",
        project_id,
        "--name",
        name,
        "--face-prompt",
        face,
        "--locale",
        locale,
        "--profile",
        profile,
        "--json",
    ]

    first = _run_gflow(create_args, env=env, timeout=_CREATE_TIMEOUT_S)
    assert first.returncode == 0, (
        f"first character create exited {first.returncode}\n"
        f"STDOUT:\n{first.stdout}\nSTDERR:\n{first.stderr}"
    )
    first_payload = _parse_json_stdout(first, "first character create")
    first_char = first_payload["character"]
    assert isinstance(first_char, dict)
    first_entity = first_char["entity_id"]
    first_wf = first_char["workflow_ids"]
    assert isinstance(first_wf, list)

    second = _run_gflow(create_args, env=env, timeout=_CREATE_TIMEOUT_S)
    assert second.returncode == 0, (
        f"second (resume) character create exited {second.returncode}\n"
        f"STDOUT:\n{second.stdout}\nSTDERR:\n{second.stderr}"
    )
    second_payload = _parse_json_stdout(second, "second character create")
    second_char = second_payload["character"]
    assert isinstance(second_char, dict)

    # Same entity — the resume did NOT mint a new one.
    assert second_char["entity_id"] == first_entity, (
        "re-run with the same name created a DIFFERENT entity (double-spend): "
        f"{second_char['entity_id']!r} != {first_entity!r}"
    )
    # Workflow set did not double.
    second_wf = second_char["workflow_ids"]
    assert isinstance(second_wf, list)
    assert len(second_wf) <= len(first_wf), (
        "re-run grew the workflow set (re-generated faces — double-spend): "
        f"first={first_wf}, second={second_wf}"
    )

    # ---- data-layer ledger: exactly one character-mode entity persisted ----
    conn = _open_db(env)
    try:
        op_rows = conn.execute(
            "SELECT flow_operation_id, status FROM operations WHERE mode='character'"
        ).fetchall()
        entity_ids = {r["flow_operation_id"] for r in op_rows}
        assert len(entity_ids) == 1, (
            "expected exactly ONE character entity persisted across two same-name "
            f"runs, got {sorted(entity_ids)} (rows: {[dict(r) for r in op_rows]})"
        )
        assert first_entity in entity_ids, (
            f"persisted entity {sorted(entity_ids)} != reported {first_entity!r}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Scenario #18 — accented name + personality round-trip byte-for-byte (UTF-8)
# ---------------------------------------------------------------------------


def test_character_personality_utf8(e2e_env: dict[str, str]) -> None:
    """Accented ``--name`` and ``--personality`` survive the full create →
    read-back round-trip byte-for-byte.

    PYTHONUTF8=1 is pinned by ``e2e_env`` / ``_character_env`` so the subprocess
    encodes argv + stdout as UTF-8 on every platform (Windows defaults to a
    legacy code page otherwise — this is the exact regression #18 guards).
    """
    _require_character_optin()
    project_id = _require_project()
    env = _character_env(e2e_env)
    profile = env["GFLOW_CLI_PROFILE"]
    locale = os.environ.get(_LOCALE_ENV, _DEFAULT_LOCALE)
    face = os.environ.get(_FACE_ENV, _DEFAULT_FACE_PROMPT)
    personality = os.environ.get(_PERSONALITY_ENV, _DEFAULT_PERSONALITY)

    # Accented display name with a unique suffix for an unambiguous read-back.
    accented_name = f"Renée Désirée Côrtes {uuid.uuid4().hex[:6]}"

    result = _run_gflow(
        [
            "character",
            "create",
            "--project",
            project_id,
            "--name",
            accented_name,
            "--face-prompt",
            face,
            "--personality",
            personality,
            "--locale",
            locale,
            "--profile",
            profile,
            "--json",
        ],
        env=env,
        timeout=_CREATE_TIMEOUT_S,
    )
    assert result.returncode == 0, (
        f"character create (utf-8) exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    created = _parse_json_stdout(result, "character create (utf-8)")["character"]
    assert isinstance(created, dict)
    # The name round-trips byte-for-byte on the create payload.
    assert created["name"] == accented_name, (
        f"accented name corrupted on create: {created['name']!r} != {accented_name!r}"
    )
    entity_id = created["entity_id"]
    assert entity_id

    # ---- read-back: accented chars survive byte-for-byte ----
    show = _run_gflow(
        [
            "character",
            "show",
            "--project",
            project_id,
            "--id",
            str(entity_id),
            "--profile",
            profile,
            "--json",
        ],
        env=env,
        timeout=_SHOW_TIMEOUT_S,
    )
    assert show.returncode == 0, (
        f"character show (utf-8) exited {show.returncode}\n"
        f"STDOUT:\n{show.stdout}\nSTDERR:\n{show.stderr}"
    )
    shown = _parse_json_stdout(show, "character show (utf-8)")["character"]
    assert isinstance(shown, dict)
    assert shown["display_name"] == accented_name, (
        f"accented name corrupted on read-back: {shown['display_name']!r} != {accented_name!r}"
    )
    # Personality (when surfaced by the read-back) must also be byte-for-byte.
    shown_personality = shown.get("personality")
    if shown_personality:
        assert shown_personality == personality, (
            f"accented personality corrupted on read-back: {shown_personality!r} != {personality!r}"
        )


# ---------------------------------------------------------------------------
# Scenario #6 — `--format-prompt` clicks Flow's in-editor Format button (#383)
# ---------------------------------------------------------------------------


def test_character_create_format_prompt_clicks_format_button(e2e_env: dict[str, str]) -> None:
    """Live ``character create --format-prompt`` (1 image generation, zero credits): Flow's
    in-editor **Format** button must actually be found, enabled, and clicked.

    `format_character_prompt` is best-effort by design — a missing button logs
    a warning and submits the prompt as typed. That safety net also means a
    selector that silently stops matching would degrade to a no-op flag with a
    green exit code, so the exit code alone proves nothing. The observable
    proof is the ``ui_automation.prompt_formatted`` event, which is emitted
    only after a visible AND enabled button was clicked; the two failure
    telemetry events are asserted absent so a disabled-button skip
    (Flow ships it disabled on an empty box) can't pass as success.

    What this does NOT assert: the *content* of Flow's rewrite. The reshaped
    text lives in Flow's editor, is authored server-side, and is not returned
    to the CLI — the recorded prompt is the one gflow typed. Verifying the
    rewrite itself stays a human read of the generated character.
    """
    _require_character_optin()
    project_id = _require_project()
    env = _character_env(e2e_env)
    env["GFLOW_CLI_LOG_FORMAT"] = "json"  # make the transport events assertable
    profile = env["GFLOW_CLI_PROFILE"]
    locale = os.environ.get(_LOCALE_ENV, _DEFAULT_LOCALE)
    face = os.environ.get(_FACE_ENV, _DEFAULT_FACE_PROMPT)
    name = f"e2e-fmt-{uuid.uuid4().hex[:8]}"

    result = _run_gflow(
        [
            "character",
            "create",
            "--project",
            project_id,
            "--name",
            name,
            "--face-prompt",
            face,
            "--locale",
            locale,
            "--profile",
            profile,
            "--format-prompt",
            "--json",
        ],
        env=env,
        timeout=_CREATE_TIMEOUT_S,
    )
    assert result.returncode == 0, (
        f"character create --format-prompt exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    payload = _parse_json_stdout(result, "character create --format-prompt")
    assert payload.get("status") == "ok", f"unexpected create payload: {payload}"
    created = payload["character"]
    assert isinstance(created, dict)
    assert created["entity_id"], f"create returned empty entity_id: {created}"

    events = [
        str(json.loads(line).get("event", ""))
        for line in result.stderr.splitlines()
        if line.strip().startswith("{")
    ]
    assert "ui_automation.prompt_formatted" in events, (
        "--format-prompt did not click Flow's Format button — the selector "
        "cascade (anchored on the `personal_recommendations` ligature) has "
        f"likely drifted. Observed events: {sorted(set(events))}"
    )
    for miss in ("ui_automation.format_button_not_found", "ui_automation.format_button_disabled"):
        assert miss not in events, f"format button was skipped ({miss}), not clicked"
