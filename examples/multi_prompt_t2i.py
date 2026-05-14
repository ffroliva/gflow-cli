"""Run a shell-friendly multi-prompt image batch via Google Flow.

Wrapper around ``gflow image t2i`` showing the v0.6 shell-multi-prompt
surface — N different prompts in one Flow session without authoring a
JSON config. The same code path that ``gflow run --config`` uses powers
this; the difference is only the ergonomics of how prompts are supplied.

The CLI accepts three equivalent input surfaces:

1. **Positional variadic** — ``gflow image t2i "p1" "p2" "p3"``
2. **--prompts-file** — ``gflow image t2i --prompts-file prompts.txt``
3. **--stdin** — ``cat prompts.txt | gflow image t2i --stdin``

This script demonstrates ``--prompts-file`` against the bundled
``examples/sample_prompts.txt``. ``--aspect``, ``--model``, and
``--continue-on-error`` / ``--fail-fast`` apply globally across all
prompts in the batch.

Requirements
------------
- Active **Google AI Pro or Ultra subscription**.
- A profile signed in via ``gflow auth login --profile <name>``.

Usage
-----
Default prompts file (``examples/sample_prompts.txt``) + profile from env::

    GFLOW_EXAMPLE_PROFILE=<your-profile> python examples/multi_prompt_t2i.py

Custom prompts file + explicit profile::

    python examples/multi_prompt_t2i.py \\
        --profile <your-profile> \\
        --prompts-file path/to/your-prompts.txt \\
        --aspect 9:16

For per-prompt overrides (different aspect / model / seed per prompt),
use ``gflow run --config`` and see ``examples/batch_from_config.py``.
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
        "--prompts-file",
        type=Path,
        default=Path(__file__).parent / "sample_prompts.txt",
        help="Path to a prompts file (one prompt per line; '#' lines and blanks skipped).",
    )
    parser.add_argument(
        "--aspect",
        default=None,
        help="Aspect ratio applied to all prompts (e.g. 16:9, 9:16, 1:1).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model identifier applied to all prompts (e.g. nano2, narwhal).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to gflow-cli's per-batch default.",
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
    if not args.prompts_file.exists():
        print(f"error: prompts file not found: {args.prompts_file}", file=sys.stderr)
        sys.exit(2)

    cmd: list[str] = [
        sys.executable,
        "-m",
        "gflow_cli",
        "image",
        "t2i",
        "--prompts-file",
        str(args.prompts_file),
        "--profile",
        args.profile,
    ]
    if args.aspect is not None:
        cmd.extend(["--aspect", args.aspect])
    if args.model is not None:
        cmd.extend(["--model", args.model])
    if args.output_dir is not None:
        cmd.extend(["--out", str(args.output_dir)])
    if args.fail_fast:
        cmd.append("--fail-fast")

    print(f"Running: {' '.join(cmd)}\n")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
