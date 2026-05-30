"""Drive a t2i -> i2i -> i2v chain in one Flow session from Python.

This script is a runnable example of how to compose Google Flow's image
and video surfaces into a 3-step creative chain from Python, using the
same ``FlowApiClient`` the ``gflow`` CLI uses internally. The whole chain
runs inside ONE ``async with FlowApiClient(...)`` block, so it shares
one Chromium profile, one Flow project, and one browser window from
start to finish.

The chain:

1. ``generate_image`` (t2i) — produces the **initial frame** from a prompt.
2. ``generate_image`` (i2i) — uses the t2i output as ``ref_paths``;
   produces the **end frame** in the same visual style.
3. ``generate_video`` (i2v) — passes BOTH the t2i path as ``start_image``
   and the i2i path as ``end_image``; Veo interpolates the motion.

The defaults render a minimalist motivational stickman waking at dawn —
a deliberately on-brand, runnable subject so the example exercises the
ref-attach + start/end-frame paths end-to-end without you needing to
supply your own prompts. Override any prompt via the CLI flags if you
want a different subject.

Requirements
------------
- An active **Google AI Pro or Ultra subscription**. gflow-cli does not
  bypass billing, plan tiers, or usage limits — each phase consumes the
  same credits as the equivalent click in the Flow editor.
- A Playwright Chromium user-data-dir already signed in to Flow. Create
  one once via::

      gflow auth login --profile <your-profile-name>

  Set the profile name via the ``--profile`` flag or the
  ``GFLOW_EXAMPLE_PROFILE`` environment variable.

Why drive ``FlowApiClient`` directly?
-------------------------------------
The ``gflow`` CLI shells one project per command. Driving
``FlowApiClient`` lets you keep the same Flow project across phases so
the ref-image ↔ start-frame ↔ end-frame relationships are visible in
one place in the editor — and it avoids re-opening Chromium between
phases.

The three in-process gotchas this example codifies
--------------------------------------------------
1. ``Aspect`` / ``Model`` / ``VideoModel`` enum values are the WIRE
   format (``IMAGE_ASPECT_RATIO_PORTRAIT`` etc). To pass the friendly
   CLI strings (``"9:16"``, ``"nano-pro"``, ``"omni-flash"``), use
   ``Aspect.from_cli(...)`` — the same translation the CLI does.
2. ``client.download_image(image, target)`` **returns the actual saved
   path**. ``gflow_cli.paths.correct_image_extension()`` sniffs magic
   bytes and renames the file post-write if Flow served (say) a JPEG
   against your ``.png`` target. The i2i ``ref_paths`` attach and the
   i2v ``start_image`` / ``end_image`` must use the **real** returned
   path, not the path you asked for.
3. ``generate_video`` does **not** accept ``project_id`` (unlike
   ``generate_image``). It derives the project context from the
   transport's current session. Passing ``project_id=`` raises
   ``TypeError``.

Usage
-----
Defaults via env var::

    GFLOW_EXAMPLE_PROFILE=<your-profile> python examples/workflow_chain.py

Custom subject — override any of the three prompts independently::

    python examples/workflow_chain.py \\
        --profile <your-profile> \\
        --prompt-t2i  "a quiet mountain lake at dawn, cinematic photography" \\
        --prompt-i2i  "same lake, now bathed in golden midday light" \\
        --prompt-motion "slow cinematic push-in across the lake at sunrise"

Output is written to ``~/gflow-output/<UTC-timestamp>/`` by default —
three files: ``01-t2i.png``, ``02-i2i.png``, ``03-video.mp4`` (extensions
may differ; the script prints the actual saved paths).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

from gflow_cli import profile_store
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest, Model
from gflow_cli.api.video import Aspect as VideoAspect
from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoModel

# Default subject — a minimalist stickman waking at dawn. The motion
# prompt explicitly anchors the 2D ink line-art style so Veo's
# omni-flash interpolation doesn't drift into its preferred 3D
# photoreal aesthetic. If you change the subject, mirror the same
# "Maintain exact <style> ... same characters as start/end frame"
# anchoring pattern in --prompt-motion.
_DEFAULT_PROMPT_T2I = (
    "A bold minimalist motivational stickman character, 2D black ink "
    "line-art style, simple body with round head and tiny determined "
    "eyes, sitting at a desk under a warm lamp with an open notebook "
    "and a coffee mug, dark blue pre-dawn cityscape through the "
    "window. Clean high-contrast vertical poster frame, no text, no "
    "logos, no watermark."
)
_DEFAULT_PROMPT_I2I = (
    "Same minimalist 2D black ink line-art stickman in the same "
    "apartment, now standing and stretching with quiet determination "
    "as golden sunrise light floods through the window. Warmer, "
    "brighter, more triumphant. Same clean high-contrast vertical "
    "poster style. No text, no logos, no watermark."
)
_DEFAULT_PROMPT_MOTION = (
    "Animate the provided start and end frames. Maintain exact 2D "
    "black ink line-art style, simple stickman body, round head with "
    "tiny drawn eyes, dark cartoony illustration aesthetic — the same "
    "characters and apartment shown in the start frame and end frame. "
    "The stickman rises and stretches with quiet determination; "
    "coffee steam drifts; golden sunrise glow slowly strengthens "
    "through the window. Gentle cinematic push-in, subtle, premium. "
    "No 3D rendering, no plastic surface, no realistic textures."
)


async def _run_chain(
    profile_dir: Path,
    output_dir: Path,
    *,
    prompt_t2i: str,
    prompt_i2i: str,
    prompt_motion: str,
    aspect_str: str,
    image_model_str: str,
    video_model_str: str,
) -> tuple[Path, Path, Path]:
    # Enum gotcha (#1): `from_cli` maps the friendly strings the CLI
    # accepts ("9:16", "nano-pro", "omni-flash") onto the wire-format
    # enum values. The bare constructors take the wire format and would
    # raise ValueError on these inputs.
    image_aspect = ImageAspect.from_cli(aspect_str)
    video_aspect = VideoAspect.from_cli(aspect_str)
    image_model = Model.from_cli(image_model_str)
    video_model = VideoModel.from_cli(video_model_str)

    output_dir.mkdir(parents=True, exist_ok=True)

    # `out_dir=output_dir` routes every download (including the
    # generate_video auto-download) through one directory directly,
    # avoiding cross-drive shutil.move on completion.
    async with FlowApiClient(
        profile_dir=profile_dir,
        headless=False,
        transport="ui_automation",
        out_dir=output_dir,
    ) as client:
        project = await client.create_project(title="gflow-cli workflow chain")

        # --- Phase 1: t2i — initial frame from prompt only.
        print("[chain] phase 1 (t2i) ...", flush=True)
        t2i_req = GenerateImageRequest(
            prompt=prompt_t2i,
            aspect=image_aspect,
            model=image_model,
            count=1,
        )
        t2i_img = await client.generate_image(
            project_id=project.project_id,
            req=t2i_req,
        )
        # Download gotcha (#2): use the RETURNED path; the file may have
        # been renamed if Flow served a different format than the
        # extension we asked for.
        t2i_path = await client.download_image(t2i_img, output_dir / "01-t2i.png")
        print(f"[chain]   -> {t2i_path}", flush=True)

        # --- Phase 2: i2i — end frame, conditioned on the t2i output.
        print("[chain] phase 2 (i2i with ref_paths=t2i) ...", flush=True)
        i2i_req = GenerateImageRequest(
            prompt=prompt_i2i,
            aspect=image_aspect,
            model=image_model,
            ref_paths=(t2i_path,),  # pyright: ignore[reportCallIssue]
            count=1,
        )
        i2i_img = await client.generate_image(
            project_id=project.project_id,
            req=i2i_req,
        )
        i2i_path = await client.download_image(i2i_img, output_dir / "02-i2i.png")
        print(f"[chain]   -> {i2i_path}", flush=True)

        # --- Phase 3: i2v — interpolate start (t2i) -> end (i2i).
        print("[chain] phase 3 (i2v with start+end frames) ...", flush=True)
        i2v_req = GenerateVideoRequest(
            prompt=prompt_motion,
            mode=Mode.I2V,
            aspect=video_aspect,
            model=video_model,
            start_image=t2i_path,
            end_image=i2i_path,
            count=1,
        )
        # generate_video gotcha (#3): do NOT pass project_id here.
        video_result = await client.generate_video(req=i2v_req)
        video_target = output_dir / "03-video.mp4"
        if video_result.local_path and Path(video_result.local_path) != video_target:
            shutil.move(str(video_result.local_path), str(video_target))
        print(f"[chain]   -> {video_target}", flush=True)

        return t2i_path, i2i_path, video_target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default=os.getenv("GFLOW_EXAMPLE_PROFILE"),
        help=(
            "Profile name (must be signed in via "
            "`gflow auth login --profile <name>`). "
            "Defaults to $GFLOW_EXAMPLE_PROFILE."
        ),
    )
    parser.add_argument(
        "--prompt-t2i",
        default=_DEFAULT_PROMPT_T2I,
        help="Prompt for phase 1 (t2i, initial frame).",
    )
    parser.add_argument(
        "--prompt-i2i",
        default=_DEFAULT_PROMPT_I2I,
        help="Prompt for phase 2 (i2i, end frame; ref=t2i output).",
    )
    parser.add_argument(
        "--prompt-motion",
        default=_DEFAULT_PROMPT_MOTION,
        help=(
            "Prompt for phase 3 (i2v). Anchor visual style explicitly — "
            "Veo's interpolation drifts into its default aesthetic "
            "otherwise."
        ),
    )
    parser.add_argument(
        "--aspect",
        default="9:16",
        help="Aspect ratio for all three phases (default 9:16).",
    )
    parser.add_argument(
        "--image-model",
        default="nano-pro",
        help="Image model alias for t2i + i2i (default nano-pro).",
    )
    parser.add_argument(
        "--video-model",
        default="omni-flash",
        help="Video model alias for i2v (default omni-flash).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for outputs. Defaults to ~/gflow-output/<UTC>/.",
    )
    args = parser.parse_args()

    if not args.profile:
        print(
            "error: --profile is required (or set GFLOW_EXAMPLE_PROFILE). "
            "Run `gflow auth login --profile <name>` first to create one.",
            file=sys.stderr,
        )
        sys.exit(2)

    profile_dir = profile_store.profile_dir(args.profile)
    if not profile_dir.exists():
        print(
            f"error: profile dir does not exist: {profile_dir}. "
            f"Run `gflow auth login --profile {args.profile}` first.",
            file=sys.stderr,
        )
        sys.exit(2)

    output_dir = args.output_dir or (
        Path.home() / "gflow-output" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )

    t2i, i2i, video = asyncio.run(
        _run_chain(
            profile_dir,
            output_dir,
            prompt_t2i=args.prompt_t2i,
            prompt_i2i=args.prompt_i2i,
            prompt_motion=args.prompt_motion,
            aspect_str=args.aspect,
            image_model_str=args.image_model,
            video_model_str=args.video_model,
        ),
    )
    print(f"\nChain complete:\n  t2i:   {t2i}\n  i2i:   {i2i}\n  video: {video}")


if __name__ == "__main__":
    main()
