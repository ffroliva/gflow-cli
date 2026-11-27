"""E2E for #1550: `gflow doctor --json` reads Flow's live model catalog and compares it.

Doctor runs as a real subprocess with the direct read switched on. The check must have
read and compared the catalog: ``pass`` (no drift) or ``warn`` (drift, each difference named).
``info`` means nothing was read, which proves nothing, so it fails here.

Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` set. **Cost: zero.** One shell GET, one
``nzlxg`` balance read and one ``HTrJv`` catalog read; no browser, nothing generated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e._redacted_env import RedactedEnv

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


def test_doctor_compares_the_live_model_catalog(
    e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")
    monkeypatch.setenv("GFLOW_CLI_PROFILE", os.environ["GFLOW_CLI_E2E_PROFILE"].strip())

    proc = subprocess.run(  # noqa: S603 - our own CLI, fixed arguments
        [sys.executable, "-m", "gflow_cli", "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
        env=RedactedEnv(os.environ),
        check=False,
    )

    assert proc.returncode in (0, 33), proc.stderr[-2000:]  # 33: findings, possibly unrelated
    [check] = [c for c in json.loads(proc.stdout)["checks"] if c["check"] == "models.catalog_drift"]
    assert check["severity"] in ("pass", "warn"), check
    if check["severity"] == "pass":
        assert check["summary"].endswith("families, all mapped"), check
    else:
        assert "difference(s) from gflow's model mapping" in check["summary"], check
