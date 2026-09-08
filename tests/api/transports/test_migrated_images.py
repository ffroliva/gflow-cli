"""Image generation on Flow's migrated Angular composer (#639)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import (
    AgentInstruction,
    Aspect,
    GenerateImageRequest,
    ImageRef,
    Model,
)
from gflow_cli.errors import WireFormatError

MEDIA = "11111111-1111-4111-8111-111111111111"
WORKFLOW = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
REFERENCE = "44444444-4444-4444-8444-444444444444"
URL = f"https://flow-content.google/image/{MEDIA}?Expires=1&Signature=secret"


def image_payload(*, reference: str | None = None) -> list[Any]:
    reference_bits: list[Any] = [] if reference is None else [[[None, 1, reference]]]
    media = [
        MEDIA,
        None,
        WORKFLOW,
        None,
        None,
        None,
        [
            [
                None,
                12345,
                None,
                None,
                None,
                None,
                1,
                "a blue cup",
                25,
                None,
                None,
                WORKFLOW,
                None,
                URL,
                3,
                [None, None, [["a blue cup"]], reference_bits],
                None,
                MEDIA,
            ],
            None,
            [1376, 768],
        ],
    ]
    workflow = [
        WORKFLOW,
        None,
        None,
        ["Blue cup", [1, 2], None, None, MEDIA, "batch", [3, 4]],
        PROJECT,
    ]
    return [[[media]], [[workflow]]]


def _request(**changes: Any) -> GenerateImageRequest:
    values: dict[str, Any] = {"prompt": "a blue cup"}
    values.update(changes)
    return GenerateImageRequest(**values)


def test_image_records_parse_the_measured_ogiz0b_shape() -> None:
    from gflow_cli.api.transports.batchexecute import image_records

    records = image_records("ogiZ0b", image_payload(reference=REFERENCE))
    assert len(records) == 1
    record = records[0]
    assert record.media_id == MEDIA
    assert record.workflow_id == WORKFLOW
    assert record.project_id == PROJECT
    assert record.seed == 12345
    assert record.prompt == "a blue cup"
    assert record.image_url == URL
    assert record.dimensions == (1376, 768)
    assert record.display_name == "Blue cup"
    assert record.reference_ids == (REFERENCE,)


def test_image_records_reject_unknown_envelopes_without_leaking_tokens() -> None:
    from gflow_cli.api.transports.batchexecute import image_records

    token = "A" * 180
    with pytest.raises(WireFormatError) as info:
        image_records("ogiZ0b", ["changed", token])
    assert token not in str(info.value)
    assert info.value.route == "batchexecute:ogiZ0b"


def test_migrated_image_capability_is_narrow_and_pre_submit() -> None:
    from gflow_cli.api.transports.migrated_composer import migrated_image_can_serve

    local = Path("reference.png")
    assert migrated_image_can_serve(_request(), PROJECT)
    assert migrated_image_can_serve(_request(ref_paths=(local,)), PROJECT)
    assert not migrated_image_can_serve(_request(), None)
    assert not migrated_image_can_serve(
        _request(refs=(ImageRef(REFERENCE),)),
        PROJECT,
    )
    assert not migrated_image_can_serve(
        _request(reference_entities=("entity-1",), reference_entity_names=("Hero",)),
        PROJECT,
    )
    assert not migrated_image_can_serve(
        _request(instructions=(AgentInstruction(text="keep it blue"),)),
        PROJECT,
    )
    assert not migrated_image_can_serve(_request(model=Model.IMAGEN_3_5), PROJECT)


def test_image_submit_body_requires_every_uploaded_reference() -> None:
    from gflow_cli.api.transports.migrated_composer import _image_body_problem

    body = f'[["ogiZ0b", "GEM_PIX_2 {REFERENCE}"]]'
    assert _image_body_problem(body, (REFERENCE,)) is None
    problem = _image_body_problem(body, (REFERENCE, MEDIA))
    assert problem is not None
    assert MEDIA in problem


def test_nano_banana_2_does_not_match_the_lite_sibling() -> None:
    from gflow_cli.api.transports.migrated_composer import IMAGE_MODEL_MENU_MATCHERS

    matcher = IMAGE_MODEL_MENU_MATCHERS[Model.NARWHAL]
    assert matcher.matches("🍌 Nano Banana 2")
    assert not matcher.matches("🍌 Nano Banana 2 Lite")


class _PageOwnedImageTransport:
    def __init__(self, owned: bool) -> None:
        self.owned = owned
        self.request: GenerateImageRequest | None = None

    def uses_page_owned_image_recaptcha(
        self, project_id: str, request: GenerateImageRequest
    ) -> bool:
        return self.owned

    async def generate_images(self, **kwargs: Any) -> list[GeneratedImage]:
        self.request = kwargs["request"]
        return [
            GeneratedImage(
                media_name=MEDIA,
                workflow_id=WORKFLOW,
                seed=1,
                prompt="a blue cup",
                model_name_type="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                fife_url=URL,
                dimensions=(1376, 768),
            )
        ]


async def test_client_skips_legacy_mint_when_the_page_owns_image_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _PageOwnedImageTransport(owned=True)
    client = FlowApiClient.__new__(FlowApiClient)
    client.transport = transport  # type: ignore[assignment]
    mint = AsyncMock(side_effect=AssertionError("legacy mint must not run"))
    monkeypatch.setattr(client, "_mint_recaptcha_token", mint)

    images = await client._drive_images_generation(  # noqa: SLF001
        project_id=PROJECT,
        req=_request(),
        recaptcha_action="imageGeneration",
    )

    assert images[0].media_name == MEDIA
    assert transport.request is not None
    assert transport.request.recaptcha_token == ""
    mint.assert_not_awaited()


async def test_client_keeps_legacy_mint_for_other_image_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _PageOwnedImageTransport(owned=False)
    client = FlowApiClient.__new__(FlowApiClient)
    client.transport = transport  # type: ignore[assignment]
    mint = AsyncMock(return_value="minted")
    monkeypatch.setattr(client, "_mint_recaptcha_token", mint)

    await client._drive_images_generation(  # noqa: SLF001
        project_id=PROJECT,
        req=_request(aspect=Aspect.PORTRAIT),
        recaptcha_action="imageGeneration",
    )

    assert transport.request is not None
    assert transport.request.recaptcha_token == "minted"
    mint.assert_awaited_once_with("imageGeneration")


async def test_migrated_image_route_dispatches_before_the_labs_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "auto")
    reset_settings()
    transport = UiAutomationTransport()
    page = MagicMock()
    page.url = f"https://flow.google.com/project/{PROJECT}"

    async def goto(url: str, **_: Any) -> None:
        page.url = url

    page.goto = goto
    transport._page = page  # noqa: SLF001
    transport._setup_done = True  # noqa: SLF001
    generated = [
        GeneratedImage(
            media_name=MEDIA,
            workflow_id=WORKFLOW,
            seed=1,
            prompt="a blue cup",
            model_name_type=Model.NARWHAL.value,
            aspect_ratio=Aspect.PORTRAIT.value,
            fife_url=URL,
            dimensions=(768, 1376),
        )
    ]
    run_images = AsyncMock(return_value=generated)
    monkeypatch.setattr("gflow_cli.api.transports.migrated_composer.run_images", run_images)

    result = await transport.generate_images(project_id=PROJECT, request=_request())

    assert result == generated
    run_images.assert_awaited_once()
    assert page.url == "about:blank"


def test_page_owned_recaptcha_is_only_selected_for_the_migrated_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "auto")
    reset_settings()
    transport = UiAutomationTransport()
    page = MagicMock()
    transport._page = page  # noqa: SLF001

    page.url = f"https://labs.google/fx/en/tools/flow/project/{PROJECT}"
    assert not transport.uses_page_owned_image_recaptcha(PROJECT, _request())
    page.url = f"https://flow.google.com/project/{PROJECT}"
    assert transport.uses_page_owned_image_recaptcha(PROJECT, _request())
