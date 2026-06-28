"""Apply a resolved tool to a prompt (build instruction → expand → de-ban)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import structlog

from gflow_cli.config import get_settings
from gflow_cli.tools.banned import strip_banned_keywords
from gflow_cli.tools.expander import ExpansionResult, PromptExpander

if TYPE_CHECKING:
    from gflow_cli.tools.spec import ToolConfig, ToolSpec

log = structlog.get_logger(__name__)


def build_instruction(config: ToolConfig, style: str | None) -> str:
    # The TOML system_template carries ONLY the formula (no trailing marker);
    # build_instruction appends the user-prompt marker EXACTLY ONCE, after any
    # domain vocabulary — so the domain and no-domain branches never duplicate it.
    parts = [config.system_template.rstrip()]
    domain = config.domain(style)
    if style is not None and domain is None:
        log.warning("tool_unknown_style", style=style)
    if domain is not None:
        parts.append(f"Apply this {domain.name} style vocabulary: {domain.vocabulary}")
    return "\n\n".join(parts) + "\n\nUser prompt: "


def apply_tool(
    spec: ToolSpec,
    prompt: str,
    options: Mapping[str, str],
    *,
    expander: PromptExpander | None = None,
) -> ExpansionResult:
    style = options.get("style")
    instruction = build_instruction(spec.config, style)
    if expander is None:
        settings = get_settings()
        expander = PromptExpander(
            settings.gemini_api_key,
            model=spec.config.model,
            system_instruction=instruction,
            max_input_chars=spec.config.max_input_chars,
            max_output_chars=spec.config.max_output_chars,
        )
    result = expander.expand(prompt)
    if not result.was_expanded:
        return result
    cleaned, removed = strip_banned_keywords(result.expanded)
    if removed:
        log.info("tool_banned_keywords_stripped", tool=spec.name, removed=removed)
    return ExpansionResult(original=result.original, expanded=cleaned, was_expanded=True)
