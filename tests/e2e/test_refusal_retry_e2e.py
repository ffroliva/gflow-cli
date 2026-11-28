"""E2E: a direct image submit Flow refuses is retried once from a new browser process.

Task 13 of the headless direct transport plan (SCENARIO.md rows 1, 2). The first token
minted for the ``ogiZ0b`` submit is corrupted on purpose, as
``test_migrated_refusal_live_bdd.py`` does on the UI path; Google refuses it as it refuses a
low-scoring browser (HTTP 200, ``PUBLIC_ERROR_UNUSUAL_ACTIVITY``, not billed). The retry loop
relaunches the browser at the setting's launch visibility and sends once more with a fresh
token.

Asserts: two submits, the second from a new browser process at the expected visibility
(headless under ``auto``), an image written, and an unchanged credit balance.

**Cost and opt-in.** Zero credits; one image of daily quota. A refused token can raise the
profile's WAF score for later image tests, so this runs only with
``GFLOW_CLI_E2E_RUN_REFUSAL=1``, on a probe profile. The headed variant runs only when
``GFLOW_CLI_BROWSER_VISIBILITY=headed`` is set.

    GFLOW_CLI_E2E_PROFILE=<probe> GFLOW_CLI_E2E_RUN_REFUSAL=1 \\
        uv run pytest -m e2e_image tests/e2e/test_refusal_retry_e2e.py -v
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.api.transports.direct_http import _mint
from gflow_cli.api.transports.direct_http._gateway import SubmitAttempt
from gflow_cli.config import reset_settings

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]

_RUN_REFUSAL_ENV = "GFLOW_CLI_E2E_RUN_REFUSAL"
_VISIBILITY_ENV = "GFLOW_CLI_BROWSER_VISIBILITY"
_JPEG_OR_PNG = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")


def _corrupt(token: str) -> str:
    return token[:20] + token[20:][::-1]


@pytest.fixture
def refusal_profile(e2e_profile_dir: Path) -> Path:
    if os.environ.get(_RUN_REFUSAL_ENV) != "1":
        pytest.skip(
            f"Set {_RUN_REFUSAL_ENV}=1 to run: it sends one corrupted reCAPTCHA token, "
            "which can raise the profile's WAF score for later image tests."
        )
    return e2e_profile_dir


@pytest.mark.parametrize(
    "visibility",
    [
        pytest.param(BrowserVisibility.HEADLESS, id="headless"),
        pytest.param(BrowserVisibility.HEADED, id="headed"),
    ],
)
def test_a_refused_direct_submit_is_retried_once_from_a_new_browser(
    refusal_profile: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    visibility: BrowserVisibility,
) -> None:
    explicit_headed = os.environ.get(_VISIBILITY_ENV) == "headed"
    if (visibility is BrowserVisibility.HEADED) != explicit_headed:
        pytest.skip(f"the {visibility.value} variant runs when {_VISIBILITY_ENV} matches it")
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "direct_http")
    reset_settings()

    world: dict[str, Any] = {"mints": 0, "submits": 0, "relaunches": [], "contexts": []}
    real_mint = _mint._mint  # pyright: ignore[reportPrivateUsage]

    async def mint_first_corrupted(page: Any, project_id: str, action: str) -> str:
        token = await real_mint(page, project_id, action)
        world["mints"] += 1
        return _corrupt(token) if world["mints"] == 1 else token

    real_send = SubmitAttempt.send

    async def counting_send(self: SubmitAttempt, client: Any, request: Any) -> Any:
        if request.url.params.get("rpcids") == "ogiZ0b":
            world["submits"] += 1
        return await real_send(self, client, request)

    monkeypatch.setattr(_mint, "_mint", mint_first_corrupted)
    monkeypatch.setattr(SubmitAttempt, "send", counting_send)

    async def run() -> None:
        async with FlowApiClient(
            refusal_profile, visibility=visibility, transport="direct_http"
        ) as client:
            real_relaunch = client.relaunch

            async def recording_relaunch(target: BrowserVisibility) -> None:
                world["contexts"].append(client._context)  # noqa: SLF001
                await real_relaunch(target)
                world["relaunches"].append(target)
                world["contexts"].append(client._context)  # noqa: SLF001

            client.relaunch = recording_relaunch  # type: ignore[method-assign]
            before = await client.get_credits()
            image = await client.generate_image(
                req=GenerateImageRequest(prompt="a quiet harbour at dusk, soft light")
            )
            world["path"] = await client.download_image(image, tmp_path / "retried.jpg")
            world["balance"] = (before.credits, (await client.get_credits()).credits)

    asyncio.run(run())

    assert world["submits"] == 2, world
    assert world["relaunches"] == [visibility], world
    old_context, new_context = world["contexts"]
    assert new_context is not old_context  # a new browser process served the retry
    saved = sorted(tmp_path.glob("retried.*"))
    assert len(saved) == 1 and saved[0].read_bytes().startswith(_JPEG_OR_PNG)
    assert world["balance"][0] == world["balance"][1]  # the refusal was not billed
