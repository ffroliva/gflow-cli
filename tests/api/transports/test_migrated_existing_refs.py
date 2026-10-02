"""Reference an image already in the project, with no upload (#913, PLAN Task 5).

Modelled on the measured surface (docs/superpowers/spikes/2026-10-01-batch-ref-dropped.md
§ Gate): `@` opens a picker dialog whose options carry a thumbnail `/asb/<token>`; the
project grid tile `img[data-media-id=<uuid>]` carries the same token. Captions collide
(the picker listed the older of two "a single red apple" first), so the option is chosen
by token, never by caption or position.
"""

from __future__ import annotations

from typing import Any

import pytest

from gflow_cli.api.image import Aspect, GenerateImageRequest, ImageRef, Model
from gflow_cli.api.transports.migrated_composer import (
    PICKER_OPTION,
    MigratedComposer,
    _guard_image_submit,
    _unported_image_form,
)
from gflow_cli.errors import ReferenceNotFoundError

PARENT = "3f4272fd-2897-4621-b0cd-0fb9576758d3"


@pytest.fixture(autouse=True)
def _short_grid_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake page's waits return at once; keep the real-clock budget short.
    from gflow_cli.api.transports import migrated_composer

    monkeypatch.setattr(migrated_composer, "EXISTING_REF_WAIT_S", 0.2)


OTHER = "90879017-ff54-4232-9186-f6a61298a6cc"


class _Loc:
    def __init__(self, page: FakePage, kind: str) -> None:
        self.page, self.kind = page, kind

    @property
    def first(self) -> _Loc:
        return self

    async def click(self, **_: Any) -> None:
        if self.kind == "composer":
            self.page.composer_clicks += 1


class _Keyboard:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def type(self, text: str, **_: Any) -> None:
        self.page.typed.append(text)
        if text == "@":
            self.page.dialog_open = True
            self.page.active = 0

    async def press(self, key: str) -> None:
        self.page.typed.append(f"<{key}>")
        if key == "ArrowDown":
            self.page.active += 1
        elif key == "Escape":
            self.page.dialog_open = False
        elif key == "Enter" and self.page.dialog_open:
            options = self.page.options()
            if self.page.active < len(options):
                self.page.bound.append(options[self.page.active][1])
                self.page.chips.append({"text": "x", "entity_id": "", "reference_type": "media"})
            self.page.dialog_open = False


class FakePage:
    def __init__(
        self,
        *,
        grid: dict[str, str],
        options: list[tuple[str, str]],
        miss_first: int = 0,
        grid_after: int = 0,
    ) -> None:
        self.keyboard = _Keyboard(self)
        self.grid = grid
        self._options = options
        self.miss_first = miss_first
        #: Grid lookups that find nothing before the tile renders (measured live: a
        #: just-generated image is missing from the grid for a while).
        #: Reloads before a just-generated tile is in the grid. Measured live: the grid is
        #: the asset list fetched when the editor loads; it is not updated in place.
        self.grid_after = grid_after
        self.reloads = 0
        self.searches = 0
        self.typed: list[str] = []
        self.chips: list[dict[str, str]] = []
        self.bound: list[str] = []
        self.dialog_open = False
        self.active = 0
        self.composer_clicks = 0

    def options(self) -> list[tuple[str, str]]:
        return [] if self.searches <= self.miss_first else self._options

    def locator(self, css: str) -> _Loc:
        if css == "[contenteditable='true']":
            return _Loc(self, "composer")
        raise AssertionError(f"unmodelled selector: {css!r}")

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def reload(self, **_: Any) -> None:
        self.reloads += 1

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if "data-media-id" in script:
            return self.grid.get(arg, "") if self.reloads >= self.grid_after else ""

        if arg == PICKER_OPTION:
            self.searches += 1
            return [token for _caption, token in self.options()]
        return self.chips


def _ref(display_name: str = "a single red apple") -> ImageRef:
    return ImageRef(name=PARENT, display_name=display_name, in_project=True)


def _composer() -> MigratedComposer:
    composer = MigratedComposer()

    async def no_editor(*_: Any, **__: Any) -> None:
        return None

    composer.ensure_editor = no_editor  # type: ignore[method-assign]
    composer.apply_image_settings = no_editor  # type: ignore[method-assign]
    return composer


async def _attach(page: Any, refs: tuple[ImageRef, ...]) -> tuple[str, ...]:
    request = GenerateImageRequest(
        prompt="p", aspect=Aspect.from_cli("1:1"), model=Model.from_cli("nano2"), refs=refs
    )
    return await _composer().reference_existing(page, "proj", request)


async def test_the_option_is_chosen_by_token_when_captions_collide() -> None:
    page: Any = FakePage(
        grid={PARENT: "tokNew", OTHER: "tokOld"},
        options=[("a single red apple", "tokOld"), ("a single red apple", "tokNew")],
    )
    ids = await _attach(page, (_ref(),))
    assert ids == (PARENT,)
    assert page.bound == ["tokNew"]
    assert page.typed.count("<ArrowDown>") == 1


async def test_a_parent_missing_from_the_grid_is_refused_before_searching() -> None:
    page: Any = FakePage(grid={}, options=[("a single red apple", "tokNew")])
    with pytest.raises(ReferenceNotFoundError, match=PARENT):
        await _attach(page, (_ref(),))
    assert "<Enter>" not in page.typed


async def test_a_search_miss_reloads_the_editor_and_tries_again() -> None:
    # Live e2e 2026-10-01: three searches in one page load offered nothing; the next
    # row's fresh load found the same image at once.
    page: Any = FakePage(grid={PARENT: "tokNew"}, options=[("c", "tokNew")], miss_first=1)
    ids = await _attach(page, (_ref("c"),))
    assert ids == (PARENT,)
    assert page.reloads == 1
    assert page.typed.index("<Escape>") < [i for i, t in enumerate(page.typed) if t == "@"][1]


async def test_no_matching_option_is_refused_without_binding_anything() -> None:
    page: Any = FakePage(grid={PARENT: "tokNew"}, options=[("c", "tokOld")])
    with pytest.raises(ReferenceNotFoundError):
        await _attach(page, (_ref("c"),))
    assert page.bound == []
    assert "<Enter>" not in page.typed


@pytest.mark.parametrize(
    "caption",
    ["two\nlines", "say @hi", "", "   ", "x" * 121, "zero\u200bwidth", "line\u2028sep"],
)
async def test_an_unusable_caption_is_refused_before_typing(caption: str) -> None:
    page: Any = FakePage(grid={PARENT: "tokNew"}, options=[(caption, "tokNew")])
    with pytest.raises(ReferenceNotFoundError):
        await _attach(page, (_ref(caption),))
    assert page.typed == []


def _req(**kw: Any) -> GenerateImageRequest:
    return GenerateImageRequest(
        prompt="p", aspect=Aspect.from_cli("1:1"), model=Model.from_cli("nano2"), **kw
    )


def test_only_an_in_project_captioned_reference_is_ported(tmp_path: Any) -> None:
    assert _unported_image_form(_req(refs=(_ref(),))) is None
    # A catalog/MCP UUID ref carries a caption too, but is not from this run's project:
    # it stays unported on flow.google.com (exit 36), as before #913.
    catalog = ImageRef(name=PARENT, display_name="a single red apple")
    assert _unported_image_form(_req(refs=(catalog,))) == "a reference given by Flow media UUID"
    no_caption = ImageRef(name=PARENT, in_project=True)
    assert "without a caption" in (_unported_image_form(_req(refs=(no_caption,))) or "")
    local = tmp_path / "x.png"
    local.write_bytes(b"\x89PNG")
    assert _unported_image_form(_req(refs=(_ref(),), ref_paths=(local,))) is not None


class _Route:
    def __init__(self) -> None:
        self.aborted = False
        self.continued = False

    async def abort(self, *_: Any) -> None:
        self.aborted = True

    async def continue_(self, **_: Any) -> None:
        self.continued = True


class _Request:
    def __init__(self, body: str) -> None:
        self.post_data = body


async def test_a_submit_missing_its_reference_is_aborted_before_flow_acts() -> None:
    route = _Route()
    problem = await _guard_image_submit(route, _Request("ogiZ0b no ids here"), (PARENT,), None)
    assert problem is not None and PARENT in problem
    assert route.aborted and not route.continued


async def test_a_submit_carrying_its_reference_goes_through() -> None:
    route = _Route()
    problem = await _guard_image_submit(route, _Request(f"ogiZ0b {PARENT}"), (PARENT,), None)
    assert problem is None
    assert route.continued and not route.aborted


async def test_a_parent_missing_after_load_is_found_by_reloading_the_editor() -> None:
    # Live e2e 2026-10-01: row 1 opened the editor one second after row 0 was generated
    # and polled the grid for 30 s without the tile; row 2 reloaded and found it at once.
    page: Any = FakePage(grid={PARENT: "tokNew"}, options=[("c", "tokNew")], grid_after=2)
    ids = await _attach(page, (_ref("c"),))
    assert ids == (PARENT,)
    assert page.reloads == 2


async def test_a_parent_tile_that_never_appears_is_refused_after_the_budget() -> None:
    page: Any = FakePage(grid={PARENT: "tokNew"}, options=[("c", "tokNew")], grid_after=10**9)
    with pytest.raises(ReferenceNotFoundError, match="grid"):
        await _attach(page, (_ref("c"),))
    assert page.reloads >= 1  # it reloaded before giving up
    assert "<Enter>" not in page.typed


async def test_a_character_chip_is_not_taken_for_the_image() -> None:
    # A caption query can also match a character entity; only a media chip is the image.
    page: Any = FakePage(grid={PARENT: "tokNew"}, options=[("c", "tokNew")])

    async def entity_chips(script: str, arg: Any = None) -> Any:
        if "data-media-id" in script:
            return page.grid.get(arg, "")
        if arg == PICKER_OPTION:
            page.searches += 1
            return [token for _caption, token in page.options()]
        return [{"text": "x", "entity_id": "e1", "reference_type": "entity"}] * len(page.chips)

    page.evaluate = entity_chips
    with pytest.raises(ReferenceNotFoundError):
        await _attach(page, (_ref("c"),))


async def test_the_arrow_settles_before_enter() -> None:
    page: Any = FakePage(grid={PARENT: "tokNew"}, options=[("c", "tokOld"), ("c", "tokNew")])
    waits: list[float] = []

    async def record(ms: float) -> None:
        waits.append(ms)

    page.wait_for_timeout = record
    await _attach(page, (_ref("c"),))
    arrow_at = page.typed.index("<ArrowDown>")
    assert arrow_at < page.typed.index("<Enter>")
    assert 3500 in waits  # measured UpteDb settle (capture_migrated_attach_rpcs.py)
