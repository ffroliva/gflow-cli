"""Small file-integrity checks shared by catalog resolution and UI upload."""

from __future__ import annotations

import hashlib
from pathlib import Path


def matches_recorded_file(path: Path, *, sha256: str, size: int | None = None) -> bool:
    """Return whether an on-disk file still matches its required catalog digest."""
    if not sha256:
        return False
    try:
        if not path.is_file() or (size is not None and path.stat().st_size != size):
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == sha256
    except OSError:
        return False
