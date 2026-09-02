"""JSONL chain-manifest parser for ``gflow video chain`` (Task 6).

Format: **JSONL** — one JSON object per line, e.g.::

    {"prompt": "a lone wolf on a ridge", "model": "veo-lite", "aspect": "16:9"}
    {"prompt": "it turns to face the storm"}

Each object is one chain link, in order. ``prompt`` is required and non-empty;
``model`` / ``duration`` / ``aspect`` are optional per-link overrides (``None``
means "inherit the chain default"). Blank lines and ``#``-prefixed comment lines
are skipped. At least one valid link is required.

``duration`` parses here but **no chain can apply it** (#634): Flow renders a
duration control for ``omni_flash`` alone and chains reject ``omni_flash``, so
:func:`gflow_cli.chain.run_chain` rejects any link carrying one before the first
link is submitted. It stays parseable rather than being rejected at this layer so
the error names the chain rule once, in the place that owns it — and so a
programmatic caller building specs directly hits the identical guard. The example
above deliberately no longer shows it; it used to, which is exactly how the
crashing combination became the documented one.

**Why JSONL, not a headered TSV?** The chain's per-link overrides are sparse —
most links carry a prompt alone. A positional TSV would force empty sentinel
columns on every line and make "field omitted" indistinguishable from "field set
to empty"; JSON's explicit object keys express optionality directly, type the
``duration`` as a real number (no string-to-int reparse ambiguity), and tolerate
field reordering.

Model and aspect strings are mapped through the SAME canonical path the CLI uses
(:meth:`gflow_cli.api.video.VideoModel.from_cli` /
:meth:`gflow_cli.api.video.Aspect.from_cli`) — no alias strings are invented
here. Any malformed input raises :class:`gflow_cli.errors.ChainManifestError`
citing the offending line number.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from gflow_cli.api.video import Aspect, VideoModel
from gflow_cli.chain import ChainLinkSpec
from gflow_cli.errors import ChainManifestError

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["parse_chain_manifest"]

_KNOWN_KEYS = frozenset({"prompt", "model", "duration", "aspect"})


def _fail(lineno: int, reason: str) -> ChainManifestError:
    return ChainManifestError(f"line {lineno}: {reason}")


def _parse_model_field(lineno: int, raw_model: object) -> VideoModel | None:
    """Parse the optional 'model' field; return None if absent."""
    if raw_model is None:
        return None
    if not isinstance(raw_model, str):
        raise _fail(lineno, "'model' must be a string alias")
    try:
        return VideoModel.from_cli(raw_model)
    except ValueError as exc:
        raise _fail(lineno, str(exc)) from exc


def _parse_duration_field(lineno: int, raw_duration: object) -> int | None:
    """Parse the optional 'duration' field; return None if absent."""
    if raw_duration is None:
        return None
    # bool is a subclass of int; reject it explicitly so a JSON ``true``
    # cannot masquerade as a duration.
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, int):
        raise _fail(lineno, "'duration' must be an integer (seconds)")
    return raw_duration


def _parse_aspect_field(lineno: int, raw_aspect: object) -> Aspect | None:
    """Parse the optional 'aspect' field; return None if absent."""
    if raw_aspect is None:
        return None
    if not isinstance(raw_aspect, str):
        raise _fail(lineno, "'aspect' must be a string (9:16 | 16:9 | 1:1)")
    try:
        return Aspect.from_cli(raw_aspect)
    except ValueError as exc:
        raise _fail(lineno, str(exc)) from exc


def _parse_line(lineno: int, raw: str) -> ChainLinkSpec:
    """Parse one non-blank, non-comment JSONL line into a ChainLinkSpec."""
    try:
        loaded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _fail(lineno, f"not valid JSON ({exc.msg})") from exc

    if not isinstance(loaded, dict):
        raise _fail(lineno, f"expected a JSON object, got {type(loaded).__name__}")

    obj = cast("dict[str, object]", loaded)
    unknown = set(obj) - _KNOWN_KEYS
    if unknown:
        allowed = ", ".join(sorted(_KNOWN_KEYS))
        raise _fail(
            lineno,
            f"unknown field(s) {sorted(unknown)}; allowed fields are: {allowed}",
        )

    prompt = obj.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _fail(lineno, "'prompt' is required and must be a non-empty string")

    return ChainLinkSpec(
        prompt=prompt.strip(),
        model=_parse_model_field(lineno, obj.get("model")),
        duration=_parse_duration_field(lineno, obj.get("duration")),
        aspect=_parse_aspect_field(lineno, obj.get("aspect")),
    )


def parse_chain_manifest(path: Path) -> list[ChainLinkSpec]:
    """Parse an ordered JSONL chain manifest into per-link specs.

    Args:
        path: The JSONL manifest file. One JSON object per line; blank lines and
            ``#``-prefixed comment lines are ignored.

    Returns:
        One :class:`~gflow_cli.chain.ChainLinkSpec` per link, in file order.

    Raises:
        ChainManifestError: The file has zero valid links, or a line is
            malformed (bad JSON, missing/empty ``prompt``, unknown field,
            unknown model alias, non-int ``duration``, or invalid ``aspect``).
            The message cites the offending line number.
    """
    text = path.read_text(encoding="utf-8")
    links: list[ChainLinkSpec] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        links.append(_parse_line(lineno, stripped))

    if not links:
        raise ChainManifestError(
            "chain manifest contains no links (every line was blank or a comment)",
        )
    return links
