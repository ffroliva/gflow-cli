"""Run a JSON-described batch of image generations via Google Flow.

Wrapper around ``gflow run --config <file>`` showing the canonical way
to script multi-prompt batches. The same JSON schema is loaded directly
by the CLI; this example just shows the invocation shape.

Requirements
------------
- Active **Google AI Pro or Ultra subscription**.
- A profile signed in via ``gflow auth login --profile <name>``.

Usage
-----
Default config (``examples/sample_config.json``) + profile from env::

    GFLOW_EXAMPLE_PROFILE=<your-profile> python examples/batch_from_config.py

Custom config + explicit profile::

    python examples/batch_from_config.py \\
        --profile <your-profile> \\
        --config path/to/your-config.json

The batch runs sequentially through a single Flow session. See
``examples/sample_config.json`` for the schema reference; the full spec
is documented in ``USAGE.md`` (section "Batch runs via gflow run").
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


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
        "--config",
        type=Path,
        default=Path(__file__).parent / "sample_config.json",
        help="Path to the batch config JSON.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override the output_dir from the config.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Halt on the first prompt failure (default: continue-on-error).",
    )
    args = parser.parse_args()

    if not args.profile:
        print(
            "error: --profile is required (or set GFLOW_EXAMPLE_PROFILE). "
            "Run `gflow auth login --profile <name>` first to create one.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not args.config.exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        sys.exit(2)

    cmd: list[str] = [
        sys.executable,
        "-m",
        "gflow_cli",
        "run",
        "--config",
        str(args.config),
        "--profile",
        args.profile,
    ]
    if args.output_dir is not None:
        cmd.extend(["--output-dir", str(args.output_dir)])
    if args.fail_fast:
        cmd.append("--fail-fast")

    print(f"Running: {' '.join(cmd)}\n")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
