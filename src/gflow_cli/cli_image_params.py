"""Image-to-image request parameters for ``gflow image i2i``.

Extracted from :mod:`gflow_cli.cli_image` to keep that module under the size gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gflow_cli.api.image import GenerateImageRequest, ImageRef

if TYPE_CHECKING:
    from gflow_cli.api.image import AgentInstruction, Aspect, Model, ThinkingLevel
    from gflow_cli.config import UiMode
    from gflow_cli.tools.invocation import AppliedTool

I2I_PROJECT_TITLE = "gflow-cli i2i"


@dataclass(frozen=True)
class I2IParams:
    """Bundles image-generation options for :func:`_run_i2i`.

    Separating these from the profile/transport/output fields keeps the
    function signature below Sonar's 13-parameter limit (S107) while preserving
    every CLI option.
    """

    prompt: str
    classified_refs: list[ImageRef | Path]
    aspect: Aspect
    model: Model
    reference_entities: tuple[str, ...]
    reference_entity_names: tuple[str, ...]
    # Tool provenance (set when a --tool rewrote the prompt; recorded only).
    original_prompt: str | None = None
    tool: AppliedTool | None = None
    instructions: tuple[AgentInstruction, ...] | None = None
    ui_mode: UiMode | None = None
    thinking_level: ThinkingLevel | None = None


def i2i_request(params: I2IParams, uuid_refs: list[ImageRef]) -> GenerateImageRequest:
    return GenerateImageRequest(
        prompt=params.prompt,
        aspect=params.aspect,
        model=params.model,
        refs=tuple(uuid_refs),
        ref_paths=tuple(r for r in params.classified_refs if isinstance(r, Path)),
        reference_entities=params.reference_entities,
        reference_entity_names=params.reference_entity_names,
        original_prompt=params.original_prompt,
        tool=params.tool,
        instructions=params.instructions,
        ui_mode=params.ui_mode,
        thinking_level=params.thinking_level,
    )
