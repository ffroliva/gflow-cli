"""MigratedComposer — the flow.google.com (Angular Material) editor driver.

The fake page models exactly what the 2026-09-05 spike measured: a
`.settings-trigger-button`, a `.cdk-overlay-pane` with six `[role='radiogroup']`s
of `[role='radio']` buttons (ligature + label text, `aria-checked`), a model
`button` with an `arrow_drop_down` ligature that opens a `[role='menu']` of
`[role='menuitem']`s, a `textarea` that is NOT clickable, a `[contenteditable]`
composer, and an `arrow_forward` submit button. Locators resolve a fixed set of
CSS shapes — the composer must not grow new ones without a test here.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.errors import (
    ConfigurationError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)

# --- a tiny DOM -------------------------------------------------------------


@dataclass
class Radio:
    lig: str
    label: str
    checked: bool = False
    stale: bool = False  # click does not flip aria-checked (scenario 3)

    @property
    def text(self) -> str:
        return f"{self.lig}{self.label}"


@dataclass
class Dom:
    groups: dict[str, list[Radio]]
    models: list[str] = field(default_factory=lambda: ["Omni 1.1 Flash", "Veo 3.1 - Lite"])
    model_label: str = "Omni 1.1 Flash"
    trigger_present: bool = True
    pane_open: bool = False
    menu_open: bool = False
    prompt: str = ""
    submit_clicked: int = 0
    submit_enable_after_reads: int = 0  # Angular flips `disabled` a tick after insert_text (#670)
    menu_lingers: bool = False  # Angular keeps a detached menu pane as the LAST overlay
    events: list[str] = field(default_factory=list)


def _default_dom() -> Dom:
    return Dom(
        groups={
            "mode": [Radio("image", "Image"), Radio("videocam", "Video", checked=True)],
            "submode": [
                Radio("crop_free", "Frames", checked=True),
                Radio("chrome_extension", "Ingredients"),
            ],
            "aspect": [Radio("crop_16_9", "16:9", checked=True), Radio("crop_9_16", "9:16")],
            "resolution": [Radio("info", "360p"), Radio("", "720p", checked=True)],
            "duration": [
                Radio("", "4s"),
                Radio("", "6s"),
                Radio("", "8s", checked=True),
                Radio("", "10s"),
            ],
            "count": [
                Radio("", "x1", checked=True),
                Radio("", "x2"),
                Radio("", "x3"),
                Radio("", "x4"),
            ],
        }
    )


class FakeLocator:
    def __init__(self, page: FakePage, kind: str, items: list[Any]) -> None:
        self.page, self.kind, self.items = page, kind, items

    # --- narrowing --------------------------------------------------------
    @property
    def first(self) -> FakeLocator:
        return FakeLocator(self.page, self.kind, self.items[:1])

    @property
    def last(self) -> FakeLocator:
        return FakeLocator(self.page, self.kind, self.items[-1:])

    def filter(self, *, has: FakeLocator | None = None, has_text: Any = None) -> FakeLocator:
        items = self.items
        if has is not None and has.kind == "group":  # pane.filter(has=<radiogroup>)
            items = [i for i in items if i == "pane" and self.page.dom.pane_open]
        elif has is not None:  # `has=page.locator("mat-icon").filter(has_text=re)` → ligature match
            pat = has.page._pending_lig
            items = [
                r for r in items if isinstance(r, Radio) and pat is not None and pat.search(r.lig)
            ]
        if has_text is not None:
            pat = has_text if hasattr(has_text, "search") else re.compile(re.escape(str(has_text)))
            items = [i for i in items if pat.search(i.text if isinstance(i, Radio) else str(i))]
        return FakeLocator(self.page, self.kind, items)

    def locator(self, css: str) -> FakeLocator:
        return self.page.locator(css, scope=self)

    # --- reads --------------------------------------------------------------
    async def count(self) -> int:
        return len(self.items)

    async def is_visible(self) -> bool:
        return bool(self.items)

    async def is_enabled(self) -> bool:
        if self.kind == "submit":
            dom = self.page.dom
            if dom.submit_enable_after_reads > 0:
                dom.submit_enable_after_reads -= 1
                return False
            return bool(dom.prompt)
        return bool(self.items)

    async def get_attribute(self, name: str) -> str | None:
        if name == "aria-checked" and self.items and isinstance(self.items[0], Radio):
            return "true" if self.items[0].checked else "false"
        return None

    async def text_content(self) -> str | None:
        return str(self.items[0]) if self.items else None

    async def all_text_contents(self) -> list[str]:
        return [str(i) for i in self.items]

    async def wait_for(self, *, state: str = "visible", timeout: float = 0) -> None:
        if not self.items:
            await asyncio.sleep(0)
            msg = f"waiting for {self.kind} to be {state}"
            raise PlaywrightTimeoutError(msg)

    # --- actions ------------------------------------------------------------
    async def click(self, **_: Any) -> None:
        if not self.items:
            raise PlaywrightTimeoutError(f"click: no {self.kind}")
        dom = self.page.dom
        target = self.items[0]
        if self.kind == "trigger":
            dom.pane_open = not dom.pane_open
        elif self.kind == "radio":
            if not target.stale:
                group = next(g for g in dom.groups.values() if target in g)
                for r in group:
                    r.checked = r is target
        elif self.kind == "model_button":
            dom.menu_open = True
        elif self.kind == "menuitem":
            dom.model_label = str(target)
            dom.menu_open = False
        elif self.kind == "textarea":
            raise PlaywrightTimeoutError("Locator.click: Timeout 4000ms exceeded (textarea)")
        elif self.kind == "composer":
            dom.events.append("composer_focused")
        elif self.kind == "submit":
            dom.submit_clicked += 1
            self.page._fire_submit()


class PlaywrightTimeoutError(Exception):
    pass


class FakeKeyboard:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def press(self, key: str) -> None:
        if key == "Escape":
            if self.page.dom.menu_open:
                self.page.dom.menu_open = False
            else:
                self.page.dom.pane_open = False

    async def type(self, text: str, **_: Any) -> None:
        self.page.dom.prompt += text

    async def insert_text(self, text: str) -> None:
        self.page.dom.prompt += text


class FakeResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url, self._text, self.status = url, text, 200

    async def text(self) -> str:
        return self._text


class FakePage:
    """Resolves the fixed set of CSS the composer is allowed to use."""

    def __init__(
        self, dom: Dom | None = None, *, url: str = "https://flow.google.com/project/p1"
    ) -> None:
        self.dom = dom or _default_dom()
        self.url = url
        self.keyboard = FakeKeyboard(self)
        self.gotos: list[str] = []
        self._handlers: list[Any] = []
        self._pending_lig: re.Pattern[str] | None = None
        self.scripted_responses: list[tuple[str, str]] = []  # fired on submit click

    async def goto(self, url: str, **_: Any) -> None:
        self.gotos.append(url)
        self.url = url

    def on(self, event: str, handler: Any) -> None:
        assert event == "response"
        self._handlers.append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        self._handlers = [h for h in self._handlers if h is not handler]

    def _fire_submit(self) -> None:
        for url, text in self.scripted_responses:
            for h in list(self._handlers):
                asyncio.get_event_loop().create_task(_maybe_await(h(FakeResponse(url, text))))

    def locator(self, css: str, *, scope: FakeLocator | None = None) -> FakeLocator:
        dom = self.dom
        if css == ".settings-trigger-button":
            return FakeLocator(self, "trigger", ["trigger"] if dom.trigger_present else [])
        if css == ".cdk-overlay-pane":
            panes = (["pane"] if dom.pane_open else []) + (["menu"] if dom.menu_open else [])
            if dom.menu_lingers and dom.pane_open and not dom.menu_open:
                panes.append("stale-menu")  # what `.last` sees after a model switch
            return FakeLocator(self, "pane", panes)
        if css == "[role='radiogroup']":
            if scope is not None and scope.items and scope.items[0] != "pane":
                return FakeLocator(self, "group", [])  # a menu pane has no option groups
            return FakeLocator(self, "group", list(dom.groups) if dom.pane_open else [])
        if css == "[role='radio']":
            if scope is not None and scope.items and scope.items[0] != "pane":
                return FakeLocator(self, "radio", [])
            radios = [r for g in dom.groups.values() for r in g] if dom.pane_open else []
            return FakeLocator(self, "radio", radios)
        if css == "mat-icon":
            # the composer builds `page.locator("mat-icon").filter(has_text=re)` and passes
            # it as `has=`; remember the regex so the parent filter can apply it
            loc = FakeLocator(self, "icon", [])
            orig = loc.filter

            def _filter(*, has: Any = None, has_text: Any = None) -> FakeLocator:
                self._pending_lig = has_text
                return orig(has=has, has_text=None)

            loc.filter = _filter  # type: ignore[method-assign]
            return loc
        if css == "button":
            # only ever narrowed by a ligature: arrow_drop_down (model) or arrow_forward (submit)
            return _ButtonLocator(self)
        if css == "[role='menuitem']":
            return FakeLocator(self, "menuitem", list(dom.models) if dom.menu_open else [])
        if css == "textarea":
            return FakeLocator(self, "textarea", ["textarea"])
        if css == "[contenteditable='true']":
            return FakeLocator(self, "composer", ["composer"])
        raise AssertionError(f"composer used an unmodelled selector: {css!r}")


class _ButtonLocator(FakeLocator):
    def __init__(self, page: FakePage) -> None:
        super().__init__(page, "button", [])

    def filter(self, *, has: FakeLocator | None = None, has_text: Any = None) -> FakeLocator:
        pat = self.page._pending_lig
        lig = pat.pattern if pat is not None else ""
        if "arrow_drop_down" in lig:
            return FakeLocator(
                self.page,
                "model_button",
                [self.page.dom.model_label] if self.page.dom.pane_open else [],
            )
        if "arrow_forward" in lig:
            return FakeLocator(self.page, "submit", ["submit"])
        return FakeLocator(self.page, "button", [])


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value):
        await value


# --- fixtures ---------------------------------------------------------------

WF = "11111111-1111-4111-8111-111111111111"
PROJ = "p1"
MEDIA = "33333333-3333-4333-8333-333333333333"
VIDEO_URL = "https://flow-content.google/v/abc.mp4?Expires=1&KeyName=k&Signature=s"


def _record(status: int, url: str | None = None) -> list[Any]:
    """The captured record layout: DETAILS[10] = poster JPEG URL, MEDIA_INFO[0][8] =
    the mp4 URL (both only once done), DETAILS[13] = mp4 bytes."""
    details: list[Any] = [
        [1, 2],
        "prompt",
        None,
        None,
        None,
        None,
        [None, [["abra_t2v_8s", 1]]],
        None,
        [status],
        1,
    ]
    if url:
        details += ["https://flow-content.google/p/poster.jpg?Signature=p", [], None, 42]
    media_info: list[Any] = [
        [None, 1, None, None, None, None, None, "prompt", url, None, None, None, "abra_t2v_8s"],
        [None, None, [8]],
    ]
    return [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        MEDIA,
        "CAE",
        None,
        details,
        None,
        media_info,
    ]


def _frame(rpcid: str, payload: Any) -> str:
    frame = [["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]]
    return ")]}'\n\n10\n" + json.dumps(frame) + "\n"


def _batch_url(rpcid: str) -> str:
    return (
        f"https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids={rpcid}&rt=c"
    )


def _t2v(**kw: Any) -> GenerateVideoRequest:
    base: dict[str, Any] = {
        "prompt": "a crane",
        "mode": Mode.T2V,
        "aspect": Aspect.LANDSCAPE,
        "duration": 8,
    }
    base.update(kw)
    return GenerateVideoRequest(**base)


# --- settings ---------------------------------------------------------------


async def test_apply_video_settings_selects_each_axis_and_reads_back() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.groups["aspect"][0].checked, page.dom.groups["aspect"][1].checked = False, True
    await MigratedComposer().apply_video_settings(
        page, _t2v(aspect=Aspect.LANDSCAPE, duration=6, count=2)
    )
    assert page.dom.groups["aspect"][0].checked  # 16:9
    assert page.dom.groups["duration"][1].checked  # 6s
    assert page.dom.groups["count"][1].checked  # x2
    assert not page.dom.pane_open  # closed afterwards


async def test_missing_axis_is_a_configuration_error_naming_it() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    del page.dom.groups["duration"]
    with pytest.raises(ConfigurationError, match="duration"):
        await MigratedComposer().apply_video_settings(page, _t2v(duration=10))


async def test_axis_left_at_default_when_not_requested() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    del page.dom.groups["duration"]  # no control, but nothing requested either
    await MigratedComposer().apply_video_settings(page, _t2v(duration=None))
    assert page.dom.groups["count"][0].checked


async def test_stale_radio_that_never_flips_is_selector_drift_with_host() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.groups["duration"][3].stale = True
    with pytest.raises(UiSelectorDriftError) as exc_info:
        await MigratedComposer().apply_video_settings(page, _t2v(duration=10))
    assert "migrated" in str(exc_info.value) and "10s" in str(exc_info.value)


async def test_model_selected_from_menu_by_product_name() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    await MigratedComposer().apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_LITE))
    assert page.dom.model_label == "Veo 3.1 - Lite"
    assert not page.dom.menu_open and not page.dom.pane_open


async def test_axes_still_resolve_after_a_model_switch() -> None:
    """$0 run 2026-09-05: with --model, every axis after the menu switch read
    "0 option groups" because a detached menu pane was the last overlay."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.menu_lingers = True
    await MigratedComposer().apply_video_settings(
        page, _t2v(model=VideoModel.VEO_3_1_LITE, aspect=Aspect.PORTRAIT, duration=6)
    )
    assert page.dom.model_label == "Veo 3.1 - Lite"
    assert page.dom.groups["aspect"][1].checked  # 9:16 selected AFTER the switch
    assert page.dom.groups["duration"][1].checked


async def test_model_not_offered_lists_the_menu() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    with pytest.raises(ConfigurationError) as exc_info:
        await MigratedComposer().apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_QUALITY))
    assert "Veo 3.1 - Lite" in str(exc_info.value)
    assert page.dom.submit_clicked == 0


# --- prompt + submit --------------------------------------------------------


async def test_prompt_goes_into_the_contenteditable_not_the_textarea() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    await MigratedComposer().send_prompt(page, "a crane")
    assert page.dom.prompt == "a crane"
    assert "composer_focused" in page.dom.events


async def test_submit_observes_submit_then_poll_then_result() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("jwpduf"), _frame("jwpduf", [None, 881, [[_record(2)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    started: list[Any] = []
    rec = await MigratedComposer().submit_and_observe(
        page,
        poll_timeout_s=2.0,
        on_started=started.append,
        project_id=PROJ,
    )
    assert rec.is_done and rec.video_url == VIDEO_URL and rec.media_id == MEDIA
    assert (
        started and started[0].media_id == MEDIA and started[0].flow_operation_id == rec.workflow_id
    )
    assert started[0].project_id == PROJ
    assert page.dom.submit_clicked == 1


async def test_submit_waits_for_the_button_to_enable_after_insert_text() -> None:
    """#670: the button reads disabled for a tick after the prompt lands; wait, don't raise."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.dom.submit_enable_after_reads = 2
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    rec = await MigratedComposer().submit_and_observe(
        page, poll_timeout_s=2.0, on_started=None, project_id=PROJ
    )
    assert rec.is_done and page.dom.submit_clicked == 1
    assert page.dom.submit_enable_after_reads == 0


async def test_submit_still_disabled_after_the_budget_is_selector_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "SUBMIT_ENABLE_BUDGET_S", 0.05)
    monkeypatch.setattr(migrated_composer, "SUBMIT_ENABLE_POLL_S", 0.01)
    page = FakePage()
    page.dom.prompt = ""  # nothing in the composer: the button never enables
    with pytest.raises(UiSelectorDriftError, match="stayed disabled"):
        await MigratedComposer().submit_and_observe(
            page, poll_timeout_s=2.0, on_started=None, project_id=PROJ
        )
    assert page.dom.submit_clicked == 0


async def test_status_three_in_a_poll_is_terminal_even_without_as29s() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("jwpduf"), _frame("jwpduf", [None, 881, [[_record(3, VIDEO_URL)]]])),
    ]
    rec = await MigratedComposer().submit_and_observe(
        page, poll_timeout_s=2.0, on_started=None, project_id=PROJ
    )
    assert rec.is_done and rec.video_url == VIDEO_URL


async def test_status_three_without_url_waits_for_the_record_that_carries_it() -> None:
    """Live 2026-09-05: the jwpduf poll reported 3 with no URL; as29s brought the
    URL 5 s later. The first status-3 record must NOT end the observation."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("jwpduf"), _frame("jwpduf", [None, 881, [[_record(3)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    rec = await MigratedComposer().submit_and_observe(
        page, poll_timeout_s=5.0, on_started=None, project_id=PROJ
    )
    assert rec.is_done and rec.video_url == VIDEO_URL


async def test_status_three_without_url_stands_after_the_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.transports import migrated_composer

    monkeypatch.setattr(migrated_composer, "RESULT_URL_GRACE_S", 0.2)
    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("jwpduf"), _frame("jwpduf", [None, 881, [[_record(3)]]])),
    ]
    rec = await migrated_composer.MigratedComposer().submit_and_observe(
        page, poll_timeout_s=5.0, on_started=None, project_id=PROJ
    )
    assert rec.is_done and rec.video_url is None


async def test_download_uses_the_signed_url_before_the_redirect_route(tmp_path: Any) -> None:
    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    class _Resp:
        status = 200

        async def body(self) -> bytes:
            return b"\x00\x00\x00\x18ftypmp42"

    class _Req:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get(self, url: str, **_: Any) -> _Resp:
            self.urls.append(url)
            return _Resp()

    page = FakePage()
    page.request = _Req()
    transport = type("T", (), {})()

    async def _never(*_: Any, **__: Any) -> Any:
        raise AssertionError("redirect route must not be tried when a URL exists")

    transport._download_video = _never
    rec = GenerationRecord(
        workflow_id=WF, project_id=PROJ, media_id=MEDIA, status=3, video_url=VIDEO_URL
    )
    path = await MigratedComposer().download(page, rec, tmp_path)
    assert path is not None and path.read_bytes()[4:8] == b"ftyp"
    assert page.request.urls == [VIDEO_URL]


async def test_download_falls_back_to_the_other_url_when_the_first_is_a_jpeg(
    tmp_path: Any,
) -> None:
    """Live 2026-09-05: the first URL served the poster JPEG; the mp4 was the other."""
    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    bodies = {
        "https://flow-content.google/p/x.jpg?Signature=a": b"\xff\xd8\xff\xfe\x00\tGoogle",
        "https://flow-content.google/v/x.mp4?Signature=b": b"\x00\x00\x00\x18ftypmp42",
    }

    class _Resp:
        def __init__(self, body: bytes) -> None:
            self._body, self.status = body, 200

        async def body(self) -> bytes:
            return self._body

    class _Req:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get(self, url: str, **_: Any) -> _Resp:
            self.urls.append(url)
            return _Resp(bodies[url])

    page = FakePage()
    page.request = _Req()
    rec = GenerationRecord(
        workflow_id=WF,
        project_id=PROJ,
        media_id=MEDIA,
        status=3,
        video_url="https://flow-content.google/p/x.jpg?Signature=a",  # mislabelled on purpose
        poster_url="https://flow-content.google/v/x.mp4?Signature=b",
    )
    path = await MigratedComposer().download(page, rec, tmp_path)
    assert path is not None and path.read_bytes()[4:8] == b"ftyp"
    assert len(page.request.urls) == 2


async def test_download_refuses_a_foreign_host_before_any_request(tmp_path: Any) -> None:
    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    calls: list[str] = []

    class _Req:
        async def get(self, url: str, **_: Any) -> Any:
            calls.append(url)
            raise AssertionError("must not be reached")

    page = FakePage()
    page.request = _Req()
    rec = GenerationRecord(
        workflow_id=WF,
        project_id=PROJ,
        media_id=MEDIA,
        status=3,
        video_url="https://evil.example/v.mp4?Signature=s",
    )
    with pytest.raises(WireFormatError, match="evil.example"):
        await MigratedComposer().download(page, rec, tmp_path)
    assert calls == []


async def test_download_never_follows_redirects(tmp_path: Any) -> None:
    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    seen: dict[str, Any] = {}

    class _Resp:
        status = 200

        async def body(self) -> bytes:
            return b"\x00\x00\x00\x18ftypmp42"

    class _Req:
        async def get(self, url: str, **kw: Any) -> _Resp:
            seen.update(kw)
            return _Resp()

    page = FakePage()
    page.request = _Req()
    rec = GenerationRecord(
        workflow_id=WF, project_id=PROJ, media_id=MEDIA, status=3, video_url=VIDEO_URL
    )
    await MigratedComposer().download(page, rec, tmp_path)
    assert seen.get("max_redirects") == 0


async def test_download_refuses_when_no_url_is_an_mp4(tmp_path: Any) -> None:
    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    class _Resp:
        status = 200

        async def body(self) -> bytes:
            return b"\xff\xd8\xff\xfe"

    class _Req:
        async def get(self, url: str, **_: Any) -> _Resp:
            return _Resp()

    page = FakePage()
    page.request = _Req()
    rec = GenerationRecord(
        workflow_id=WF, project_id=PROJ, media_id=MEDIA, status=3, video_url=VIDEO_URL
    )
    with pytest.raises(WireFormatError, match="ftyp"):
        await MigratedComposer().download(page, rec, tmp_path)


def test_migrated_can_serve_decides_what_the_new_host_takes() -> None:
    from gflow_cli.api.transports.migrated_composer import migrated_can_serve

    assert migrated_can_serve(_t2v(), "p1")
    assert migrated_can_serve(_t2v(model=VideoModel.VEO_3_1_FAST), "p1")
    assert not migrated_can_serve(_t2v(), None)  # project creation not ported
    assert not migrated_can_serve(_t2v(model=VideoModel.VEO_3_1_LITE_LOWER_PRIORITY), "p1")
    assert not migrated_can_serve(
        GenerateVideoRequest(prompt="x", mode=Mode.I2V, start_image_ref_name="a"), "p1"
    )


async def test_unknown_status_ends_the_wait_as_failed() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("jwpduf"), _frame("jwpduf", [None, 881, [[_record(7)]]])),
    ]
    rec = await MigratedComposer().submit_and_observe(
        page, poll_timeout_s=2.0, on_started=None, project_id=PROJ
    )
    assert rec.is_failed and rec.status == 7


async def test_no_submit_frame_within_budget_is_a_timeout() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_responses = []
    with pytest.raises(TransportTimeoutError):
        await MigratedComposer().submit_and_observe(
            page, poll_timeout_s=0.3, on_started=None, project_id=PROJ
        )


async def test_submit_frame_without_record_is_wire_format_error() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_responses = [(_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [["nope"]]]))]
    with pytest.raises(WireFormatError):
        await MigratedComposer().submit_and_observe(
            page, poll_timeout_s=1.0, on_started=None, project_id=PROJ
        )


# --- download ---------------------------------------------------------------


def test_cdn_host_is_allowed_and_others_are_not() -> None:
    from gflow_cli.api.transports.ui_automation import _is_allowed_download_host

    assert _is_allowed_download_host(VIDEO_URL)
    assert not _is_allowed_download_host("https://evil.example/v.mp4?Signature=s")


# --- readiness --------------------------------------------------------------


async def test_ensure_editor_navigates_direct_when_not_on_the_project() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage(url="https://labs.google/fx/en/tools/flow/project/p1")
    await MigratedComposer().ensure_editor(page, "p1", timeout_s=1.0)
    assert page.gotos == ["https://flow.google.com/project/p1"]


async def test_ensure_editor_skips_navigation_when_already_there() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage(url="https://flow.google.com/project/p1")
    await MigratedComposer().ensure_editor(page, "p1", timeout_s=1.0)
    assert page.gotos == []


async def test_ensure_editor_without_trigger_is_selector_drift() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.trigger_present = False
    with pytest.raises(UiSelectorDriftError, match="migrated"):
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=0.2)
