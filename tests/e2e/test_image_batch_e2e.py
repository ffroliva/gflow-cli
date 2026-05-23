"""Live e2e for `gflow image batch`. Spends Flow credits. Skipped by
default; opt in by setting GFLOW_CLI_E2E_PROFILE (the canonical e2e gate).

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md §8.3.

Env vars (all GFLOW_CLI_E2E_BATCH_*):
  - GFLOW_CLI_E2E_PROFILE             master gate; Chrome-strategy profile name
  - GFLOW_CLI_E2E_BATCH_MANIFEST      default: test_assets/sample_batch.tsv
  - GFLOW_CLI_E2E_BATCH_JITTER        "0" or "1"; default "1". When "0",
                                       passes jitter_range=(0,0) via DI.

Output: pytest's tmp_path (auto-cleaned). No hand-rolled timestamp dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import structlog
from structlog.testing import LogCapture

from gflow_cli.image_batch import (
    JITTER_MAX_SECONDS,
    JITTER_MIN_SECONDS,
    parse_manifest_file,
    run_manifest_image_batch,
)

pytestmark = pytest.mark.e2e

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"
_E2E_MANIFEST_ENV = "GFLOW_CLI_E2E_BATCH_MANIFEST"
_E2E_JITTER_ENV = "GFLOW_CLI_E2E_BATCH_JITTER"

# Aspect tolerance — Flow may return H.264-aligned dimensions (e.g., 1920x1088
# for 16:9 = 0.74%); ±2% is generous enough to absorb that without masking real
# regressions.
_ASPECT_TOLERANCE = 0.02


def _resolve_jitter_range() -> tuple[float, float]:
    enabled = os.environ.get(_E2E_JITTER_ENV, "1").strip() == "1"
    if not enabled:
        return (0.0, 0.0)
    return (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)


def _resolve_manifest_path() -> Path:
    raw = os.environ.get(_E2E_MANIFEST_ENV, "test_assets/sample_batch.tsv").strip()
    path = Path(raw)
    # Defensive: refuse the malformed fixture so a misconfigured env never
    # burns Flow credits on `sample_batch_invalid.tsv`.
    assert "_invalid" not in path.stem, f"live e2e refuses malformed-row fixture: {path}"
    return path


@pytest.fixture
def log_capture():
    """Capture structlog events; reset config on teardown so test ordering
    cannot bleed events between tests."""
    capture = LogCapture()
    structlog.configure(processors=[capture])
    try:
        yield capture
    finally:
        structlog.reset_defaults()


def _image_kind(path: Path) -> str | None:
    with path.open("rb") as f:
        head = f.read(12)
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    return None


def _aspect_within(
    ratio_str: str,
    actual: tuple[int, int],
    tol: float = _ASPECT_TOLERANCE,
) -> bool:
    a, b = (int(x) for x in ratio_str.split(":"))
    expected = a / b
    observed = actual[0] / actual[1]
    return abs(expected - observed) / expected <= tol


@pytest.mark.asyncio
async def test_image_batch_e2e(
    e2e_profile_dir: Path,  # pytest fixture from tests/e2e/conftest.py
    tmp_path: Path,
    log_capture: LogCapture,
) -> None:
    """Live image-batch e2e. Spends Flow credits when GFLOW_CLI_E2E_PROFILE is set."""
    manifest_path = _resolve_manifest_path()
    assert manifest_path.is_file(), f"manifest not found: {manifest_path}"

    prompts = parse_manifest_file(manifest_path)
    jitter_range = _resolve_jitter_range()

    out = tmp_path / "out"
    out.mkdir()

    # Wrap the credit-spending call in try/except so a failure captures the
    # last few observability events into the evidence file — useful when
    # diagnosing matrix-run failures post-mortem (spec §11 risk register).
    try:
        outcomes = await run_manifest_image_batch(
            profile_dir=e2e_profile_dir,
            headless=False,
            transport=None,
            prompts=prompts,
            output_dir=out,
            continue_on_error=False,
            project_title="gflow-cli e2e",
            jitter_range=jitter_range,
        )
    except Exception:
        (tmp_path / "last_events.json").write_text(
            json.dumps(log_capture.entries[-10:], default=str, indent=2),
            encoding="utf-8",
        )
        raise

    # 1. All rows succeeded
    assert all(o.status == "ok" for o in outcomes), [o.status for o in outcomes]

    # 2. File cardinality: sum of row counts. The transport writes count-setter
    # diagnostic screenshots into ``out/_diagnostics/`` per commit c5c8d4a;
    # exclude that subdir so we count only real Flow outputs.
    expected_files = sum(p.count for p in prompts)
    image_files = [
        f
        for f in sorted(out.rglob("*"))
        if f.is_file()
        and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        and "_diagnostics" not in f.parts
    ]
    assert len(image_files) == expected_files, (
        f"expected {expected_files} got {len(image_files)}: {[f.name for f in image_files]}"
    )

    # 3. Magic bytes — PNG / JPEG / WebP only. Fail loud on anything else.
    for f in image_files:
        kind = _image_kind(f)
        assert kind is not None, f"unsupported magic bytes in {f}: {f.read_bytes()[:12]!r}"

    # 4. Pillow aspect-ratio check, ±2%.
    from PIL import Image

    for outcome in outcomes:
        # Locate this outcome's files. Tolerate either `file_paths` (list) or
        # `saved_paths` / `file_path` (single) on BatchOutcome.
        paths: list[Path] = []
        for attr in ("file_paths", "saved_paths", "file_path"):
            if hasattr(outcome, attr):
                value = getattr(outcome, attr)
                if isinstance(value, list):
                    paths = [Path(p) for p in value]
                elif value is not None:
                    paths = [Path(value)]
                if paths:
                    break
        for f in paths:
            with Image.open(f) as im:
                assert _aspect_within(outcome.prompt.aspect_ratio, im.size), (
                    f"aspect mismatch on {f.name}: "
                    f"expected {outcome.prompt.aspect_ratio}, got {im.size}"
                )

    # 5. Listener event count — Playwright's response handler fires for every
    # matching HTTP response from Flow (initial submit, progress polls, final
    # result), so the floor is "at least one response per generated image".
    # We assert a lower bound, not equality, because count>1 rows and
    # same-project multiplexing produce multiple listener hits per row.
    seen = [e for e in log_capture.entries if e["event"] == "ui_automation.batch_response_seen"]
    expected_min = sum(p.count for p in prompts)
    assert len(seen) >= expected_min, (
        f"expected >= {expected_min} batch_response_seen, got {len(seen)}"
    )

    # 6. New application event count — one image_batch.row_completed per image.
    completed = [e for e in log_capture.entries if e["event"] == "image_batch.row_completed"]
    assert len(completed) == sum(p.count for p in prompts)

    # 7. submission_attempt event present per row, with project_id.
    attempt_events = [
        e for e in log_capture.entries if e["event"] == "image_batch.submission_attempt"
    ]
    assert len(attempt_events) == len(prompts), "missing submission_attempt events"

    # 8. Shared-project invariant (the v3-3 bug fix verification).
    # Today BatchOutcome does not carry project_id, so we read it from the
    # submission_attempt events. When BatchOutcome gains a project_id field
    # in a future change, prefer reading from outcomes directly.
    project_ids = {e["project_id"] for e in attempt_events}
    assert len(project_ids) == 1, (
        f"--same-project always-on: expected 1 shared project_id, got {project_ids}"
    )
