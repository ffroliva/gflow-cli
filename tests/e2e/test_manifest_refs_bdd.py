"""E2E: `"ref": "batch:N"` on `gflow run --config`, against live Flow (#913).

Binds ``tests/features/manifest_refs_live.feature``. Selected by ``-m e2e_image``; see
``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e.** Offline tests pin our wiring with a fake page. Only a live run proves Flow
still lists the parent in the ``@`` picker under its reply caption, that the picker
option's thumbnail token still equals the grid tile's, and that the submit Flow accepts
carries the parent's media id (the route guard aborts it otherwise, and logs each
decision as ``migrated.image_submit_guarded``).

**The rows.** Rows 0 and 1 share a short prompt, so Flow often gives them the same
caption; row 2 references row 1, the newer, which the picker lists after the older. That
exercises the token binder past the first option (logged as
``migrated.existing_reference_option``). Row 3 references row 2 (a chain).

**Cost.** Four images of daily quota, zero Veo credits. Prints the option indices and the
reload count: the measurements PLAN Task 8 asks for.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image
from pytest_bdd import given, scenarios, then, when

scenarios("../features/manifest_refs_live.feature")

_ROWS = [
    {"text": "a single red apple", "aspect_ratio": "1:1"},
    {"text": "a single red apple", "aspect_ratio": "1:1"},
    {"text": "the same apple, now green", "aspect_ratio": "1:1", "ref": "batch:1"},
    {"text": "the green apple on a blue plate", "aspect_ratio": "1:1", "ref": "batch:2"},
]
_PARENT = {2: 1, 3: 2}


@given(
    "a run config whose referencing rows point at earlier rows, one sharing its caption",
    target_fixture="world",
)
def _config(tmp_path: Path, e2e_env: dict[str, str]) -> dict[str, Any]:
    cfg = tmp_path / "run.json"
    cfg.write_text(json.dumps({"prompts": _ROWS}), encoding="utf-8")
    return {"cfg": cfg, "env": e2e_env, "out": tmp_path / "run_out"}


@when("gflow run executes it on the live profile")
def _run(world: dict[str, Any]) -> None:
    env = {**world["env"], "GFLOW_CLI_LOG_FORMAT": "json"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "run",
            "--config",
            str(world["cfg"]),
            "--output-dir",
            str(world["out"]),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=1500,
    )
    events: list[dict[str, Any]] = []
    for line in proc.stderr.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    world.update(proc=proc, events=events)
    log = world["out"].parent / "run.log"
    log.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    names = [e.get("event") for e in events]
    options = [
        e.get("option_index")
        for e in events
        if e.get("event") == "migrated.existing_reference_option"
    ]
    print(f"\n[#913 log] {log}")
    print(f"[#913] option indices chosen: {options}")
    print(
        f"[#913] reloads: {names.count('migrated.existing_reference_reload')}, "
        f"not listed: {names.count('migrated.existing_reference_not_listed')}, "
        f"mention misses: {names.count('migrated.mention_miss')}"
    )


@then("every row succeeds and saves a real image")
def _all_ok(world: dict[str, Any]) -> None:
    proc = world["proc"]
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    saved = sorted(world["out"].glob("prompt_*"))
    assert [p.name.split("_")[1] for p in saved] == ["0", "1", "2", "3"], saved
    for path in saved:
        head = path.read_bytes()[:4]
        assert head[:3] == b"\xff\xd8\xff" or head == b"\x89PNG", (path, head)
        with Image.open(path) as img:
            assert min(img.size) >= 512, (path, img.size)


@then("each referencing row attached its parent in place, with no upload")
def _in_place(world: dict[str, Any]) -> None:
    events = world["events"]
    names = [e.get("event") for e in events]
    assert names.count("migrated.existing_references_attached") == len(_PARENT), names
    assert "migrated.references_attached" not in names  # the upload path never ran
    guarded = [
        e.get("outcome") for e in events if e.get("event") == "migrated.image_submit_guarded"
    ]
    assert guarded == ["passed"] * len(_PARENT), guarded


@then("the catalog records each referencing row as image-to-image with its parent as input")
def _lineage(world: dict[str, Any]) -> None:
    db = Path(world["env"]["GFLOW_CLI_DB_PATH"])
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT o.prompt, o.mode, a.flow_media_id, l.role FROM operation_assets l "
            "JOIN operations o ON o.id = l.operation_id JOIN assets a ON a.id = l.asset_id"
        ).fetchall()
    by_prompt: dict[str, dict[str, Any]] = {}
    for prompt, mode, media, role in rows:
        entry = by_prompt.setdefault(prompt, {"mode": mode, "output": [], "input": []})
        entry[role].append(media)
    texts = [r["text"] for r in _ROWS]
    for child, parent in _PARENT.items():
        assert by_prompt[texts[child]]["mode"] == "i2i", by_prompt
        parent_out = by_prompt[texts[parent]]["output"]
        assert (
            by_prompt[texts[child]]["input"] and by_prompt[texts[child]]["input"][0] in parent_out
        )


# --- local file, uploaded once (PR C) ---------------------------------------------


@given("a run config whose two rows name the same local image file", target_fixture="world")
def _file_config(tmp_path: Path, e2e_env: dict[str, str]) -> dict[str, Any]:
    refs = tmp_path / "refs"
    refs.mkdir()
    img = Image.new("RGB", (768, 768))
    for x in range(768):
        for y in range(0, 768, 8):
            img.putpixel((x, y), (x % 256, (y * 2) % 256, 160))
    img.save(refs / "palette.png")
    rows = [
        {
            "text": "a ceramic vase in these colours",
            "aspect_ratio": "1:1",
            "ref": "refs/palette.png",
        },
        {
            "text": "a woollen scarf in these colours",
            "aspect_ratio": "1:1",
            "ref": "refs/palette.png",
        },
    ]
    cfg = tmp_path / "run.json"
    cfg.write_text(json.dumps({"prompts": rows}), encoding="utf-8")
    return {"cfg": cfg, "env": e2e_env, "out": tmp_path / "run_out", "rows": rows}


@then("both rows succeed and save a real image")
def _both_ok(world: dict[str, Any]) -> None:
    proc = world["proc"]
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    saved = sorted(world["out"].glob("prompt_*"))
    assert [p.name.split("_")[1] for p in saved] == ["0", "1"], saved
    for path in saved:
        with Image.open(path) as img:
            assert min(img.size) >= 512, (path, img.size)


@then("the file was uploaded once and attached in place by both rows")
def _uploaded_once(world: dict[str, Any]) -> None:
    events = world["events"]
    names = [e.get("event") for e in events]
    uploads = [e for e in events if e.get("event") == "migrated.reference_uploaded"]
    assert len(uploads) == 1, names
    assert names.count("migrated.existing_references_attached") == 2, names
    assert "migrated.references_attached" not in names  # never the per-row upload path
    guarded = [
        e.get("outcome") for e in events if e.get("event") == "migrated.image_submit_guarded"
    ]
    assert guarded == ["passed", "passed"], guarded
    db = Path(world["env"]["GFLOW_CLI_DB_PATH"])
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT mode, metadata_json FROM operations").fetchall()
    uploaded_id = uploads[0]["media_id"]
    assert [mode for mode, _ in rows] == ["i2i", "i2i"], rows
    for _mode, meta in rows:
        assert json.loads(meta or "{}").get("reference_media_ids") == [uploaded_id], meta
