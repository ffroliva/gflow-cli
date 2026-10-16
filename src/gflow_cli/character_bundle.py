"""A portable character: one plain folder that carries it between projects and accounts.

```
nora/
  manifest.json   {"schema_version": 1, "name", "voice", "personality",
                   "images": [{"role": "portrait"|"body", "file", "sha256", "mime"}]}
  portrait.jpg
  body.png        (optional)
```

Flow has no way to move a character: it belongs to one project and its images are bound
there. So ``gflow character export`` writes this folder and ``gflow character import``
rebuilds the character from it, uploading the images again (spike 2026-10-16).

The manifest is an **allowlist**: nothing in it ties a bundle to an account — no project
or entity id, no signed link, no address. On import the folder is untrusted input, so
:func:`read_bundle` refuses everything doubtful before anything is sent: a path that is
not a bare file name, a symlink, a non-image, an oversized file, a changed file (sha256),
an unknown schema, and text with control, bidi or zero-width characters is cleaned.

Pure: local files only, no Flow. The sha256 protects the bundle itself — Flow re-encodes
uploads, so it can never be compared with what Flow serves back.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from gflow_cli.errors import ConfigurationError
from gflow_cli.image_files import MAX_IMAGE_BYTES, image_extension, image_kind

SCHEMA_VERSION = 1
MANIFEST = "manifest.json"
ROLES = ("portrait", "body")
MAX_NAME_CHARS = 100
MAX_PERSONALITY_CHARS = 2000
MAX_VOICE_CHARS = 40

_MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
# Bidi embeddings/overrides/isolates and marks, zero-width characters and BOM: invisible,
# and able to make a name read differently from what it is.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁩﻿]")


@dataclass(frozen=True)
class BundleImage:
    role: str
    path: Path
    sha256: str
    mime: str


@dataclass(frozen=True)
class CharacterBundle:
    root: Path
    name: str
    voice: str | None
    personality: str | None
    images: tuple[BundleImage, ...]

    def image(self, role: str) -> BundleImage | None:
        return next((i for i in self.images if i.role == role), None)


def _refuse(bundle: Path, what: str) -> ConfigurationError:
    return ConfigurationError(detail=f"character bundle {bundle}: {what}")


def _clean(text: str, *, multiline: bool) -> str:
    """NFC, minus invisible characters and control characters (newline kept if asked)."""
    text = _INVISIBLE.sub("", unicodedata.normalize("NFC", text))
    keep: set[str] = {"\n"} if multiline else set()
    return "".join(
        ch for ch in text if ch in keep or unicodedata.category(ch) not in ("Cc", "Cf")
    ).strip()


def _text(
    bundle: Path, data: dict[str, Any], key: str, limit: int, *, multiline: bool
) -> str | None:
    raw = data.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _refuse(bundle, f"{key} must be text")
    if len(raw) > limit:
        raise _refuse(bundle, f"{key} is {len(raw)} characters; the limit is {limit}")
    return _clean(raw, multiline=multiline) or None


def write_bundle(
    out: Path,
    *,
    name: str,
    voice: str | None,
    personality: str | None,
    images: list[tuple[str, bytes]],
) -> CharacterBundle:
    """Write a bundle into ``out``, which must not exist or be an empty folder.

    ``images`` is ``(role, bytes)`` in slot order. Each file is named by its role and the
    extension of its REAL type (from its bytes) — never from the character's name."""
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise _refuse(out, "the destination exists and is not an empty folder")
    entries: list[dict[str, str]] = []
    files: list[tuple[str, bytes]] = []
    for role, body in images:
        ext = image_extension(body[:12])
        kind = image_kind(body[:12])
        if role not in ROLES or ext is None or kind is None:
            raise _refuse(out, f"the {role} image is not a PNG, JPEG, WebP or GIF")
        file = f"{role}{ext}"
        entries.append(
            {
                "role": role,
                "file": file,
                "sha256": hashlib.sha256(body).hexdigest(),
                "mime": _MIME[kind],
            }
        )
        files.append((file, body))
    out.mkdir(parents=True, exist_ok=True)
    for file, body in files:
        (out / file).write_bytes(body)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "voice": voice,
        "personality": personality,
        "images": entries,
    }
    (out / MANIFEST).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return read_bundle(out)


def _image(bundle: Path, entry: object) -> BundleImage:
    if not isinstance(entry, dict):
        raise _refuse(bundle, "an images[] entry is not an object")
    item = cast("dict[str, Any]", entry)
    role, file, digest = item.get("role"), item.get("file"), item.get("sha256")
    if role not in ROLES:
        raise _refuse(bundle, f"unknown image role {role!r}")
    if (
        not isinstance(file, str)
        or file in ("", ".", "..")
        or Path(file).name != file
        or "\\" in file
    ):
        raise _refuse(bundle, f"image file {file!r} is not a bare file name inside the bundle")
    path = bundle / file
    if path.is_symlink():
        raise _refuse(bundle, f"{file} is a symbolic link; a bundle holds real files only")
    if not path.is_file():
        raise _refuse(bundle, f"{file} is missing")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise _refuse(bundle, f"{file} is larger than {MAX_IMAGE_BYTES // 1_048_576} MB")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise _refuse(bundle, f"{file} does not match its sha256 — it changed after export")
    kind = image_kind(body[:12])
    if kind is None:
        raise _refuse(bundle, f"{file} is not an image (PNG, JPEG, WebP or GIF)")
    return BundleImage(role=str(role), path=path, sha256=str(digest), mime=_MIME[kind])


def read_bundle(bundle: Path) -> CharacterBundle:
    """Read and fully validate a bundle folder. Every refusal is ``ConfigurationError``
    (exit 11), raised before the caller has sent anything anywhere."""
    manifest = bundle / MANIFEST
    if not manifest.is_file() or manifest.is_symlink():
        raise _refuse(bundle, f"no {MANIFEST} (is this an exported character folder?)")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _refuse(bundle, f"{MANIFEST} is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise _refuse(bundle, f"{MANIFEST} is not an object")
    fields = cast("dict[str, Any]", data)
    if fields.get("schema_version") != SCHEMA_VERSION:
        raise _refuse(
            bundle,
            f"schema_version {fields.get('schema_version')!r} is not {SCHEMA_VERSION}; "
            "this gflow cannot read it",
        )
    name = _text(bundle, fields, "name", MAX_NAME_CHARS, multiline=False)
    if not name:
        raise _refuse(bundle, "the character has no name")
    entries = fields.get("images")
    if not isinstance(entries, list) or not 1 <= len(cast("list[Any]", entries)) <= len(ROLES):
        raise _refuse(bundle, "images must list a portrait and at most a body")
    images = tuple(_image(bundle, e) for e in cast("list[Any]", entries))
    roles = [i.role for i in images]
    if len(set(roles)) != len(roles):
        raise _refuse(bundle, "an image role appears twice")
    if "portrait" not in roles:
        raise _refuse(bundle, "there is no portrait image")
    return CharacterBundle(
        root=bundle,
        name=name,
        voice=_text(bundle, fields, "voice", MAX_VOICE_CHARS, multiline=False),
        personality=_text(bundle, fields, "personality", MAX_PERSONALITY_CHARS, multiline=True),
        images=tuple(sorted(images, key=lambda i: ROLES.index(i.role))),
    )
