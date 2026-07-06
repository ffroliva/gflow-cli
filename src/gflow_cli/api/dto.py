"""Typed DTOs for Flow API requests/responses.

All frozen dataclasses — once constructed, instances are immutable and
hashable. Parsers (`*.from_response`) defensively read JSON dicts and
raise `ValueError` on missing/malformed fields rather than letting
KeyErrors leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from gflow_cli.errors import GFlowError


@dataclass(frozen=True)
class ProjectInfo:
    """A Flow project — owns assets, jobs, library entries."""

    project_id: str
    title: str

    @classmethod
    def from_create_response(cls, data: dict[str, Any]) -> ProjectInfo:
        """Parse `POST .../project.createProject` JSON.

        Wire shape:
          {result: {data: {json: {result: {projectId, projectInfo: {projectTitle}}}}}}
        """
        try:
            inner = data["result"]["data"]["json"]["result"]
            return cls(
                project_id=inner["projectId"],
                title=inner["projectInfo"]["projectTitle"],
            )
        except (KeyError, TypeError) as e:
            msg = f"unexpected createProject response shape: {e}"
            raise ValueError(msg) from e


@dataclass(frozen=True)
class AssetInfo:
    """A media asset (image or video) registered in a Flow project."""

    name: str  # asset UUID
    project_id: str
    workflow_id: str
    display_name: str
    width: int
    height: int

    @classmethod
    def from_upload_response(cls, data: dict[str, Any]) -> AssetInfo:
        """Parse `POST /v1/flow/uploadImage` JSON.

        Wire shape:
          {media: {name, projectId, workflowId, image: {dimensions: {width, height}}, ...},
           workflow: {metadata: {displayName}, ...}}
        """
        try:
            media = data["media"]
            dims = media.get("image", {}).get("dimensions", {})
            return cls(
                name=media["name"],
                project_id=media["projectId"],
                workflow_id=media["workflowId"],
                display_name=data.get("workflow", {}).get("metadata", {}).get("displayName", ""),
                width=int(dims.get("width", 0)),
                height=int(dims.get("height", 0)),
            )
        except (KeyError, TypeError) as e:
            msg = f"unexpected uploadImage response shape: {e}"
            raise ValueError(msg) from e


@dataclass(frozen=True)
class UploadedImage:
    """Result of `POST /v1/flow/uploadImage` — image-MVP shape.

    A trimmed companion to `AssetInfo` that exposes the fields image
    workflows actually need: the asset id, its workflow id, and the
    pixel dimensions packed as a `(width, height)` tuple.
    """

    media_name: str  # asset UUID — the same id used for imageInputs.name
    workflow_id: str
    dimensions: tuple[int, int]  # (width, height)

    @classmethod
    def from_upload_response(cls, data: dict[str, Any]) -> UploadedImage:
        """Parse `POST /v1/flow/uploadImage` JSON.

        Wire shape:
          {media: {name, workflowId, image: {dimensions: {width, height}}, ...}, ...}
        """
        try:
            media = data["media"]
            dims = media.get("image", {}).get("dimensions", {})
            return cls(
                media_name=media["name"],
                workflow_id=media["workflowId"],
                dimensions=(int(dims.get("width", 0)), int(dims.get("height", 0))),
            )
        except (KeyError, TypeError) as e:
            msg = f"unexpected uploadImage response shape: {e}"
            raise ValueError(msg) from e


@dataclass(frozen=True)
class GeneratedImage:
    """One image produced by `flowMedia:batchGenerateImages`.

    Captured wire shape (per media[] item):
      {name, workflowId,
       image: {generatedImage: {seed, prompt, modelNameType, workflowId,
                                fifeUrl, aspectRatio, ...},
               dimensions: {width, height}}}
    """

    media_name: str  # asset UUID — Flow's id for this generated image
    workflow_id: str
    seed: int
    prompt: str
    model_name_type: str  # e.g. "NARWHAL"
    aspect_ratio: str  # e.g. "IMAGE_ASPECT_RATIO_PORTRAIT"
    fife_url: str  # CDN URL — usually expires after ~6 hours
    dimensions: tuple[int, int]  # (width, height)
    media_generation_id: str | None = None
    display_name: str | None = None

    @property
    def is_signed_url(self) -> bool:
        """True when the fife URL carries a `Signature=` query parameter."""
        return "Signature=" in self.fife_url

    @classmethod
    def from_response_item(cls, item: dict[str, Any]) -> GeneratedImage:
        """Parse one element of the `media[]` array in a batchGenerateImages response."""
        try:
            image = item["image"]
            generated = image["generatedImage"]
            dims = image["dimensions"]
            return cls(
                media_name=item["name"],
                workflow_id=item["workflowId"],
                seed=int(generated["seed"]),
                prompt=generated["prompt"],
                model_name_type=generated["modelNameType"],
                aspect_ratio=generated["aspectRatio"],
                fife_url=generated["fifeUrl"],
                dimensions=(int(dims["width"]), int(dims["height"])),
                media_generation_id=generated.get("mediaGenerationId"),
            )
        except (KeyError, TypeError) as e:
            msg = f"unexpected batchGenerateImages media item shape: {e}"
            raise ValueError(msg) from e

    @classmethod
    def from_response_dict(cls, data: dict[str, Any]) -> list[GeneratedImage]:
        """Parse the full `flowMedia:batchGenerateImages` response into a list.

        Wire shape:
          {media: [<item>, ...], workflows: [...]}
        Always returns a list — even when the API returns a single entry.
        """
        try:
            media = data["media"]
        except (KeyError, TypeError) as e:
            msg = f"unexpected batchGenerateImages response shape: {e}"
            raise ValueError(msg) from e
        if not isinstance(media, list):
            msg = "unexpected batchGenerateImages response shape: media is not a list"
            raise ValueError(msg)
        workflows = data.get("workflows", [])
        workflow_map: dict[str, str] = {}
        if isinstance(workflows, list):
            for w in workflows:
                if not isinstance(w, dict):
                    continue
                w_id = w.get("name")
                if isinstance(w_id, str):
                    metadata = w.get("metadata")
                    if isinstance(metadata, dict):
                        display_name = metadata.get("displayName")
                        if isinstance(display_name, str):
                            workflow_map[w_id] = display_name

        items = cast("list[dict[str, Any]]", media)
        result: list[GeneratedImage] = []
        for item in items:
            img = cls.from_response_item(item)
            w_id = img.workflow_id
            if w_id in workflow_map:
                from dataclasses import replace
                img = replace(img, display_name=workflow_map[w_id])
            result.append(img)
        return result


@dataclass(frozen=True)
class BatchSubmissionResult:
    """Per-prompt outcome from `UiAutomationTransport.generate_images_batch`.

    `project_id` is identical across all results of a single batch (the
    shared Flow project the editor stayed mounted on). `prompt_idx` is the
    0-based submission position. `prompt_hash` is the SHA-256 prefix used
    consistently across image_batch's structlog events.
    """

    status: Literal["ok", "fail"]
    project_id: str
    prompt_idx: int
    prompt_hash: str
    images: tuple[GeneratedImage, ...] = ()
    error: GFlowError | None = None
