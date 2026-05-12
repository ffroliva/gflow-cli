"""Generate a single image via Google Flow on a logged-in Pro/Ultra profile.

This script is a minimal, runnable example of how to drive Google Flow's
image generation surface from Python. It uses the same code path as the
``gflow image t2i`` CLI command — the production-validated UI-mimicry
transport that mirrors the editor flow a human developer uses on a
Google AI Pro or Ultra subscription.

Requirements
------------
- An active **Google AI Pro or Ultra subscription**. gflow-cli does not
  bypass billing, plan tiers, or usage limits — it consumes them on the
  authenticated profile's behalf.
- A Playwright Chromium user-data-dir already signed in to Flow. Create
  one once via::

      gflow auth login --profile <your-profile-name>

  Set the profile name via the ``--profile`` flag or the
  ``GFLOW_EXAMPLE_PROFILE`` environment variable.

What it does
------------
1. Opens a Playwright-managed Chromium against the named profile.
2. Navigates to Flow's editor on a logged-in session.
3. Creates a new Flow project, types the prompt, submits.
4. Captures the ``batchGenerateImages`` response and downloads the PNG to
   the output directory.

The public endpoint exercised is
``aisandbox-pa.googleapis.com/flowMedia:batchGenerateImages`` — the same
URL Flow's own UI hits when you click *Create* in the editor.

Usage
-----
Default profile name via env var, default prompt::

    GFLOW_EXAMPLE_PROFILE=<your-profile> python examples/single_image_t2i.py

Custom prompt + explicit profile::

    python examples/single_image_t2i.py \\
        --profile <your-profile> \\
        --prompt "a quiet mountain lake at dawn, cinematic photography"

Output is written to ``./gflow-output/<UTC-timestamp>/image_00.png`` by
default. Override with ``--output-dir``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from gflow_cli import profile_store
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.ui_automation import UiAutomationTransport

_DEFAULT_PROMPT = "a quiet mountain lake at dawn, cinematic photography"


async def _run(profile_dir: Path, prompt_text: str, output_dir: Path) -> Path:
    transport = UiAutomationTransport()
    async with FlowApiClient(
        profile_dir=profile_dir, headless=False, transport=transport
    ) as client:
        project = await client.create_project(title="gflow-cli example")
        req = GenerateImageRequest(
            prompt=prompt_text,
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
        )
        image = await client.generate_image(project_id=project.project_id, req=req)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / "image_00.png"
        return await client.download_image(image, target)


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
        "--prompt",
        default=_DEFAULT_PROMPT,
        help="Prompt text to generate from.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write the PNG. Defaults to ./gflow-output/<UTC>/.",
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
        Path("gflow-output") / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )
    saved = asyncio.run(_run(profile_dir, args.prompt, output_dir))
    print(f"\nImage saved: {saved} ({saved.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
