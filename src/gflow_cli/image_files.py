"""Which local files gflow will send to Flow as images — one rule for every caller.

Used by the upload path (``api/client.py``) and by character bundles
(``character_bundle.py``), which must refuse exactly what the upload would refuse.
Pure: no I/O beyond what a caller hands in.
"""

from __future__ import annotations

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB

# Extension per accepted container; every one is accepted by Flow's web UI.
_EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "gif": ".gif"}


def image_kind(header: bytes) -> str | None:
    """The image container ``header`` (a file's first 12 bytes) starts, or ``None``.

    * **PNG** — ``\\x89PNG\\r\\n\\x1a\\n``
    * **JPEG** — bytes 0..2 are ``\\xff\\xd8\\xff``
    * **WebP** — bytes 0..3 ``RIFF`` + bytes 8..11 ``WEBP``
    * **GIF** — ``GIF87a`` or ``GIF89a``

    Rejecting anything else is a defense-in-depth measure: combined with
    ``resolve_path=True`` on a CLI argument it stops a symlink-laundering attack
    (``./photo.png -> ~/.ssh/id_rsa``) at the bytes layer.
    """
    if len(header) < 12:
        return None
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def image_extension(header: bytes) -> str | None:
    """The file extension for the container ``header`` starts (``.jpg`` for JPEG)."""
    kind = image_kind(header)
    return _EXTENSIONS[kind] if kind else None
