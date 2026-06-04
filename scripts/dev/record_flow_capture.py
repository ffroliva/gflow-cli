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
from typing import Any

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

# Language-agnostic editor name-input locator (proven by the #151 recon spikes):
# tag the topmost visible, editable text <input> with data-gflow-title. The editor
# title is decoupled from displayName (#151) — typed text shows on screen but does
# NOT persist, which is exactly what a promo recording needs (display only).
_FIND_NAME_INPUT_JS = r"""
() => {
  // Idempotent: drop any prior tag so [data-gflow-title] stays single-valued.
  document
    .querySelectorAll('[data-gflow-title]')
    .forEach((el) => el.removeAttribute('data-gflow-title'));
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.offsetParent !== null;
  };
  const cands = Array.from(document.querySelectorAll('input')).filter((el) => {
    const t = (el.getAttribute('type') || 'text').toLowerCase();
    if (['hidden', 'search', 'checkbox', 'radio', 'file', 'range', 'color'].includes(t))
      return false;
    if (el.readOnly || el.disabled) return false;
    if (!vis(el)) return false;
    if ((el.getAttribute('role') || '').toLowerCase() === 'searchbox') return false;
    return true;
  });
  cands.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  if (cands.length) cands[0].setAttribute('data-gflow-title', '1');
  return { count: cands.length };
}
"""

# React-controlled inputs ignore plain value sets; set via the native setter then
# dispatch input/change so React's onChange fires (fallback when keyboard typing
# does not register on the controlled input).
_NATIVE_SET_JS = r"""
(value) => {
  const el = document.querySelector('[data-gflow-title]');
  if (!el) return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value'
  ).set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}
"""


async def _type_name_for_display(page: Any, name: str) -> None:
    """Type *name* into the editor name input for ON-SCREEN display in the video.

    Language-agnostic (tags the topmost editable text input structurally — no
    localized placeholder). Display-only: the editor title is decoupled from
    displayName (#151) and will not persist, which is irrelevant to the video.
    Degrades gracefully if the input cannot be found.
    """
    found = await page.evaluate(_FIND_NAME_INPUT_JS)
    if not found.get("count"):
        step("warn", "editor name input not found — skipping display typing", prefix="rec")
        return
    loc = page.locator("[data-gflow-title]").first
    await loc.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await page.keyboard.type(name, delay=80)  # slow enough to read on screen
    if (await loc.input_value()) != name:
        await page.evaluate(_NATIVE_SET_JS, name)  # React-controlled fallback
    await page.wait_for_timeout(1500)  # hold so the typed name is readable
    step("name", f"typed {name!r} into editor name input (display-only)", prefix="rec")


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
    *, profile: str, profile_dir: Path, name: str, face_prompt: str, locale: str, out_path: Path
) -> int:
    from structlog.testing import capture_logs

    rec_dir = out_path.parent / "_rec_tmp"
    rec_dir.mkdir(parents=True, exist_ok=True)
    # Isolate the data store AND the image output dir so the real catalog/gallery
    # are never touched; force concurrency=1 so exactly one .webm is produced.
    tmp_root = Path(tempfile.mkdtemp(prefix="rec-iso-"))
    iso_env = {
        "GFLOW_CLI_DB_PATH": str(tmp_root / "catalog.db"),
        "GFLOW_CLI_OUTPUT_DIR": str(tmp_root / "out"),
        "GFLOW_CLI_CONCURRENCY": "1",
    }
    saved_env = {k: os.environ.get(k) for k in iso_env}
    cap: list[Any] = []  # bound even if context entry raises early
    try:
        os.environ.update(iso_env)
        settings = Settings()  # re-reads the isolated env vars set above
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
                step(
                    "rec",
                    f"project={proj.project_id} — running real character_create",
                    prefix="rec",
                )
                with capture_logs() as cap:
                    await character_create(
                        client,
                        recorder,
                        profile_name=profile,
                        profile_dir=profile_dir,
                        project_id=proj.project_id,
                        name=name,
                        face=face,
                        locale=locale,
                    )
                # Promo polish: type the name into the editor for ON-SCREEN display
                # (display-only; the editor title is decoupled from displayName per
                # #151, so it won't persist — irrelevant to the recorded video).
                # Use the slot-0 page directly (not the deprecated _page alias).
                await _type_name_for_display(client._pages[0], name)
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
            step(
                "ERR",
                f"{len(webms)} webms produced (expected 1) — refusing to guess",
                prefix="rec",
            )
            return 1
        _transcode(webms[0], out_path)
        shutil.rmtree(rec_dir, ignore_errors=True)
        step("done", f"recorded -> {out_path}", prefix="rec")
        return 0
    finally:
        # Restore the process env and remove the throwaway isolation dir so the
        # driver is reentrant and leaves no catalog.db behind.
        for key, prev in saved_env.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        shutil.rmtree(tmp_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Record gflow's real character-create flow (image/free)."
    )
    ap.add_argument("--profile", required=True, help="Flow profile name (chrome-strategy).")
    ap.add_argument(
        "--name", default="Marina", help="Character name (typed into the editor for display)."
    )
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
            name=args.name,
            face_prompt=args.face_prompt,
            locale=args.locale,
            out_path=out_path,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
