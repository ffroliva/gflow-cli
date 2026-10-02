r"""Are manifest ``ref`` / ``reference_entity`` fields applied by `gflow image batch`? (#913)

Cost: 4 images of daily quota (zero Veo credits). Drives the real CLI on a live profile.

Static reading (develop 773f6ef9): ``parse_batch_item_dict`` accepts ``ref`` and
``reference_entity`` as any string; ``resolve_batch_dependencies`` has no caller in src/;
neither request builder (``_to_request``, ``run_one_image_prompt``) reads either field.
This measures the behaviour instead of inferring it.

Arms (structured logs captured from stderr, ``GFLOW_CLI_LOG_FORMAT=json``):

  control   `gflow image i2i "<p>" --ref <local.jpg>`: a reference that IS applied.
            Proves ``migrated.references_attached`` fires when a reference is attached.
  batch     `gflow image batch m.json`, 3 rows:
              row 0  plain prompt
              row 1  "ref": "batch:0"      (the #317 intra-batch form)
              row 2  "ref": "<local.jpg>"   (a non-batch ref: is it dropped too?)

Pre-registered reading (written before the first run):

  control fires references_attached, batch exits 0, and batch fires it 0 times
      -> both forms are accepted and silently dropped; #913 confirmed, and wider
         than filed (non-batch refs dropped too).
  batch fires it once (row 2 only)
      -> local refs work, only batch:N is dropped; #913 as filed.
  batch exits non-zero naming the field
      -> refused, not dropped; #913 does not reproduce.
  control does NOT fire references_attached
      -> the signal is wrong for this host; the batch arm settles nothing.

Run 1 (2026-10-01, ci-probe, flow.google.com): control fired references_attached once
(then hit an unrelated 30 s API deadline, exit 9); batch exited 1 before any row:
`image batch` is not ported to the migrated composer, so it measured nothing.

Added arm, registered before running it:

  run       `gflow run --config c.json`, the same 3 rows (it parses rows with the same
            ``parse_batch_item_dict`` and is reachable on flow.google.com).
  run exits 0 with 3 submits and 0 references_attached
      -> both ref forms are silently dropped on the surface users can reach.
  run fires references_attached for row 2 only -> only batch:N is dropped.
  run refuses a row naming the field -> refused, not dropped.

    python scripts/dev/spike_batch_ref_dropped.py --profile ci-probe --ref-image <path.jpg>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import default_out_path, step  # noqa: E402

_SIGNAL = "migrated.references_attached"


def _run(args: list[str], out_dir: Path) -> dict[str, Any]:
    env = {**os.environ, "GFLOW_CLI_LOG_FORMAT": "json", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    events: list[dict[str, Any]] = []
    for line in proc.stderr.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    names = [e.get("event", "") for e in events]
    return {
        "args": args[:3],
        "exit": proc.returncode,
        "references_attached": names.count(_SIGNAL),
        "submits": names.count("migrated.submit_clicked"),
        "files": sorted(p.name for p in out_dir.rglob("*") if p.is_file()),
        "stderr_tail": proc.stderr[-400:] if proc.returncode else "",
    }


def main(profile: str, ref_image: Path, arms: list[str]) -> int:
    base = default_out_path("batch_ref_dropped", "")
    rows = [
        {"text": "a red apple on a wooden table", "aspect_ratio": "1:1"},
        {"text": "the same apple, now green", "aspect_ratio": "1:1", "ref": "batch:0"},
        {"text": "the same scene at night", "aspect_ratio": "1:1", "ref": str(ref_image)},
    ]
    findings: dict[str, Any] = {}

    if "control" in arms:
        d = base / "control"
        d.mkdir(parents=True)
        step("control", "image i2i --ref <local>")
        findings["control"] = _run(
            ["image", "i2i", "the same scene at night", "--ref", str(ref_image),
             "--profile", profile, "--aspect", "1:1", "--out", str(d)],
            d,
        )
        step("control", json.dumps(findings["control"]))

    if "batch" in arms:
        d = base / "batch"
        d.mkdir(parents=True)
        manifest = base / "manifest.json"
        manifest.write_text(json.dumps(rows), encoding="utf-8")
        step("batch", "image batch (row1 ref batch:0, row2 ref <local>)")
        findings["batch"] = _run(
            ["image", "batch", str(manifest), "--profile", profile, "--out", str(d)], d
        )
        step("batch", json.dumps(findings["batch"]))

    if "run" in arms:
        d = base / "run"
        d.mkdir(parents=True)
        config = base / "run_config.json"
        config.write_text(json.dumps({"prompts": rows}), encoding="utf-8")
        step("run", "run --config (row1 ref batch:0, row2 ref <local>)")
        findings["run"] = _run(
            ["run", "--config", str(config), "--profile", profile, "--output-dir", str(d)], d
        )
        step("run", json.dumps(findings["run"]))

    (base / "findings.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
    step("wrote", str(base / "findings.json"))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--ref-image", type=Path, required=True)
    ap.add_argument("--arms", nargs="+", default=["control", "batch", "run"],
                    choices=["control", "batch", "run"])
    a = ap.parse_args()
    raise SystemExit(main(a.profile, a.ref_image, a.arms))
