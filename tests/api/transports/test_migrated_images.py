"""Image generation on Flow's migrated Angular composer (#639)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

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


def test_every_image_aspect_flow_renders_is_offered() -> None:
    """All five of gflow's image aspects are served on flow.google.com.

    The 2026-09-08 enumeration found four radios and 3:4 was refused (exit 36). The
    2026-09-17 re-enumeration (scripts/dev/spike_migrated_aspect_radios.py) found five:
    ``crop_16_9, crop_landscape, crop_square, crop_portrait, crop_9_16``.
    """
    from gflow_cli.api.transports.migrated_composer import (
        IMAGE_ASPECT_LIGATURE,
        IMAGE_ASPECT_LIGATURE_MEASURED,
        _unported_image_form,
    )

    assert set(Aspect) == IMAGE_ASPECT_LIGATURE_MEASURED
    assert IMAGE_ASPECT_LIGATURE[Aspect.PORTRAIT_THREE_FOUR] == "crop_portrait"
    for aspect in Aspect:
        assert _unported_image_form(_request(aspect=aspect)) is None, aspect


def test_image_submit_body_requires_every_uploaded_reference() -> None:
    from gflow_cli.api.transports.migrated_composer import _image_body_problem

    body = f'[["ogiZ0b", "GEM_PIX_2 {REFERENCE}"]]'
    assert _image_body_problem(body, (REFERENCE,)) is None
    problem = _image_body_problem(body, (REFERENCE, MEDIA))
    assert problem is not None
    assert MEDIA in problem


def test_nano2_submit_accepts_the_beluga_token_nano_banana_2_1_sends() -> None:
    """2.1 replaced 2 in the menu and sends BELUGA (#958, spike 2026-10-08).

    Before this, the default model aborted every image run on those accounts.
    """
    from gflow_cli.api.transports.migrated_composer import _image_body_problem

    for token in ("NARWHAL", "BELUGA"):
        body = f'[["ogiZ0b", "{token} {REFERENCE}"]]'
        assert _image_body_problem(body, (REFERENCE,), model=Model.NARWHAL) is None, token

    problem = _image_body_problem(
        f'[["ogiZ0b", "GEM_PIX_2 {REFERENCE}"]]', (REFERENCE,), model=Model.NARWHAL
    )
    assert problem is not None
    assert "NARWHAL" in problem


def test_nano2_matches_exactly_one_entry_of_the_live_2_1_menu() -> None:
    from gflow_cli.api.transports.migrated_composer import IMAGE_MODEL_MENU_MATCHERS

    # Measured on denon82 and ffroliva, 2026-10-08 (spike_nano_banana_21_wire.py).
    offered = ["🍌 Nano Banana Pro", "🍌 Nano Banana 2 Lite", "🍌 Nano Banana 2.1"]
    matcher = IMAGE_MODEL_MENU_MATCHERS[Model.NARWHAL]
    assert [label for label in offered if matcher.matches(label)] == ["🍌 Nano Banana 2.1"]


def test_nano_banana_2_does_not_match_the_lite_sibling() -> None:
    from gflow_cli.api.transports.migrated_composer import IMAGE_MODEL_MENU_MATCHERS

    matcher = IMAGE_MODEL_MENU_MATCHERS[Model.NARWHAL]
    assert matcher.matches("🍌 Nano Banana 2")
    assert not matcher.matches("🍌 Nano Banana 2 Lite")

    lite_matcher = IMAGE_MODEL_MENU_MATCHERS[Model.HARBOR_SEAL]
    assert lite_matcher.matches("🍌 Nano Banana 2 Lite")
    assert not lite_matcher.matches("🍌 Nano Banana 2")
    assert not lite_matcher.matches("🍌 Nano Banana Pro")


class _HttpImageTransport:
    """An image transport that sends the client's token itself (bearer, evaluate_fetch):
    it does not declare ``uses_page_owned_image_recaptcha``."""

    def __init__(self) -> None:
        self.request: GenerateImageRequest | None = None

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


class _PageOwnedImageTransport(_HttpImageTransport):
    def uses_page_owned_image_recaptcha(self) -> bool:
        return True


async def test_client_skips_legacy_mint_when_the_page_owns_image_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _PageOwnedImageTransport()
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
    transport = _HttpImageTransport()
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


def test_ui_transport_owns_its_image_recaptcha_on_about_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#891: the answer no longer depends on the page. The URL-derived version read the
    parked ``about:blank`` as a labs account, and the client minted there -- turning
    exit 36 into a RecaptchaError. The UI transport never reads a client token."""
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    transport = UiAutomationTransport()
    page = MagicMock()
    page.url = "about:blank"
    transport._page = page  # noqa: SLF001

    assert transport.uses_page_owned_image_recaptcha()


async def test_image_kill_switch_refuses_a_migrated_page_with_exit_36(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#891: with the client mint gone for the UI transport, this route guard is the only
    thing that keeps `GFLOW_CLI_FLOW_HOST=labs.google` an exit 36 for images."""
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.config import reset_settings
    from gflow_cli.errors import FlowHostMigratedError

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "labs.google")
    reset_settings()
    transport = UiAutomationTransport()
    page = MagicMock()
    page.url = f"https://flow.google.com/project/{PROJECT}"
    transport._page = page  # noqa: SLF001
    transport._setup_done = True  # noqa: SLF001

    with structlog.testing.capture_logs() as logs, pytest.raises(FlowHostMigratedError):
        await transport.generate_images(project_id=PROJECT, request=_request())
    assert {"at": "image_flow_host_kill_switch"}.items() <= next(
        e for e in logs if e.get("event") == "ui_driver.migrated_host_bail"
    ).items()


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


async def test_a_failed_migrated_image_run_defers_its_park_for_the_incident_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#792, image half: the park sat in a bare ``finally``, so a failed image run
    navigated to about:blank before ``FlowApiClient._capture_incident`` read the
    page and the bundle shipped an empty DOM. The park is deferred to the client's
    failure boundary; the routing/latch invariant above is unchanged."""
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
    monkeypatch.setattr(
        "gflow_cli.api.transports.migrated_composer.run_images",
        AsyncMock(side_effect=WireFormatError(detail="maseQ answered 200 without a media id")),
    )

    with pytest.raises(WireFormatError):
        await transport.generate_images(project_id=PROJECT, request=_request())

    assert page.url != "about:blank", "evidence destroyed before the bundle was staged"
    assert transport._deferred_park_pending is True  # noqa: SLF001

    await transport.park_deferred_page()
    assert page.url == "about:blank"
    assert transport._deferred_park_pending is False  # noqa: SLF001


async def test_a_cancelled_migrated_image_run_still_parks_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#792: the BaseException arm is the only one still parking inline — nothing
    stages a bundle for a cancel, so nothing is waiting on the page. It latches too,
    because a re-delivered cancel can pre-empt the park at its own `await`."""
    import asyncio

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
    monkeypatch.setattr(
        "gflow_cli.api.transports.migrated_composer.run_images",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await transport.generate_images(project_id=PROJECT, request=_request())

    assert page.url == "about:blank"
    assert transport._deferred_park_pending is True  # noqa: SLF001
