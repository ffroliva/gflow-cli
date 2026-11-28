"""In-project image UUID Start/End frames on flow.google.com (#1542).

Covers the migrated Frames picker binding path (`migrated_existing_frames.py`),
`_unported_form` gating, caption sanitization, colliding-caption token matching,
stale-caption `fill("")` unfiltered fallback, `#792` confirm click, caption redaction
in error messages, reload-before-settings ordering, and uploaded-only `adopt_ids`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports import migrated_existing_frames as mef
from gflow_cli.api.transports.batchexecute import GenerationRecord
from gflow_cli.api.transports.migrated_composer import (
    BOUND_CHIP,
    EMPTY_CHIP,
    OVERLAY,
    PICKER,
    PICKER_CONFIRM,
    PICKER_OPTION,
    PICKER_SEARCH,
    MigratedComposer,
    _unported_form,
    migrated_can_serve,
    run_video,
)
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.errors import (
    FlowHostMigratedError,
    ReferenceNotFoundError,
    UiSelectorDriftError,
)

START_UUID = "11111111-1111-4111-8111-111111111111"
END_UUID = "22222222-2222-4222-8222-222222222222"
UPLOADED_START_UUID = "33333333-3333-4333-8333-333333333333"
UPLOADED_END_UUID = "44444444-4444-4444-8444-444444444444"


def _req(**kw: Any) -> GenerateVideoRequest:
    base: dict[str, Any] = {
        "prompt": "slow camera push",
        "mode": Mode.I2V,
        "aspect": Aspect.PORTRAIT,
        "model": VideoModel.VEO_3_1_LITE,
    }
    base.update(kw)
    return GenerateVideoRequest(**base)


def _png(tmp_path: Path, name: str = "frame.png") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return path


# --------------------------------------------------------------------------- _unported_form


def test_unported_form_allows_in_project_captioned_uuid_frames_and_refuses_others(
    tmp_path: Path,
) -> None:
    png = _png(tmp_path)

    # Ported combinations: same-project + non-empty caption.
    start_ok = _req(
        start_image_ref_id=START_UUID,
        start_image_ref_display_name="Stone corridor",
        start_image_ref_in_project=True,
    )
    assert _unported_form(start_ok) is None
    assert migrated_can_serve(start_ok, "proj-1") is True

    both_ok = _req(
        start_image_ref_id=START_UUID,
        start_image_ref_display_name="Stone corridor",
        start_image_ref_in_project=True,
        end_image_ref_id=END_UUID,
        end_image_ref_display_name="Warm lamp",
        end_image_ref_in_project=True,
    )
    assert _unported_form(both_ok) is None
    assert migrated_can_serve(both_ok, "proj-1") is True

    mixed_local_start = _req(
        start_image=png,
        end_image_ref_id=END_UUID,
        end_image_ref_display_name="Warm lamp",
        end_image_ref_in_project=True,
    )
    assert _unported_form(mixed_local_start) is None

    mixed_local_end = _req(
        start_image_ref_id=START_UUID,
        start_image_ref_display_name="Stone corridor",
        start_image_ref_in_project=True,
        end_image=png,
    )
    assert _unported_form(mixed_local_end) is None

    # Refused at $0 (exit 36): cross-project or missing/whitespace caption.
    cross_start = _req(
        start_image_ref_id=START_UUID,
        start_image_ref_display_name="Stone corridor",
        start_image_ref_in_project=False,
    )
    assert "not record in this --project" in (_unported_form(cross_start) or "")
    assert migrated_can_serve(cross_start, "proj-1") is False

    cross_end = _req(
        start_image=png,
        end_image_ref_id=END_UUID,
        end_image_ref_display_name="Warm lamp",
        end_image_ref_in_project=False,
    )
    end_reason = _unported_form(cross_end) or ""
    assert "end frame given by Flow media UUID" in end_reason
    assert "not record in this --project" in end_reason

    uncaptioned_start = _req(
        start_image_ref_id=START_UUID,
        start_image_ref_display_name="   ",
        start_image_ref_in_project=True,
    )
    assert "without a catalog caption" in (_unported_form(uncaptioned_start) or "")

    uncaptioned_end = _req(
        start_image=png,
        end_image_ref_id=END_UUID,
        end_image_ref_display_name="",
        end_image_ref_in_project=True,
    )
    end_uncaptioned = _unported_form(uncaptioned_end) or ""
    assert "end frame given by Flow media UUID" in end_uncaptioned
    assert "without a catalog caption" in end_uncaptioned


# --------------------------------------------------------------------------- frame_search_query


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Stone corridor", "Stone corridor"),
        ("  Stone corridor  ", "Stone corridor"),
        ("Golden lantern over quiet harbor…", "Golden lantern over quiet harbor"),
        ("Golden lantern over quiet harbor...", "Golden lantern over quiet harbor"),
        ("   …  ", ""),
        ("", ""),
        ("   ", ""),
        ("x" * 121, ""),
        ("line1\nline2", ""),
        ("tab\there", ""),
        ("zero\u200bwidth", ""),
        ("line\u2028sep", ""),
        ("para\u2029sep", ""),
    ],
)
def test_frame_search_query_sanitizes_and_strips_ellipsis(raw: str, expected: str) -> None:
    assert mef.frame_search_query(raw) == expected


# ----------------------------------------------------------------------- Fake Frames Picker Page


class _FakeLocator:
    def __init__(
        self,
        page: FakeFramesPage,
        kind: str,
        index: int = 0,
        token: str | None = None,
    ) -> None:
        self.page = page
        self.kind = kind
        self.index = index
        self.token = token

    @property
    def first(self) -> _FakeLocator:
        return _FakeLocator(self.page, self.kind, 0, token=self.token)

    @property
    def last(self) -> _FakeLocator:
        return self

    def nth(self, idx: int) -> _FakeLocator:
        return _FakeLocator(self.page, self.kind, idx, token=self.token)

    def filter(self, **_: Any) -> _FakeLocator:
        return self

    def locator(self, css: str) -> _FakeLocator:
        if css == PICKER_SEARCH:
            return _FakeLocator(self.page, "search")
        if css == PICKER_OPTION:
            return _FakeLocator(self.page, "option")
        if css.startswith(f"{PICKER_OPTION}:has(img[src*='/asb/"):
            prefix = f"{PICKER_OPTION}:has(img[src*='/asb/"
            tok = css[len(prefix) :].split("'", 1)[0]
            return _FakeLocator(self.page, "option", token=tok)
        if css == PICKER_CONFIRM:
            return _FakeLocator(self.page, "confirm")
        raise AssertionError(f"unexpected child selector: {css!r}")

    async def count(self) -> int:
        if self.kind == "empty_chip":
            return max(0, 2 - self.page.bound_chips)
        if self.kind == "bound_chip":
            return self.page.bound_chips
        if self.kind == "option":
            return len(self.page.visible_options())
        if self.kind == "confirm":
            return 1 if self.page.has_confirm else 0
        return 1

    async def is_visible(self) -> bool:
        if self.kind == "confirm":
            return self.page.picker_open and self.page.has_confirm
        if self.kind == "picker":
            return self.page.picker_open
        return True

    async def wait_for(self, *, state: str = "visible", timeout: int = 4000) -> None:
        del timeout
        if self.kind == "picker" and state == "hidden":
            if self.page.picker_open:
                raise PlaywrightTimeoutError("picker stayed open")
            return
        if self.kind == "bound_chip" and state == "visible":
            if self.page.bound_chips <= self.index:
                raise PlaywrightTimeoutError("chip not bound")
            return
        if self.kind == "search" and state == "visible":
            if not self.page.picker_open:
                raise PlaywrightTimeoutError("picker did not open")
            return

    async def click(self, **_: Any) -> None:
        if self.kind == "empty_chip":
            self.page.picker_open = True
            self.page.search_value = ""
            return
        if self.kind == "search":
            return
        if self.kind == "option":
            opts = self.page.visible_options()
            if self.token is not None:
                matches = [tok for _title, tok in opts if tok == self.token]
                assert matches, f"token {self.token!r} not in visible options {opts!r}"
                chosen_token = matches[0]
            else:
                chosen_token = opts[self.index][1]
            self.page.clicked_tokens.append(chosen_token)
            if self.page.auto_close:
                self.page.picker_open = False
                if not self.page.drop_bound_chip:
                    self.page.bound_chips += 1
            return
        if self.kind == "confirm":
            self.page.confirm_clicks += 1
            if self.page.confirm_commits:
                self.page.picker_open = False
                if not self.page.drop_bound_chip:
                    self.page.bound_chips += 1
            return
        raise AssertionError(f"unexpected click on {self.kind!r}")

    async def fill(self, value: str, **_: Any) -> None:
        assert self.kind == "search"
        self.page.fills.append(value)
        self.page.search_value = value


class _FakeKeyboard:
    def __init__(self, page: FakeFramesPage) -> None:
        self.page = page

    async def insert_text(self, text: str) -> None:
        self.page.inserted.append(text)
        self.page.search_value = text

    async def press(self, key: str) -> None:
        self.page.keys.append(key)
        if key == "Escape":
            self.page.picker_open = False


class FakeFramesPage:
    """In-memory model of flow.google.com's project grid + Frames picker."""

    def __init__(
        self,
        *,
        grid: dict[str, str],
        options: list[tuple[str, str]],
        grid_after_reload: int = 0,
        auto_close: bool = True,
        has_confirm: bool = False,
        confirm_commits: bool = True,
        drop_bound_chip: bool = False,
    ) -> None:
        self.url = "https://flow.google.com/project/proj-1"
        self.grid = grid
        self._options = options
        self.grid_after_reload = grid_after_reload
        self.auto_close = auto_close
        self.has_confirm = has_confirm
        self.confirm_commits = confirm_commits
        self.drop_bound_chip = drop_bound_chip
        self.keyboard = _FakeKeyboard(self)
        self.picker_open = False
        self.search_value = ""
        self.bound_chips = 0
        self.reloads = 0
        self.confirm_clicks = 0
        self.inserted: list[str] = []
        self.fills: list[str] = []
        self.keys: list[str] = []
        self.clicked_tokens: list[str] = []
        self.grid_queries: list[str] = []

    def visible_options(self) -> list[tuple[str, str]]:
        if not self.picker_open:
            return []
        q = self.search_value.strip().casefold()
        if not q:
            return list(self._options)
        return [o for o in self._options if q in o[0].casefold()]

    def locator(self, css: str) -> _FakeLocator:
        if css == EMPTY_CHIP:
            return _FakeLocator(self, "empty_chip")
        if css == BOUND_CHIP:
            return _FakeLocator(self, "bound_chip")
        if css in (OVERLAY, PICKER):
            return _FakeLocator(self, "picker")
        if css == PICKER_OPTION:
            return _FakeLocator(self, "option")
        raise AssertionError(f"unmodelled selector: {css!r}")

    async def reload(self, **_: Any) -> None:
        self.reloads += 1

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if "data-media-id" in script:
            self.grid_queries.append(str(arg))
            return self.grid.get(str(arg), "") if self.reloads >= self.grid_after_reload else ""
        if "asb" in script:
            return [tok for _title, tok in self.visible_options()]
        raise AssertionError(f"unexpected evaluate: {script!r}")


def _make_composer(*, editor_kind: str = "classic") -> MigratedComposer:
    composer = MigratedComposer()

    async def _ensure(*_: Any, **__: Any) -> str:
        return editor_kind

    async def _click(_page: Any, loc: Any, **_: Any) -> None:
        await loc.click()

    composer.ensure_editor = _ensure  # type: ignore[method-assign]
    composer._click = _click  # type: ignore[method-assign]
    return composer


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mef, "EXISTING_REF_WAIT_S", 0.15)
    monkeypatch.setattr(mef, "EXISTING_REF_RELOAD_PAUSE_S", 0.01)
    monkeypatch.setattr(mef, "FRAME_SEARCH_RETRY_PAUSE_S", 0.01)
    monkeypatch.setattr(mef, "FRAME_PICKER_POLL_S", 0.01)
    monkeypatch.setattr(mef, "FRAME_PICKER_SETTLE_S", 0.05)


# --------------------------------------------------------------------------- Grid token wait


async def test_await_existing_frame_tokens_returns_immediately_without_reload() -> None:
    page: Any = FakeFramesPage(
        grid={START_UUID: "tokStart", END_UUID: "tokEnd"},
        options=[],
    )
    tokens = await mef.await_existing_frame_tokens(
        _make_composer(), page, "proj-1", (START_UUID, END_UUID, START_UUID)
    )
    assert tokens == {START_UUID: "tokStart", END_UUID: "tokEnd"}
    assert page.reloads == 0
    assert page.grid_queries == [START_UUID, END_UUID]


async def test_await_existing_frame_tokens_reloads_until_tile_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mef, "EXISTING_REF_WAIT_S", 30.0)
    page: Any = FakeFramesPage(
        grid={START_UUID: "tokStart"},
        options=[],
        grid_after_reload=2,
    )
    tokens = await mef.await_existing_frame_tokens(_make_composer(), page, "proj-1", (START_UUID,))
    assert tokens == {START_UUID: "tokStart"}
    assert page.reloads == 2


async def test_await_existing_frame_tokens_refuses_agent_only_after_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mef, "EXISTING_REF_WAIT_S", 30.0)
    page: Any = FakeFramesPage(grid={START_UUID: "tokStart"}, options=[], grid_after_reload=1)
    composer = _make_composer(editor_kind="agent_only")
    with pytest.raises(FlowHostMigratedError, match="image-to-video"):
        await mef.await_existing_frame_tokens(composer, page, "proj-1", (START_UUID,))
    assert page.reloads == 1


async def test_await_existing_frame_tokens_raises_exit_32_on_missing_tile() -> None:
    page: Any = FakeFramesPage(grid={}, options=[])
    with pytest.raises(ReferenceNotFoundError, match=START_UUID):
        await mef.await_existing_frame_tokens(_make_composer(), page, "proj-1", (START_UUID,))
    assert page.reloads >= 1


# --------------------------------------------------------------------------- Picker selection


async def test_existing_frame_picker_disambiguates_colliding_captions_by_token() -> None:
    page: Any = FakeFramesPage(
        grid={START_UUID: "tokNew"},
        options=[("Stone corridor", "tokOld"), ("Stone corridor", "tokNew")],
    )
    mid = await mef.attach_existing_frame(
        _make_composer(),
        page,
        media_id=START_UUID,
        display_name="Stone corridor",
        token="tokNew",
    )
    assert mid == START_UUID
    assert page.clicked_tokens == ["tokNew"]
    assert page.bound_chips == 1
    assert page.inserted == ["Stone corridor"]
    assert page.fills == []


async def test_existing_frame_picker_strips_ellipsis_before_typing() -> None:
    page: Any = FakeFramesPage(
        grid={START_UUID: "tokEllipsis"},
        options=[("Golden lantern over quiet harbor at dusk", "tokEllipsis")],
    )
    mid = await mef.attach_existing_frame(
        _make_composer(),
        page,
        media_id=START_UUID,
        display_name="Golden lantern over quiet harbor…",
        token="tokEllipsis",
    )
    assert mid == START_UUID
    assert page.inserted == ["Golden lantern over quiet harbor"]
    assert page.clicked_tokens == ["tokEllipsis"]


async def test_existing_frame_picker_skips_typing_for_unsanitized_caption() -> None:
    page: Any = FakeFramesPage(
        grid={START_UUID: "tokRaw"},
        options=[("Unfiltered tile", "tokRaw")],
    )
    mid = await mef.attach_existing_frame(
        _make_composer(),
        page,
        media_id=START_UUID,
        display_name="line1\nline2",
        token="tokRaw",
    )
    assert mid == START_UUID
    assert page.inserted == []
    assert page.fills == []
    assert page.clicked_tokens == ["tokRaw"]
    assert page.bound_chips == 1


async def test_existing_frame_picker_falls_back_via_fill_empty_on_stale_caption() -> None:
    page: Any = FakeFramesPage(
        grid={START_UUID: "tokRenamed"},
        options=[("Unrelated", "tokOther"), ("Renamed in Flow UI", "tokRenamed")],
    )
    mid = await mef.attach_existing_frame(
        _make_composer(),
        page,
        media_id=START_UUID,
        display_name="Old stale catalog caption",
        token="tokRenamed",
    )
    assert mid == START_UUID
    assert page.inserted == ["Old stale catalog caption"]
    assert page.fills == [""]
    assert page.clicked_tokens == ["tokRenamed"]
    assert page.bound_chips == 1


async def test_existing_frame_picker_handles_confirm_cohort() -> None:
    page: Any = FakeFramesPage(
        grid={START_UUID: "tokConfirm"},
        options=[("Stone corridor", "tokConfirm")],
        auto_close=False,
        has_confirm=True,
        confirm_commits=True,
    )
    mid = await mef.attach_existing_frame(
        _make_composer(),
        page,
        media_id=START_UUID,
        display_name="Stone corridor",
        token="tokConfirm",
    )
    assert mid == START_UUID
    assert page.confirm_clicks == 1
    assert page.bound_chips == 1


async def test_existing_frame_picker_errors_never_leak_raw_caption() -> None:
    secret_caption = "PRIVATE-SENSITIVE-CAPTION-999"
    # Miss in picker -> ReferenceNotFoundError without caption text
    page_miss: Any = FakeFramesPage(
        grid={START_UUID: "tokMissing"},
        options=[("Other tile", "tokOther")],
    )
    with pytest.raises(ReferenceNotFoundError) as miss_exc:
        await mef.attach_existing_frame(
            _make_composer(),
            page_miss,
            media_id=START_UUID,
            display_name=secret_caption,
            token="tokMissing",
        )
    assert START_UUID in str(miss_exc.value)
    assert secret_caption not in str(miss_exc.value)

    # Confirm-missing drift -> UiSelectorDriftError without caption text
    page_stuck: Any = FakeFramesPage(
        grid={START_UUID: "tokStuck"},
        options=[(secret_caption, "tokStuck")],
        auto_close=False,
        has_confirm=False,
    )
    with pytest.raises(UiSelectorDriftError) as stuck_exc:
        await mef.attach_existing_frame(
            _make_composer(),
            page_stuck,
            media_id=START_UUID,
            display_name=secret_caption,
            token="tokStuck",
        )
    assert START_UUID in str(stuck_exc.value)
    assert secret_caption not in str(stuck_exc.value)

    # Unbound chip -> UiSelectorDriftError without caption text
    page_unbound: Any = FakeFramesPage(
        grid={START_UUID: "tokUnbound"},
        options=[(secret_caption, "tokUnbound")],
        drop_bound_chip=True,
    )
    with pytest.raises(UiSelectorDriftError) as unbound_exc:
        await mef.attach_existing_frame(
            _make_composer(),
            page_unbound,
            media_id=START_UUID,
            display_name=secret_caption,
            token="tokUnbound",
        )
    assert START_UUID in str(unbound_exc.value)
    assert secret_caption not in str(unbound_exc.value)


# --------------------------------------------------------------------------- run_video wiring


def _wire_run_video(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tokens: dict[str, str],
) -> dict[str, Any]:
    """Stub MigratedComposer steps to record exact call order and submit kwargs."""
    trace: dict[str, Any] = {"order": [], "attached": [], "submit": {}}

    async def _ensure_for(*_: Any, **__: Any) -> str:
        return "classic"

    async def _await_tokens(
        _c: Any, _p: Any, _pid: str, media_ids: tuple[str, ...]
    ) -> dict[str, str]:
        trace["order"].append(("await_tokens", media_ids))
        return {mid: tokens[mid] for mid in media_ids}

    async def _apply_settings(*_: Any, **__: Any) -> int:
        trace["order"].append(("apply_video_settings",))
        return 10

    async def _attach_existing(
        _c: Any,
        _p: Any,
        *,
        media_id: str,
        display_name: str,
        token: str,
        chip_label: str = "Start",
        expect_bound_chips: int = 1,
    ) -> str:
        trace["order"].append(("attach_existing", chip_label, media_id, expect_bound_chips))
        trace["attached"].append((chip_label, media_id, display_name, token, expect_bound_chips))
        return media_id

    async def _upload_start(*_: Any, **__: Any) -> str:
        trace["order"].append(("attach_start_frame",))
        return UPLOADED_START_UUID

    async def _upload_end(*_: Any, **__: Any) -> str:
        trace["order"].append(("attach_end_frame",))
        return UPLOADED_END_UUID

    async def _send_prompt(*_: Any, **__: Any) -> None:
        trace["order"].append(("send_prompt",))

    async def _submit(*_a: Any, **kw: Any) -> GenerationRecord:
        trace["submit"] = kw
        return GenerationRecord(
            workflow_id="wf-1",
            project_id="proj-1",
            media_id="vid-1",
            status=3,
            video_url="https://x",
        )

    monkeypatch.setattr(MigratedComposer, "ensure_editor_for", _ensure_for)
    monkeypatch.setattr(MigratedComposer, "apply_video_settings", _apply_settings)
    monkeypatch.setattr(MigratedComposer, "attach_start_frame", _upload_start)
    monkeypatch.setattr(MigratedComposer, "attach_end_frame", _upload_end)
    monkeypatch.setattr(MigratedComposer, "send_prompt", _send_prompt)
    monkeypatch.setattr(MigratedComposer, "submit_and_observe", _submit)
    monkeypatch.setattr(mef, "await_existing_frame_tokens", _await_tokens)
    monkeypatch.setattr(mef, "attach_existing_frame", _attach_existing)
    return trace


class _MinimalPage:
    url = "https://flow.google.com/project/proj-1"

    def on(self, *_: Any) -> None:
        return None

    def remove_listener(self, *_: Any) -> None:
        return None


async def test_run_video_resolves_tokens_before_settings_and_excludes_uuids_from_adopt_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = _wire_run_video(monkeypatch, tokens={START_UUID: "tokS", END_UUID: "tokE"})
    request = _req(
        start_image_ref_id=START_UUID,
        start_image_ref_display_name="Stone corridor",
        start_image_ref_in_project=True,
        end_image_ref_id=END_UUID,
        end_image_ref_display_name="Warm lamp",
        end_image_ref_in_project=True,
    )
    await run_video(
        _MinimalPage(),  # type: ignore[arg-type]
        request,
        project_id="proj-1",
        save=AsyncMock(),
        poll_timeout_s=5.0,
        download=False,
        on_started=None,
    )
    # Token lookup MUST run before apply_video_settings so reloads never wipe settings.
    assert trace["order"][0] == ("await_tokens", (START_UUID, END_UUID))
    assert trace["order"][1] == ("apply_video_settings",)
    assert trace["order"][2] == ("attach_existing", "Start", START_UUID, 1)
    assert trace["order"][3] == ("attach_existing", "End", END_UUID, 2)
    assert ("attach_start_frame",) not in trace["order"]
    assert ("attach_end_frame",) not in trace["order"]
    # Route guard checks BOTH UUIDs, while adopt_ids excludes reused UUIDs (#1112 hazard).
    assert trace["submit"]["expect_media_id"] == START_UUID
    assert trace["submit"]["expect_end_media_id"] == END_UUID
    assert trace["submit"]["adopt_ids"] == ()


async def test_run_video_mixed_local_and_uuid_puts_only_uploaded_id_in_adopt_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    png = _png(tmp_path)

    # Local Start + UUID End
    trace1 = _wire_run_video(monkeypatch, tokens={END_UUID: "tokE"})
    await run_video(
        _MinimalPage(),  # type: ignore[arg-type]
        _req(
            start_image=png,
            end_image_ref_id=END_UUID,
            end_image_ref_display_name="Warm lamp",
            end_image_ref_in_project=True,
        ),
        project_id="proj-1",
        save=AsyncMock(),
        poll_timeout_s=5.0,
        download=False,
        on_started=None,
    )
    assert trace1["order"][0] == ("await_tokens", (END_UUID,))
    assert trace1["order"][1] == ("apply_video_settings",)
    assert trace1["order"][2] == ("attach_start_frame",)
    assert trace1["order"][3] == ("attach_existing", "End", END_UUID, 2)
    assert trace1["submit"]["expect_media_id"] == UPLOADED_START_UUID
    assert trace1["submit"]["expect_end_media_id"] == END_UUID
    assert trace1["submit"]["adopt_ids"] == (UPLOADED_START_UUID,)

    # UUID Start + Local End
    trace2 = _wire_run_video(monkeypatch, tokens={START_UUID: "tokS"})
    await run_video(
        _MinimalPage(),  # type: ignore[arg-type]
        _req(
            start_image_ref_id=START_UUID,
            start_image_ref_display_name="Stone corridor",
            start_image_ref_in_project=True,
            end_image=png,
        ),
        project_id="proj-1",
        save=AsyncMock(),
        poll_timeout_s=5.0,
        download=False,
        on_started=None,
    )
    assert trace2["order"][0] == ("await_tokens", (START_UUID,))
    assert trace2["order"][1] == ("apply_video_settings",)
    assert trace2["order"][2] == ("attach_existing", "Start", START_UUID, 1)
    assert trace2["order"][3] == ("attach_end_frame",)
    assert trace2["submit"]["expect_media_id"] == START_UUID
    assert trace2["submit"]["expect_end_media_id"] == UPLOADED_END_UUID
    assert trace2["submit"]["adopt_ids"] == (UPLOADED_END_UUID,)


async def test_run_video_same_start_and_end_uuid_deduplicates_token_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = _wire_run_video(monkeypatch, tokens={START_UUID: "tokS"})
    await run_video(
        _MinimalPage(),  # type: ignore[arg-type]
        _req(
            start_image_ref_id=START_UUID,
            start_image_ref_display_name="Stone corridor",
            start_image_ref_in_project=True,
            end_image_ref_id=START_UUID,
            end_image_ref_display_name="Stone corridor",
            end_image_ref_in_project=True,
        ),
        project_id="proj-1",
        save=AsyncMock(),
        poll_timeout_s=5.0,
        download=False,
        on_started=None,
    )
    assert trace["order"][0] == ("await_tokens", (START_UUID,))
    assert trace["submit"]["expect_media_id"] == START_UUID
    assert trace["submit"]["expect_end_media_id"] == START_UUID
    assert trace["submit"]["adopt_ids"] == ()
