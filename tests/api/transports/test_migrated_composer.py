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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports import migrated_composer
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    ConfigurationError,
    FlowHostMigratedError,
    InsufficientCreditsError,
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
    # Two INDEPENDENT axes, deliberately. Short of credits for the model asked, Flow
    # does not DISABLE the
    # submit control, it REPLACES it: `arrow_forward` disappears and a
    # `.prompt-warning-button` carrying aria-label='Insufficient credits warning' takes
    # its place. Measured by A/B on 2026-09-07
    # (scripts/dev/spike_migrated_submit_anchor.py): same probe, same host, same code,
    # ~60 s apart -- the short account had arrow_forward ABSENT and the warning PRESENT;
    # funded account the mirror image.
    #
    # Kept as two fields so a missing anchor with NO warning is still expressible. That
    # combination is real selector drift, and a guard that cannot tell the two apart
    # would trade a false "file a frontend bug" for a false "top up your account".
    submit_anchor_present: bool = True
    credits_warning_present: bool = False
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
    #: #719: the one-time upload-terms modal. It appears only once the file has been
    #: handed over, and Flow sends NOTHING until a human accepts it — so this both
    #: raises a dialog and withholds the reply, which is the whole shape of the bug.
    consent_dialog_on_upload: bool = False
    #: The page dies once the file is handed over (navigation, crash, context
    #: teardown) — the likeliest reason a reply never comes, and the state in which
    #: the post-timeout dialog probe would itself raise.
    page_dies_on_upload: bool = False
    dialog_probe_raises: bool = False
    dialog_closed: int = 0
    cookie_bar_present: bool = False
    # --- #749: Flow's agent mode ------------------------------------------------------
    # Pressed, the chip leaves `.settings-trigger-button` in the DOM under a bare `hidden`
    # — present to `count()`, never visible. The knobs below are the states the driver has
    # to tell apart; `migrated_composer.AGENT_MODE_CHIP` carries the selector rationale
    # and points at the spike that measured all of this.
    agent_mode: bool = False
    #: The mode is on but the chip is not in the DOM — nothing to click, so the driver
    #: must fall back to the ordinary drift message rather than promise a recovery.
    agent_chip_present: bool = True
    # A chip that will not toggle off. NOT observed on either of our accounts — kept
    # because the reporter's might pin the mode, and a fix that cannot say "I tried and
    # the trigger stayed hidden" would report that as ordinary selector drift again.
    agent_chip_sticks: bool = False
    agent_chip_clicks: int = 0
    #: The unmeasured cohort: agent mode on, and the trigger left VISIBLE anyway. Every
    #: account seen so far hides it, but nothing downstream would notice if one did not —
    #: `send_prompt` would type into the agent composer and the run would be wasted.
    agent_mode_hides_trigger: bool = True
    # A page that cannot answer the probe (closed / detached). The index is which probe
    # stops answering, because the driver reads the chip more than once and the reads are
    # not interchangeable: 0 = never answers, so no claim of agent mode may be made at
    # all; 1 = answers, is clicked, and then goes dark before the read-back — where the
    # unsafe claim is the OPPOSITE one, "the mode is off, so file a drift bug".
    agent_chip_probe_raises_from: int | None = None
    agent_chip_probes: int = 0
    agent_chip_click_raises: bool = False
    agent_panel_expanded: bool = False


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
    def __init__(
        self,
        page: FakePage,
        kind: str,
        items: list[Any],
        *,
        visible: bool | Callable[[], bool] = True,
    ) -> None:
        self.page, self.kind, self.items = page, kind, items
        #: Present in the DOM but not visible — what a bare `hidden` attribute does
        #: (#749: the settings trigger in agent mode). `count()` still sees it; a
        #: `wait_for(state="visible")` must not. Modelling this as absence instead
        #: would hide the exact distinction the bug turns on.
        #:
        #: A CALLABLE here is re-evaluated on every read, because a real Playwright
        #: locator is lazy: it re-queries the DOM each time, so one held across a click
        #: that unhides its target then passes. A bool snapshotted at construction made
        #: the agent-mode recovery look broken when only the fake was.
        self.visible = visible

    @property
    def _visible_now(self) -> bool:
        return bool(self.visible()) if callable(self.visible) else bool(self.visible)

    # --- narrowing --------------------------------------------------------
    @property
    def first(self) -> FakeLocator:
        return FakeLocator(self.page, self.kind, self.items[:1], visible=self.visible)

    @property
    def last(self) -> FakeLocator:
        return FakeLocator(self.page, self.kind, self.items[-1:], visible=self.visible)

    def nth(self, index: int) -> FakeLocator:
        return FakeLocator(
            self.page, self.kind, self.items[index : index + 1], visible=self.visible
        )

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
        return FakeLocator(self.page, self.kind, items, visible=self.visible)

    def locator(self, css: str) -> FakeLocator:
        return self.page.locator(css, scope=self)

    # --- reads --------------------------------------------------------------
    async def count(self) -> int:
        if self.kind == "cookie_bar":
            return 1 if self.page.dom.cookie_bar_present else 0
        if self.kind == "dialog" and self.page.dom.dialog_probe_raises:
            raise PlaywrightTimeoutError("count: Target page, context or browser has been closed")
        if self.kind == "agent_chip":
            dom = self.page.dom
            index, dom.agent_chip_probes = dom.agent_chip_probes, dom.agent_chip_probes + 1
            if dom.agent_chip_probe_raises_from is not None and (
                index >= dom.agent_chip_probe_raises_from
            ):
                raise PlaywrightTimeoutError(
                    "count: Target page, context or browser has been closed"
                )
        return len(self.items)

    async def is_visible(self) -> bool:
        return bool(self.items) and self._visible_now

    async def scroll_into_view_if_needed(self, **_: Any) -> None:
        await asyncio.sleep(0)

    async def bounding_box(self) -> dict[str, float] | None:
        if not self.items:
            return None
        return {"x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0}

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
        if name == "aria-pressed" and self.kind == "agent_toggle":
            return "true" if self.page.dom.agent_mode else "false"
        return None

    async def text_content(self) -> str | None:
        return str(self.items[0]) if self.items else None

    async def all_text_contents(self) -> list[str]:
        return [str(i) for i in self.items]

    async def wait_for(self, *, state: str = "visible", timeout: float = 0) -> None:
        await asyncio.sleep(0)
        present = bool(self.items) and self._visible_now
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
        elif self.kind == "agent_close":
            dom.agent_panel_expanded = False
        elif self.kind == "agent_toggle":
            dom.agent_mode = not dom.agent_mode
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
        elif self.kind == "cookie_button":
            dom.cookie_bar_present = False
        elif self.kind == "dialog_close":
            dom.dialog_present = False
            dom.dialog_closed += 1
        elif self.kind == "agent_chip":
            dom.agent_chip_clicks += 1
            if dom.agent_chip_click_raises:
                raise PlaywrightTimeoutError("click: element is not stable")
            if not dom.agent_chip_sticks:
                dom.agent_mode = False


class PlaywrightTimeoutError(Exception):
    """A stand-in, NOT ``playwright.async_api.TimeoutError`` — and that matters now.

    Since #776, ``MigratedComposer._click`` catches the *real* Playwright class to
    convert a click timeout into ``UiSelectorDriftError``. Raising this one from a fake
    locator therefore does **not** match that ``except``, so the post-mortem never runs
    and the test silently exercises nothing. If you are writing a click-timeout
    regression test, import the real class — see
    ``tests/api/transports/test_click_attribution.py``.
    """


class FakeFileChooser:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def set_files(self, files: Any) -> None:
        dom = self.page.dom
        dom.chosen_files.append(str(files))
        if dom.consent_dialog_on_upload:
            dom.dialog_present = True
            return  # ...and no upload request is ever made
        if dom.page_dies_on_upload:
            dom.dialog_probe_raises = True
            return
        reply = dom.maseq_reply
        if reply == "none":
            return
        # Everything past here means the upload actually left the page.
        self.page._fire_request(_batch_url("maseQ"))
        if reply == "sent_no_reply":
            return
        payloads: dict[str, list[Any]] = {
            "ok": [MEDIA_UP, PROJ_UUID, "44444444-4444-4444-8444-444444444444", "CAE"],
            "no_id": ["CAE", 1],
            "project_first": [PROJ_UUID, MEDIA_UP, "CAE"],
        }
        status = 200 if reply in payloads else int(reply)
        text = _frame("maseQ", payloads[str(reply)]) if status == 200 else ""
        self.page._fire_response(FakeResponse(_batch_url("maseQ"), text, status))


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
        # (url, text) or (url, text, http_status) — the status defaults to 200, so an
        # existing 2-tuple keeps working and a non-200 reply is expressible.
        self.scripted_responses: list[tuple[str, ...]] = []  # fired on submit click
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

    def _fire_request(self, url: str) -> None:
        for h in list(self._handlers["request"]):
            asyncio.get_event_loop().create_task(_maybe_await(h(FakeRequest(url, ""))))

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
        for scripted in self.scripted_responses:
            url, text, *rest = scripted
            self._fire_response(FakeResponse(url, text, *rest))

    def expect_file_chooser(self, **_: Any) -> FakeChooserContext:
        return FakeChooserContext(self)

    async def evaluate(self, expression: str, argument: Any = None) -> Any:
        if "elementFromPoint" in expression:
            return {"target": True, "top": None}
        return {}

    def locator(self, css: str, *, scope: FakeLocator | None = None) -> FakeLocator:
        dom = self.dom
        if css == migrated_composer.COOKIE_BAR:
            return FakeLocator(
                self, "cookie_bar", ["cookie"], visible=lambda: dom.cookie_bar_present
            )
        if css == "button" and scope is not None and scope.kind == "cookie_bar":
            return FakeLocator(self, "cookie_button", ["button"] if dom.cookie_bar_present else [])
        if css == ".settings-trigger-button":
            # In agent mode it is still THERE (count 1) — just `hidden`. The reporter
            # counted 57 locator matches on an element that never became visible.
            return FakeLocator(
                self,
                "trigger",
                ["trigger"] if dom.trigger_present else [],
                visible=lambda: not (dom.agent_mode and dom.agent_mode_hides_trigger),
            )
        if css == migrated_composer.AGENT_MODE_CHIP:
            pressed = dom.agent_mode and dom.agent_chip_present
            return FakeLocator(self, "agent_chip", ["chip"] if pressed else [])
        if css == "flow-agent-panel button":
            buttons = [Radio("close", "Close")] if dom.agent_panel_expanded else []
            return FakeLocator(self, "agent_close", buttons)
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
        if css == migrated_composer.CREDITS_WARNING:
            hits = ["warning"] if dom.credits_warning_present else []
            return FakeLocator(self, "credits_warning", hits)
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
            present = ["submit"] if self.page.dom.submit_anchor_present else []
            return FakeLocator(self.page, "submit", present)
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


async def test_apply_image_settings_selects_mode_model_aspect_and_count() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.models = ["🍌 Nano Banana 2", "🍌 Nano Banana 2 Lite", "🍌 Nano Banana Pro"]
    page.dom.model_label = "🍌 Nano Banana 2 Lite"
    page.dom.groups["aspect"] = [
        Radio("crop_16_9", "16:9"),
        Radio("crop_landscape", "4:3"),
        Radio("crop_square", "1:1", checked=True),
        Radio("crop_portrait", "3:4"),
        Radio("crop_9_16", "9:16"),
    ]

    await MigratedComposer().apply_image_settings(
        page,
        GenerateImageRequest(
            prompt="a crane",
            model=ImageModel.NARWHAL,
            aspect=ImageAspect.PORTRAIT_THREE_FOUR,
            count=3,
        ),
    )

    assert page.dom.groups["mode"][0].checked
    assert page.dom.model_label == "🍌 Nano Banana 2"
    assert page.dom.groups["aspect"][3].checked
    assert page.dom.groups["count"][2].checked
    assert not page.dom.pane_open


async def test_the_submode_follows_the_reference_not_the_mode_name() -> None:
    """A character reference forces Ingredients even on a t2v request (#723).

    Flow treats an attached character as reference-to-video whatever the caller called
    the mode: the 2026-09-07 route-aborted capture, taken in Ingredients, submitted
    `abra_r2v_8s`. A t2v request that attached a character chip while the composer sat
    under Frames clicked submit and got no reply at all — so this is measured, not a
    tidy-looking guess, and it must not be "simplified" back to `mode is R2V`.
    """
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    assert page.dom.groups["submode"][0].checked  # Frames, the fake's default
    await MigratedComposer().apply_video_settings(
        page, _t2v(reference_entities=("ent-kael",), reference_entity_names=("Kael",))
    )
    assert page.dom.groups["submode"][1].checked  # Ingredients
    assert not page.dom.groups["submode"][0].checked


async def test_a_plain_t2v_still_leaves_the_submode_alone() -> None:
    """The control for the rule above: without a reference nothing touches submode, so
    the forcing cannot silently change every t2v run."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    await MigratedComposer().apply_video_settings(page, _t2v())
    assert page.dom.groups["submode"][0].checked  # untouched


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


def _r2v_request(**kw: Any) -> GenerateVideoRequest:
    base: dict[str, Any] = {
        "prompt": "the presenter holds the product",
        "mode": Mode.R2V,
        "aspect": Aspect.PORTRAIT,
        "reference_images": (Path("a.png"),),
        "duration": None,
    }
    base.update(kw)
    return GenerateVideoRequest(**base)


async def test_r2v_pins_the_base_duration_instead_of_inheriting_the_editors() -> None:
    """Measured 2026-09-06 at $0: at 4s and 6s this host does not refuse a references run,
    it flattens the mention chips to plain prompt text and submits
    `veo_3_1_t2v_lite_4s_low_priority` — a full-price text-to-video clip carrying the file
    NAMES and none of the images. Only 8s submits `veo_3_1_r2v_lite_low_priority`.

    The editor remembers the last duration, so passing none is not "no opinion": it is
    whatever the previous run left. #125's shape, one axis over."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.groups["duration"][2].checked = False  # 8s
    page.dom.groups["duration"][0].checked = True  # the editor remembers 4s
    with capture_logs() as logs:
        await MigratedComposer().apply_video_settings(page, _r2v_request())
    assert page.dom.groups["duration"][2].checked and not page.dom.groups["duration"][0].checked
    assert "migrated.r2v_duration_pinned" in [e["event"] for e in logs]


async def test_r2v_on_a_pane_with_no_duration_row_is_left_alone() -> None:
    """A cohort that renders no duration row already submits the base tier — that is the
    maintainer's account, where r2v has always worked. A missing row is the correct state,
    so the pin must not go through `_select`, whose duration branch raises exit 11."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    del page.dom.groups["duration"]
    with capture_logs() as logs:
        await MigratedComposer().apply_video_settings(page, _r2v_request())
    events = [e["event"] for e in logs]
    assert "migrated.r2v_duration_row_absent" in events
    assert "migrated.r2v_duration_pinned" not in events


async def test_r2v_with_an_explicit_degrading_duration_is_refused_before_submit() -> None:
    """Exit 11 at zero credits beats the post-submit body assertion, which can only name
    the fault after Flow has already been asked to bill it."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    with pytest.raises(ConfigurationError) as exc_info:
        await MigratedComposer().apply_video_settings(page, _r2v_request(duration=4))
    message = str(exc_info.value)
    assert "8s" in message and "silently drops the references" in message
    assert page.dom.groups["duration"][0].checked is False  # nothing was selected


async def test_r2v_at_the_supported_duration_is_allowed_through() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    await MigratedComposer().apply_video_settings(page, _r2v_request(duration=8))
    assert page.dom.groups["duration"][2].checked


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


async def test_a_missing_submit_with_a_credits_warning_is_not_reported_as_drift() -> None:
    """A credit shortfall must not be diagnosed as a moved frontend.

    Measured 2026-09-07 by A/B (`scripts/dev/spike_migrated_submit_anchor.py`): same
    probe, same host, same code, ~60 s apart. The short account rendered NO
    `arrow_forward` and a `prompt-warning-button` with
    ``aria-label='Insufficient credits warning'``; the funded account the mirror image.
    So the anchor's absence tracks the WALLET, not the frontend.

    Reported as `UiSelectorDriftError` this told the user "Google may have updated their
    frontend. Check for a newer gflow-cli release, then file a bug" — a wrong diagnosis
    pointed at a wrong culprit, and one that manufactures frontend-drift reports no code
    change can ever fix.
    """
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.dom.submit_anchor_present = False
    page.dom.credits_warning_present = True

    with pytest.raises(InsufficientCreditsError) as caught:
        await MigratedComposer().submit_and_observe(
            page, poll_timeout_s=2.0, on_started=None, project_id=PROJ
        )

    assert page.dom.submit_clicked == 0
    assert not isinstance(caught.value, UiSelectorDriftError), (
        "an out-of-credits account must not be reported as selector drift"
    )


async def test_a_missing_submit_with_no_credits_warning_is_still_drift() -> None:
    """The guard must not swallow real drift: no warning button means the anchor really
    is gone, and that IS a frontend change worth reporting."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.dom.submit_anchor_present = False
    page.dom.credits_warning_present = False  # anchor gone, wallet fine

    with pytest.raises(UiSelectorDriftError, match="is missing"):
        await MigratedComposer().submit_and_observe(
            page, poll_timeout_s=2.0, on_started=None, project_id=PROJ
        )


async def test_a_submit_that_never_enables_also_checks_the_wallet_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The policy is "never blame the frontend without checking the wallet", and it has
    to hold on every path that gives up on the submit control -- not only the one the
    report came in through.

    This asserts OUR behaviour, not a claim about Flow's DOM: the measured drained state
    removes the anchor entirely (see the test above), and whether Flow ever renders it
    present-but-disabled alongside the warning is unmeasured. The guard costs one DOM
    read on a path that is already failing, and when the warning is absent nothing
    changes -- so a sibling branch left unguarded would be the only way this defect
    comes back.
    """
    from gflow_cli.api.transports import migrated_composer as mc
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(mc, "SUBMIT_ENABLE_BUDGET_S", 0.05)
    monkeypatch.setattr(mc, "SUBMIT_ENABLE_POLL_S", 0.01)
    page = FakePage()
    page.dom.prompt = ""  # the button never enables
    page.dom.credits_warning_present = True

    with pytest.raises(InsufficientCreditsError):
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


async def test_ensure_editor_exits_persisted_agent_mode_before_waiting_for_settings() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage(url="https://flow.google.com/project/p1")
    page.dom.agent_mode = True
    page.dom.agent_panel_expanded = True

    await MigratedComposer().ensure_editor(page, "p1", timeout_s=1.0)

    assert not page.dom.agent_mode
    assert not page.dom.agent_panel_expanded


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
    # No dialog opened, so this stays the plain timeout — the terms wording must not
    # leak onto a run where nothing was asked of the user.
    assert "one-time upload-terms" not in str(ei.value)


async def test_attach_names_the_one_time_terms_dialog_when_one_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#719 shape A. Flow holds the first upload of an account behind a one-time
    terms dialog, rendered only AFTER the file is handed over — so `_dismiss_dialog`,
    which runs back in `ensure_editor`, never sees it, and the driver waited out the
    full budget for a request the page had already declined to make. The old message
    said the upload "never reached Flow" and told the user to re-encode their image;
    it is neither the file nor the network."""
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_UPLOAD_S", 0.2)
    page = FakePage()
    page.dom.consent_dialog_on_upload = True
    with pytest.raises(MediaUploadRejectedError) as ei:
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert "one-time upload-terms" in str(ei.value)
    assert "most likely" in str(ei.value)  # a count is evidence for a guess, not a fact
    assert "rights" in ei.value.remediation_hint
    # A different modal (an error, a quota notice) must leave the user somewhere to go.
    assert "different dialog" in ei.value.remediation_hint
    # The wrong advice this replaces must not survive on this branch.
    assert "re-encoding" not in ei.value.remediation_hint
    assert ei.value.route == "batchexecute:maseQ"


async def test_attach_does_not_blame_a_dialog_that_was_already_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the guard. It fires on a dialog that appeared DURING the upload,
    never on one that was already on screen — otherwise a lingering changelog modal
    (#26) would rewrite every unrelated upload timeout into a terms message and send
    the user to accept something that was never asked of them."""
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_UPLOAD_S", 0.2)
    page = FakePage()
    page.dom.dialog_present = True  # already there before the file is chosen
    page.dom.maseq_reply = "none"
    with pytest.raises(MediaUploadRejectedError) as ei:
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert "one-time upload-terms" not in str(ei.value)
    assert "no upload request ever left the page" in str(ei.value)


async def test_attach_says_the_request_left_the_page_when_flow_just_never_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#719 shape B, and the half the old message could not express. Watching only
    responses cannot tell "the page never asked" from "Flow was slow" — and they are
    different bugs with opposite fixes: one argues for a retry, the other against.
    `FRAME_UPLOAD_S` itself concedes a large file on a slow link can exceed the budget,
    so claiming nothing was sent would have been false on a perfectly healthy upload."""
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_UPLOAD_S", 0.2)
    page = FakePage()
    page.dom.maseq_reply = "sent_no_reply"
    with pytest.raises(MediaUploadRejectedError) as ei:
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert "the request left the page and Flow did not answer" in str(ei.value)
    assert "one-time upload-terms" not in str(ei.value)
    # This message ships a user-visible string, so it must ship somewhere to go with it —
    # and must not repeat the re-encode advice #719 ruled out across three files.
    assert "re-run" in ei.value.remediation_hint
    assert "Re-encoding" in ei.value.remediation_hint
    assert "#719" in ei.value.remediation_hint


async def test_attach_keeps_exit_27_when_the_dialog_probe_itself_dies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe must never become the failure it was added to describe. The likeliest
    reason no reply arrived is that the page died or navigated — in which case the dialog
    count raises too, and letting that escape would swap a mapped exit 27 and its
    remediation for an unmapped traceback. Same lesson as #752, one surface over."""
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "FRAME_UPLOAD_S", 0.2)
    page = FakePage()
    page.dom.page_dies_on_upload = True
    with pytest.raises(MediaUploadRejectedError) as ei:
        await MigratedComposer().attach_start_frame(page, PROJ, _png(tmp_path))
    assert EXIT_CODE_MAP[MediaUploadRejectedError] == 27
    assert "no upload request ever left the page" in str(ei.value)


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


async def test_submit_body_that_cannot_be_read_is_named_as_such() -> None:
    """An unreadable body must not be reported as "a text-to-video key" — that is a
    different fault with a different fix."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a crane"
    page.scripted_request = ("eb1hJf", None)  # Playwright's post_data raised
    page.scripted_responses = [
        (_batch_url("eb1hJf"), _frame("eb1hJf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    with pytest.raises(WireFormatError, match="could not be read"):
        await MigratedComposer().submit_and_observe(
            page, poll_timeout_s=2.0, on_started=None, project_id=PROJ, expect_media_id=MEDIA_UP
        )


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


REF_A = "77777777-7777-4777-8777-777777777777"
REF_B = "88888888-8888-4888-8888-888888888888"


def _r2v_body(*ids: str, key: str = "veo_3_1_r2v_lite_low_priority") -> str:
    packed = ",".join(f'\\"{i}\\"' for i in ids)
    return f'f.req=[[["MZZa6b","[\\"{key}\\",{packed},\\"{PROJ}\\"]",null,"generic"]]]'


def _r2v_replies() -> list[tuple[str, str]]:
    return [
        (_batch_url("MZZa6b"), _frame("MZZa6b", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]


async def test_submit_arms_the_body_assertion_on_the_r2v_path() -> None:
    """The round trip, not the unit: drive the real `submit_and_observe` with a body that
    dropped a reference and assert `_r2v_body_problem` actually fired. Calling that helper
    directly proves it is correct; only this proves it RUNS. It did not — the listener was
    registered under `expect_media_id is not None`, which is never true for r2v, so the
    check was unreachable in a live run while every unit test stayed green."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a woman holding the product"
    page.scripted_request = ("MZZa6b", _r2v_body(REF_A))  # REF_B never made it
    page.scripted_responses = _r2v_replies()  # the app answers anyway; the error must win
    with pytest.raises(WireFormatError, match=REF_B) as ei:
        await MigratedComposer().submit_and_observe(
            page,
            poll_timeout_s=2.0,
            on_started=None,
            project_id=PROJ,
            expect_reference_ids=(REF_A, REF_B),
        )
    assert "missing 1 of 2" in str(ei.value)
    assert "MZZa6b" in ei.value.route and EXIT_CODE_MAP[WireFormatError] == 7


async def test_submit_on_the_r2v_path_names_a_t2v_key_as_the_lost_references() -> None:
    """The expensive shape: the picker closed having inserted nothing, so the app submits
    as plain text-to-video and Flow bills a clip with none of the references on it."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a woman holding the product"
    page.scripted_request = ("MZZa6b", _r2v_body(REF_A, REF_B, key="veo_3_1_lite_low_priority"))
    page.scripted_responses = _r2v_replies()
    with pytest.raises(WireFormatError, match="no reference was bound") as ei:
        await MigratedComposer().submit_and_observe(
            page,
            poll_timeout_s=2.0,
            on_started=None,
            project_id=PROJ,
            expect_reference_ids=(REF_A, REF_B),
        )
    # MODEL_KEY has to match an r2v-shaped key too, or the diagnostic reports "no model
    # key" for the one mode it was added to serve.
    assert "veo_3_1_lite_low_priority" in str(ei.value)


async def test_submit_r2v_with_every_reference_in_the_body_proceeds() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a woman holding the product"
    page.scripted_request = ("MZZa6b", _r2v_body(REF_A, REF_B))
    page.scripted_responses = _r2v_replies()
    rec = await MigratedComposer().submit_and_observe(
        page,
        poll_timeout_s=2.0,
        on_started=None,
        project_id=PROJ,
        expect_reference_ids=(REF_A, REF_B),
    )
    assert rec.is_done and rec.media_id == MEDIA
    assert page.listeners("request") == [] and page.listeners("response") == []


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


# --- #749: agent mode hides the settings trigger ----------------------------
# Measured 2026-09-08 on two accounts: the chip is `button.agent-mode-chip[aria-pressed]`,
# and pressed it puts a bare `hidden` on `.settings-trigger-button`. Flow remembers it per
# account, so without this the account is broken for every run until someone clicks the
# chip back in a browser.


async def test_ensure_editor_leaves_agent_mode_before_waiting_for_the_trigger() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.agent_mode = True
    with capture_logs() as logs:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=1.0)
    assert page.dom.agent_chip_clicks == 1
    assert not page.dom.agent_mode
    assert "migrated.agent_mode_exited" in [e["event"] for e in logs]


async def test_ensure_editor_does_not_touch_the_chip_in_classic_mode() -> None:
    """The control. A recovery that fires unconditionally would click the chip INTO
    agent mode on every healthy run — the same bug, mirrored."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    with capture_logs() as logs:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=1.0)
    assert page.dom.agent_chip_clicks == 0
    assert not page.dom.agent_mode
    assert "migrated.agent_mode_exited" not in [e["event"] for e in logs]


async def test_ensure_editor_names_agent_mode_when_the_chip_will_not_toggle_off() -> None:
    """Not observed on our accounts — but if Flow pins the mode, the user must be told
    THAT, not "file a frontend-drift bug"."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.agent_mode = True
    page.dom.agent_chip_sticks = True
    with pytest.raises(UiSelectorDriftError) as exc:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=0.2)
    assert page.dom.agent_chip_clicks == 1
    assert "agent mode" in str(exc.value).casefold()


async def test_ensure_editor_does_not_claim_agent_mode_when_the_probe_itself_fails() -> None:
    """An unreadable page is ordinary drift. Claiming "you were in agent mode" on the
    strength of a query that never answered sends the user to fix a mode they may never
    have been in."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.agent_mode = True
    page.dom.agent_chip_probe_raises_from = 0
    with capture_logs() as logs, pytest.raises(UiSelectorDriftError) as exc:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=0.2)
    assert page.dom.agent_chip_clicks == 0
    assert "agent mode" not in str(exc.value).casefold()
    assert "migrated.agent_mode_probe_failed" in [e["event"] for e in logs]


async def test_ensure_editor_still_names_agent_mode_when_the_chip_click_fails() -> None:
    """The mirror of the above: the chip WAS found, so agent mode is confirmed whether or
    not the click landed. But a click that never landed changed nothing, so the message
    must say THAT — "the chip was clicked to leave it" sends a user to toggle a chip when
    a modal was covering it — and the run must not wait AGENT_RECOVERY_S for a trigger it
    knows was never asked to change. The click's own exception is chained, not truncated
    into a warning nobody reads."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.agent_mode = True
    page.dom.agent_chip_click_raises = True
    with capture_logs() as logs, pytest.raises(UiSelectorDriftError) as exc:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=0.2)
    assert page.dom.agent_chip_clicks == 1
    assert "agent mode" in str(exc.value).casefold()
    assert "could not be clicked" in str(exc.value)
    assert "pinned" not in str(exc.value)
    assert isinstance(exc.value.__cause__, PlaywrightTimeoutError)
    assert "migrated.agent_mode_exit_failed" in [e["event"] for e in logs]


async def test_ensure_editor_falls_back_to_drift_when_agent_mode_renders_no_chip() -> None:
    """Mode on, chip absent — a cohort that hides it, or a renamed class. There is
    nothing to click, so nothing may be promised: the ordinary drift message is the
    honest one, and the `agent_chip_present` knob exists for exactly this state."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.agent_mode = True
    page.dom.agent_chip_present = False
    with pytest.raises(UiSelectorDriftError) as exc:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=0.2)
    assert page.dom.agent_chip_clicks == 0
    assert "agent mode" not in str(exc.value).casefold()


async def test_ensure_editor_reports_drift_once_agent_mode_is_actually_off() -> None:
    """The chip clicked, the mode left, and the trigger STILL missing — a real
    selector-drift bug someone should file. A pinned-mode message written without reading
    `aria-pressed` back would bury it behind an account setting the driver just changed."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.agent_mode = True
    page.dom.trigger_present = False  # gone for real, not merely hidden
    with pytest.raises(UiSelectorDriftError) as exc:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=0.2)
    assert page.dom.agent_chip_clicks == 1
    assert not page.dom.agent_mode
    assert "ordinary selector drift" in str(exc.value)
    assert "pinned" not in str(exc.value)


async def test_open_pane_names_agent_mode_when_the_mode_flips_after_readiness() -> None:
    """#749 at the second gate. `ensure_editor` passed, then the mode flipped: a `count()`
    guard sees the still-present trigger, clicks the hidden element, and the run dies as a
    bare Playwright timeout with no exit code and no mention of the mode."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    composer = MigratedComposer()
    await composer.ensure_editor(page, "p1", timeout_s=1.0)
    page.dom.agent_mode = True
    with pytest.raises(UiSelectorDriftError) as exc:
        await composer.apply_video_settings(page, _t2v())
    assert "agent mode" in str(exc.value).casefold()


async def test_ensure_editor_will_not_call_it_drift_when_the_chip_cannot_be_read_back() -> None:
    """The mirror of the probe-failure case, and the one that bites harder. `False` and
    "could not answer" are the same value to a bool, but here the claim rides on the
    NEGATIVE: an account genuinely pinned in agent mode, on a page that went dark during
    the recovery wait, would be told the mode is off and to file a frontend-drift bug —
    the exact mis-routing this whole issue exists to stop, one inversion further on."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.agent_mode = True
    page.dom.agent_chip_sticks = True
    page.dom.agent_chip_probe_raises_from = 1  # answers once, dark by the read-back
    with pytest.raises(UiSelectorDriftError) as exc:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=0.2)
    assert page.dom.agent_chip_clicks == 1
    assert "could not be read back" in str(exc.value)
    assert "ordinary selector drift" not in str(exc.value)
    assert "pinned" not in str(exc.value)


async def test_ensure_editor_reports_but_never_clicks_a_chip_pressed_beside_a_ready_trigger() -> (
    None
):
    """The unmeasured cohort: agent mode on, trigger visible anyway. The readiness gate
    passes, so nothing downstream would ever notice that `send_prompt` is about to type
    into the agent composer. It is recorded — and NOT clicked: a click here mutates a
    server-remembered account setting on a run that is otherwise healthy, and the same
    line fires right after a successful recovery, where a chip still reading pressed for
    one frame would toggle the account straight back into agent mode."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.agent_mode = True
    page.dom.agent_mode_hides_trigger = False
    with capture_logs() as logs:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=1.0)
    events = [e["event"] for e in logs]
    assert "migrated.agent_mode_chip_pressed_while_ready" in events
    assert "migrated.editor_ready" in events
    assert page.dom.agent_chip_clicks == 0
    assert page.dom.agent_mode is True


async def test_ensure_editor_drift_message_does_not_blame_agent_mode_in_classic() -> None:
    """A trigger missing outright is ordinary drift — the agent-mode wording must not
    leak onto it, or the next reporter gets sent to the wrong place."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.trigger_present = False
    with pytest.raises(UiSelectorDriftError) as exc:
        await MigratedComposer().ensure_editor(page, "p1", timeout_s=0.2)
    assert "agent mode" not in str(exc.value).casefold()


# ----------------------------------------------------------------------------------
# submit_images_and_observe — the migrated image drive path.
#
# These mirror the video `submit_and_observe` cases above against the same FakePage.
# The path had no offline coverage at all: every image test mocked `run_images` away,
# so the branches that decide whether a run is TRUSTWORTHY — the route-error listener
# that refuses to report a text-only generation as i2i, and a non-200 submit — were
# reachable only from a live account.
# ----------------------------------------------------------------------------------


def _image_frame(reference: str | None = None) -> str:
    from tests.api.transports.test_migrated_images import image_payload

    return _frame("ogiZ0b", image_payload(reference=reference))


async def test_image_submit_decodes_the_ogiz0b_reply() -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a blue cup"
    page.scripted_responses = [(_batch_url("ogiZ0b"), _image_frame())]

    images = await MigratedComposer().submit_images_and_observe(
        page, GenerateImageRequest(prompt="a blue cup")
    )

    assert len(images) == 1
    assert images[0].fife_url.startswith("https://flow-content.google/image/")
    assert images[0].dimensions == (1376, 768)
    assert page.dom.submit_clicked == 1


async def test_an_image_submit_missing_its_reference_is_refused_not_reported_as_i2i() -> None:
    """The route-error listener is the only thing between a dropped upload and a
    plausible T2I result handed back as image-to-image. Flow answers 200 either way.
    """
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    missing = "44444444-4444-4444-8444-444444444444"
    page = FakePage()
    page.dom.prompt = "a blue cup"
    page.scripted_request = ("ogiZ0b", '[["ogiZ0b", "NARWHAL no-reference-here"]]')
    page.scripted_responses = [(_batch_url("ogiZ0b"), _image_frame())]

    with pytest.raises(WireFormatError) as info:
        await MigratedComposer().submit_images_and_observe(
            page, GenerateImageRequest(prompt="a blue cup"), reference_ids=(missing,)
        )
    assert missing in str(info.value)


async def test_a_non_200_image_submit_is_a_wire_format_error_carrying_the_status() -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakePage()
    page.dom.prompt = "a blue cup"
    page.scripted_responses = [(_batch_url("ogiZ0b"), "", 500)]

    with pytest.raises(WireFormatError) as info:
        await MigratedComposer().submit_images_and_observe(
            page, GenerateImageRequest(prompt="a blue cup")
        )
    assert "HTTP 500" in str(info.value)


async def test_an_image_reply_that_never_arrives_is_a_timeout_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports import migrated_composer
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "IMAGE_REPLY_BUDGET_S", 0.05)
    page = FakePage()
    page.dom.prompt = "a blue cup"
    page.scripted_responses = []  # Flow answers nothing

    with pytest.raises(TransportTimeoutError, match="ogiZ0b"):
        await MigratedComposer().submit_images_and_observe(
            page, GenerateImageRequest(prompt="a blue cup")
        )


# ----------------------------------------------------------------------------------
# run_images — the migrated image entry point.
#
# Every image test mocked this away, so the whole orchestration (guard → project →
# editor → settings → references → prompt → submit) was reachable only from a live
# account. That left SonarCloud's new-code coverage under its gate on the merge, and
# more importantly left the reference chip-count check — the one thing standing
# between a dropped upload and a T2I result returned as i2i — never executed offline.
# ----------------------------------------------------------------------------------


def _image_page() -> FakePage:
    """A FakePage on a migrated project, wired for the image axes."""
    page = FakePage(url="https://flow.google.com/project/p1")
    page.dom.prompt = "a blue cup"
    page.dom.models = ["🍌 Nano Banana 2", "🍌 Nano Banana 2 Lite", "🍌 Nano Banana Pro"]
    page.dom.model_label = "🍌 Nano Banana 2"
    page.dom.groups["aspect"] = [
        Radio("crop_16_9", "16:9", checked=True),
        Radio("crop_landscape", "4:3"),
        Radio("crop_square", "1:1"),
        Radio("crop_9_16", "9:16"),
    ]
    page.scripted_responses = [(_batch_url("ogiZ0b"), _image_frame())]
    return page


async def test_run_images_drives_the_whole_t2i_path() -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import run_images

    page = _image_page()
    images = await run_images(page, GenerateImageRequest(prompt="a blue cup"), project_id="p1")

    assert len(images) == 1
    assert images[0].fife_url.startswith("https://flow-content.google/image/")
    assert page.dom.submit_clicked == 1


async def test_run_images_refuses_an_unported_form_before_touching_the_page() -> None:
    from gflow_cli.api.image import GenerateImageRequest, ImageRef
    from gflow_cli.api.transports.migrated_composer import run_images

    page = _image_page()
    with pytest.raises(FlowHostMigratedError, match="media UUID"):
        await run_images(
            page,
            GenerateImageRequest(prompt="a blue cup", refs=(ImageRef(MEDIA),)),
            project_id="p1",
        )
    assert page.dom.submit_clicked == 0
    assert page.gotos == []


async def test_run_images_without_a_project_is_a_configuration_error() -> None:
    """A fresh project can only be made through the labs gallery, so the caller must
    name one. Exit 11, not a mid-run failure after the editor is already mounted.
    """
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import run_images

    page = _image_page()
    page.url = "https://flow.google.com/"  # no project id to extract
    with pytest.raises(ConfigurationError, match="--project"):
        await run_images(page, GenerateImageRequest(prompt="a blue cup"), project_id=None)
    assert page.dom.submit_clicked == 0


async def test_run_images_refuses_when_a_reference_uploaded_but_never_bound(
    tmp_path: Path,
) -> None:
    """Uploads that do not become mention chips are the silent-degrade-to-T2I case:
    Flow would accept the submit and bill it, and the caller would get a plausible
    image that ignored their reference.
    """
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import MigratedComposer, run_images

    ref = tmp_path / "reference.png"
    ref.write_bytes(b"png")
    page = _image_page()

    async def _attach(*_: Any, **__: Any) -> tuple[str, ...]:
        return (MEDIA_UP,)

    async def _no_chips(*_: Any, **__: Any) -> list[str]:
        return []

    with (
        patch.object(MigratedComposer, "attach_references", _attach),
        patch.object(MigratedComposer, "read_chips", _no_chips),
        pytest.raises(ReferenceNotFoundError, match="mention chip"),
    ):
        await run_images(
            page, GenerateImageRequest(prompt="a blue cup", ref_paths=(ref,)), project_id="p1"
        )
    assert page.dom.submit_clicked == 0


async def test_a_missing_image_submit_with_a_credits_warning_is_not_drift() -> None:
    """The image path carries its own copy of the video path's credits check, so it
    needs its own proof. A drained wallet REPLACES Flow's submit button (#721); calling
    that selector drift tells the user to file a frontend bug no code change can fix.
    """
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = _image_page()
    page.dom.submit_anchor_present = False
    page.dom.credits_warning_present = True

    with pytest.raises(InsufficientCreditsError) as caught:
        await MigratedComposer().submit_images_and_observe(
            page, GenerateImageRequest(prompt="a blue cup")
        )
    assert page.dom.submit_clicked == 0
    assert not isinstance(caught.value, UiSelectorDriftError)


async def test_a_missing_image_submit_with_no_credits_warning_is_still_drift() -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = _image_page()
    page.dom.submit_anchor_present = False
    page.dom.credits_warning_present = False

    with pytest.raises(UiSelectorDriftError, match="is missing"):
        await MigratedComposer().submit_images_and_observe(
            page, GenerateImageRequest(prompt="a blue cup")
        )


async def test_an_image_submit_that_stays_disabled_is_drift_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    monkeypatch.setattr(migrated_composer, "SUBMIT_ENABLE_BUDGET_S", 0.05)
    monkeypatch.setattr(migrated_composer, "SUBMIT_ENABLE_POLL_S", 0.01)
    page = _image_page()
    page.dom.prompt = ""  # nothing in the composer: the button never enables

    with pytest.raises(UiSelectorDriftError, match="stayed disabled"):
        await MigratedComposer().submit_images_and_observe(
            page, GenerateImageRequest(prompt="a blue cup")
        )
    assert page.dom.submit_clicked == 0


async def test_pre_submit_gate_refuses_blocking_overlay_before_network_observers() -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = _image_page()
    page.dom.pane_open = True

    with pytest.raises(UiSelectorDriftError, match="blocking overlay"):
        await MigratedComposer().submit_images_and_observe(
            page, GenerateImageRequest(prompt="a blue cup")
        )

    assert page.dom.submit_clicked == 0
    assert page.listeners("request") == []
    assert page.listeners("response") == []
