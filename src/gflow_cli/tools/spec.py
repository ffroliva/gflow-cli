"""Pydantic models for tool definitions (loaded from packaged TOML)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DomainMode(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    vocabulary: str


class ToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: str = "gemini-2.5-flash"
    system_template: str
    banned_keywords: tuple[str, ...] = ()
    domains: tuple[DomainMode, ...] = ()
    max_input_chars: int = 4000
    max_output_chars: int = 3500

    def domain(self, name: str | None) -> DomainMode | None:
        if name is None:
            return None
        lowered = name.lower()
        return next((d for d in self.domains if d.name.lower() == lowered), None)


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    # Slug only — used as a registry key, an error-message token, and (once the
    # My-Tools loader activates) a potential filename. Constrain now. (council D3)
    name: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    description: str
    category: Literal["image", "video", "both"]
    author: str = "gflow"
    version: str
    requires_env: tuple[str, ...] = ()
    options_schema: dict[str, str] = {}
    config: ToolConfig

    def supports(self, category: Literal["image", "video"]) -> bool:
        return self.category in (category, "both")
