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


def test_image_records_reject_unknown_envelopes_without_leaking_tokens() -> None:
    from gflow_cli.api.transports.batchexecute import image_records

    token = "A" * 180
    with pytest.raises(WireFormatError) as info:
        image_records("ogiZ0b", ["changed", token])
    assert token not in str(info.value)
    assert info.value.route == "batchexecute:ogiZ0b"


def test_unported_image_forms_are_named_and_refused_pre_submit() -> None:
    from gflow_cli.api.transports.migrated_composer import _unported_image_form

    local = Path("reference.png")
    assert _unported_image_form(_request()) is None
    assert _unported_image_form(_request(ref_paths=(local,))) is None
    assert _unported_image_form(_request(refs=(ImageRef(REFERENCE),))) == (
        "a reference given by Flow media UUID"
    )
    assert (
        _unported_image_form(
            _request(reference_entities=("entity-1",), reference_entity_names=("Hero",)),
        )
        == "character references"
    )
    assert (
        _unported_image_form(_request(instructions=(AgentInstruction(text="keep it blue"),)))
        == "Agent instructions"
    )
    assert _unported_image_form(_request(model=Model.IMAGEN_3_5)) is not None


def test_only_the_measured_aspects_are_offered_and_three_four_is_refused() -> None:
    """3:4 has no radio in the enumerated aspect row, so it must be refused as an
    unported form (exit 36) — never left to miss its selector and surface as
    UiSelectorDriftError (exit 23), which tells the user to file a frontend bug
    about a frontend that is behaving correctly.
    """
    from gflow_cli.api.transports.migrated_composer import (
        IMAGE_ASPECT_LIGATURE_MEASURED,
        _unported_image_form,
    )

    for aspect in IMAGE_ASPECT_LIGATURE_MEASURED:
        assert _unported_image_form(_request(aspect=aspect)) is None, aspect
    assert Aspect.PORTRAIT_THREE_FOUR not in IMAGE_ASPECT_LIGATURE_MEASURED
    refusal = _unported_image_form(_request(aspect=Aspect.PORTRAIT_THREE_FOUR))
    assert refusal is not None
    assert "aspect" in refusal


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

    def uses_page_owned_image_recaptcha(self) -> bool:
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
    assert not transport.uses_page_owned_image_recaptcha()
    page.url = f"https://flow.google.com/project/{PROJECT}"
    assert transport.uses_page_owned_image_recaptcha()


def test_page_owned_recaptcha_survives_the_post_run_page_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SECOND image in one client session must still skip the labs mint.

    Every migrated image run ends by parking the page on ``about:blank``
    (``_generate_images_locked``), which routes as ``labs``. Deriving the
    capability from ``page.url`` alone therefore answered ``False`` on the next
    call, sending it back to ``_mint_recaptcha_token`` on the pooled bootstrap
    page — the exact ``RecaptchaError`` of #673 that the page-owned mint exists
    to prevent. Reachable from ``gflow image batch``, which runs every prompt
    through one ``FlowApiClient`` (``image_batch.py::_run_sequential``), so no
    single-image test could see it.
    """
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "auto")
    reset_settings()
    transport = UiAutomationTransport()
    page = MagicMock()
    transport._page = page  # noqa: SLF001

    page.url = f"https://flow.google.com/project/{PROJECT}"
    assert transport.uses_page_owned_image_recaptcha()

    page.url = "about:blank"
    assert transport.uses_page_owned_image_recaptcha(), (
        "the parked page must not read as a labs account on the next generation"
    )


def test_a_never_migrated_transport_does_not_latch_into_the_page_owned_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The latch is one-way, but it must never arm without evidence — otherwise a
    labs account would stop minting the token it genuinely needs.
    """
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "auto")
    reset_settings()
    transport = UiAutomationTransport()
    page = MagicMock()
    transport._page = page  # noqa: SLF001

    for url in ("about:blank", f"https://labs.google/fx/en/tools/flow/project/{PROJECT}"):
        page.url = url
        assert not transport.uses_page_owned_image_recaptcha(), url


async def test_image_batch_is_refused_on_the_migrated_host_before_any_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`generate_images_batch` drives labs selectors only. Without this guard it ran
    them against flow.google.com and died as UiSelectorDriftError (exit 23) — telling
    the user to file a frontend-drift bug about a frontend that was working. The
    single-image path had a guard from the start; the batch path had none, and the
    parity gate could not see it because both are behind the same leaf command.
    """
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.config import reset_settings
    from gflow_cli.errors import FlowHostMigratedError

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "auto")
    reset_settings()
    transport = UiAutomationTransport()
    page = MagicMock()
    page.url = f"https://flow.google.com/project/{PROJECT}"
    transport._page = page  # noqa: SLF001
    transport._setup_done = True  # noqa: SLF001

    with pytest.raises(FlowHostMigratedError):
        await transport.generate_images_batch(prompts=[_request()], jitter_range=(0.0, 0.0))
