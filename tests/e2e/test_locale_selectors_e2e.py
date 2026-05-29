"""Live e2e smoke for locale-invariant selectors (issue #24 Phase 4 / PR #93).

Currently skipped — exists as a runnable scaffold for the manual non-EN smoke
the PR body asks reviewers to run, and as the future home for an automated
backstop once Flow-credit-aware CI infra exists.

**Purpose**: prove ``NEW_PROJECT_SELECTORS`` (icon-first tier + 14-locale text
tier) actually clicks a real "+ New project" button on a non-English Chrome
profile. Memory ``pr-must-verify-on-affected-surface`` (PR #70 / issue #63
incident) shows that selector-only unit invariants ship green-tested but
broken — this file closes that loop.

**How to run manually**::

    GFLOW_CLI_LOCALE=de-DE GFLOW_CLI_E2E_RUN_LOCALE_SMOKE=1 \\
        .venv/Scripts/python.exe -m pytest -q \\
        tests/e2e/test_locale_selectors_e2e.py -s

Costs ~1 Flow image credit per run. Requires an authenticated Chrome-strategy
profile (per memory ``real-browser-auth-mandatory``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_LIVE_SMOKE_ENABLED = os.environ.get("GFLOW_CLI_E2E_RUN_LOCALE_SMOKE") == "1"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.e2e_image,
    pytest.mark.live,
    pytest.mark.skipif(
        not _LIVE_SMOKE_ENABLED,
        reason="manual: set GFLOW_CLI_E2E_RUN_LOCALE_SMOKE=1 (spends Flow credits)",
    ),
]


@pytest.mark.parametrize("locale", ["de-DE", "pt-BR"])
def test_image_t2i_succeeds_under_non_en_locale(locale: str, tmp_path: Path) -> None:
    """Run ``gflow image t2i`` under a non-EN locale and assert mp4-or-png exists.

    The selector cascade is exercised end-to-end: onboarding bypass, "+ New
    project" click (NEW_PROJECT_SELECTORS), prompt entry, submit
    (SUBMIT_BUTTON_SELECTORS). If any locale-text fallback is broken, the
    Playwright timeout will fail this test before any image is produced.
    """
    env = os.environ.copy()
    env["GFLOW_CLI_LOCALE"] = locale
    out_dir = tmp_path / f"locale_{locale}"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "image",
            "t2i",
            "a calm forest at dawn",
            "--model",
            "nano2",
            "--out",
            str(out_dir),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, (
        f"gflow image t2i failed for locale={locale}: stderr={result.stderr[-500:]}"
    )

    images = list(out_dir.rglob("*.png")) + list(out_dir.rglob("*.jpg"))
    assert images, f"No image produced for locale={locale}; out_dir={out_dir}"
    assert images[0].stat().st_size > 1024, (
        f"Image suspiciously small for locale={locale}: {images[0]} "
        f"({images[0].stat().st_size} bytes)"
    )
