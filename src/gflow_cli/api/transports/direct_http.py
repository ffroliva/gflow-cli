"""Direct HTTP transport for image generation on flow.google.com."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gflow_cli.api.image import Aspect

if TYPE_CHECKING:
    from gflow_cli.api.image import GenerateImageRequest


def direct_can_serve(request: GenerateImageRequest) -> bool:
    """Return True only for plain T2I requests supported by direct HTTP.

    Excludes reference images (refs, ref_paths), character entities,
    non-portrait aspect ratios, multi-image counts, Agent instructions,
    and character mentions (@).
    """
    if request.aspect is not Aspect.PORTRAIT:
        return False
    if request.count != 1:
        return False
    if bool(request.refs) or bool(request.ref_paths) or bool(request.reference_entities):
        return False
    if bool(request.instructions):
        return False
    if "@" in request.prompt:
        return False
    return True
