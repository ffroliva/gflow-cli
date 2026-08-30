"""A video model that cannot be selected must fail BEFORE spending credits.

The image arm was made loud on 2026-08-26; the video arm was left as it was, and
video is the arm that actually costs Flow **credits** — veo-quality is 100
against veo-lite's 10, a 10x spread. `_select_video_model` refused only when
``required=True`` (i2v with frames, issue #125). For plain t2v it logged
``model_option_not_found`` with the note "Flow default model applies" and
returned, so the run generated on whatever model Flow last had selected and
charged for that tier.

Why refusing is unambiguously right here, unlike the image panel-miss:
``configure_video_settings`` calls this **only** when ``effective_model is not
None`` (drivers/classic.py), and ``--model`` defaults to ``None`` on every video
command. So reaching this function means a model was EXPLICITLY requested. There
is no "is it the default or did the user ask?" ambiguity that forced the image
panel-miss to stay a warning.

Live evidence, profile denon82, 2026-08-26 — the picker rendered exactly:

    Omni Flash / Veo 3.1 - Lite / Veo 3.1 - Fast / Veo 3.1 - Quality

That snapshot is HISTORICAL and deliberately not refreshed: it is the menu that
made the bug below reachable. Flow has since renamed the first entry to
'Omni 1.1 Flash' (#604). For what the picker renders today, read
`tests/fixtures/flow_model_inventory.json`, never this file.

`VideoModel.VEO_3_1_LITE_LOWER_PRIORITY`'s ``has-text('[Lower Priority]')``
matches none of them. So `gflow video t2v --model veo-lite-lp` was a live,
reachable, credit-spending wrong-model path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.transports.ui_automation_video import (
    VIDEO_MODEL_OPTION_SELECTORS,
    VideoGenerationMixin,
)
from gflow_cli.api.video import VideoModel
from gflow_cli.errors import VideoModelSelectionError


class _Loc:
    def __init__(self, count: int, *, visible: list[bool] | None = None) -> None:
        self._count = count
        self._visible = visible if visible is not None else [True] * count
        self.click = AsyncMock()

    async def wait_for(self, **_: Any) -> None:
        """Absence must actually fail, or a MISS test passes for the wrong reason.

        `_probe_selector_cascade` resolves `.first` and awaits `wait_for`; an
        unconditional AsyncMock here would make every probe "succeed" on an empty
        locator and the miss branches would never be reached.
        """
        if self._count == 0 or not any(self._visible):
            raise TimeoutError("no such element")

    def nth(self, i: int) -> _Loc:
        return _Loc(1, visible=[self._visible[i] if i < len(self._visible) else True])

    @property
    def first(self) -> _Loc:
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self, **_: Any) -> bool:
        return bool(self._visible and self._visible[0])


class _Page:
    def __init__(
        self,
        counts: dict[str, int],
        offered: list[str] | None = None,
        visible: dict[str, list[bool]] | None = None,
    ) -> None:
        self._counts = counts
        self._visible = visible or {}
        self._offered = offered or []
        self.keyboard = AsyncMock()
        self.wait_for_timeout = AsyncMock()

    def locator(self, sel: str) -> _Loc:
        if "arrow_drop_down" in sel:  # the model picker trigger
            return _Loc(1)
        return _Loc(self._counts.get(sel, 0), visible=self._visible.get(sel))

    async def evaluate(self, *_a: Any, **_k: Any) -> list[str]:
        return self._offered


_LIVE_MENU = [
    "volume_up Omni Flash",
    "volume_up Veo 3.1 - Lite",
    "volume_up Veo 3.1 - Fast",
    "volume_up Veo 3.1 - Quality",
]


@pytest.mark.asyncio
async def test_t2v_miss_refuses_instead_of_charging_for_flows_default() -> None:
    """The regression this file exists for: required=False must still refuse.

    This is `--model veo-lite-lp` against the live 2026-08-26 picker.
    """
    page = _Page(counts={}, offered=_LIVE_MENU)

    with pytest.raises(VideoModelSelectionError) as exc:
        await VideoGenerationMixin._select_video_model(
            page, VideoModel.VEO_3_1_LITE_LOWER_PRIORITY, out_dir=None
        )

    msg = str(exc.value)
    # The message must name what Flow DID offer, or the operator cannot act.
    assert "Veo 3.1 - Quality" in msg, msg
    # ...and must say nothing was spent, because nothing was.
    assert "credit" in msg.lower(), msg


@pytest.mark.asyncio
async def test_ambiguous_video_model_refuses_rather_than_guessing_with_first() -> None:
    """`.first` picks by DOM order — across tiers that differ 10x in credits.

    `has-text('Veo 3.1 - Lite')` is a PREFIX of `Veo 3.1 - Lite [Lower Priority]`.
    If Flow ships the LP tier to this account, an unguarded selector would match
    both and silently resolve to whichever renders first.
    """
    sel = VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_FAST]
    page = _Page(counts={sel: 2}, offered=_LIVE_MENU)

    with pytest.raises(VideoModelSelectionError) as exc:
        await VideoGenerationMixin._select_video_model(page, VideoModel.VEO_3_1_FAST, out_dir=None)

    assert "ambiguous" in str(exc.value).lower(), str(exc.value)


@pytest.mark.asyncio
async def test_unique_match_still_selects_normally() -> None:
    """The guard must not break the working path."""
    sel = VIDEO_MODEL_OPTION_SELECTORS[VideoModel.OMNI_FLASH]
    page = _Page(counts={sel: 1}, offered=_LIVE_MENU)

    await VideoGenerationMixin._select_video_model(page, VideoModel.OMNI_FLASH, out_dir=None)


@pytest.mark.asyncio
async def test_hidden_duplicate_does_not_force_a_false_ambiguous() -> None:
    """Radix keeps menus mounted, so a stale menu inflates a raw count()."""
    sel = VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_QUALITY]
    page = _Page(counts={sel: 2}, visible={sel: [True, False]}, offered=_LIVE_MENU)

    await VideoGenerationMixin._select_video_model(page, VideoModel.VEO_3_1_QUALITY, out_dir=None)


@pytest.mark.asyncio
async def test_missing_picker_trigger_refuses_on_t2v_too() -> None:
    """The trigger-miss branch had the same required-only gate."""

    class _NoTrigger(_Page):
        def locator(self, sel: str) -> _Loc:
            return _Loc(0)

    page = _NoTrigger(counts={}, offered=[])
    with pytest.raises(VideoModelSelectionError):
        await VideoGenerationMixin._select_video_model(page, VideoModel.OMNI_FLASH, out_dir=None)


def test_every_video_model_selector_excludes_the_lower_priority_sibling() -> None:
    """Substring hazard, pinned. `has-text` is a SUBSTRING match.

    Any selector whose text is a prefix of another offered label must carry a
    `:not(...)` guard, or it goes AMBIGUOUS the moment Flow ships that sibling.
    """
    lp = VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_LITE_LOWER_PRIORITY]
    lite = VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_LITE]
    assert "[Lower Priority]" in lp
    assert ":not(" in lite, "veo-lite must exclude the [Lower Priority] sibling"
