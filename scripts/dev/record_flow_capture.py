"""Record gflow's REAL character-create flow as a browser-only video.

Runs the genuine ``services.character_create.character_create`` saga through a
``RecordingFlowApiClient`` (Playwright ``record_video`` on the client's
persistent context), then transcodes the ``.webm`` to ``.mp4``. Character/image
generation is FREE (only video generation spends credits), so a real recorded
run costs nothing.

Isolation: the data store (``GFLOW_CLI_DB_PATH``) and the image output dir
(``GFLOW_CLI_OUTPUT_DIR``) are pointed at throwaway temp dirs so the real
catalog and gallery are never touched; concurrency is forced to 1 so exactly one
``.webm`` (the slot-0 editor page) is produced.

Usage (FREE — image gen only):
    python scripts/dev/record_flow_capture.py --profile promo-denon82 \
        --out scripts/dev/_spike_out/flow-create.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _recording_client import RecordingFlowApiClient  # noqa: E402
from _spike_common import resolve_profile_dir, step  # noqa: E402

from gflow_cli.api.character import CharacterImageRequest  # noqa: E402
from gflow_cli.config import Settings  # noqa: E402
from gflow_cli.data.recorder import OperationRecorder  # noqa: E402
from gflow_cli.services.character_create import character_create  # noqa: E402

_DEFAULT_FACE = "a woman with short dark hair, round glasses, navy sweater, soft studio portrait"

# Positive structlog events proving the IMAGE creation path ran (character_create
# only ever generates images — it never calls a video endpoint). Their presence
# is the "image-not-video" proof the absence of refs=1 alone cannot give.
_COMPLETED_EVENT = "character_create.completed"
_FAILED_EVENT = "character_create.failed"
_FACE_EVENTS = {
    "character_create.face_done",
    "character_create.face_skipped_already_recorded",
}


def _transcode(src: Path, dst: Path) -> None:
    """Transcode webm -> mp4 via ffmpeg; keep the raw .webm if ffmpeg is absent."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        fallback = dst.with_suffix(".webm")
        shutil.copyfile(src, fallback)
        step("warn", f"ffmpeg not found — kept raw webm at {fallback}", prefix="rec")
        return
    subprocess.run(
        [ffmpeg, "-y", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)],
        check=True,
        capture_output=True,
    )


def _verify_image_path(event_names: set[str]) -> str | None:
    """Return an error string if the captured events don't prove a free image run."""
    if _FAILED_EVENT in event_names:
        return "character_create emitted a failure event"
    if _COMPLETED_EVENT not in event_names:
        return "character_create did not complete"
    if not (_FACE_EVENTS & event_names):
        return "no face image was generated (image path did not run)"
    return None


async def _run(
    *, profile: str, profile_dir: Path, face_prompt: str, locale: str, out_path: Path
) -> int:
    from structlog.testing import capture_logs

    rec_dir = out_path.parent / "_rec_tmp"
    rec_dir.mkdir(parents=True, exist_ok=True)
    # Isolate the data store AND the image output dir so the real catalog/gallery
    # are never touched; force concurrency=1 so exactly one .webm is produced.
    tmp_root = Path(tempfile.mkdtemp(prefix="rec-iso-"))
    os.environ["GFLOW_CLI_DB_PATH"] = str(tmp_root / "catalog.db")
    os.environ["GFLOW_CLI_OUTPUT_DIR"] = str(tmp_root / "out")
    os.environ["GFLOW_CLI_CONCURRENCY"] = "1"
    settings = Settings()  # re-reads the env vars set above

    face = CharacterImageRequest(prompt=face_prompt)
    recorder = OperationRecorder.open(settings)
    try:
        async with RecordingFlowApiClient(
            profile_dir=profile_dir,
            headless=False,
            settings=settings,
            record_video_dir=rec_dir,
        ) as client:
            proj = await client.create_project(title="gflow character — live demo")
            step("rec", f"project={proj.project_id} — running real character_create", prefix="rec")
            with capture_logs() as cap:
                await character_create(
                    client,
                    recorder,
                    profile_name=profile,
                    profile_dir=profile_dir,
                    project_id=proj.project_id,
                    name="Marina",
                    face=face,
                    locale=locale,
                )
    finally:
        recorder.close()

    # Image-not-video guard (council hardening): prove the real image path ran.
    err = _verify_image_path({e.get("event", "") for e in cap})
    if err is not None:
        step("ERR", err, prefix="rec")
        return 1
    step("ok", "image path verified via character_create structlog events", prefix="rec")

    # Context closed -> webm finalized. Expect EXACTLY ONE webm (concurrency=1).
    webms = sorted(rec_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not webms:
        step("ERR", "no .webm produced", prefix="rec")
        return 1
    if len(webms) > 1:
        step("ERR", f"{len(webms)} webms produced (expected 1) — refusing to guess", prefix="rec")
        return 1
    _transcode(webms[0], out_path)
    shutil.rmtree(rec_dir, ignore_errors=True)
    step("done", f"recorded -> {out_path}", prefix="rec")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Record gflow's real character-create flow (image/free)."
    )
    ap.add_argument("--profile", required=True, help="Flow profile name (chrome-strategy).")
    ap.add_argument("--face-prompt", default=_DEFAULT_FACE, help="Face reference prompt.")
    ap.add_argument(
        "--locale",
        default="en-US",
        help="UI locale (BCP-47; normalized to the short Flow URL segment, #153).",
    )
    ap.add_argument("--out", type=Path, default=None, help="Output .mp4 path.")
    args = ap.parse_args(argv)
    out_path: Path = args.out or (_HERE / "_spike_out" / "flow-create.mp4")
    profile_dir = resolve_profile_dir(args.profile)
    return asyncio.run(
        _run(
            profile=args.profile,
            profile_dir=profile_dir,
            face_prompt=args.face_prompt,
            locale=args.locale,
            out_path=out_path,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
