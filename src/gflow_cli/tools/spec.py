"""Pydantic models for tool definitions (loaded from packaged TOML)."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A domain may target image generation, video generation, or both. "both" is
# the permissive default so a domain with no explicit category matches either.
DomainCategory = Literal["image", "video", "both"]


class DomainMode(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    vocabulary: str
    # Which generation category this domain applies to. Lets an image style and
    # a video style share a name (e.g. "product") without colliding — the
    # ``ToolConfig.domain()`` lookup disambiguates by category. (review fold-in)
    category: DomainCategory = "both"


class ToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: str = "gemini-2.5-flash"
    system_template: str
    banned_keywords: tuple[str, ...] = ()
    domains: tuple[DomainMode, ...] = ()
    max_input_chars: int = 4000
    max_output_chars: int = 3500

    def domain(self, name: str | None, category: DomainCategory | None = None) -> DomainMode | None:
        """Resolve a domain by *name*, optionally gated to a *category*.

        With ``category=None`` the first name match wins (category-agnostic,
        used by the standalone ``gflow tools run`` preview). With a concrete
        ``"image"``/``"video"`` category, only a domain that targets that
        category (or ``"both"``) matches — so an image style is invisible to a
        video generation and vice versa.
        """
        if name is None:
            return None
        lowered = name.lower()
        for d in self.domains:
            if d.name.lower() != lowered:
                continue
            if category is None or d.category in (category, "both"):
                return d
        return None


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
    options_schema: Mapping[str, str] = Field(default_factory=dict)
    config: ToolConfig

    @field_validator("options_schema", mode="after")
    @classmethod
    def _freeze_options_schema(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        # The model is frozen, but a plain dict attribute would still be mutable
        # in place. Wrap it read-only so a tool's declared schema is truly
        # immutable post-load. (review fold-in)
        return MappingProxyType(dict(value))

    def supports(self, category: Literal["image", "video"]) -> bool:
        return self.category in (category, "both")
