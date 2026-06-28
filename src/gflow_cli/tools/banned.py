"""Deterministic banned-keyword cleanup for tool outputs.

The Creative Director instruction already tells Gemini to avoid these
Stable-Diffusion-era terms (they degrade Nano Banana / Imagen output), but the
model is not guaranteed to comply — so we also strip them post-hoc for CLI
determinism. Source list: banana-claude references/prompt-engineering.md.
"""

from __future__ import annotations

import re

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

# Longest phrases first so "ultra detailed" is matched before "ultra".
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kw, re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
    for kw in sorted(BANNED_KEYWORDS, key=len, reverse=True)
)


def strip_banned_keywords(text: str) -> tuple[str, list[str]]:
    """Remove banned keywords (whole-word, case-insensitive). Returns
    ``(cleaned_text, removed_terms)``. Never raises."""
    removed: list[str] = []
    cleaned = text
    for keyword, pattern in _PATTERNS:
        if pattern.search(cleaned):
            removed.append(keyword)
            cleaned = pattern.sub("", cleaned)
    # Tidy separators left behind: ", ," / double spaces / leading punctuation.
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
    return cleaned, removed
