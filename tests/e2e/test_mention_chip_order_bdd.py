"""E2E for #997: ``@`` mentions land after the chips already in the prompt, in order.

Binds ``tests/features/mention_chip_order.feature`` — see ``docs/E2E_TESTING.md``
§ BDD-bound e2e::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
        uv run pytest -m e2e_auth tests/e2e/test_mention_chip_order_bdd.py -v

Drives production ``attach_existing_references`` (the in-place reference path) on the
real composer, the way ``scripts/dev/spike_997_fourth_ref_caret.py`` measured it. On the
pre-fix code the chips read ``1, 2, 4, 3`` (spike, 3/3). **$0**: existing images are
mentioned in place, no submit is sent (asserted), and the composer is cleared.

The miss scenario (#1143) drives ``_mention_by_name`` for a name no asset carries. Before
that fix the same run raised a raw ``TimeoutError`` and left ``@`` in the composer.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest, ImageRef
from gflow_cli.api.transports.migrated_composer import (
    _OPTION_TITLES_JS,
    _OPTION_TOKENS_JS,
    COMPOSER,
    IMAGE_SUBMIT_RPC,
    PICKER_OPTION,
    SUBMIT_RPCS,
    MigratedComposer,
)
from gflow_cli.errors import ReferenceNotFoundError

scenarios("../features/mention_chip_order.feature")

_PROJECT_ENV = "GFLOW_CLI_E2E_PROJECT"
_MENTIONS = 4
_NO_SUCH_ASSET = "e2e-1143-no-such-asset"

#: (media id, /asb/ token) for every grid tile, in grid order.
_GRID_JS = r"""() => [...document.querySelectorAll('img[data-media-id]')].map(e => {
  const m = (e.getAttribute('src') || '').match(/\/asb\/([A-Za-z0-9_-]+)/);
  return [e.getAttribute('data-media-id'), m ? m[1] : ''];
}).filter(x => x[1])"""


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


async def _targets(page: Any, composer: MigratedComposer) -> list[tuple[str, str, str]]:
    """(media id, token, caption) for distinct, typeable captions — the spike's method."""
    grid = await page.evaluate(_GRID_JS)
    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2500)
    titles = [str(t) for t in await page.evaluate(_OPTION_TITLES_JS, PICKER_OPTION)]
    tokens = [str(t) for t in await page.evaluate(_OPTION_TOKENS_JS, PICKER_OPTION)]
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)
    await composer.clear_composer(page)
    caption = {tok: t for tok, t in zip(tokens, titles, strict=False) if tok and t}
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for media_id, token in grid:
        cap = caption.get(token, "")
        if cap and cap not in seen and "\n" not in cap and "@" not in cap and len(cap) <= 120:
            seen.add(cap)
            out.append((media_id, token, cap))
    return out[:_MENTIONS]


async def _drive(
    profile_dir: Path, project: str, world: dict[str, Any], *, miss: bool = False
) -> None:
    composer = MigratedComposer()
    async with FlowApiClient(profile_dir=profile_dir) as client:
        page = client._page  # noqa: SLF001 - the e2e drives the live page
        assert page is not None
        world["kind"] = await composer.ensure_editor(page, project)
        if world["kind"] != "classic":
            return
        submits: list[str] = []
        rpcs = (*SUBMIT_RPCS, IMAGE_SUBMIT_RPC)

        def _on_request(request: Any) -> None:
            body = request.post_data or ""
            submits.extend(r for r in rpcs if r in request.url or r in body)

        page.on("request", _on_request)
        await composer.apply_image_settings(page, GenerateImageRequest(prompt="e2e-997"))
        targets = await _targets(page, composer)
        world["targets"] = targets
        if len(targets) < _MENTIONS:
            return
        chosen = targets[:1] if miss else targets
        refs = tuple(ImageRef(name=m, display_name=c, in_project=True) for m, _, c in chosen)
        try:
            await composer.attach_existing_references(page, refs, {m: t for m, t, _ in chosen})
            if miss:
                await composer._mention_by_name(  # noqa: SLF001 - no public path to a miss
                    page, _NO_SUCH_ASSET, expect_chips=2
                )
            world["error"] = None
        except Exception as exc:  # noqa: BLE001 - the outcome is the assertion
            world["error"] = exc
        finally:
            world["chips"] = [c.get("text", "") for c in await composer.read_chips(page)]
            world["composer_text"] = await page.locator(COMPOSER).first.inner_text()
            await composer.clear_composer(page)
            world["chips_after_clear"] = len(await composer.read_chips(page))
            world["submits"] = submits


# --------------------------------------------------------------------------- given


@given("a flow.google.com project holding at least four captioned images")
def _project(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    project = os.environ.get(_PROJECT_ENV, "").strip()
    if not project:
        pytest.skip(f"{_PROJECT_ENV} must name a project with >= 4 images (see module doc)")
    world["project"] = project
    world["profile_dir"] = e2e_profile_dir


# ---------------------------------------------------------------------------- when


@when("the driver mentions four of them in order without submitting")
def _mention(world: dict[str, Any]) -> None:
    asyncio.run(_drive(world["profile_dir"], world["project"], world))
    _skip_unless_driven(world)


def _skip_unless_driven(world: dict[str, Any]) -> None:
    if world["kind"] != "classic":
        pytest.skip(f"project renders the {world['kind']} composer; #997 is the classic one")
    if len(world["targets"]) < _MENTIONS:
        pytest.skip(f"project has {len(world['targets'])} distinct captioned images, need 4")


@when("the driver attaches one image and then mentions a name that matches nothing")
def _mention_a_miss(world: dict[str, Any]) -> None:
    asyncio.run(_drive(world["profile_dir"], world["project"], world, miss=True))
    _skip_unless_driven(world)


# ---------------------------------------------------------------------------- then


@then("the prompt's chips read in the order they were requested")
def _in_order(world: dict[str, Any]) -> None:
    requested = [c for _, _, c in world["targets"]]
    order = [requested.index(t) + 1 if t in requested else "?" for t in world["chips"]]
    assert world["error"] is None, f"attach refused: {world['error']!r}; chip order {order}"
    assert order == list(range(1, _MENTIONS + 1)), f"chip order {order}"


@then("the mention is refused with the chip kept and no query text left behind")
def _refused(world: dict[str, Any]) -> None:
    assert isinstance(world["error"], ReferenceNotFoundError), repr(world["error"])
    assert world["chips"] == [world["targets"][0][2]], world["chips"]
    assert "@" not in world["composer_text"], world["composer_text"]
    assert _NO_SUCH_ASSET not in world["composer_text"], world["composer_text"]


@then("nothing was submitted and the composer was cleared")
def _no_submit(world: dict[str, Any]) -> None:
    assert world["submits"] == [], world["submits"]
    assert world["chips_after_clear"] == 0, world["chips_after_clear"]
