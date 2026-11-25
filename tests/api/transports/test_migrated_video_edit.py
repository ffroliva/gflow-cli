"""The video-to-video edit transport: preparation, credit safety and the submit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports import migrated_video_edit as edit_mod
from gflow_cli.api.transports._mc_upload_video import UploadedVideo
from gflow_cli.api.transports.batchexecute import GenerationRecord
from gflow_cli.api.transports.migrated_extend import SourceClip
from gflow_cli.api.video import Aspect, EditVideoRequest, VideoModel, VideoStarted
from gflow_cli.errors import (
    ConfigurationError,
    CreditCapExceededError,
    FlowHostMigratedError,
    ReferenceNotFoundError,
    UiSelectorDriftError,
    WireFormatError,
)

_PID = "11111111-1111-1111-1111-111111111111"
_MID = "22222222-2222-2222-2222-222222222222"
_WF = "33333333-3333-3333-3333-333333333333"
_REF1 = "44444444-4444-4444-4444-444444444444"
ORIGINAL_STABLE_QUOTE = edit_mod._stable_quote
ORIGINAL_ATTACH_BY_CLICK = edit_mod._attach_by_click

_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _freq(rpcid: str, inner: Any) -> str:
    return f"f.req={json.dumps([[[rpcid, json.dumps(inner), None, 'generic']]])}"


def _edit_body(model_key: str = "macaron_cookie_edit", refs: tuple[str, ...] = (_REF1,)) -> str:
    refs_wire = [[None, ref] for ref in refs]
    return _freq("jIps6", [[[[None, _WF], None, model_key, 2, None, None, None, None, refs_wire]]])


class _Loc:
    def __init__(self, count: int = 1) -> None:
        self._count = count

    @property
    def first(self) -> _Loc:
        return self

    def locator(self, _sel: str) -> _Loc:
        return self

    async def count(self) -> int:
        return self._count

    async def hover(self, **_kw: Any) -> None:
        return None

    async def wait_for(self, **_kw: Any) -> None:
        return None

    async def click(self, **_kw: Any) -> None:
        return None


class _Page:
    """Just enough page for the two preparation paths; records the route handlers."""

    url = f"https://flow.google.com/project/{_PID}/edit/{_MID}"

    def __init__(self) -> None:
        self.keyboard = MagicMock(insert_text=AsyncMock(), press=AsyncMock(), type=AsyncMock())
        self.routes: list[Any] = []

    def on(self, *_a: Any) -> None:
        return None

    def remove_listener(self, *_a: Any) -> None:
        return None

    async def route(self, _matcher: Any, handler: Any) -> None:
        self.routes.append(handler)

    async def unroute(self, _matcher: Any, handler: Any) -> None:
        self.routes.remove(handler)

    def locator(self, sel: str) -> _Loc:
        return _Loc(0 if sel == edit_mod.CHIP_CONTAINER_SELECTOR else 1)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def evaluate(self, *_a: Any) -> None:
        return None


def _done_record() -> GenerationRecord:
    return GenerationRecord(
        workflow_id="66666666-6666-6666-6666-666666666666",
        project_id=_PID,
        media_id="77777777-7777-7777-7777-777777777777",
        status=3,
        video_url="https://flow-content.google/v.mp4",
        poster_url=None,
        size_bytes=len(_MP4_BYTES),
    )


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    clip = tmp_path / "in.mp4"
    clip.write_bytes(_MP4_BYTES)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_PNG_BYTES)
    bad = tmp_path / "secret.png"
    bad.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----")
    return {"clip": clip, "ref": ref, "bad": bad, "out": tmp_path / "edited.mp4"}


@pytest.fixture
def composer(monkeypatch: pytest.MonkeyPatch, files: dict[str, Path]) -> MagicMock:
    """A composer whose submit checks the edit request the way the browser route does."""
    fake = MagicMock()
    fake.ensure_editor_for = AsyncMock(return_value="classic")
    fake.upload = AsyncMock(return_value=(_REF1, "ref-aaaaaaaa.png"))
    fake.clear_composer = AsyncMock()
    fake.send_prompt = AsyncMock()
    fake._mention_by_name = AsyncMock()

    async def submit(_page: Any, **kwargs: Any) -> GenerationRecord:
        assert kwargs["request_check"]("jIps6", _edit_body()) is None
        kwargs["on_started"](VideoStarted(media_id=_MID, project_id=_PID, workflow_id="w"))
        return _done_record()

    fake.submit_and_observe = AsyncMock(side_effect=submit)
    fake.download = AsyncMock(return_value=files["out"])
    monkeypatch.setattr(edit_mod, "MigratedComposer", lambda: fake)
    monkeypatch.setattr(
        edit_mod,
        "upload_video_for_mention",
        AsyncMock(return_value=UploadedVideo(_MID, _WF, "in-12345678")),
    )
    monkeypatch.setattr(edit_mod, "_apply_main_edit_settings", AsyncMock(return_value=9))
    monkeypatch.setattr(edit_mod, "_attach_by_click", AsyncMock())
    monkeypatch.setattr(edit_mod, "_source_clip", AsyncMock(return_value=SourceClip(_WF, "video")))
    monkeypatch.setattr(edit_mod, "_confirm_editor", MagicMock())
    monkeypatch.setattr(edit_mod, "_anchor", AsyncMock())
    monkeypatch.setattr(edit_mod, "_select_clip_editor_model", AsyncMock())
    monkeypatch.setattr(edit_mod, "_stable_quote", AsyncMock(return_value=9))
    return fake


async def _run(request: EditVideoRequest, page: _Page | None = None, **kw: Any) -> Any:
    return await edit_mod.run_video_edit(
        page or _Page(),
        request,
        project_id=_PID,
        save=AsyncMock(),
        poll_timeout_s=30.0,
        download=True,
        on_started=kw.pop("on_started", None),
        **kw,
    )


@pytest.mark.asyncio
async def test_unsupported_reference_is_exit_11_before_any_upload(
    files: dict[str, Path], composer: MagicMock
) -> None:
    with pytest.raises(ConfigurationError, match="cannot upload this file") as info:
        await _run(
            EditVideoRequest(
                prompt="g", source_video=files["clip"], reference_images=(files["bad"],)
            )
        )
    assert info.value.charged == "no"
    edit_mod.upload_video_for_mention.assert_not_called()  # type: ignore[attr-defined]
    composer.ensure_editor_for.assert_not_called()


@pytest.mark.asyncio
async def test_360p_on_an_existing_clip_is_refused_before_any_click(
    composer: MagicMock,
) -> None:
    with pytest.raises(FlowHostMigratedError, match="360p"):
        await _run(EditVideoRequest(prompt="g", source_media_id=_MID, resolution="360p"))
    edit_mod._source_clip.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_local_edit_completes_and_reports_checkpoints_around_the_click(
    files: dict[str, Path], composer: MagicMock
) -> None:
    phases: list[str] = []
    started: list[str] = []
    res = await _run(
        EditVideoRequest(
            prompt="golden glow",
            source_video=files["clip"],
            reference_images=(files["ref"],),
            aspect=Aspect.LANDSCAPE,
            resolution="720p",
            max_credits=12,
        ),
        on_started=lambda s: started.append(s.media_id),
        on_checkpoint=lambda cp: phases.append(cp.phase),
    )
    assert res.status.succeeded is True
    assert res.local_path == files["out"]
    assert res.quoted_credits == 9
    assert started == [_MID]
    assert phases == ["submit_attempted", "remote_started"]
    # Clip and reference are committed by click, never by the shared Enter-based mention.
    composer._mention_by_name.assert_not_called()
    assert edit_mod._attach_by_click.await_count == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_clip_editor_edit_completes(files: dict[str, Path], composer: MagicMock) -> None:
    res = await _run(
        EditVideoRequest(
            prompt="add snow",
            source_media_id=_MID,
            reference_images=(files["ref"],),
            max_credits=12,
        )
    )
    assert res.status.succeeded is True
    assert res.quoted_credits == 9
    assert composer.submit_and_observe.await_args.kwargs["submit_selector"] == (
        edit_mod.CLIP_EDITOR_SUBMIT
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["local", "clip"])
async def test_quote_over_the_cap_refuses_before_the_click(
    source: str, files: dict[str, Path], composer: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(edit_mod, "_apply_main_edit_settings", AsyncMock(return_value=20))
    monkeypatch.setattr(edit_mod, "_stable_quote", AsyncMock(return_value=20))
    src: dict[str, Any] = (
        {"source_video": files["clip"]} if source == "local" else {"source_media_id": _MID}
    )
    phases: list[str] = []
    with pytest.raises(CreditCapExceededError) as info:
        await _run(
            EditVideoRequest(prompt="g", max_credits=0, **src),
            on_checkpoint=lambda cp: phases.append(cp.phase),
        )
    assert info.value.charged == "no"
    composer.submit_and_observe.assert_not_called()
    assert phases == []


@pytest.mark.asyncio
async def test_an_unreadable_quote_with_a_cap_refuses(
    files: dict[str, Path], composer: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(edit_mod, "_stable_quote", AsyncMock(return_value=None))
    with pytest.raises(CreditCapExceededError):
        await _run(EditVideoRequest(prompt="g", source_media_id=_MID, max_credits=50))
    composer.submit_and_observe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("rpcid", ["jIps6", "YhhmEf"])
async def test_a_submit_the_page_sends_while_preparing_is_aborted(
    rpcid: str, files: dict[str, Path], composer: MagicMock
) -> None:
    """A stray Enter during preparation would bill before the cap is checked."""
    page = _Page()
    aborted: list[str] = []

    async def stray_enter(*_a: Any, **_kw: Any) -> None:
        route = MagicMock(abort=AsyncMock(side_effect=lambda: aborted.append(rpcid)))
        request = MagicMock(
            url=f"https://flow.google.com/_/batchexecute?rpcids={rpcid}",
            post_data=_edit_body() if rpcid == "jIps6" else _freq(rpcid, []),
        )
        await page.routes[0](route, request)

    composer.send_prompt = AsyncMock(side_effect=stray_enter)
    with pytest.raises(WireFormatError, match="still preparing the video edit") as info:
        await _run(
            EditVideoRequest(
                prompt="g", source_video=files["clip"], reference_images=(files["ref"],)
            ),
            page=page,
        )
    assert aborted == [rpcid]
    assert info.value.charged == "no"
    composer.submit_and_observe.assert_not_called()
    assert page.routes == []


@pytest.mark.asyncio
async def test_a_playwright_timeout_while_preparing_is_a_typed_refusal(
    files: dict[str, Path], composer: MagicMock
) -> None:
    composer.send_prompt = AsyncMock(side_effect=PlaywrightTimeoutError("Timeout 5000ms"))
    with pytest.raises(UiSelectorDriftError, match="did not respond") as info:
        await _run(EditVideoRequest(prompt="g", source_video=files["clip"]))
    assert info.value.charged == "no"


@pytest.mark.asyncio
async def test_agent_only_composer_is_refused_before_the_upload(
    files: dict[str, Path], composer: MagicMock
) -> None:
    composer.ensure_editor_for = AsyncMock(return_value="agent_only")
    with pytest.raises(FlowHostMigratedError, match="agent-only"):
        await _run(EditVideoRequest(prompt="g", source_video=files["clip"]))
    edit_mod.upload_video_for_mention.assert_not_called()  # type: ignore[attr-defined]


# --- the settled quote and the clip editor's model check ---------------------------


class _Clock:
    """A fake event-loop clock that ``asyncio.sleep`` advances."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(edit_mod.asyncio, "get_running_loop", lambda: fake)
    monkeypatch.setattr(edit_mod.asyncio, "sleep", fake.sleep)
    return fake


def _reads(*values: int | None) -> Any:
    seq = iter(values)
    last: list[int | None] = [None]

    async def read() -> int | None:
        last[0] = next(seq, last[0])
        return last[0]

    return read


@pytest.mark.asyncio
async def test_a_quote_is_taken_only_once_it_has_held_for_the_settle_time(clock: _Clock) -> None:
    """Two equal reads 0.25 s apart are not settled: a re-price may start late."""
    quoted = await edit_mod._stable_quote(_reads(10, 10, 10, 15))
    assert quoted == 15
    assert clock.now >= edit_mod.QUOTE_SETTLE_S


@pytest.mark.asyncio
async def test_a_quote_that_keeps_changing_or_is_unreadable_is_none(clock: _Clock) -> None:
    flapping = iter(range(1000))

    async def read() -> int | None:
        return next(flapping)

    assert await edit_mod._stable_quote(read) is None
    assert await edit_mod._stable_quote(_reads(None)) is None


@pytest.mark.asyncio
async def test_no_quote_is_read_without_a_cap(files: dict[str, Path], composer: MagicMock) -> None:
    res = await _run(
        EditVideoRequest(prompt="g", source_media_id=_MID, reference_images=(files["ref"],))
    )
    assert res.quoted_credits is None
    edit_mod._stable_quote.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_clip_editor_quote_is_read_from_the_prompt_box_with_menus_closed(
    files: dict[str, Path], composer: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: list[str] = []

    async def label(_page: Any, selector: str) -> int | None:
        read.append(selector)
        return 7

    monkeypatch.setattr(edit_mod, "_read_label", label)
    monkeypatch.setattr(edit_mod, "_stable_quote", ORIGINAL_STABLE_QUOTE)
    monkeypatch.setattr(edit_mod, "QUOTE_SETTLE_S", 0.0)
    res = await _run(
        EditVideoRequest(
            prompt="g", source_media_id=_MID, reference_images=(files["ref"],), max_credits=9
        )
    )
    assert res.quoted_credits == 7
    assert set(read) == {edit_mod.CLIP_EDITOR_CREDIT_LABEL}


# --- the jIps6 request check ----------------------------------------------------------


def _entry_body(refs_wire: Any, aspect: Any = 2) -> str:
    return _freq(
        "jIps6",
        [[[[None, _WF], None, "macaron_cookie_edit", aspect, None, None, None, None, refs_wire]]],
    )


@pytest.mark.parametrize(
    ("refs_wire", "expected"),
    [
        ([[None, _REF1], ["bad"]], (_REF1,)),  # an unreadable slot is not skipped
        ([[None, _REF1], [None, _MID]], (_MID, _REF1)),  # order matters
        ([[None, _REF1], [None, _REF1]], (_REF1,)),  # a duplicate is not one reference
    ],
    ids=["unreadable", "order", "duplicate"],
)
def test_the_reference_check_fails_closed(refs_wire: Any, expected: tuple[str, ...]) -> None:
    err = edit_mod.edit_request_problem(
        "jIps6", _entry_body(refs_wire), workflow_id=_WF, expected_reference_ids=expected
    )
    assert isinstance(err, WireFormatError), err


def test_an_unreadable_aspect_is_refused_when_one_was_asked() -> None:
    err = edit_mod.edit_request_problem(
        "jIps6", _entry_body([], aspect=None), workflow_id=_WF, expected_aspect=Aspect.PORTRAIT
    )
    assert isinstance(err, WireFormatError)
    assert "could not read the aspect" in err.detail


@pytest.mark.parametrize("rpcid", ["ogiZ0b", "fZytfe", "YhhmEf", "eb1hJf", "MZZa6b", "nprQif"])
def test_every_other_billed_submit_is_refused(rpcid: str) -> None:
    err = edit_mod.edit_request_problem(rpcid, _freq(rpcid, []), workflow_id=_WF)
    assert isinstance(err, WireFormatError), rpcid


# --- the clip editor's own controls (live e2e on 2026-11-25: no jIps6 left the page) ---------


class _ClipEditorPage(_Page):
    """A clip editor whose prompt box submits the edit on Enter, as a composer does.

    An option click in the ``@`` picker commits a chip; Enter sends ``jIps6`` through the
    page's routes (the pre-submit hold while preparing).
    """

    def __init__(self) -> None:
        super().__init__()
        self.chips = 0
        self.pressed: list[str] = []
        self.keyboard.press = AsyncMock(side_effect=self._press)

    async def _press(self, key: str) -> None:
        self.pressed.append(key)
        if key == "Enter":
            route = MagicMock(abort=AsyncMock(), fallback=AsyncMock())
            request = MagicMock(
                url="https://flow.google.com/_/batchexecute?rpcids=jIps6", post_data=_edit_body()
            )
            for handler in list(self.routes):
                await handler(route, request)

    def locator(self, sel: str) -> Any:
        page = self
        if sel == edit_mod.CLIP_EDITOR_MENTION_CHIP:
            return MagicMock(count=AsyncMock(side_effect=lambda: page.chips))
        if sel == edit_mod.ASSET_BROWSER_OPTION:
            option = MagicMock(
                is_visible=AsyncMock(return_value=True),
                click=AsyncMock(side_effect=lambda **_k: setattr(page, "chips", page.chips + 1)),
            )
            return MagicMock(
                all_text_contents=AsyncMock(return_value=["ref-aaaaaaaa image"]),
                nth=MagicMock(return_value=option),
            )
        return super().locator(sel)


@pytest.mark.asyncio
async def test_clip_editor_references_are_committed_by_click_never_by_enter(
    files: dict[str, Path], composer: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enter in the edit prompt box submits: the hold aborted that jIps6, so the checked
    submit never left the page and the run failed as an early submit."""

    async def shared_mention(page: Any, *_a: Any, **_k: Any) -> None:
        await page.keyboard.press("Enter")  # the shared @-mention commits with Enter

    composer._mention_by_name = AsyncMock(side_effect=shared_mention)
    monkeypatch.setattr(edit_mod, "_attach_by_click", ORIGINAL_ATTACH_BY_CLICK)
    page = _ClipEditorPage()
    res = await _run(
        EditVideoRequest(prompt="add snow", source_media_id=_MID, reference_images=(files["ref"],)),
        page=page,
    )
    assert res.status.succeeded is True
    assert "Enter" not in page.pressed
    assert page.chips == 1
    composer.submit_and_observe.assert_awaited_once()


class _PickerPage:
    """A ``@`` picker that lists the fresh upload only from the *listed_from*-th poll on."""

    def __init__(self, names: list[str], *, listed_from: int) -> None:
        self.names = names
        self.listed_from = listed_from
        self.polls = 0
        self.chips = 0
        self.keyboard = MagicMock(type=AsyncMock(side_effect=self._typed), press=AsyncMock())

    async def _typed(self, text: str, **_kw: Any) -> None:
        if text == "@":
            self.polls += 1

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def evaluate(self, *_a: Any) -> None:
        return None

    def locator(self, sel: str) -> Any:
        page = self
        if sel.endswith(edit_mod.PICKER_OPTION):
            option = MagicMock(
                is_visible=AsyncMock(return_value=True),
                click=AsyncMock(side_effect=lambda **_k: setattr(page, "chips", 1)),
            )
            return MagicMock(
                all_text_contents=AsyncMock(
                    side_effect=lambda: page.names if page.polls >= page.listed_from else []
                ),
                nth=MagicMock(return_value=option),
            )
        if sel == "chip":
            return MagicMock(count=AsyncMock(side_effect=lambda: page.chips))
        loc = MagicMock(count=AsyncMock(return_value=0), click=AsyncMock())
        loc.first = loc
        return loc


async def _attach(page: _PickerPage, query: str) -> None:
    await edit_mod._attach_by_click(
        page,  # type: ignore[arg-type]
        composer_selector="box",
        chip_selector="chip",
        query=query,
        media_id=_REF1,
        expect_count=1,
        what="reference image",
    )


@pytest.mark.asyncio
async def test_a_fresh_upload_is_awaited_until_the_picker_lists_it(clock: _Clock) -> None:
    """Live 2026-11-25: a just-uploaded reference was not listed for the first 3 tries."""
    page = _PickerPage(["ref-f823374b.png"], listed_from=6)
    await _attach(page, "ref-f823374b.png")
    assert page.chips == 1
    assert page.polls == 6


@pytest.mark.asyncio
async def test_an_upload_never_listed_times_out_naming_its_media_id(clock: _Clock) -> None:
    page = _PickerPage(["ref-f823374b.png"], listed_from=10_000)
    with pytest.raises(ReferenceNotFoundError, match=_REF1):
        await _attach(page, "ref-f823374b.png")
    assert clock.now >= edit_mod.VIDEO_INGEST_READY_BUDGET_S - 30


@pytest.mark.parametrize(
    ("query", "option"),
    [
        ("ref-215ca6ac.png", "ref-215ca6ac.png"),
        ("ref-215ca6ac.png", "ref-215ca6ac"),  # the option drops the extension
        ("source-11628e4b", "source-11628e4b.mp4"),  # the option adds one
        ("source-11628e4b", "Video source-11628e4b 0:08"),  # other words around it
        ("Ref-215CA6AC.PNG", "ref-215ca6ac.png"),  # case-insensitive
        # Live 2026-11-25: the name and a media-type label run together in the option text.
        ("source-5e66c86f", "source-5e66c86fVideo"),
        ("ref-d56d9d65.png", "ref-d56d9d65Image"),
        ("ref-d56d9d65.png", "ref-d56d9d65.png Image"),
        ("ref-d56d9d65.png", "ref-d56d9d65.pngImage"),  # the clip editor's asset browser
    ],
)
def test_an_option_names_the_upload_with_or_without_its_extension(query: str, option: str) -> None:
    assert edit_mod._names_option(query, option)


@pytest.mark.parametrize(
    "option",
    ["ref-215ca6ac-2.png", "xref-215ca6ac.png", "ref-215ca6", "ref-215ca6ac0Image"],
)
def test_an_option_naming_another_upload_does_not_match(option: str) -> None:
    assert not edit_mod._names_option("ref-215ca6ac.png", option)


@pytest.mark.parametrize("option", ["source-5e66c86f0Video", "source-5e66c86fVideos"])
def test_a_longer_token_before_the_label_does_not_match(option: str) -> None:
    assert not edit_mod._names_option("source-5e66c86f", option)


@pytest.mark.asyncio
async def test_a_timeout_lists_what_the_picker_offered(clock: _Clock) -> None:
    page = _PickerPage(["other-1.png", "x" * 80], listed_from=0)
    with pytest.raises(ReferenceNotFoundError, match="offered: 'other-1.png'") as info:
        await _attach(page, "ref-215ca6ac.png")
    assert "..." in info.value.detail


@pytest.mark.asyncio
async def test_the_picker_option_is_matched_as_a_whole_name(clock: _Clock) -> None:
    # "clip" must not bind "clip-2" (another upload whose name it prefixes).
    page = _PickerPage(["clip-2", "clips.mp4"], listed_from=0)
    with pytest.raises(ReferenceNotFoundError):
        await _attach(page, "clip")
    assert page.chips == 0
    # Two assets with exactly the name: ambiguous, refused rather than guessed.
    page = _PickerPage(["clip", "clip.mp4"], listed_from=0)
    with pytest.raises(ReferenceNotFoundError, match="lists 2 assets"):
        await _attach(page, "clip")


@pytest.mark.asyncio
async def test_a_selected_model_the_trigger_does_not_confirm_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger = MagicMock(text_content=AsyncMock(return_value="Veo 3.1 - Fast"))
    items = MagicMock(
        all_text_contents=AsyncMock(return_value=["Omni 1.2 Flash", "Veo 3.1 - Fast"]),
        nth=MagicMock(return_value=MagicMock(click=AsyncMock())),
    )
    monkeypatch.setattr(edit_mod, "_anchor", AsyncMock(return_value=trigger))
    monkeypatch.setattr(
        edit_mod, "_open_clip_editor_model_menu", AsyncMock(return_value=(trigger, items))
    )
    page = MagicMock(keyboard=MagicMock(press=AsyncMock()), wait_for_timeout=AsyncMock())
    with pytest.raises(UiSelectorDriftError, match="shows model"):
        await edit_mod._select_clip_editor_model(page, VideoModel.OMNI_1_2_FLASH)


# --- the clip editor's asset browser (spike 2026-11-25) -----------------------------


class _AssetBrowserPage:
    """The clip editor's ``@``: Flow's asset browser, filtered by what was typed.

    Items are ``flow-add-menu-asset-list button[role=option].asset-item`` with the text
    ``<name>Image``; a click selects one, and the detail pane's add button attaches it.
    """

    def __init__(self, names: list[str], *, listed_from: int = 0) -> None:
        self.names = names
        self.listed_from = listed_from
        self.polls = 0
        self.typed = ""
        self.selected: int | None = None
        self.chips = 0
        self.keyboard = MagicMock(type=AsyncMock(side_effect=self._typed), press=AsyncMock())

    async def _typed(self, text: str, **_kw: Any) -> None:
        if text == "@":
            self.polls += 1
            self.typed = ""
        elif text.strip():
            self.typed = text

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def evaluate(self, *_a: Any) -> None:
        return None

    def _listed(self) -> list[str]:
        if self.polls < self.listed_from:
            return []
        return [f"{n}Image" for n in self.names if n.casefold().startswith(self.typed.casefold())]

    def locator(self, sel: str) -> Any:
        page = self
        if sel == edit_mod.ASSET_BROWSER_OPTION:

            def nth(i: int) -> Any:
                return MagicMock(
                    is_visible=AsyncMock(return_value=True),
                    click=AsyncMock(side_effect=lambda **_k: setattr(page, "selected", i)),
                )

            return MagicMock(
                all_text_contents=AsyncMock(side_effect=page._listed),
                nth=MagicMock(side_effect=nth),
            )
        if sel == edit_mod.ASSET_BROWSER_ADD:

            async def add(**_k: Any) -> None:
                page.chips += page.selected is not None

            button = MagicMock(
                count=AsyncMock(return_value=1),
                is_visible=AsyncMock(return_value=True),
                is_disabled=AsyncMock(return_value=False),
                click=AsyncMock(side_effect=add),
            )
            button.first = button
            return button
        if sel == "chip":
            return MagicMock(count=AsyncMock(side_effect=lambda: page.chips))
        loc = MagicMock(count=AsyncMock(return_value=0), click=AsyncMock())
        loc.first = loc
        return loc


async def _attach_in_clip_editor(page: _AssetBrowserPage, query: str) -> None:
    await edit_mod._attach_by_click(
        page,  # type: ignore[arg-type]
        composer_selector="box",
        chip_selector="chip",
        query=query,
        media_id=_REF1,
        expect_count=1,
        what="reference image",
        picker=edit_mod.CLIP_EDITOR_ASSET_BROWSER,
    )


@pytest.mark.asyncio
async def test_the_clip_editor_attaches_through_the_asset_browser(clock: _Clock) -> None:
    page = _AssetBrowserPage(["ref-1cbf2bef.png", "ref-d56d9d65.png"])
    await _attach_in_clip_editor(page, "ref-d56d9d65.png")
    assert page.chips == 1
    assert page.selected == 0  # the one item the typed name left in the list
    assert page.typed == "ref-d56d9d65"  # the name without its extension filters the list


@pytest.mark.asyncio
async def test_the_asset_browser_is_polled_until_the_upload_is_listed(clock: _Clock) -> None:
    page = _AssetBrowserPage(["ref-d56d9d65.png"], listed_from=4)
    await _attach_in_clip_editor(page, "ref-d56d9d65.png")
    assert page.chips == 1
    assert page.polls == 4


@pytest.mark.asyncio
async def test_two_items_with_the_name_in_the_asset_browser_are_refused(clock: _Clock) -> None:
    page = _AssetBrowserPage(["ref-d56d9d65.png", "ref-d56d9d65.jpg"])
    with pytest.raises(ReferenceNotFoundError, match="lists 2 assets"):
        await _attach_in_clip_editor(page, "ref-d56d9d65.png")
    assert page.chips == 0


@pytest.mark.asyncio
async def test_an_asset_browser_timeout_lists_the_items_offered(clock: _Clock) -> None:
    page = _AssetBrowserPage(["other-11112222.png"])
    page.typed = ""
    with pytest.raises(ReferenceNotFoundError, match="the picker offered"):
        await _attach_in_clip_editor(page, "ref-d56d9d65.png")
