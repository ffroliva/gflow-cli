"""Value object describing a tool applied to a generation prompt.

Lives in its own leaf module (stdlib-only imports) so the pure ``api.image`` /
``api.video`` request DTOs can carry an :class:`AppliedTool` for the recorder
without importing the heavier ``tools.runtime`` / ``tools.expander`` machinery.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    model: str
    config_hash: str
    params: tuple[tuple[str, str], ...] = ()

    def params_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.params}


def config_hash(config: ToolConfig) -> str:
    """Stable sha256 of a tool's resolved config (tamper-evidence)."""
    return hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()


def applied_tool_from_spec(spec: ToolSpec, options: dict[str, str]) -> AppliedTool:
    """Build an :class:`AppliedTool` snapshot from a resolved spec + run options."""
    params = tuple(sorted((str(k), str(v)) for k, v in options.items()))
    return AppliedTool(
        name=spec.name,
        version=spec.version,
        model=spec.config.model,
        config_hash=config_hash(spec.config),
        params=params,
    )
