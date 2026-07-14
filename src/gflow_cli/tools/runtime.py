"""Apply a resolved tool to a prompt (build instruction → expand → de-ban)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog

from gflow_cli.config import get_settings
from gflow_cli.tools.banned import strip_banned_keywords
from gflow_cli.tools.expander import ExpansionResult, PromptExpander

if TYPE_CHECKING:
    from gflow_cli.tools.spec import DomainCategory, ToolConfig, ToolSpec

log = structlog.get_logger(__name__)

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_CLAUDE_VIDEO_DIR = "C:/development/github/claude-video"


def _is_url(s: str) -> bool:
    if s.startswith("-"):
        return False
    parsed = urlparse(s)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _is_video_file(s: str) -> bool:
    try:
        p = Path(s)
        return p.is_file() and p.suffix.lower() in VIDEO_EXTS
    except Exception:
        return False


def _is_image_file(s: str) -> bool:
    try:
        p = Path(s)
        return p.is_file() and p.suffix.lower() in IMAGE_EXTS
    except Exception:
        return False


def _get_clean_name(prompt: str) -> str:
    if _is_url(prompt):
        parsed = urlparse(prompt)
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts:
            # e.g., for /reel/Dag0kcwMrX0/, return Dag0kcwMrX0
            return path_parts[-1]
        return "url_video"
    else:
        return Path(prompt).stem


def build_instruction(
    config: ToolConfig,
    style: str | None,
    category: DomainCategory | None = None,
) -> str:
    # The TOML system_template carries ONLY the formula (no trailing marker);
    # build_instruction appends the user-prompt marker EXACTLY ONCE, after any
    # domain vocabulary — so the domain and no-domain branches never duplicate it.
    # ``category`` gates which same-named domain (image vs video) is selected.
    parts = [config.system_template.rstrip()]
    domain = config.domain(style, category)
    if style is not None and domain is None:
        log.warning("tool_unknown_style", style=style, category=category)
    if domain is not None:
        parts.append(f"Apply this {domain.name} style vocabulary: {domain.vocabulary}")
    return "\n\n".join(parts) + "\n\nUser prompt: "


def _collect_frames(prompt: str) -> list[str]:
    """Return frame image paths from a video/URL prompt via watch.py, or [] on failure."""
    watch_py = Path(DEFAULT_CLAUDE_VIDEO_DIR) / "scripts" / "watch.py"
    if not watch_py.exists():
        log.warning(
            "watch_py_not_found",
            path=str(watch_py),
            reason="falling back to text-only reverse engineering",
        )
        return []

    clean_name = _get_clean_name(prompt)
    out_dir = Path("tmp/watch") / clean_name
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(watch_py), prompt, "--no-whisper", "--out-dir", str(out_dir)]
    log.info("running_watch_py", cmd=cmd)
    log.info("saving_collateral_under", path=str(out_dir))

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)  # noqa: S603
    if res.returncode != 0:
        log.warning("watch_py_failed", code=res.returncode, stderr=res.stderr)
        return []

    frames_dir = out_dir / "frames"
    if not frames_dir.exists():
        return []

    frames = sorted(frames_dir.glob("*.jpg"))
    if len(frames) > 5:
        frames = [frames[int(i * len(frames) / 5)] for i in range(5)]
    image_paths = [str(f.resolve()) for f in frames]
    log.info("extracted_frames_for_analysis", count=len(image_paths))
    return image_paths


def _apply_multimodal_reverse_engineering(
    spec: ToolSpec,
    prompt: str,
    expander: PromptExpander,
) -> ExpansionResult | None:
    """Attempt multimodal reverse engineering; return result or None to fall through."""
    log.info("multimodal_reverse_engineering_detected", prompt=prompt)
    log.info("engine_inspired_by_claude_video_watch_skill")
    try:
        if _is_image_file(prompt):
            image_paths: list[str] = [str(Path(prompt).resolve())]
        else:
            image_paths = _collect_frames(prompt)

        if not image_paths:
            return None

        result = expander.expand_multimodal(prompt, image_paths)
        if not result.was_expanded:
            return None

        cleaned, removed = strip_banned_keywords(result.expanded, spec.config.banned_keywords)
        if removed:
            log.info("tool_banned_keywords_stripped", tool=spec.name, removed=removed)
        return ExpansionResult(original=result.original, expanded=cleaned, was_expanded=True)
    except Exception as e:  # noqa: BLE001
        log.warning("multimodal_reverse_engineering_error", error=str(e))
        return None


def apply_tool(
    spec: ToolSpec,
    prompt: str,
    options: Mapping[str, str],
    *,
    category: DomainCategory | None = None,
    expander: PromptExpander | None = None,
) -> ExpansionResult:
    style = options.get("style")
    instruction = build_instruction(spec.config, style, category)
    if expander is None:
        settings = get_settings()
        expander = PromptExpander(
            settings.gemini_api_key,
            model=spec.config.model,
            system_instruction=instruction,
            max_input_chars=spec.config.max_input_chars,
            max_output_chars=spec.config.max_output_chars,
        )

    # 1. Attempt multimodal reverse engineering for qualifying inputs
    if spec.name == "reverse-engineer" and (
        _is_url(prompt) or _is_video_file(prompt) or _is_image_file(prompt)
    ):
        result = _apply_multimodal_reverse_engineering(spec, prompt, expander)
        if result is not None:
            return result

    # 2. Fall back to standard text-only expansion
    result = expander.expand(prompt)
    if not result.was_expanded:
        return result
    cleaned, removed = strip_banned_keywords(result.expanded, spec.config.banned_keywords)
    if removed:
        log.info("tool_banned_keywords_stripped", tool=spec.name, removed=removed)
    return ExpansionResult(original=result.original, expanded=cleaned, was_expanded=True)
