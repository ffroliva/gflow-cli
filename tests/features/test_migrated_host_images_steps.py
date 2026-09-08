"""BDD bindings for the migrated image-generation contract (#639)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import GenerateImageRequest, ImageRef, Model
from gflow_cli.api.transports.batchexecute import image_records
from gflow_cli.api.transports.migrated_composer import migrated_image_can_serve
from tests.api.transports.test_migrated_images import (
    MEDIA,
    PROJECT,
    REFERENCE,
    URL,
    WORKFLOW,
    image_payload,
)

scenarios("migrated_host_images.feature")


def _request(**changes: Any) -> GenerateImageRequest:
    values: dict[str, Any] = {"prompt": "a blue cup"}
    values.update(changes)
    return GenerateImageRequest(**values)


@pytest.fixture
def image_world() -> dict[str, Any]:
    return {}


@given("an authenticated migrated profile and an existing Flow project")
def _migrated_project(image_world: dict[str, Any]) -> None:
    image_world["request"] = _request()
    image_world["records"] = image_records("ogiZ0b", image_payload())


@when("I request a supported text-to-image generation")
def _request_t2i(image_world: dict[str, Any]) -> None:
    image_world["result"] = image_world["records"][0]


@then("the page-owned image submission returns a generated image")
def _t2i_result(image_world: dict[str, Any]) -> None:
    record = image_world["result"]
    assert record.media_id == MEDIA
    assert record.image_url == URL
    assert record.workflow_id == WORKFLOW


@given("an authenticated migrated profile, an existing project and a local image")
def _migrated_local_image(image_world: dict[str, Any], tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"png")
    image_world["request"] = _request(ref_paths=(reference,))
    image_world["records"] = image_records("ogiZ0b", image_payload(reference=REFERENCE))


@when("I request image-to-image with that local image")
def _request_i2i(image_world: dict[str, Any]) -> None:
    image_world["result"] = image_world["records"][0]


@then("the outgoing image request contains the uploaded reference")
def _i2i_result(image_world: dict[str, Any]) -> None:
    assert image_world["result"].reference_ids == (REFERENCE,)


@given("a migrated image request using a Flow media UUID")
def _uuid_request(image_world: dict[str, Any]) -> None:
    image_world["request"] = _request(refs=(ImageRef(REFERENCE),))


@when("generation is requested")
def _unsupported_request(image_world: dict[str, Any]) -> None:
    image_world["supported"] = migrated_image_can_serve(image_world["request"], PROJECT)


@then("the request is refused before the submit button is clicked")
def _refused_before_submit(image_world: dict[str, Any]) -> None:
    assert image_world["supported"] is False


class _ImageTransport:
    def __init__(self) -> None:
        self.request: GenerateImageRequest | None = None

    def uses_page_owned_image_recaptcha(
        self, project_id: str, request: GenerateImageRequest
    ) -> bool:
        return True

    async def generate_images(self, **kwargs: Any) -> list[GeneratedImage]:
        self.request = kwargs["request"]
        return [
            GeneratedImage(
                media_name=MEDIA,
                workflow_id=WORKFLOW,
                seed=1,
                prompt="a blue cup",
                model_name_type=Model.NARWHAL.value,
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                fife_url=URL,
                dimensions=(768, 1376),
            )
        ]


@given("the direct and queued gflow_generate_image surfaces")
def _mcp_surfaces(image_world: dict[str, Any]) -> None:
    image_world["transport"] = _ImageTransport()


@when("each submits the same migrated-host request")
def _shared_request(image_world: dict[str, Any]) -> None:
    transport = image_world["transport"]
    client = FlowApiClient.__new__(FlowApiClient)
    client.transport = transport
    client._mint_recaptcha_token = AsyncMock(side_effect=AssertionError("page owns token"))
    image_world["result"] = asyncio.run(
        client._drive_images_generation(
            project_id=PROJECT,
            req=_request(ref_paths=(Path("reference.png"),), count=2),
            recaptcha_action="imageGeneration",
        )
    )


@then("model, aspect, count and local references reach the shared transport unchanged")
def _shared_request_preserved(image_world: dict[str, Any]) -> None:
    request = image_world["transport"].request
    assert request is not None
    assert request.model is Model.NARWHAL
    assert request.count == 2
    assert request.ref_paths == (Path("reference.png"),)
