"""Value object describing a tool applied to a generation prompt.

Lives in its own leaf module (stdlib-only imports) so the pure ``api.image`` /
``api.video`` request DTOs can carry an :class:`AppliedTool` for the recorder
without importing the heavier ``tools.runtime`` / ``tools.expander`` machinery.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from gflow_cli.tools.spec import ToolConfig, ToolSpec


@dataclass(frozen=True)
class AppliedTool:
    """Provenance of one tool applied to a prompt — recorded in
    ``operations.metadata_json.tool``.

    ``params`` is a sorted tuple of ``(key, value)`` option pairs (hashable, so
    this dataclass stays frozen/hashable). ``config_hash`` is a tamper-evidence
    digest of the tool's resolved :class:`~gflow_cli.tools.spec.ToolConfig`,
    paired with the hand-bumped ``version`` (council D7).
    """

    name: str
    version: str
    #: Effective model, or ``None`` when the gateway was left to choose it.
    model: str | None
    config_hash: str
    params: tuple[tuple[str, str], ...] = ()

    def params_dict(self) -> dict[str, str]:
        return dict(self.params)


def config_hash(config: ToolConfig) -> str:
    """Stable sha256 of a tool's resolved config (tamper-evidence)."""
    return hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()


def applied_tool_from_spec(spec: ToolSpec, options: dict[str, str]) -> AppliedTool:
    """Build an :class:`AppliedTool` snapshot from a resolved spec + run options.

    ``model`` records the *effective* model, resolved through the same
    precedence the expander uses, so provenance reflects what was actually
    requested rather than a TOML pin that may not exist. It is ``None`` when the
    gateway was left to choose, which is the honest record in that case.
    """
    from gflow_cli.config import get_settings
    from gflow_cli.tools.expander import resolve_model

    settings = get_settings()
    params = tuple(sorted((str(k), str(v)) for k, v in options.items()))
    return AppliedTool(
        name=spec.name,
        version=spec.version,
        model=resolve_model(spec.config.model, settings.llm_model, settings.llm_base_url),
        config_hash=config_hash(spec.config),
        params=params,
    )


class ToolInvocation(BaseModel):
    """One MCP-side tool request: ``{"name": str, "options": {k: v}}``.

    Validates agent-supplied input so a malformed ``tools`` array fails cleanly
    at the MCP boundary instead of as an uncaught ``TypeError`` once generation
    is wired. ``to_spec`` renders the CLI ``--tool name[:k=v,...]`` form so the
    MCP surface and the CLI share one tool-application path (council D3).
    """

    model_config = ConfigDict(frozen=True)
    name: str = Field(pattern=r"^[a-z0-9-]+$")
    options: dict[str, str] = Field(default_factory=dict)

    def to_spec(self) -> str:
        if not self.options:
            return self.name
        opts = ",".join(f"{k}={v}" for k, v in self.options.items())
        return f"{self.name}:{opts}"


def tool_specs_from_invocations(items: list[dict[str, Any]] | None) -> tuple[str, ...]:
    """Adapt an MCP ``tools`` array (``list[dict]``) to CLI ``--tool`` specs.

    Returns a tuple of ``name[:k=v,...]`` strings consumable by
    ``_cli_helpers.apply_tool_option``. Raises ``pydantic.ValidationError`` on a
    malformed item. Empty / ``None`` input → ``()``.
    """
    if not items:
        return ()
    return tuple(ToolInvocation.model_validate(item).to_spec() for item in items)
