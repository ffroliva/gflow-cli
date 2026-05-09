"""TSV manifest parser for `gflow video batch`.

Format (tab-separated, optional ``# ``-prefixed comments + blank lines)::

    start_image\tprompt\tend_image\taspect\toutput_path

Columns:

- start_image: path to PNG/JPG (empty -> T2V)
- prompt: required, non-empty
- end_image: optional second-frame for transition I2V (not yet wired in MVP)
- aspect: 9:16 | 16:9 | 1:1 (default 9:16)
- output_path: optional output file path; empty -> default scheme
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flow_cli.api.video import Aspect


@dataclass(frozen=True)
class ManifestEntry:
    prompt: str
    start_image: Path | None = None
    end_image: Path | None = None
    aspect: Aspect = Aspect.PORTRAIT
    output_path: Path | None = None


def parse_manifest(path: Path) -> list[ManifestEntry]:
    """Parse a TSV manifest file. Raises ValueError on bad rows."""
    entries: list[ManifestEntry] = []
    text = path.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cols = raw.split("\t")
        # Pad to 5 columns so unpacking is always safe.
        while len(cols) < 5:
            cols.append("")
        start_image_s, prompt, end_image_s, aspect_s, output_s = (c.strip() for c in cols[:5])
        if not prompt:
            raise ValueError(f"line {lineno}: prompt is required (got empty)")
        aspect = Aspect.from_cli(aspect_s) if aspect_s else Aspect.PORTRAIT
        entries.append(
            ManifestEntry(
                prompt=prompt,
                start_image=Path(start_image_s) if start_image_s else None,
                end_image=Path(end_image_s) if end_image_s else None,
                aspect=aspect,
                output_path=Path(output_s) if output_s else None,
            )
        )
    return entries
