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
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    ConfigurationError,
    MediaUploadRejectedError,
    ReferenceNotFoundError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)

# --- a tiny DOM -------------------------------------------------------------

#: The toolbar `+` — the only add affordance OUTSIDE the prompt box (the box has its own
#: `add` icons for chips). XPath, because CSS cannot say "no such ancestor".
TOOLBAR_ADD_XPATH = (
    "xpath=//button[.//mat-icon[normalize-space()='add']][not(ancestor::flow-prompt-box)]"
)


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
    # Picking from the model menu closes it logically but leaves its overlay VISIBLE and
    # eating the next Escape — measured 2026-09-05: after a switch one visible overlay
    # remains until a second Escape. This is what made a switched run fail and a repeated
    # one pass.
    menu_overlay_lingering: bool = False
    escapes_ignored: bool = False  # a pane that refuses to close at all
    toast_visible: bool = False  # an unrelated CDK overlay (snackbar/tooltip)
    events: list[str] = field(default_factory=list)
    # --- i2v: the toolbar upload path and the Frames picker (2026-09-05 frames spike) ---
    add_button_present: bool = True  # the toolbar `+` outside flow-prompt-box
    add_menu_open: bool = False
    upload_item_present: bool = True  # `[role=menuitem]` with the `upload` ligature
    chooser_opens: bool = True  # clicking the upload item fires `filechooser`
    chooser_pending: bool = False
    chosen_files: list[str] = field(default_factory=list)
    # "ok" → 200 frame `[media_id, project_id, …]`; "none" → the app never answers;
    # "no_id" → 200 carrying no UUID; "project_first" → 200 whose first UUID is the
    # project id (a reply-shape change); an int → that HTTP status with an empty body
    maseq_reply: Any = "ok"
    picker_open: bool = False
    picker_options: list[str] = field(
        default_factory=lambda: ["01-pre-submit.png", "Blue sphere on table"]
    )
    picker_query: str = ""
    picker_searches: int = 0
    # A fresh upload is indexed server-side: the picker listed it only on a later search
    # on a project with 30+ assets (denon82, 2026-09-05). 0 = listed at once.
    picker_lists_after_searches: int = 0
    picked: list[str] = field(default_factory=list)
    chip_binds: bool = True  # the option click flips the Start chip to a bound one
    chip_bound: bool = False
    dialog_present: bool = False  # a `[role=dialog]` (the changelog modal) on load
    dialog_closed: int = 0


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

    def nth(self, index: int) -> FakeLocator:
        return FakeLocator(self.page, self.kind, self.items[index : index + 1])

    def filter(self, *, has: FakeLocator | None = None, has_text: Any = None) -> FakeLocator:
        items = self.items
        if has is not None and has.kind == "ours":  # overlays we are allowed to dismiss
            items = [i for i in items if i in ("pane", "menu", "lingering-menu")]
        elif has is not None and has.kind == "group":  # pane.filter(has=<radiogroup>)
            items = [i for i in items if i == "pane" and self.page.dom.pane_open]
        elif has is not None and has.kind == "picker_marker":  # OVERLAY.filter(has=<picker>)
            items = [i for i in items if i == "picker"]
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
        await asyncio.sleep(0)
        present = bool(self.items)
        if (state == "hidden") == present:
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
            dom.menu_overlay_lingering = True
        elif self.kind == "textarea":
            raise PlaywrightTimeoutError("Locator.click: Timeout 4000ms exceeded (textarea)")
        elif self.kind == "composer":
            if dom.pane_open or dom.menu_open or dom.menu_overlay_lingering:
                raise PlaywrightTimeoutError(
                    "Locator.click: Timeout 5000ms exceeded. waiting for element to be "
                    "visible, enabled and stable (an overlay is covering it)"
                )
            dom.events.append("composer_focused")
        elif self.kind == "submit":
            dom.submit_clicked += 1
            self.page._fire_submit()
        elif self.kind == "toolbar_add":
            dom.add_menu_open = True
            dom.events.append("add_menu_opened")
        elif self.kind == "upload_item":
            dom.add_menu_open = False
            dom.chooser_pending = dom.chooser_opens
        elif self.kind == "empty_chip":
            dom.picker_open = True
            dom.picker_query = ""
        elif self.kind == "picker_search":
            dom.events.append("picker_search_focused")
        elif self.kind == "picker_option":
            dom.picked.append(str(target))
            dom.picker_open = False
            if dom.chip_binds:
                dom.chip_bound = True
        elif self.kind == "dialog_close":
            dom.dialog_present = False
            dom.dialog_closed += 1


class PlaywrightTimeoutError(Exception):
    pass


class FakeFileChooser:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def set_files(self, files: Any) -> None:
        dom = self.page.dom
        dom.chosen_files.append(str(files))
        reply = dom.maseq_reply
        if reply == "none":
            return
        payloads: dict[str, list[Any]] = {
            "ok": [MEDIA_UP, PROJ_UUID, "44444444-4444-4444-8444-444444444444", "CAE"],
            "no_id": ["CAE", 1],
            "project_first": [PROJ_UUID, MEDIA_UP, "CAE"],
        }
        status = 200 if reply in payloads else int(reply)
        text = _frame("maseQ", payloads.get(str(reply), []))
        self.page._fire_response(
            FakeResponse(_batch_url("maseQ"), text if status == 200 else "", status)
        )


class FakeChooserContext:
    """`async with page.expect_file_chooser() as fc: … ; await fc.value`."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self._value: asyncio.Future[FakeFileChooser] = asyncio.get_event_loop().create_future()

    async def __aenter__(self) -> FakeChooserContext:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        dom = self.page.dom
        if exc[0] is not None:
            return
        if dom.chooser_pending:
            dom.chooser_pending = False
            self._value.set_result(FakeFileChooser(self.page))
            return
        raise PlaywrightTimeoutError('Timeout while waiting for event "filechooser"')

    @property
    def value(self) -> asyncio.Future[FakeFileChooser]:
        return self._value


class FakeRequest:
    def __init__(self, url: str, post_data: str | None) -> None:
        self.url, self.post_data = url, post_data


class FakeKeyboard:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def press(self, key: str) -> None:
        if key == "Escape":
            dom = self.page.dom
            if dom.escapes_ignored:
                return
            # One overlay per press, top of the stack first.
            if dom.menu_overlay_lingering:
                dom.menu_overlay_lingering = False
            elif dom.menu_open:
                dom.menu_open = False
            else:
                dom.pane_open = False

    async def type(self, text: str, **_: Any) -> None:
        self.page.dom.prompt += text

    async def insert_text(self, text: str) -> None:
        dom = self.page.dom
        if dom.picker_open:
            dom.picker_query += text
            dom.picker_searches += 1
        else:
            dom.prompt += text


class FakeResponse:
    def __init__(self, url: str, text: str, status: int = 200) -> None:
        self.url, self._text, self.status = url, text, status

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
        self._handlers: dict[str, list[Any]] = {"response": [], "request": []}
        self._pending_lig: re.Pattern[str] | None = None
        self.scripted_responses: list[tuple[str, str]] = []  # fired on submit click
        self.scripted_request: tuple[str, str] | None = None  # (rpcid, POST body) on submit

    async def goto(self, url: str, **_: Any) -> None:
        self.gotos.append(url)
        self.url = url

    def on(self, event: str, handler: Any) -> None:
        self._handlers[event].append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    def listeners(self, event: str) -> list[Any]:
        return list(self._handlers[event])

    def _fire_response(self, response: FakeResponse) -> None:
        for h in list(self._handlers["response"]):
            asyncio.get_event_loop().create_task(_maybe_await(h(response)))

    def _fire_submit(self) -> None:
        if self.scripted_request is not None:
            rpcid, body = self.scripted_request
            for h in list(self._handlers["request"]):
                asyncio.get_event_loop().create_task(
                    _maybe_await(h(FakeRequest(_batch_url(rpcid), body)))
                )
        for url, text in self.scripted_responses:
            self._fire_response(FakeResponse(url, text))

    def expect_file_chooser(self, **_: Any) -> FakeChooserContext:
        return FakeChooserContext(self)

    def locator(self, css: str, *, scope: FakeLocator | None = None) -> FakeLocator:
        dom = self.dom
        if css == ".settings-trigger-button":
            return FakeLocator(self, "trigger", ["trigger"] if dom.trigger_present else [])
        if css == TOOLBAR_ADD_XPATH:
            return FakeLocator(self, "toolbar_add", ["add"] if dom.add_button_present else [])
        if css == ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('upload'))":
            present = dom.add_menu_open and dom.upload_item_present
            return FakeLocator(self, "upload_item", ["upload"] if present else [])
        if css == "flow-prompt-box button.empty-chip":
            chips = ["End"] if dom.chip_bound else ["Start", "End"]
            return FakeLocator(self, "empty_chip", chips)
        if css == "flow-prompt-box button.chip-container:has(img)":
            return FakeLocator(self, "bound_chip", ["Start"] if dom.chip_bound else [])
        if css == "flow-add-menu-popover-content":
            return FakeLocator(self, "picker_marker", ["picker"])
        if css == "input[type='text']":
            inside = scope is not None and scope.items == ["picker"]
            return FakeLocator(self, "picker_search", ["search"] if inside else [])
        if css == "button.asset-item[role='option']":
            inside = scope is not None and scope.items == ["picker"]
            q = dom.picker_query.casefold()
            indexed = dom.picker_searches > dom.picker_lists_after_searches
            opts = [o for o in dom.picker_options if q in o.casefold()] if inside else []
            return FakeLocator(self, "picker_option", opts if indexed else [])
        if css == "[role='dialog']":
            return FakeLocator(self, "dialog", ["dialog"] if dom.dialog_present else [])
        if css == "[role='dialog'] button:has(mat-icon:text-is('close'))":
            return FakeLocator(self, "dialog_close", ["close"] if dom.dialog_present else [])
        if css in (".cdk-overlay-pane", ".cdk-overlay-pane:visible"):
            panes = (["pane"] if dom.pane_open else []) + (["menu"] if dom.menu_open else [])
            if dom.menu_overlay_lingering:
                panes.append("lingering-menu")
            if dom.toast_visible:
                panes.append("toast")
            if dom.picker_open:
                panes.append("picker")
            if css.endswith(":visible"):
                # The detached menu pane is in the DOM but NOT visible — which is exactly
                # why the old `OVERLAY.first` read-back stayed silent.
                return FakeLocator(self, "pane", panes)
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
        if css == "[role='radiogroup'], [role='menuitem']":
            # `_close_pane` narrows to overlays that are OURS — the settings pane and the
            # model menu — so a snackbar or tooltip in the same CDK container is left
            # alone. Marker locator; the pane filter below decides per overlay.
            return FakeLocator(self, "ours", ["ours"])
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
MEDIA_UP = "55555555-5555-4555-8555-555555555555"  # what `maseQ` answers for an upload
PROJ_UUID = "66666666-6666-4666-8666-666666666666"  # the project id in the same reply
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


# --- the lower-priority tier ------------------------------------------------
#
# `veo_3_1_lite_lower_priority` has no captured label — Flow has never rendered the
# entry on any account gflow has driven — so it is matched by the `[Lower Priority]`
# tag alone, exactly as the labs driver has since #539. These fixtures therefore
# describe the menu that tag implies, not one that was measured.

LP_LITE = "Veo 3.1 - Lite [Lower Priority]"


async def test_lower_priority_tier_selects_by_its_tag() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.models = ["Omni 1.1 Flash", "Veo 3.1 - Lite", LP_LITE]
    await MigratedComposer().apply_video_settings(
        page, _t2v(model=VideoModel.VEO_3_1_LITE_LOWER_PRIORITY)
    )
    assert page.dom.model_label == LP_LITE


async def test_plain_lite_never_binds_its_lower_priority_sibling() -> None:
    """`Veo 3.1 - Lite` is a substring of `Veo 3.1 - Lite [Lower Priority]`, so the
    pre-fix `.first` bound whichever Flow listed first — here, the wrong one."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.models = [LP_LITE, "Veo 3.1 - Lite"]  # LP deliberately first
    await MigratedComposer().apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_LITE))
    assert page.dom.model_label == "Veo 3.1 - Lite"


async def test_lower_priority_button_readback_does_not_pass_for_plain_lite() -> None:
    """The pane already shows the LP tier: `startswith('Veo 3.1 - Lite')` said "already
    selected" and returned without touching the menu, silently keeping it."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.models = ["Veo 3.1 - Lite", LP_LITE]
    page.dom.model_label = LP_LITE
    await MigratedComposer().apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_LITE))
    assert page.dom.model_label == "Veo 3.1 - Lite"


async def test_a_matching_button_readback_never_opens_the_menu() -> None:
    """Live 2026-09-05: the account's picker was already on the lower-priority tier, so
    the run bound it without touching the menu — and emitted no model event at all,
    which is why `migrated.model_already_selected` now marks the path."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.models = ["Omni 1.1 Flash", "Veo 3.1 - Lite", LP_LITE]
    page.dom.model_label = LP_LITE
    await MigratedComposer().apply_video_settings(
        page, _t2v(model=VideoModel.VEO_3_1_LITE_LOWER_PRIORITY)
    )
    assert page.dom.model_label == LP_LITE
    assert not page.dom.menu_open


async def test_an_ambiguous_menu_refuses_instead_of_guessing() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.models = ["Veo 3.1 - Lite", LP_LITE, "Omni 1.1 Flash [Lower Priority]"]
    with pytest.raises(ConfigurationError) as exc_info:
        await MigratedComposer().apply_video_settings(
            page, _t2v(model=VideoModel.VEO_3_1_LITE_LOWER_PRIORITY)
        )
    message = str(exc_info.value)
    assert "matched 2 entries" in message
    assert LP_LITE in message and "Omni 1.1 Flash [Lower Priority]" in message
    assert page.dom.model_label == "Omni 1.1 Flash"  # untouched
    assert page.dom.submit_clicked == 0


async def test_a_model_switch_leaves_no_overlay_covering_the_composer() -> None:
    """Field report: "switch the model and the run dies; re-run with the same model and
    it works." A switch stacks TWO overlays and each Escape dismisses one, so the single
    Escape `_close_pane` used to press left the settings pane over the composer —
    `send_prompt`'s click then died as a bare Playwright TimeoutError naming
    `[contenteditable='true']`, pointing nowhere near the pane."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    composer = MigratedComposer()
    await composer.apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_LITE))
    assert page.dom.model_label == "Veo 3.1 - Lite"  # the switch really happened
    assert not page.dom.pane_open and not page.dom.menu_overlay_lingering

    await composer.send_prompt(page, "a man crying")
    assert page.dom.prompt == "a man crying"


async def test_repeating_the_same_model_never_stacks_the_second_overlay() -> None:
    """The other half of the report: a re-run binds at the button read-back, never opens
    the menu, and so passed even while a switch failed."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.model_label = "Veo 3.1 - Lite"
    composer = MigratedComposer()
    await composer.apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_LITE))
    assert not page.dom.menu_overlay_lingering  # the menu was never opened
    await composer.send_prompt(page, "a man crying")
    assert page.dom.prompt == "a man crying"


async def test_a_pane_that_refuses_to_close_is_named_not_left_to_the_composer() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.escapes_ignored = True
    with pytest.raises(UiSelectorDriftError) as exc_info:
        await MigratedComposer().apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_LITE))
    message = str(exc_info.value)
    assert "still visible" in message and "migrated" in message
    assert page.dom.submit_clicked == 0


async def test_a_stuck_pane_never_masks_the_error_already_travelling() -> None:
    """Review D1: `_close_pane` in a bare `finally` replaced the caller's error. A model
    Flow does not offer raises ConfigurationError (exit 11) naming what IS offered; a
    pane that then refuses to close turned that into UiSelectorDriftError (exit 23) and
    the list was gone."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.escapes_ignored = True  # the pane will not close
    with pytest.raises(ConfigurationError) as exc_info:
        await MigratedComposer().apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_QUALITY))
    message = str(exc_info.value)
    assert "Veo 3.1 - Lite" in message  # the offered-models list survived
    assert "still visible" not in message


async def test_an_unrelated_overlay_is_not_ours_to_dismiss() -> None:
    """Review D2: `.cdk-overlay-pane` is generic CDK — Flow mounts snackbars and tooltips
    in the same container. One visible at the wrong moment must not burn the escapes and
    abort a run that was fine."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.toast_visible = True
    composer = MigratedComposer()
    await composer.apply_video_settings(page, _t2v(model=VideoModel.VEO_3_1_LITE))
    assert page.dom.toast_visible  # left alone, not escaped at
    assert not page.dom.pane_open and not page.dom.menu_overlay_lingering
    await composer.send_prompt(page, "a man crying")
    assert page.dom.prompt == "a man crying"


def test_pane_close_escapes_matches_the_measured_stack_depth() -> None:
    """Review D14: the constant is evidence, not headroom — a switch stacks exactly two
    overlays (the menu, then the pane) and each Escape dismisses one."""
    from gflow_cli.api.transports.migrated_composer import PANE_CLOSE_ESCAPES

    assert PANE_CLOSE_ESCAPES == 2


def test_matcher_excludes_the_lower_priority_suffix_by_default() -> None:
    from gflow_cli.api.transports.migrated_composer import (
        VIDEO_MODEL_MENU_MATCHERS,
        ModelMenuMatcher,
    )

    lite = VIDEO_MODEL_MENU_MATCHERS[VideoModel.VEO_3_1_LITE]
    assert lite.matches("Veo 3.1 - Lite")
    assert not lite.matches(LP_LITE)

    lp = VIDEO_MODEL_MENU_MATCHERS[VideoModel.VEO_3_1_LITE_LOWER_PRIORITY]
    assert lp.matches(LP_LITE)
    assert not lp.matches("Veo 3.1 - Lite")

    # Flow's ligature prefix and casing must not decide the match.
    assert ModelMenuMatcher("Veo 3.1 - Lite").matches("volume_up veo 3.1 - lite")
    assert lp.matches("volume_up Veo 3.1 - Lite [LOWER PRIORITY]")


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


# --- i2v: Frames submode, start-frame attach, submit body -------------------
# Measured 2026-09-05 ($0 frames spike): the toolbar `+` opens a menu whose `upload`
# item fires the file chooser; the app uploads through rpc `maseQ` and answers
# `[media_id, project_id, …]`; the Start chip opens a library-only picker searched
# by display name (uploads list under their file name, no UUID in the DOM); the i2v
# submit is rpc `eb1hJf` with the bound media id in the body.


def _png(tmp_path: Path, name: str = "01-pre-submit.png") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    return path


def _i2v(path: Path, **kw: Any) -> GenerateVideoRequest:
    base: dict[str, Any] = {
        "prompt": "a crane",
        "mode": Mode.I2V,
        "start_image": path,
        "aspect": Aspect.LANDSCAPE,
    }
    base.update(kw)
    return GenerateVideoRequest(**base)


def _ingredients_default(page: FakePage) -> list[Radio]:
    sub = page.dom.groups["submode"]
    sub[0].checked, sub[1].checked = False, True  # Flow remembers Ingredients
    return sub


async def test_i2v_settings_select_the_frames_submode(tmp_path: Path) -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    sub = _ingredients_default(page)
    await MigratedComposer().apply_video_settings(page, _i2v(_png(tmp_path)))
    assert sub[0].checked and not sub[1].checked  # crop_free = Frames
    assert not page.dom.pane_open


async def test_t2v_settings_leave_the_submode_alone() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    sub = _ingredients_default(page)
    await MigratedComposer().apply_video_settings(page, _t2v())
    assert sub[1].checked  # untouched: t2v never needs the chips


async def test_i2v_without_a_model_binds_the_i2v_default(tmp_path: Path) -> None:
    """#125: a queued MCP request carries no model, and the editor would otherwise
    submit on whatever tier it last remembered — possibly a 100-credit one."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.model_label = "Veo 3.1 - Quality"  # what the account last used
    with capture_logs() as logs:
        await MigratedComposer().apply_video_settings(page, _i2v(_png(tmp_path), model=None))
    assert page.dom.model_label == "Veo 3.1 - Lite"
    defaulted = [e for e in logs if e["event"] == "migrated.i2v_model_defaulted"]
    assert defaulted and defaulted[0]["model"] == "veo_3_1_lite"
    # the timeline must name the EFFECTIVE model, not the (absent) requested one
    applied = [e for e in logs if e["event"] == "migrated.settings_applied"]
    assert applied and applied[0]["model"] == "veo_3_1_lite"


async def test_t2v_without_a_model_leaves_the_picker_alone() -> None:
    """Flow's sticky t2v default is unknowable here; only i2v gets a bound default."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.model_label = "Veo 3.1 - Quality"
    await MigratedComposer().apply_video_settings(page, _t2v())
    assert page.dom.model_label == "Veo 3.1 - Quality"


async def test_attach_uploads_then_binds_the_frame_by_file_name(tmp_path: Path) -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    path = _png(tmp_path)
    with capture_logs() as logs:
        media_id = await MigratedComposer().attach_start_frame(page, PROJ, path)
    assert media_id == MEDIA_UP
    assert page.dom.chosen_files == [str(path)]
    assert page.dom.picker_query == path.name
    assert page.dom.picked == [path.name]
    assert page.dom.chip_bound and not page.dom.picker_open
    events = [e["event"] for e in logs]
    assert "migrated.frame_uploaded" in events and "migrated.frame_bound" in events
    uploaded = next(e for e in logs if e["event"] == "migrated.frame_uploaded")
    assert uploaded["media_id"] == MEDIA_UP
    assert path.name not in json.dumps(uploaded)  # never the file name in a log line
    assert page.listeners("response") == []  # listener removed


async def test_attach_refuses_a_non_image_before_any_click(tmp_path: Path) -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(ValueError, match="supported image"):
        await MigratedComposer().attach_start_frame(page, PROJ, path)
    assert "add_menu_opened" not in page.dom.events and page.dom.chosen_files == []


async def test_attach_refuses_when_the_add_menu_has_no_upload_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_PICKER_OPEN_S", 0.05)
    page = FakePage()
    page.dom.upload_item_present = False
    with pytest.raises(UiSelectorDriftError, match="upload"):
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert page.dom.chosen_files == []


async def test_attach_refuses_when_no_file_chooser_opens(tmp_path: Path) -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.chooser_opens = False
    with pytest.raises(UiSelectorDriftError, match="file chooser"):
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert page.dom.chosen_files == [] and page.listeners("response") == []


async def test_attach_is_upload_rejected_when_maseq_does_not_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_UPLOAD_S", 0.2)
    page = FakePage()
    page.dom.maseq_reply = "none"
    with pytest.raises(MediaUploadRejectedError) as ei:
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert ei.value.route == "batchexecute:maseQ"
    assert EXIT_CODE_MAP[MediaUploadRejectedError] == 27
    assert page.dom.picked == [] and not page.dom.picker_open


async def test_attach_is_upload_rejected_on_a_non_200_maseq(tmp_path: Path) -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.maseq_reply = 400
    with pytest.raises(MediaUploadRejectedError, match="400") as ei:
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert ei.value.route == "batchexecute:maseQ" and ei.value.status == 400
    assert page.dom.picked == []


async def test_attach_is_upload_rejected_when_maseq_names_no_media_id(tmp_path: Path) -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.maseq_reply = "no_id"
    with pytest.raises(MediaUploadRejectedError, match="without a media id"):
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert page.dom.picked == []


async def test_attach_refuses_when_the_first_id_in_the_reply_is_the_project(
    tmp_path: Path,
) -> None:
    """The measured reply is ``[media_id, project_id, …]``. If that order ever flips,
    binding the project id would sail through the submit-body check — every body
    carries the project id — so the shape change is refused here instead."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.maseq_reply = "project_first"
    with pytest.raises(MediaUploadRejectedError, match="project id"):
        await MigratedComposer().attach_start_frame(page, PROJ_UUID, _png(tmp_path))
    assert page.dom.picked == []


async def test_attach_is_reference_not_found_when_the_picker_lists_no_such_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_PICKER_OPEN_S", 0.1)
    page = FakePage()
    page.dom.picker_options = ["Blue sphere on table"]  # the upload never listed
    path = _png(tmp_path)
    with pytest.raises(ReferenceNotFoundError, match=re.escape(path.name)):
        await MigratedComposer().attach_start_frame(page, PROJ, path)
    assert EXIT_CODE_MAP[ReferenceNotFoundError] == 32
    assert page.dom.picked == [] and not page.dom.chip_bound


async def test_attach_is_selector_drift_when_the_chip_stays_empty_after_the_pick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_THUMB_VISIBLE_S", 0.1)
    page = FakePage()
    page.dom.chip_binds = False
    with pytest.raises(UiSelectorDriftError, match="chip"):
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert page.dom.picked == ["01-pre-submit.png"]  # the pick happened; the bind did not


async def test_attach_searches_again_when_the_upload_is_not_indexed_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """denon82, 2026-09-05: two runs missed the fresh upload within 8 s on a project
    with 30+ assets and a third listed it — the picker search is server-side. A
    miss is re-searched from a fresh popover before it is a refusal."""
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_PICKER_OPEN_S", 0.05)
    monkeypatch.setattr(migrated_composer, "FRAME_SEARCH_RETRY_PAUSE_S", 0.01)
    page = FakePage()
    page.dom.picker_lists_after_searches = 1  # listed on the second search only
    media_id = await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert media_id == MEDIA_UP and page.dom.picker_searches == 2 and page.dom.chip_bound


async def test_attach_gives_up_after_the_search_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_PICKER_OPEN_S", 0.05)
    monkeypatch.setattr(migrated_composer, "FRAME_SEARCH_RETRY_PAUSE_S", 0.01)
    page = FakePage()
    page.dom.picker_lists_after_searches = 99
    with pytest.raises(ReferenceNotFoundError, match="nothing"):
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert page.dom.picker_searches == migrated_composer.FRAME_SEARCH_ATTEMPTS


async def test_attach_picks_the_first_option_when_names_repeat(tmp_path: Path) -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    name = "01-pre-submit.png"
    page.dom.picker_options = [name, name, "Blue sphere on table"]  # two uploads of one file
    media_id = await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path, name))
    assert media_id == MEDIA_UP and page.dom.picked == [name]


def _i2v_body(media_id: str = MEDIA_UP, key: str = "veo_3_1_i2v_lite") -> str:
    return f'f.req=[[["eb1hJf","[\\"{key}\\",\\"{media_id}\\",\\"{PROJ}\\"]",null,"generic"]]]'


async def test_submit_accepts_eb1hjf_as_the_i2v_submit_rpc() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_request = ("eb1hJf", _i2v_body())
    page.scripted_responses = [
        (_batch_url("eb1hJf"), _frame("eb1hJf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    started: list[Any] = []
    with capture_logs() as logs:
        rec = await MigratedComposer().submit_and_observe(
            page,
            poll_timeout_s=2.0,
            on_started=started.append,
            project_id=PROJ,
            expect_media_id=MEDIA_UP,
        )
    assert rec.is_done and rec.media_id == MEDIA and started[0].media_id == MEDIA
    observed = next(e for e in logs if e["event"] == "migrated.submit_observed")
    assert observed["rpc"] == "eb1hJf"
    assert page.listeners("request") == [] and page.listeners("response") == []


async def test_submit_body_without_the_bound_media_id_is_wire_format_error() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_request = ("eb1hJf", _i2v_body(media_id=MEDIA))  # some other asset
    page.scripted_responses = [  # the app still answers: the error must win anyway
        (_batch_url("eb1hJf"), _frame("eb1hJf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    with pytest.raises(WireFormatError, match=MEDIA_UP) as ei:
        await MigratedComposer().submit_and_observe(
            page, poll_timeout_s=2.0, on_started=None, project_id=PROJ, expect_media_id=MEDIA_UP
        )
    assert EXIT_CODE_MAP[WireFormatError] == 7 and "eb1hJf" in ei.value.route


async def test_submit_body_with_a_t2v_key_is_wire_format_error() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    # the labs #125 twin: chips empty at submit time, the app goes out as t2v
    page.scripted_request = ("YhhmEf", _i2v_body(key="veo_3_1_t2v_lite"))
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    with pytest.raises(WireFormatError, match="t2v"):
        await MigratedComposer().submit_and_observe(
            page, poll_timeout_s=2.0, on_started=None, project_id=PROJ, expect_media_id=MEDIA_UP
        )


async def test_submit_body_assertion_is_off_for_t2v() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_request = ("YhhmEf", _i2v_body(media_id="", key="veo_3_1_t2v_lite"))
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    rec = await MigratedComposer().submit_and_observe(
        page, poll_timeout_s=2.0, on_started=None, project_id=PROJ
    )
    assert rec.is_done and page.listeners("request") == []


async def test_ensure_editor_dismisses_a_dialog_before_waiting_for_the_trigger() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.dialog_present = True  # the "Get started" changelog modal on first visit
    with capture_logs() as logs:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=1.0)
    assert page.dom.dialog_closed == 1 and not page.dom.dialog_present
    assert "migrated.dialog_dismissed" in [e["event"] for e in logs]


async def test_ensure_editor_without_a_dialog_is_unchanged() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    with capture_logs() as logs:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=1.0)
    assert page.dom.dialog_closed == 0
    assert "migrated.dialog_dismissed" not in [e["event"] for e in logs]
