"""R2V on the migrated host: routing, upload, mention attach, and the submit rpc.

Deliberately NOT built on `test_migrated_composer.py`'s fake DOM. That fake models the
settings pane and the model menu; the reference path needs a mention picker, an upload
chooser and chips, and bolting those onto it would make one fake serve two very different
surfaces. These are small purpose-built doubles instead, each modelling exactly one
measured behaviour from
`docs/superpowers/spikes/2026-09-05-migrated-r2v-attach-surface.md`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode
from gflow_cli.errors import ReferenceNotFoundError

pytestmark = pytest.mark.anyio


def _r2v(**kw: Any) -> GenerateVideoRequest:
    base: dict[str, Any] = {
        "prompt": "a woman holding the product",
        "mode": Mode.R2V,
        "aspect": Aspect.PORTRAIT,
    }
    base.update(kw)
    return GenerateVideoRequest(**base)


# --- routing ----------------------------------------------------------------


def test_migrated_can_serve_takes_r2v_from_local_files_only() -> None:
    from gflow_cli.api.transports.migrated_composer import _unported_form, migrated_can_serve

    assert migrated_can_serve(_r2v(reference_images=(Path("a.png"),)), "p1")
    # By NAME is refused for the same reason i2v refuses a UUID frame: the picker lists
    # assets by display name with no media id, so a reference gflow did not upload has
    # nothing to anchor on — and nothing to assert on the submit body.
    assert _unported_form(_r2v(ref_names=("product1.png",))) is not None
    assert not migrated_can_serve(_r2v(ref_names=("product1.png",)), "p1")
    # Character entities are a different attach surface (a chip with an entity_id, in a
    # different wire slot) and stay on labs.
    assert not migrated_can_serve(_r2v(reference_entities=("abc",)), "p1")
    # A fresh project is still labs-only, so the gate keeps requiring one here.
    assert not migrated_can_serve(_r2v(reference_images=(Path("a.png"),)), None)


def test_a_submit_that_lost_the_references_is_a_wire_problem() -> None:
    """The expensive failure: the picker closes having inserted nothing, the app submits
    anyway, and Flow bills a clip with none of the user's references on it."""
    from gflow_cli.api.transports.migrated_composer import _r2v_body_problem

    good = '["veo_3_1_r2v_lite_low_priority", "aaaaaaaa-1111-2222-3333-444444444444"]'
    assert _r2v_body_problem(good, "MZZa6b", ("aaaaaaaa-1111-2222-3333-444444444444",)) is None

    t2v_key = '["veo_3_1_lite_low_priority", "aaaaaaaa-1111-2222-3333-444444444444"]'
    problem = _r2v_body_problem(t2v_key, "MZZa6b", ("aaaaaaaa-1111-2222-3333-444444444444",))
    assert problem is not None and "no reference was bound" in problem

    other_id = '["veo_3_1_r2v_lite_low_priority", "bbbbbbbb-1111-2222-3333-444444444444"]'
    problem = _r2v_body_problem(other_id, "MZZa6b", ("aaaaaaaa-1111-2222-3333-444444444444",))
    assert problem is not None and "missing 1 of 1" in problem

    assert "could not be read" in (_r2v_body_problem("", "MZZa6b", ("x",)) or "")


def test_submit_rpcs_cover_the_ingredients_submit() -> None:
    """An Ingredients run submits on MZZa6b, not YhhmEf. Watching only the latter is why
    every early capture reported "no submit" while the request was plainly being made."""
    from gflow_cli.api.transports.migrated_composer import SUBMIT_RPCS

    assert "YhhmEf" in SUBMIT_RPCS  # t2v
    assert "MZZa6b" in SUBMIT_RPCS  # r2v / ingredients


# --- a tiny composer double -------------------------------------------------


class FakeLoc:
    def __init__(self, page: FakeComposerPage, kind: str, items: list[Any]) -> None:
        self.page, self.kind, self.items = page, kind, items

    @property
    def first(self) -> FakeLoc:
        return FakeLoc(self.page, self.kind, self.items[:1])

    def filter(self, *, has: Any = None, has_text: Any = None) -> FakeLoc:
        if has_text == "Upload media":
            return FakeLoc(self.page, "upload", ["upload"] if self.page.upload_button else [])
        return self

    async def count(self) -> int:
        return len(self.items)

    async def all_text_contents(self) -> list[str]:
        return [str(i) for i in self.items]

    async def click(self, **_: Any) -> None:
        if self.kind == "upload":
            self.page.upload_clicked += 1
        if self.kind == "composer":
            self.page.composer_clicks += 1


class FakeKeyboard:
    def __init__(self, page: FakeComposerPage) -> None:
        self.page = page

    async def type(self, text: str, **_: Any) -> None:
        self.page.typed.append(text)

    async def press(self, key: str) -> None:
        self.page.typed.append(f"<{key}>")
        if key == "Enter":
            self.page.on_enter()

    async def insert_text(self, text: str) -> None:
        self.page.typed.append(text)


class FakeComposerPage:
    """Only what the reference path touches: a composer, an asset list, chips."""

    def __init__(
        self,
        *,
        assets: list[str] | None = None,
        chips_per_enter: int = 1,
        upload_button: bool = True,
    ) -> None:
        self.keyboard = FakeKeyboard(self)
        self.typed: list[str] = []
        self.chips: list[dict[str, str]] = []
        self.assets = assets if assets is not None else ["me.jpgImage"]
        self.chips_per_enter = chips_per_enter
        self.upload_button = upload_button
        self.upload_clicked = 0
        self.composer_clicks = 0

    def on_enter(self) -> None:
        for _ in range(self.chips_per_enter):
            self.chips.append(
                {"text": f"asset{len(self.chips)}", "entity_id": "", "reference_type": "media"}
            )

    def locator(self, css: str) -> FakeLoc:
        if css == "[contenteditable='true']":
            return FakeLoc(self, "composer", ["composer"])
        if css == "button.asset-item[role='option']":
            return FakeLoc(self, "asset", list(self.assets))
        if css == "button":
            return FakeLoc(self, "button", ["button"])
        if css == ".cdk-overlay-backdrop":
            return FakeLoc(self, "backdrop", [])
        raise AssertionError(f"unmodelled selector: {css!r}")

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def evaluate(self, _script: str) -> Any:
        return self.chips


# --- attach -----------------------------------------------------------------


async def test_each_reference_must_land_as_its_own_chip() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage()
    composer = MigratedComposer()
    await composer._mention_by_name(page, "me.jpg", expect_chips=1)  # noqa: SLF001
    await composer._mention_by_name(page, "product1.png", expect_chips=2)  # noqa: SLF001
    # ENTER is what commits a mention — a typed query alone inserts nothing.
    assert page.typed.count("<Enter>") == 2
    assert len(page.chips) == 2


async def test_a_reference_that_does_not_attach_is_refused_before_submit() -> None:
    """The picker silently inserting nothing is the failure mode that would otherwise
    generate — and bill — a clip with none of the user's references on it."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage(assets=["somethingelseImage"], chips_per_enter=0)
    with pytest.raises(ReferenceNotFoundError) as exc_info:
        await MigratedComposer()._mention_by_name(page, "me.jpg", expect_chips=1)  # noqa: SLF001
    message = str(exc_info.value)
    assert "me.jpg" in message
    assert "somethingelse" in message  # says what the picker DID offer


async def test_the_prompt_is_appended_so_the_mentions_survive() -> None:
    """Clicking the composer would move the caret away from the last mention; an r2v run
    must add its text after the chips, not on top of them."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage()
    await MigratedComposer().send_prompt(page, "a woman holding it", append=True)
    assert page.composer_clicks == 0
    assert "a woman holding it" in page.typed
