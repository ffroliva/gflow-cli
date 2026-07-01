"""Deterministic banned-keyword cleanup for tool outputs.

The Creative Director instruction already tells Gemini to avoid these
Stable-Diffusion-era terms (they degrade Nano Banana / Imagen output), but the
model is not guaranteed to comply — so we also strip them post-hoc for CLI
determinism. Source list: banana-claude references/prompt-engineering.md.
"""

from __future__ import annotations

import re
from functools import cache

BANNED_KEYWORDS: tuple[str, ...] = (
    "8k",
    "4k",
    "ultra hd",
    "high resolution",
    "masterpiece",
    "highly detailed",
    "ultra detailed",
    "trending on artstation",
    "hyperrealistic",
    "ultra realistic",
    "photorealistic",
    "best quality",
    "award winning",
)

# Precompiled separator-cleanup patterns (module-level constants, mirroring the
# _PATTERNS discipline so inline re.compile() calls are never paid per call).
_RE_TRAILING_SPACE_COMMA: re.Pattern[str] = re.compile(r"\s+,")
_RE_DOUBLE_COMMA: re.Pattern[str] = re.compile(r"(?:,\s*){2,}")
_RE_MULTI_SPACE: re.Pattern[str] = re.compile(r"\s{2,}")


@cache
def _get_patterns(keywords: tuple[str, ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Build ``(keyword, compiled-pattern)`` pairs for *keywords*.

    Longest phrases come first so "ultra detailed" is matched before "ultra".
    Cached per unique keyword tuple — patterns are never recompiled for the same
    keyword set across calls.
    """
    return tuple(
        (kw, re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
        for kw in sorted(keywords, key=len, reverse=True)
    )


# Longest phrases first so "ultra detailed" is matched before "ultra".
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = _get_patterns(BANNED_KEYWORDS)


def strip_banned_keywords(
    text: str,
    keywords: tuple[str, ...] = BANNED_KEYWORDS,
) -> tuple[str, list[str]]:
    """Remove banned keywords (whole-word, case-insensitive). Returns
    ``(cleaned_text, removed_terms)``. Never raises.

    *keywords* defaults to the module-global :data:`BANNED_KEYWORDS` list.
    Pass a custom tuple (e.g. ``spec.config.banned_keywords``) to restrict
    stripping to the per-tool list.  Patterns are cached so repeated calls
    with the same keyword tuple pay zero recompilation cost.
    """
    removed: list[str] = []
    cleaned = text
    for keyword, pattern in _get_patterns(keywords):
        if pattern.search(cleaned):
            removed.append(keyword)
            cleaned = pattern.sub("", cleaned)
    # Tidy separators left behind by removals.
    # 1. "word ," → "word,"  (space before comma)
    cleaned = _RE_TRAILING_SPACE_COMMA.sub(",", cleaned)
    # 2. Two or more consecutive commas (with optional whitespace) → single ", "
    #    e.g. ",," / ", ," / ",  ," / ", , ,"  — handles 3+ adjacent removals.
    cleaned = _RE_DOUBLE_COMMA.sub(", ", cleaned)
    # 3. Collapse multiple spaces, strip leading/trailing separators.
    cleaned = _RE_MULTI_SPACE.sub(" ", cleaned).strip(" ,")
    return cleaned, removed
