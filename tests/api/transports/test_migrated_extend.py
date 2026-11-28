"""Unit tests for the flow.google.com extend driver (design B, #882 takeover).

Shapes are the ones measured in docs/superpowers/spikes/2026-10-17-migrated-extend-ui-path.md:
the passive ``Zzl0ze`` record (round 2, B3) and the page's own ``rqZuUc`` / ``fZytfe``
request bodies (round 1, A2/A3).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from gflow_cli.api.transports import migrated_extend as mx
from gflow_cli.api.transports.batchexecute import GenerationRecord
from gflow_cli.api.video import VideoStarted
from gflow_cli.errors import (
    ConfigurationError,
    FlowAppError,
    FlowHostMigratedError,
    GFlowError,
    TransportTimeoutError,
    WireFormatError,
)
from gflow_cli.json_output import exit_code_for

PID = "95763401-d3f0-4a15-8226-e5322d7cd6d0"
MEDIA = "648d2a96-1f02-4b6b-ad47-5a5dcaa7a932"
WF = "a469dbf6-674b-4fff-8f1e-1ef819589990"
IMAGE = "c7176060-d0ee-49fa-ac83-9b7ebff4567e"
IMAGE_WF = "11111111-2222-4333-8444-555555555555"
OTHER_WF = "99999999-8888-4777-8666-555555555555"
SCENE = "80df52ec-b2f6-4b3b-89e0-622cdafb41e5"
TOKEN = "0cAFcWeA" + "x" * 2481  # stands in for the page-minted reCAPTCHA token


def _video_record(wf: str = WF, media: str = MEDIA) -> list[Any]:
    info = [None, 306773, None, None, None, None, None, "p", "https://flow-content.google/v"]
    info += [None, None, None, "veo_3_1_i2v_s", "", None, False, 2]
    return [wf, PID, media, "CAE", None, [[1, 2]], None, [info, [None, None, [8]], [media]]]


def _image_record() -> list[Any]:
    meta = [None, None, None, "https://flow.google.com/asb/x", 0, None, "image/jpeg"]
    return [IMAGE_WF, PID, IMAGE, "CAE", None, [[1, 2]], None, [None, meta, [1376, 768]]]


def _zzl0ze() -> list[Any]:
    return [[[_image_record(), _video_record()]], None]


def _body(rpcid: str, inner: Any) -> str:
    """What ``_post_data`` hands a guard: the form-decoded ``f.req`` plus ``at``."""
    outer = [[[rpcid, json.dumps(inner), None, "generic"]]]
    return f"f.req={json.dumps(outer)}&at=AJpMEa0:1760000000000&"


def _fzytfe(wf: str = WF, aspect_idx: int = 2, key: str = "veo_3_1_extension_lite") -> str:
    entry = [
        [None, wf, 169, 192],
        [None, None, [[["continue the camera move"]]]],
        key,
        aspect_idx,
        None,
        [SCENE, None, None, None, "U-A", "U-B"],
    ]
    ctx = [None, 22, None, None, None, PID, None, None, None, None, [TOKEN, 1]]
    return _body("fZytfe", [[entry], ctx, ["U-C", 1, None, [SCENE, 1]]])


def _rqzuuc(media: str = MEDIA) -> str:
    return _body("rqZuUc", [f"projects/{PID}", [media], None, None, 2])


# --- refusal matrix (PREDICT mitigation 1) ----------------------------------


@pytest.mark.parametrize(
    ("kwargs", "noun"),
    [
        ({"segments": 2, "scene_id": None, "seed": None}, "more than one segment"),
        ({"segments": 1, "scene_id": SCENE, "seed": None}, "an existing scene"),
        ({"segments": 1, "scene_id": None, "seed": 7}, "a fixed --seed"),
    ],
)
def test_unported_forms_are_refused_by_name_with_exit_36(kwargs: dict[str, Any], noun: str) -> None:
    err = mx.unported_refusal(**kwargs)
    assert isinstance(err, FlowHostMigratedError)
    assert exit_code_for(err) == 36
    assert noun in err.detail
    assert "not ported" in err.detail


def test_a_single_segment_is_ported() -> None:
    assert mx.unported_refusal(segments=1, scene_id=None, seed=None) is None


# --- Zzl0ze: media -> workflow + kind (mitigation 2) -------------------------


def test_source_record_maps_media_to_workflow_and_kind() -> None:
    clip = mx.source_record(_zzl0ze(), MEDIA)
    assert clip == mx.SourceClip(workflow_id=WF, kind="video")


# #1346, measured 2026-11-18: a clip extended twice is listed as three workflow records
# with the same media id (the original, then one per extend), in no particular order, and
# the media's own entry names the newest as its current workflow (slot 3, item 4). The
# page's next fZytfe continues that one.
ROOT_WF, MID_WF = WF, "22222222-3333-4444-8555-666666666666"
HEAD_WF = "33333333-4444-4555-8666-777777777777"


def _media_entry(current: str, media: str = MEDIA) -> list[Any]:
    return [media, None, None, ["title", [1794949969, 0], None, None, current, "E9", [1], PID]]


def _extended_zzl0ze(current: str = HEAD_WF) -> list[Any]:
    records = [_video_record(MID_WF), _video_record(ROOT_WF), _video_record(HEAD_WF)]
    return [None, [_media_entry(current)], [_image_record(), *records]]


def test_an_extended_clip_maps_to_the_workflow_its_media_entry_names() -> None:
    clip = mx.source_record(_extended_zzl0ze(), MEDIA)
    assert clip == mx.SourceClip(workflow_id=HEAD_WF, kind="video")


def test_another_clips_entry_listed_first_is_not_read_as_this_clips() -> None:
    listing = _extended_zzl0ze()
    listing[1].insert(0, _media_entry(IMAGE_WF, media=IMAGE))
    assert mx.source_record(listing, MEDIA) == mx.SourceClip(workflow_id=HEAD_WF, kind="video")


def test_a_current_workflow_of_another_clip_is_not_taken() -> None:
    # The entry's pointer must name one of THIS media's records; otherwise the first stands.
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        clip = mx.source_record(_extended_zzl0ze(current=IMAGE_WF), MEDIA)
    assert clip == mx.SourceClip(workflow_id=MID_WF, kind="video")
    assert [e["event"] for e in logs] == ["migrated_extend.current_workflow_unresolved"]


def test_source_record_reads_an_image_as_an_image() -> None:
    clip = mx.source_record(_zzl0ze(), IMAGE)
    assert clip is not None
    assert clip.kind == "image"


def test_source_record_is_none_for_a_media_not_in_the_reply() -> None:
    assert mx.source_record(_zzl0ze(), "00000000-0000-4000-8000-000000000000") is None


def test_a_media_not_in_the_project_is_exit_11_naming_project() -> None:
    with pytest.raises(ConfigurationError) as info:
        mx.require_video_source(None, media_id=MEDIA, project_id=PID)
    assert exit_code_for(info.value) == 11
    assert "--project" in info.value.detail


def test_an_image_source_is_exit_11() -> None:
    with pytest.raises(ConfigurationError) as info:
        mx.require_video_source(
            mx.SourceClip(workflow_id=IMAGE_WF, kind="image"), media_id=IMAGE, project_id=PID
        )
    assert "image" in info.value.detail
    assert "video" in info.value.detail


def test_a_video_source_returns_its_workflow() -> None:
    clip = mx.SourceClip(workflow_id=WF, kind="video")
    assert mx.require_video_source(clip, media_id=MEDIA, project_id=PID) == WF


# --- the route guard's verdicts (mitigation 3) ------------------------------


_IDS: dict[str, Any] = {"media_id": MEDIA, "workflow_id": WF, "aspect": None}


def _check(rpcid: str, body: str, aspect: str | None = None) -> Any:
    return mx.extend_request_problem(rpcid, body, media_id=MEDIA, workflow_id=WF, aspect=aspect)


def test_the_page_built_requests_pass() -> None:
    assert _check("rqZuUc", _rqzuuc()) is None
    assert _check("fZytfe", _fzytfe()) is None


def test_other_rpcs_are_not_judged() -> None:
    assert _check("as29s", "f.req=whatever") is None


def test_a_scene_for_another_clip_is_refused() -> None:
    err = _check("rqZuUc", _rqzuuc(media=IMAGE))
    assert isinstance(err, WireFormatError)
    assert MEDIA in err.detail


def test_an_extend_of_another_workflow_is_refused() -> None:
    err = _check("fZytfe", _fzytfe(wf=OTHER_WF))
    assert isinstance(err, WireFormatError)
    assert WF in err.detail and OTHER_WF in err.detail


def test_an_unreadable_submit_is_refused() -> None:
    assert isinstance(_check("fZytfe", "f.req=[[[not json"), WireFormatError)
    assert isinstance(_check("fZytfe", ""), WireFormatError)


def test_an_explicit_aspect_the_source_does_not_have_is_exit_36() -> None:
    err = _check("fZytfe", _fzytfe(aspect_idx=2), aspect="9:16")
    assert isinstance(err, FlowHostMigratedError)
    assert "9:16" in err.detail


def test_a_matching_or_absent_aspect_passes() -> None:
    assert _check("fZytfe", _fzytfe(aspect_idx=2), aspect="16:9") is None
    assert _check("fZytfe", _fzytfe(aspect_idx=1), aspect=None) is None


@pytest.mark.parametrize(
    "body", [_fzytfe(wf=OTHER_WF), _fzytfe(aspect_idx=1), 'f.req=[[["fZytfe", 1]]]']
)
def test_no_verdict_ever_carries_the_token(body: str) -> None:
    err = mx.extend_request_problem("fZytfe", body, media_id=MEDIA, workflow_id=WF, aspect="16:9")
    assert err is not None
    assert TOKEN not in str(err)
    assert TOKEN[:40] not in err.detail
    assert "AJpMEa0" not in err.detail


def test_submit_facts_reads_model_key_and_aspect() -> None:
    assert mx.submit_facts(_fzytfe(aspect_idx=1)) == ("veo_3_1_extension_lite", "9:16")
    assert mx.submit_facts("") == (None, None)


def test_submit_facts_reads_the_omni_extend_key() -> None:
    # #1180: measured 2026-11-08 on flow.google.com, the page's fZytfe carries an Omni key.
    body = _fzytfe(aspect_idx=1, key="macaron_cookie_extend_4s")
    assert mx.submit_facts(body) == ("macaron_cookie_extend_4s", "9:16")


def test_submit_facts_never_reads_the_key_from_the_prompt() -> None:
    body = _fzytfe(key="macaron_cookie_extend_4s").replace(
        "continue the camera move", "a slow_extend_shot in veo_style light"
    )
    assert mx.submit_facts(body) == ("macaron_cookie_extend_4s", "16:9")


# --- the driver (mitigations 2-5), against a fake page -----------------------

NEW_WF = "12345678-1234-4234-8234-123456789abc"
NEW_MEDIA = "87654321-4321-4321-8321-cba987654321"
EDITOR = f"https://flow.google.com/project/{PID}/edit/{MEDIA}"


def _frame(rpcid: str, payload: Any) -> str:
    frame = [["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]]
    return ")]}'\n\n10\n" + json.dumps(frame) + "\n"


class _Resp:
    def __init__(self, rpcid: str, payload: Any) -> None:
        # Batched with another rpc ahead of it, as the app may send the project read.
        self.url = f"https://flow.google.com/_/X/data/batchexecute?rpcids=HTrJv,{rpcid}&rt=c"
        self._text = _frame(rpcid, payload)

    async def text(self) -> str:
        return self._text


class _Keyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []

    async def insert_text(self, text: str) -> None:
        self.typed.append(text)

    async def press(self, key: str) -> None:
        self.typed.append(f"<{key}>")


class _Page:
    """Just enough Page for ``run_extend``: listeners, goto, route, keyboard."""

    def __init__(
        self, listing: Any, *, land: str = EDITOR, answer: bool = True, then: Any = None
    ) -> None:
        self.url = "about:blank"
        self._listings = [listing] if then is None else [listing, then]
        self._land = land
        self._answer = answer
        self.listeners: dict[str, list[Any]] = {}
        self.routes: list[Any] = []
        self.unrouted = 0
        self.keyboard = _Keyboard()
        self.visible: set[str] = set()

    def on(self, event: str, fn: Any) -> None:
        self.listeners.setdefault(event, []).append(fn)

    def remove_listener(self, event: str, fn: Any) -> None:
        self.listeners[event].remove(fn)

    async def goto(self, url: str, **_kw: Any) -> None:
        assert "/edit/" in url
        self.url = self._land
        if self._answer:
            for listing in self._listings:
                for fn in list(self.listeners.get("response", [])):
                    await fn(_Resp("Zzl0ze", listing))

    async def route(self, _match: Any, handler: Any) -> None:
        self.routes.append(handler)

    async def unroute(self, _match: Any, handler: Any) -> None:
        self.routes.remove(handler)
        self.unrouted += 1

    def locator(self, selector: str) -> _Visibility:
        return _Visibility(selector in self.visible)


class _Visibility:
    def __init__(self, visible: bool) -> None:
        self.first, self._visible = self, visible

    async def is_visible(self) -> bool:
        return self._visible


class _Locator:
    def __init__(self, clicks: list[str], name: str, *, disabled_reads: int = 0) -> None:
        self._clicks, self._name, self._disabled_reads = clicks, name, disabled_reads

    async def click(self, **_kw: Any) -> None:
        self._clicks.append(self._name)

    async def is_disabled(self) -> bool:
        """Disabled for the first ``disabled_reads`` reads (-1: always)."""
        if self._disabled_reads == 0:
            return False
        self._disabled_reads -= self._disabled_reads > 0
        return True

    async def get_attribute(self, _name: str) -> str | None:
        return None


def _done_record() -> GenerationRecord:
    return GenerationRecord(
        workflow_id=NEW_WF,
        project_id=PID,
        media_id=NEW_MEDIA,
        status=3,
        video_url="https://flow-content.google/video/x",
        poster_url=None,
        size_bytes=None,
    )


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Anchors become recorded clicks; the observer and the download are stubbed."""
    seen: dict[str, Any] = {"clicks": [], "observe": None, "workflow": NEW_WF, "disabled": {}}

    async def anchor(_page: Any, selector: str, *, named: str) -> _Locator:
        return _Locator(seen["clicks"], selector, disabled_reads=seen["disabled"].get(selector, 0))

    async def observe(_self: Any, page: Any, **kwargs: Any) -> GenerationRecord:
        seen["observe"] = kwargs
        seen["routes_during_observe"] = len(page.routes)
        kwargs["on_started"](VideoStarted(media_id=NEW_MEDIA, workflow_id=seen["workflow"]))
        page.url = f"https://flow.google.com/project/{PID}/scene/{SCENE}"
        return _done_record()

    async def fetch(_page: Any, _record: Any) -> bytes:
        return b"\x00\x00\x00\x18ftypmp42"

    monkeypatch.setattr(mx, "_anchor", anchor)
    monkeypatch.setattr(mx.MigratedComposer, "submit_and_observe", observe)
    monkeypatch.setattr(mx.MigratedComposer, "fetch_mp4", staticmethod(fetch))
    return seen


async def _run(page: _Page, media: str = MEDIA) -> Any:
    return await mx.run_extend(
        page,  # type: ignore[arg-type]
        project_id=PID,
        media_id=media,
        prompt="keep going",
        aspect=None,
        poll_timeout_s=30.0,
    )


@pytest.mark.asyncio
async def test_a_media_missing_from_the_project_is_refused_before_any_click(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mx, "LISTING_BUDGET_S", 0.01)  # a read was seen: exit 11, not 9
    page = _Page([[[_image_record()]]])
    with pytest.raises(ConfigurationError):
        await _run(page)
    assert driver["clicks"] == [] and driver["observe"] is None
    assert page.listeners["response"] == []


@pytest.mark.asyncio
async def test_a_later_listing_that_holds_the_clip_is_not_lost_to_an_earlier_one(
    driver: dict[str, Any],
) -> None:
    page = _Page([[[_image_record()]]], then=_zzl0ze())
    out = await _run(page)
    assert out.record.media_id == NEW_MEDIA


@pytest.mark.asyncio
async def test_no_captured_submit_facts_is_logged(driver: dict[str, Any]) -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        await _run(_Page(_zzl0ze()))
    assert any(e["event"] == "migrated_extend.no_submit_facts" for e in logs)


@pytest.mark.asyncio
async def test_an_image_is_refused_before_any_click(driver: dict[str, Any]) -> None:
    page = _Page([[[_image_record()]]], land=f"https://flow.google.com/project/{PID}/edit/{IMAGE}")
    with pytest.raises(ConfigurationError, match="image"):
        await _run(page, media=IMAGE)
    assert driver["clicks"] == []


@pytest.mark.asyncio
async def test_a_redirected_editor_is_refused_before_any_click(driver: dict[str, Any]) -> None:
    page = _Page(_zzl0ze(), land=f"https://flow.google.com/project/{PID}")
    with pytest.raises(FlowAppError):
        await _run(page)
    assert driver["clicks"] == []


@pytest.mark.asyncio
async def test_a_silent_listing_is_a_timeout_before_any_click(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mx, "LISTING_BUDGET_S", 0.01)
    page = _Page(_zzl0ze(), answer=False)
    with pytest.raises(TransportTimeoutError, match="Zzl0ze") as info:
        await _run(page)
    assert driver["clicks"] == []
    assert page.listeners["response"] == []
    assert info.value.charged == "no"  # #1320: the source read precedes any submit


@pytest.mark.asyncio
async def test_the_happy_path_drives_the_measured_anchors_and_downloads(
    driver: dict[str, Any],
) -> None:
    page = _Page(_zzl0ze())
    out = await _run(page)
    assert driver["clicks"] == [mx.ADD_CLIP_BUTTON, mx.EXTEND_MENU_ITEM, mx.EXTEND_PROMPT]
    assert page.keyboard.typed == ["keep going"]
    kw = driver["observe"]
    assert kw["submit_rpcs"] == ("fZytfe",)
    assert kw["submit_selector"] == mx.EXTEND_SUBMIT
    assert kw["request_check"]("fZytfe", _fzytfe(wf=OTHER_WF)) is not None
    assert driver["routes_during_observe"] == 1
    assert page.routes == [] and page.unrouted == 2  # the pre-submit hold, then the guard
    assert out.record.media_id == NEW_MEDIA
    assert out.video is not None and out.video[4:8] == b"ftyp"
    assert out.scene_id == SCENE


@pytest.mark.asyncio
async def test_an_echo_of_the_source_workflow_is_a_failure(driver: dict[str, Any]) -> None:
    driver["workflow"] = WF  # the reply names the SOURCE clip's workflow
    page = _Page(_zzl0ze())
    with pytest.raises(WireFormatError, match="source clip"):
        await _run(page)
    assert page.routes == []  # the guard is removed on the failure path too


class _Route:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def abort(self) -> None:
        self.calls.append("abort")

    async def fallback(self) -> None:
        self.calls.append("fallback")


class _Request:
    def __init__(self, rpcid: str, body: str) -> None:
        self.url = f"https://flow.google.com/_/X/data/batchexecute?rpcids={rpcid}&rt=c"
        self.post_data = body


@pytest.mark.asyncio
async def test_the_route_guard_aborts_a_wrong_request_and_releases_a_right_one(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    verdicts: list[tuple[str, str]] = []

    async def observe(_self: Any, page: Any, **_kwargs: Any) -> GenerationRecord:
        guard = page.routes[0]
        for rpcid, body in (
            ("rqZuUc", _rqzuuc(media=IMAGE)),
            ("rqZuUc", _rqzuuc()),
            ("fZytfe", _fzytfe(wf=OTHER_WF)),
            ("fZytfe", _fzytfe()),
            ("as29s", "f.req=[]"),
        ):
            route = _Route()
            await guard(route, _Request(rpcid, body))
            verdicts.append((rpcid, route.calls[0]))
        return _done_record()

    monkeypatch.setattr(mx.MigratedComposer, "submit_and_observe", observe)
    out = await _run(_Page(_zzl0ze()))
    assert verdicts == [
        ("rqZuUc", "abort"),
        ("rqZuUc", "fallback"),
        ("fZytfe", "abort"),
        ("fZytfe", "fallback"),
        ("as29s", "fallback"),
    ]
    assert out.model_key == "veo_3_1_extension_lite"
    assert out.aspect == "16:9"


class _BatchedRequest:
    """A batched request: every rpcid in the URL list, one ``f.req`` frame each."""

    def __init__(self, *frames: tuple[str, Any]) -> None:
        rpcids = ",".join(rpcid for rpcid, _ in frames)
        self.url = f"https://flow.google.com/_/X/data/batchexecute?rpcids={rpcids}&rt=c"
        outer = [[[rpcid, json.dumps(inner), None, "generic"] for rpcid, inner in frames]]
        self.post_data = f"f.req={json.dumps(outer)}&at=AJpMEa0:1760000000000&"


def _fzytfe_inner(wf: str = WF) -> Any:
    return json.loads(json.loads(_fzytfe(wf=wf)[len("f.req=") :].split("&at=")[0])[0][0][1])


async def _guard_calls(
    monkeypatch: pytest.MonkeyPatch, *requests: Any, listing: Any = None
) -> list[str]:
    calls: list[str] = []

    async def observe(_self: Any, page: Any, **_kwargs: Any) -> GenerationRecord:
        for request in requests:
            route = _Route()
            await page.routes[0](route, request)
            calls.append(route.calls[0])
        return _done_record()

    monkeypatch.setattr(mx.MigratedComposer, "submit_and_observe", observe)
    await _run(_Page(_zzl0ze() if listing is None else listing))
    return calls


@pytest.mark.asyncio
async def test_an_extended_clip_releases_only_the_extend_of_its_current_workflow(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1346: the next extend continues the newest workflow; any other is still aborted."""
    requests = (_Request("fZytfe", _fzytfe(wf=wf)) for wf in (OTHER_WF, ROOT_WF, MID_WF, HEAD_WF))
    calls = await _guard_calls(monkeypatch, *requests, listing=_extended_zzl0ze())
    assert calls == ["abort", "abort", "abort", "fallback"]


@pytest.mark.asyncio
async def test_a_wrong_extend_batched_behind_another_rpc_is_aborted(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = _BatchedRequest(("uwAyfb", [SCENE]), ("fZytfe", _fzytfe_inner(wf=OTHER_WF)))
    right = _BatchedRequest(("uwAyfb", [SCENE]), ("fZytfe", _fzytfe_inner()))
    assert await _guard_calls(monkeypatch, wrong, right) == ["abort", "fallback"]


@pytest.mark.asyncio
async def test_a_second_extend_after_a_released_one_is_aborted(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = await _guard_calls(
        monkeypatch, _Request("fZytfe", _fzytfe()), _Request("fZytfe", _fzytfe())
    )
    assert calls == ["fallback", "abort"]


def test_the_observer_check_judges_every_frame_of_a_batch() -> None:
    body = _BatchedRequest(("uwAyfb", [SCENE]), ("fZytfe", _fzytfe_inner(wf=OTHER_WF))).post_data
    assert mx.batch_request_problem(["uwAyfb"], body, **_IDS) is not None


@pytest.mark.asyncio
async def test_a_missing_anchor_is_selector_drift_exit_23(monkeypatch: pytest.MonkeyPatch) -> None:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    from gflow_cli.errors import UiSelectorDriftError

    class _Missing:
        first: Any = None

        async def wait_for(self, **_kw: Any) -> None:
            raise PlaywrightTimeoutError("not visible")

    missing = _Missing()
    missing.first = missing
    page = _Page(_zzl0ze(), land=EDITOR)
    page.locator = lambda _sel: missing  # type: ignore[attr-defined]

    async def not_a_landing(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(mx, "raise_if_known_landing", not_a_landing)
    monkeypatch.setattr(mx, "ANCHOR_TIMEOUT_S", 0.01)
    with pytest.raises(UiSelectorDriftError, match="add-clip") as info:
        await mx._anchor(page, mx.ADD_CLIP_BUTTON, named="timeline add-clip button")  # type: ignore[arg-type]
    assert exit_code_for(info.value) == 23


@pytest.mark.asyncio
async def test_a_missing_extend_anchor_before_the_submit_reports_nothing_charged(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1320: the add-clip/Extend/prompt anchors come before any request Flow could bill."""
    from gflow_cli.errors import UiSelectorDriftError

    async def missing(_page: Any, _selector: str, *, named: str) -> Any:
        raise UiSelectorDriftError(detail=f"{named} missing")

    monkeypatch.setattr(mx, "_anchor", missing)
    page = _Page([[[_image_record()]]], then=_zzl0ze())
    with pytest.raises(UiSelectorDriftError) as info:
        await _run(page)
    assert info.value.charged == "no"
    assert driver["observe"] is None


@pytest.mark.asyncio
async def test_a_disabled_extend_item_is_exit_35_before_any_click(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1334: Flow renders the Extend item disabled on some accounts; clicking it waited
    out Playwright's 30 s and surfaced an unexpected timeout."""
    from gflow_cli.errors import ExtendUnavailableError

    driver["disabled"][mx.EXTEND_MENU_ITEM] = -1
    monkeypatch.setattr(mx, "DISABLED_GRACE_S", 0.0)
    page = _Page(_zzl0ze())
    with pytest.raises(ExtendUnavailableError, match="Extend menu item") as info:
        await _run(page)
    assert exit_code_for(info.value) == 35
    assert info.value.charged == "no"
    assert driver["clicks"] == [mx.ADD_CLIP_BUTTON] and driver["observe"] is None
    assert page.keyboard.typed == ["<Escape>"]  # the menu is closed, the prompt never typed


@pytest.mark.asyncio
async def test_an_extend_item_enabled_within_the_grace_period_is_clicked(
    driver: dict[str, Any],
) -> None:
    driver["disabled"][mx.EXTEND_MENU_ITEM] = 1  # disabled on the first read only
    await _run(_Page(_zzl0ze()))
    assert driver["clicks"] == [mx.ADD_CLIP_BUTTON, mx.EXTEND_MENU_ITEM, mx.EXTEND_PROMPT]


@pytest.mark.asyncio
async def test_a_disabled_extend_item_beside_the_credits_warning_is_exit_37(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports._mc_common import CREDITS_WARNING
    from gflow_cli.errors import InsufficientCreditsError

    driver["disabled"][mx.EXTEND_MENU_ITEM] = -1
    monkeypatch.setattr(mx, "DISABLED_GRACE_S", 0.0)
    page = _Page(_zzl0ze())
    page.visible.add(CREDITS_WARNING)
    with pytest.raises(InsufficientCreditsError) as info:
        await _run(page)
    assert exit_code_for(info.value) == 37 and info.value.charged == "no"
    assert driver["clicks"] == [mx.ADD_CLIP_BUTTON] and driver["observe"] is None


# --- uploaded video recognition, duration, refs & credit cap (2026-11-24) ----

UPLOADED_MEDIA = "44444444-5555-4666-8777-888888888888"
UPLOADED_WF = "55555555-6666-4777-8888-999999999999"
REF1 = "11111111-aaaa-4bbb-8ccc-111111111111"
REF2 = "22222222-aaaa-4bbb-8ccc-222222222222"


def _uploaded_video_record(
    wf: str = UPLOADED_WF,
    media: str = UPLOADED_MEDIA,
    duration_tuple: list[Any] | None = None,
) -> list[Any]:
    return [
        wf,
        PID,
        media,
        None,
        None,
        [[1, 2], None, None, None, None, None, [None, None, None, None, 1], None, [3], 1],
        None,
        [
            None,
            [1280, 720, duration_tuple if duration_tuple is not None else [3]],
            None,
            None,
            [""],
        ],
    ]


def _fzytfe_with_refs(
    wf: str = WF,
    aspect_idx: int = 2,
    key: str = "macaron_cookie_extend_6s",
    ref_ids: tuple[str, ...] = (),
    raw_refs: Any = None,
) -> str:
    refs_wire = [[None, r] for r in ref_ids] if ref_ids else raw_refs
    entry = [
        [None, wf, 0, 72],
        [None, None, [[["continue with glow"]]]],
        key,
        aspect_idx,
        None,
        [None, MEDIA, None, None, "U-A"],
        None,
        None,
        refs_wire,
    ]
    ctx = [None, 22, None, None, None, PID, None, None, None, None, [TOKEN, 1]]
    return _body("fZytfe", [[entry], ctx, ["U-C", 2]])


@pytest.mark.parametrize("duration_tuple", [[3], [5], [8], [4.5], [0, 500_000_000]])
def test_source_record_classifies_uploaded_video_for_any_duration(
    duration_tuple: list[Any],
) -> None:
    listing = [[[_image_record(), _uploaded_video_record(duration_tuple=duration_tuple)]], None]
    clip = mx.source_record(listing, UPLOADED_MEDIA)
    assert clip == mx.SourceClip(workflow_id=UPLOADED_WF, kind="video")
    assert mx.require_video_source(clip, media_id=UPLOADED_MEDIA, project_id=PID) == UPLOADED_WF


@pytest.mark.parametrize(
    "bad_media_info",
    [
        [1280, 720, [0]],
        [1280, 720, [-3]],
        [1280, 720, []],
        [1280, 720, ["3"]],
        [1280, 720, [True]],
        [0, 720, [3]],
        [1280, 720, [1, 2, 3]],
    ],
)
def test_source_record_keeps_malformed_media_info_as_unknown(
    bad_media_info: list[Any],
) -> None:
    bad = [UPLOADED_WF, PID, UPLOADED_MEDIA, None, None, [[1, 2]], None, [None, bad_media_info]]
    clip = mx.source_record([[[bad]], None], UPLOADED_MEDIA)
    # No readable media metadata: not a video yet, and not ready to be extended.
    assert clip == mx.SourceClip(workflow_id=UPLOADED_WF, kind="unknown", ready=False)


def test_source_record_marks_processing_upload_not_ready() -> None:
    processing = _uploaded_video_record()
    processing[5][8] = [1]
    clip = mx.source_record([[[processing]], None], UPLOADED_MEDIA)
    assert clip == mx.SourceClip(workflow_id=UPLOADED_WF, kind="video", ready=False)

    ready = _uploaded_video_record()
    clip_ready = mx.source_record([[[ready]], None], UPLOADED_MEDIA)
    assert clip_ready == mx.SourceClip(workflow_id=UPLOADED_WF, kind="video", ready=True)


def test_extend_guard_verifies_duration_and_reference_ids() -> None:
    good = _fzytfe_with_refs(key="macaron_cookie_extend_6s", ref_ids=(REF1, REF2))
    assert (
        mx.extend_request_problem(
            "fZytfe",
            good,
            media_id=MEDIA,
            workflow_id=WF,
            aspect="16:9",
            duration=6,
            expected_reference_ids=(REF1, REF2),
        )
        is None
    )
    wrong_dur = mx.extend_request_problem(
        "fZytfe",
        good,
        media_id=MEDIA,
        workflow_id=WF,
        aspect="16:9",
        duration=5,
        expected_reference_ids=(REF1, REF2),
    )
    assert isinstance(wrong_dur, WireFormatError)
    missing_ref = mx.extend_request_problem(
        "fZytfe",
        _fzytfe_with_refs(key="macaron_cookie_extend_6s", ref_ids=(REF1,)),
        media_id=MEDIA,
        workflow_id=WF,
        aspect="16:9",
        duration=6,
        expected_reference_ids=(REF1, REF2),
    )
    assert isinstance(missing_ref, WireFormatError)
    assert REF2 in missing_ref.detail
    extra_ref = mx.extend_request_problem(
        "fZytfe",
        _fzytfe_with_refs(key="macaron_cookie_extend_6s", ref_ids=(REF1,)),
        media_id=MEDIA,
        workflow_id=WF,
        aspect="16:9",
        duration=6,
        expected_reference_ids=(),
    )
    assert isinstance(extra_ref, WireFormatError)
    reordered = mx.extend_request_problem(
        "fZytfe",
        _fzytfe_with_refs(key="macaron_cookie_extend_6s", ref_ids=(REF2, REF1)),
        media_id=MEDIA,
        workflow_id=WF,
        aspect="16:9",
        duration=6,
        expected_reference_ids=(REF1, REF2),
    )
    assert isinstance(reordered, WireFormatError)
    assert "order" in reordered.detail
    duplicated = mx.extend_request_problem(
        "fZytfe",
        _fzytfe_with_refs(key="macaron_cookie_extend_6s", ref_ids=(REF1, REF1)),
        media_id=MEDIA,
        workflow_id=WF,
        aspect="16:9",
        duration=6,
        expected_reference_ids=(REF1,),
    )
    assert isinstance(duplicated, WireFormatError)


def test_extend_guard_rejects_multiple_entries_or_frames() -> None:
    inner = _fzytfe_inner()
    two_entries = _body("fZytfe", [[inner[0][0], inner[0][0]], inner[1], inner[2]])
    assert isinstance(
        mx.extend_request_problem(
            "fZytfe", two_entries, media_id=MEDIA, workflow_id=WF, aspect=None
        ),
        WireFormatError,
    )
    outer = [
        [
            ["fZytfe", json.dumps(inner), None, "generic"],
            ["fZytfe", json.dumps(inner), None, "generic"],
        ]
    ]
    two_frames = f"f.req={json.dumps(outer)}&at=x&"
    assert isinstance(
        mx.extend_request_problem(
            "fZytfe", two_frames, media_id=MEDIA, workflow_id=WF, aspect=None
        ),
        WireFormatError,
    )


def test_upload_video_reply_normalizes_swapped_rest_uuids() -> None:
    from gflow_cli.api.transports._mc_upload_video import _parse_upload_reply

    raw = json.dumps(
        {
            "mediaId": UPLOADED_WF,
            "media": {"name": UPLOADED_WF, "workflowId": UPLOADED_MEDIA},
        }
    )
    assert _parse_upload_reply(raw, project_id=PID) == (UPLOADED_MEDIA, UPLOADED_WF)


@pytest.mark.asyncio
async def test_validate_video_file_accepts_mp4_mov_avi_and_rejects_bad(tmp_path: Any) -> None:
    from gflow_cli.api._flow_workspace import validate_video_file

    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
    await validate_video_file(mp4)

    mov = tmp_path / "b.mov"
    mov.write_bytes(b"\x00\x00\x00\x10wide\x00\x00\x00\x08")
    await validate_video_file(mov)

    avi = tmp_path / "c.avi"
    avi.write_bytes(b"RIFF\x00\x01\x00\x00AVI ")
    await validate_video_file(avi)

    bad_ext = tmp_path / "d.txt"
    bad_ext.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
    with pytest.raises(ValueError, match="Unsupported video extension"):
        await validate_video_file(bad_ext)

    bad_magic = tmp_path / "e.mp4"
    bad_magic.write_bytes(b"NOT_A_VIDEO_HEADER")
    with pytest.raises(ValueError, match="Not a supported video format"):
        await validate_video_file(bad_magic)


class _FakeSlider:
    def __init__(self, *, value: int = 4, min_v: int = 3, max_v: int = 10) -> None:
        self.value = value
        self.min_v = min_v
        self.max_v = max_v
        self.keys: list[str] = []
        self.first = self

    async def wait_for(self, **_kw: Any) -> None:
        return None

    async def count(self) -> int:
        return 1

    async def get_attribute(self, name: str) -> str | None:
        return {"min": str(self.min_v), "max": str(self.max_v), "step": "1"}.get(name)

    async def is_enabled(self) -> bool:
        return True

    async def press(self, key: str) -> None:
        self.keys.append(key)
        if key == "Home":
            self.value = self.min_v
        elif key == "ArrowRight":
            self.value = min(self.max_v, self.value + 1)

    async def input_value(self) -> str:
        return str(self.value)


class _FakeDurationComponent:
    def __init__(self, slider: _FakeSlider) -> None:
        self._slider = slider

    def locator(self, _sel: str) -> _FakeSlider:
        return self._slider

    async def inner_text(self) -> str:
        return f"延续镜头时长\n{self._slider.value} 秒"


class _FakeCreditLabel:
    def __init__(self, text: str | None) -> None:
        self._text = text
        self.first = self
        self.last = self

    async def inner_text(self, **_kw: Any) -> str:
        if self._text is None:
            return ""
        return self._text


class _FakeSettingsPane:
    """``quote_text`` ``None``: the label prices the slider at 3 credits a second."""

    def __init__(self, slider: _FakeSlider, quote_text: str | None = None) -> None:
        self._slider = slider
        self._quote_text = quote_text
        self.first = self
        self.last = self
        self.open = True

    async def wait_for(self, **_kw: Any) -> None:
        return None

    async def count(self) -> int:
        return 1 if self.open else 0

    async def is_visible(self) -> bool:
        return self.open

    def locator(self, sel: str) -> Any:
        if "flow-duration-slider" in sel:
            return _FakeDurationComponent(self._slider)
        if self._quote_text is not None:
            return _FakeCreditLabel(self._quote_text)
        return _FakeCreditLabel(f"Generating will use {self._slider.value * 3} credits")


@pytest.mark.asyncio
async def test_settings_slider_and_credit_cap_pass_and_refuse_pre_submit(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.errors import CreditCapExceededError

    slider = _FakeSlider(value=4)
    pane = _FakeSettingsPane(slider)

    class _SettingsPage(_Page):
        def locator(self, selector: str) -> Any:
            if selector == mx.EXTEND_SETTINGS_PANE:
                return pane
            return super().locator(selector)

    orig_press = _Keyboard.press

    async def _press_closes(kb: _Keyboard, key: str) -> None:
        await orig_press(kb, key)
        if key == "Escape":
            pane.open = False

    monkeypatch.setattr(_Keyboard, "press", _press_closes)

    # 1. Happy path: duration=6, max_credits=20 -> quote 18 accepted, slider moved Home + 3x Right
    page = _SettingsPage(_zzl0ze())
    out = await mx.run_extend(
        page,  # type: ignore[arg-type]
        project_id=PID,
        media_id=MEDIA,
        prompt="keep going",
        aspect=None,
        duration=6,
        max_credits=20,
        poll_timeout_s=30.0,
    )
    assert out.quoted_credits == 18
    assert slider.value == 6
    assert slider.keys == ["Home", "ArrowRight", "ArrowRight", "ArrowRight"]
    assert driver["observe"] is not None

    # 2. Refusal path: max_credits=0 -> CreditCapExceededError(charged="no"), never submits
    driver["observe"] = None
    pane.open = True
    slider2 = _FakeSlider(value=4)
    pane._slider = slider2
    page2 = _SettingsPage(_zzl0ze())
    with pytest.raises(CreditCapExceededError) as caught:
        await mx.run_extend(
            page2,  # type: ignore[arg-type]
            project_id=PID,
            media_id=MEDIA,
            prompt="keep going",
            aspect=None,
            max_credits=0,
            poll_timeout_s=30.0,
        )
    assert caught.value.quoted == 12  # the slider was left at Flow's 4s
    assert caught.value.max_credits == 0
    assert caught.value.charged == "no"
    assert driver["observe"] is None


@pytest.mark.asyncio
async def test_local_video_and_refs_upload_and_attach_flow(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    mentioned: list[tuple[str, int, str, str]] = []

    async def _fake_ensure(_self: Any, _page: Any, _pid: str, **_kw: Any) -> None:
        return None

    async def _fake_upload_video(_page: Any, _pid: str, _path: Any) -> str:
        return UPLOADED_MEDIA

    async def _fake_upload_frame(_self: Any, _page: Any, _pid: str, _path: Any) -> tuple[str, str]:
        return REF1, "ref-123.png"

    async def _fake_mention(
        _self: Any, _page: Any, name: str, *, expect_chips: int, composer: str, chip: str
    ) -> None:
        mentioned.append((name, expect_chips, composer, chip))

    monkeypatch.setattr(mx.MigratedComposer, "ensure_editor_for", _fake_ensure)
    monkeypatch.setattr(mx, "upload_video", _fake_upload_video)
    monkeypatch.setattr(mx.MigratedComposer, "_upload_via_toolbar", _fake_upload_frame)
    monkeypatch.setattr(mx.MigratedComposer, "_mention_by_name", _fake_mention)

    class _PromptBox(_Locator):
        @property
        def first(self) -> _PromptBox:
            return self

    class _EvalPage(_Page):
        def locator(self, selector: str) -> Any:
            if selector == mx.EXTEND_PROMPT:
                return _PromptBox([], selector)
            return super().locator(selector)

        async def evaluate(self, *_a: Any) -> None:
            return None

    page = _EvalPage(
        [[[_uploaded_video_record()]], None],
        land=f"https://flow.google.com/project/{PID}/edit/{UPLOADED_MEDIA}",
    )
    out = await mx.run_extend(
        page,  # type: ignore[arg-type]
        project_id=PID,
        source_video=src,
        reference_images=(ref,),
        prompt="continue with blue glow",
        aspect=None,
        poll_timeout_s=30.0,
    )
    # The extend box and its chips, not the main composer's.
    assert mentioned == [("ref-123.png", 1, mx.EXTEND_PROMPT, mx.EXTEND_MENTION_CHIP)]
    assert page.keyboard.typed[-1] == "continue with blue glow"
    assert out.source_media_id == UPLOADED_MEDIA
    assert (
        driver["observe"]["request_check"](
            "fZytfe", _fzytfe_with_refs(wf=UPLOADED_WF, ref_ids=(REF1,))
        )
        is None
    )
    assert (
        driver["observe"]["request_check"]("fZytfe", _fzytfe_with_refs(wf=UPLOADED_WF, ref_ids=()))
        is not None
    )


@pytest.mark.asyncio
async def test_a_plain_extend_never_opens_the_settings_pane(driver: dict[str, Any]) -> None:
    """No --duration and no --max-credits: nothing in the settings popover is needed."""
    out = await _run(_Page(_zzl0ze()))
    assert mx.EXTEND_SETTINGS_TRIGGER not in driver["clicks"]
    assert out.quoted_credits is None


class _DriftingSlider(_FakeSlider):
    """Binds the requested value, then reads back Flow's 4 s once the quote is checked."""

    def __init__(self) -> None:
        super().__init__(value=4)
        self.reads = 0

    async def input_value(self) -> str:
        self.reads += 1
        return str(self.value) if self.reads <= 2 else "4"


async def _extend_with_pane(pane: _FakeSettingsPane, **kw: Any) -> Any:
    class _ClosingKeyboard(_Keyboard):
        async def press(self, key: str) -> None:
            await super().press(key)
            pane.open = pane.open and key != "Escape"

    class _SettingsPage(_Page):
        def __init__(self, listing: Any) -> None:
            super().__init__(listing)
            self.keyboard = _ClosingKeyboard()

        def locator(self, selector: str) -> Any:
            return pane if selector == mx.EXTEND_SETTINGS_PANE else super().locator(selector)

    return await mx.run_extend(
        _SettingsPage(_zzl0ze()),  # type: ignore[arg-type]
        project_id=PID,
        media_id=MEDIA,
        prompt="keep going",
        aspect=None,
        poll_timeout_s=30.0,
        **kw,
    )


@pytest.mark.asyncio
async def test_two_lengths_at_the_same_price_pass_the_cap_once_the_slider_confirms(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The label does not move because the price did not: accepted after the wait."""
    monkeypatch.setattr(mx, "QUOTE_SETTLE_S", 0.0)
    pane = _FakeSettingsPane(_FakeSlider(value=4), "Generating will use 12 credits")
    out = await _extend_with_pane(pane, duration=6, max_credits=12)
    assert out.quoted_credits == 12
    assert driver["observe"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["Generating will use 12 credits", None])
async def test_a_quote_for_a_length_the_slider_no_longer_shows_is_exit_45(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch, label: str | None
) -> None:
    """Unchanged or freshly readable, a label is not trusted for a length that is not set."""
    from gflow_cli.errors import CreditCapExceededError

    monkeypatch.setattr(mx, "QUOTE_SETTLE_S", 0.0)
    pane = _FakeSettingsPane(_DriftingSlider(), label)
    if label is None:  # the label is unreadable before the move, readable after it
        reads = iter([None, "Generating will use 18 credits"])
        monkeypatch.setattr(
            mx,
            "_read_extend_credit_quote",
            lambda _p: _coro(mx.parse_credit_quote(next(reads, "18 credits") or "")),
        )
    with pytest.raises(CreditCapExceededError) as caught:
        await _extend_with_pane(pane, duration=6, max_credits=100)
    assert caught.value.quoted is None
    assert caught.value.charged == "no"
    assert driver["observe"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("max_credits", "code"), [(None, 23), (10, 45)])
async def test_a_settings_trigger_click_timeout_is_typed(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch, max_credits: int | None, code: int
) -> None:
    """A Playwright timeout on the trigger is exit 23, or 45 when only the cap needed it."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    class _DeadTrigger(_Locator):
        async def click(self, **_kw: Any) -> None:
            raise PlaywrightTimeoutError("Timeout 4000ms exceeded")

    async def anchor(_page: Any, selector: str, *, named: str) -> Any:
        if selector == mx.EXTEND_SETTINGS_TRIGGER:
            return _DeadTrigger([], selector)
        return _Locator(driver["clicks"], selector)

    class _PanePage(_Page):
        def locator(self, selector: str) -> Any:
            if selector == mx.EXTEND_SETTINGS_PANE:
                return _FakeSettingsPane(_FakeSlider())
            return super().locator(selector)

    monkeypatch.setattr(mx, "_anchor", anchor)
    with pytest.raises(GFlowError) as caught:
        await mx.run_extend(
            _PanePage(_zzl0ze()),  # type: ignore[arg-type]
            project_id=PID,
            media_id=MEDIA,
            prompt="keep going",
            aspect=None,
            duration=None if max_credits else 5,
            max_credits=max_credits,
            poll_timeout_s=30.0,
        )
    assert exit_code_for(caught.value) == code
    assert caught.value.charged == "no"


@pytest.mark.asyncio
async def test_missing_settings_trigger_with_max_credits_raises_exit_45(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.errors import CreditCapExceededError, UiSelectorDriftError

    orig_anchor = mx._anchor

    async def _anchor_without_settings(page: Any, selector: str, *, named: str) -> Any:
        if selector == mx.EXTEND_SETTINGS_TRIGGER:
            raise UiSelectorDriftError(detail="settings trigger missing")
        return await orig_anchor(page, selector, named=named)

    monkeypatch.setattr(mx, "_anchor", _anchor_without_settings)
    page = _Page(_zzl0ze())
    with pytest.raises(CreditCapExceededError) as caught:
        await mx.run_extend(
            page,  # type: ignore[arg-type]
            project_id=PID,
            media_id=MEDIA,
            prompt="keep going",
            aspect=None,
            max_credits=10,
            poll_timeout_s=30.0,
        )
    assert exit_code_for(caught.value) == 45
    assert caught.value.quoted is None
    assert caught.value.max_credits == 10
    assert caught.value.charged == "no"
    assert driver["observe"] is None


@pytest.mark.asyncio
async def test_submit_attempted_is_reported_after_everything_pre_submit(
    driver: dict[str, Any],
) -> None:
    """The checkpoint marks the billed click: emitted once preparation is over, never sooner."""
    phases: list[tuple[str, bool]] = []

    def checkpoint(cp: Any) -> None:
        phases.append((cp.phase, driver["observe"] is None))

    await mx.run_extend(
        _Page(_zzl0ze()),  # type: ignore[arg-type]
        project_id=PID,
        media_id=MEDIA,
        prompt="keep going",
        aspect=None,
        poll_timeout_s=30.0,
        on_checkpoint=checkpoint,
    )
    assert phases[0] == ("submit_attempted", True)
    assert [p for p, _ in phases] == ["submit_attempted", "remote_started"]


@pytest.mark.asyncio
async def test_a_refusal_before_submit_reports_no_submit_attempt(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mx, "LISTING_BUDGET_S", 0.01)
    phases: list[str] = []
    with pytest.raises(ConfigurationError):
        await mx.run_extend(
            _Page([[[_image_record()]]]),  # type: ignore[arg-type]
            project_id=PID,
            media_id=MEDIA,
            prompt="keep going",
            aspect=None,
            poll_timeout_s=30.0,
            on_checkpoint=lambda cp: phases.append(cp.phase),
        )
    assert phases == []
    assert driver["observe"] is None


@pytest.mark.parametrize(
    "unreadable",
    [["not-a-list"], [[None]], [[None, 7]], [[None, REF1], "junk"]],
    ids=["bare-string", "no-id", "non-string-id", "one-good-one-junk"],
)
def test_an_unreadable_reference_entry_fails_closed(unreadable: list[Any]) -> None:
    """An entry the guard cannot read could be a reference the run never asked for."""
    body = _fzytfe_with_refs(raw_refs=unreadable)
    entry = mx._at(mx._inner("fZytfe", body), 0, 0)  # noqa: SLF001
    assert mx._at(entry, 8) == unreadable  # noqa: SLF001 - the fixture really carries it
    err = mx.extend_request_problem(
        "fZytfe", body, media_id=MEDIA, workflow_id=WF, aspect=None, expected_reference_ids=()
    )
    assert isinstance(err, WireFormatError)
    assert "reference slots" in err.detail


@pytest.mark.asyncio
async def test_an_upload_still_processing_at_the_deadline_is_a_timeout(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Never returned not-ready (it would be extended half-ingested); exit 9, charged no."""
    monkeypatch.setattr(mx, "VIDEO_INGEST_READY_BUDGET_S", 0.0)
    processing = _uploaded_video_record()
    processing[5][8] = [1]

    async def _fake_ensure(_self: Any, _page: Any, _pid: str, **_kw: Any) -> None:
        return None

    async def _fake_upload_video(_page: Any, _pid: str, _path: Any) -> str:
        return UPLOADED_MEDIA

    monkeypatch.setattr(mx.MigratedComposer, "ensure_editor_for", _fake_ensure)
    monkeypatch.setattr(mx, "upload_video", _fake_upload_video)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
    page = _Page(
        [[[processing]], None],
        land=f"https://flow.google.com/project/{PID}/edit/{UPLOADED_MEDIA}",
    )
    with pytest.raises(TransportTimeoutError) as caught:
        await mx.run_extend(
            page,  # type: ignore[arg-type]
            project_id=PID,
            source_video=src,
            prompt="go",
            aspect=None,
            poll_timeout_s=30.0,
        )
    assert exit_code_for(caught.value) == 9
    assert caught.value.charged == "no"
    assert driver["observe"] is None


@pytest.mark.asyncio
async def test_a_disabled_extend_item_after_the_upload_is_ready_is_exit_35(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Listed ready, the clip is no longer processing: #1334's refusal, not exit 9."""
    from gflow_cli.errors import ExtendUnavailableError

    monkeypatch.setattr(mx, "DISABLED_GRACE_S", 0.0)

    async def _never_out_of_credits(_page: Any) -> None:
        return None

    async def _classic(_self: Any, _page: Any, _pid: str, **_kw: Any) -> str:
        return "classic"

    async def _fake_upload_video(_page: Any, _pid: str, _path: Any) -> str:
        return UPLOADED_MEDIA

    monkeypatch.setattr(mx, "_raise_if_out_of_credits", _never_out_of_credits)
    monkeypatch.setattr(mx.MigratedComposer, "ensure_editor_for", _classic)
    monkeypatch.setattr(mx, "upload_video", _fake_upload_video)
    driver["disabled"][mx.EXTEND_MENU_ITEM] = -1
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
    page = _Page(
        [[[_uploaded_video_record()]], None],
        land=f"https://flow.google.com/project/{PID}/edit/{UPLOADED_MEDIA}",
    )
    with pytest.raises(ExtendUnavailableError) as caught:
        await mx.run_extend(
            page,  # type: ignore[arg-type]
            project_id=PID,
            source_video=src,
            prompt="go",
            aspect=None,
            poll_timeout_s=30.0,
        )
    assert exit_code_for(caught.value) == 35
    assert driver["observe"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "data"),
    [("clip.webm", b"\x1aE\xdf\xa3" + b"\x00" * 12), ("notes.txt", b"hello"), ("x.mp4", b"hi")],
    ids=["webm", "text", "bad-magic"],
)
async def test_an_unsupported_local_file_is_exit_11_charged_no(
    tmp_path: Any, name: str, data: bytes
) -> None:
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(ConfigurationError) as caught:
        await mx.validate_extend_inputs(
            media_id=None, source_video=path, duration=None, reference_images=(), max_credits=None
        )
    assert exit_code_for(caught.value) == 11
    assert caught.value.charged == "no"


@pytest.mark.asyncio
async def test_a_missing_reference_file_is_exit_11(tmp_path: Any) -> None:
    with pytest.raises(ConfigurationError):
        await mx.validate_extend_inputs(
            media_id=MEDIA,
            source_video=None,
            duration=None,
            reference_images=(tmp_path / "gone.png",),
            max_credits=None,
        )


def test_the_video_upload_budget_scales_with_the_file_size() -> None:
    from gflow_cli.api.transports._mc_upload_video import video_upload_timeout_s

    small, cap = video_upload_timeout_s(1024 * 1024), video_upload_timeout_s(1024**3)
    assert small == pytest.approx(121.0)
    assert cap == pytest.approx(120.0 + 1024)  # 1 GB at 1 MiB/s: ~19 minutes


def test_the_click_time_bounding_prompt_adoption_is_taken_before_the_billed_click() -> None:
    """``clicked_at`` bounds same-prompt adoption for t2v/i2v: a clip created before the
    click is never adopted. Read after the click, a slow click widens that window."""
    import inspect

    from gflow_cli.api.transports._mc_submit_observe import SubmitObserveMixin

    source = inspect.getsource(SubmitObserveMixin.submit_and_observe)
    assert source.index("clicked_at = time.time()") < source.index("named=SUBMIT_BUTTON")


class _UploadPage:
    """The toolbar uploader's surface: ``scenario`` runs when the file is handed over."""

    URL = "https://aisandbox-pa.googleapis.com/upload/v1/flow/upload/video/abc"

    def __init__(self, scenario: Any) -> None:
        self.scenario, self.dialogs, self.clicks = scenario, 0, []
        self.listeners: dict[str, list[Any]] = {}
        self.files: Any = None

    def on(self, event: str, fn: Any) -> None:
        self.listeners.setdefault(event, []).append(fn)

    def remove_listener(self, event: str, fn: Any) -> None:
        self.listeners[event].remove(fn)

    def locator(self, selector: str) -> Any:
        page = self

        class _Loc:
            first = property(lambda self: self)

            async def count(self) -> int:
                return page.dialogs if "dialog" in selector else 1

            async def click(self, **_kw: Any) -> None:
                page.clicks.append(selector)

            async def wait_for(self, **_kw: Any) -> None:
                return None

        return _Loc()

    def expect_file_chooser(self, **_kw: Any) -> Any:
        page = self

        class _Chooser:
            async def set_files(self, files: Any) -> None:
                page.files = files
                await page.scenario(page)

        class _Info:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *_a: object) -> None:
                return None

            @property
            def value(self) -> Any:
                async def chooser() -> _Chooser:
                    return _Chooser()

                return chooser()

        return _Info()

    async def fire(self, *, status: int | None = None, body: str = "", sent: bool = False) -> None:
        if sent:
            for fn in list(self.listeners.get("request", [])):
                fn(SimpleNamespace(url=self.URL))
        if status is not None:
            resp = SimpleNamespace(url=self.URL, status=status, text=lambda: _coro(body))
            for fn in list(self.listeners.get("response", [])):
                await fn(resp)


async def _coro(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_video_upload_detects_the_rights_dialog_and_never_accepts_it(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flow's one-time "rights to use this video" dialog, with nothing sent, is exit 27
    naming the video remediation; gflow clicks only the toolbar add and upload entries."""
    from gflow_cli.api.transports import _mc_upload_video as uv
    from gflow_cli.errors import MediaUploadRejectedError

    monkeypatch.setattr(uv, "VIDEO_UPLOAD_BASE_S", 0.05)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")

    async def rights_dialog(page: _UploadPage) -> None:
        page.dialogs += 1

    page = _UploadPage(rights_dialog)
    with pytest.raises(MediaUploadRejectedError, match="upload-terms") as caught:
        await uv.upload_video(page, PID, clip)  # type: ignore[arg-type]
    assert "upload any video by hand" in (caught.value.remediation_hint or "")
    assert len(page.clicks) == 2  # toolbar add, then its upload entry: no dialog button
    assert str(page.files).endswith(".mp4")  # a staged path, never the bytes in memory


@pytest.mark.asyncio
async def test_video_upload_waits_past_the_empty_first_reply(tmp_path: Any) -> None:
    from gflow_cli.api.transports import _mc_upload_video as uv

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")

    async def resumable(page: _UploadPage) -> None:
        await page.fire(sent=True, status=200, body="")  # the resumable start: no JSON yet
        reply = {"mediaId": UPLOADED_WF, "media": {"workflowId": UPLOADED_MEDIA}}
        await page.fire(status=200, body=json.dumps(reply))

    assert await uv.upload_video(_UploadPage(resumable), PID, clip) == UPLOADED_MEDIA  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_non_200_video_upload_is_exit_27_naming_the_status(tmp_path: Any) -> None:
    from gflow_cli.api.transports import _mc_upload_video as uv
    from gflow_cli.errors import MediaUploadRejectedError

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")

    async def refused(page: _UploadPage) -> None:
        await page.fire(sent=True, status=413)

    with pytest.raises(MediaUploadRejectedError) as caught:
        await uv.upload_video(_UploadPage(refused), PID, clip)  # type: ignore[arg-type]
    assert caught.value.detail == "migrated host: Flow video upload answered HTTP 413"
    assert caught.value.status == 413
    assert caught.value.route == "flow:upload/video"
    assert exit_code_for(caught.value) == 27


@pytest.mark.asyncio
async def test_a_submit_the_page_sends_during_preparation_is_aborted(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Enter that lands with no picker open submits the extend before the references,
    the length and the cap are in place. That ``fZytfe`` is aborted, never released, and
    the run stops before gflow's own click, charged nothing."""
    seen: list[str] = []
    real_prompt = mx._prepare_extend_prompt  # noqa: SLF001

    async def stray_enter(composer: Any, page: Any, **kw: Any) -> None:
        await real_prompt(composer, page, **kw)
        route = _Route()
        await page.routes[-1](route, _Request("fZytfe", _fzytfe()))
        seen.extend(route.calls)

    monkeypatch.setattr(mx, "_prepare_extend_prompt", stray_enter)
    page = _Page(_zzl0ze())
    with pytest.raises(WireFormatError) as caught:
        await _run(page)
    assert seen == ["abort"]
    assert caught.value.charged == "no"
    assert driver["observe"] is None
    assert page.routes == []


@pytest.mark.asyncio
async def test_a_fresh_upload_listed_without_its_metadata_is_waited_for(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A brand-new upload is listed before its size and length: keep reading, never exit 11."""
    monkeypatch.setattr(mx, "_VIDEO_INGEST_POLL_S", 0.0)
    bare = _uploaded_video_record()
    bare[7] = [None, None]
    reads = iter([[[[bare]], None], [[[_uploaded_video_record()]], None]])

    async def _classic(_self: Any, _page: Any, _pid: str, **_kw: Any) -> str:
        return "classic"

    async def _fake_upload_video(_page: Any, _pid: str, _path: Any) -> str:
        return UPLOADED_MEDIA

    async def _listing(_page: Any, **_kw: Any) -> Any:
        return mx.source_record(next(reads), UPLOADED_MEDIA)

    monkeypatch.setattr(mx.MigratedComposer, "ensure_editor_for", _classic)
    monkeypatch.setattr(mx, "upload_video", _fake_upload_video)
    monkeypatch.setattr(mx, "_source_clip", _listing)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
    page = _Page(None, land=f"https://flow.google.com/project/{PID}/edit/{UPLOADED_MEDIA}")
    page.url = page._land  # noqa: SLF001 - the editor is already open
    out = await mx.run_extend(
        page,  # type: ignore[arg-type]
        project_id=PID,
        source_video=src,
        prompt="go",
        aspect=None,
        poll_timeout_s=30.0,
    )
    assert out.source_media_id == UPLOADED_MEDIA


@pytest.mark.asyncio
async def test_the_agent_only_composer_is_the_unported_refusal_not_drift(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    async def _agent_only(_self: Any, _page: Any, _pid: str, **_kw: Any) -> str:
        return "agent_only"

    monkeypatch.setattr(mx.MigratedComposer, "ensure_editor_for", _agent_only)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
    with pytest.raises(FlowHostMigratedError) as caught:
        await mx.run_extend(
            _Page(None),  # type: ignore[arg-type]
            project_id=PID,
            source_video=src,
            prompt="go",
            aspect=None,
            poll_timeout_s=30.0,
        )
    assert exit_code_for(caught.value) == 36
    assert caught.value.charged == "no"


def test_the_upload_timeout_names_the_uploaded_clip_to_extend_later() -> None:
    err = mx._ingest_timeout("x", media_id=UPLOADED_MEDIA, project_id=PID)  # noqa: SLF001
    assert UPLOADED_MEDIA in err.detail and UPLOADED_MEDIA in (err.remediation_hint or "")
    assert "gflow data list videos" not in (err.remediation_hint or "")


@pytest.mark.asyncio
async def test_a_folder_or_unreadable_path_is_exit_11(tmp_path: Any) -> None:
    folder = tmp_path / "clip.mp4"
    folder.mkdir()
    with pytest.raises(ConfigurationError):
        await mx.validate_extend_inputs(
            media_id=None, source_video=folder, duration=None, reference_images=(), max_credits=None
        )


@pytest.mark.asyncio
async def test_a_video_reply_without_ids_is_exit_27_with_status_200(tmp_path: Any) -> None:
    from gflow_cli.api.transports import _mc_upload_video as uv
    from gflow_cli.errors import MediaUploadRejectedError

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")

    async def incomplete(page: _UploadPage) -> None:
        await page.fire(sent=True, status=200, body="{}")

    with pytest.raises(MediaUploadRejectedError) as caught:
        await uv.upload_video(_UploadPage(incomplete), PID, clip)  # type: ignore[arg-type]
    assert caught.value.detail == (
        "migrated host: Flow video upload answered 200 without mediaId/workflowId"
    )
    assert caught.value.status == 200
    assert caught.value.route == "flow:upload/video"


@pytest.mark.asyncio
async def test_every_expectation_field_reaches_both_submit_checks(
    driver: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The observer's check and the route guard see the same, complete expectation."""

    async def prepare(*_args: Any, **_kwargs: Any) -> tuple[str, str, tuple[str, ...], int]:
        return UPLOADED_MEDIA, UPLOADED_WF, (REF1, REF2), 12

    def submit_body(**changes: Any) -> str:
        fields = {
            "wf": UPLOADED_WF,
            "aspect_idx": 2,
            "key": "macaron_cookie_extend_6s",
            "ref_ids": (REF1, REF2),
        }
        fields.update(changes)
        return _fzytfe_with_refs(**fields)

    async def observe(
        _composer: Any, page: Any, *, request_check: Any, **_kw: Any
    ) -> GenerationRecord:
        cases = [
            ("wrong clip", "rqZuUc", _rqzuuc(media=MEDIA), False),
            ("uploaded clip", "rqZuUc", _rqzuuc(media=UPLOADED_MEDIA), True),
            ("wrong workflow", "fZytfe", submit_body(wf=OTHER_WF), False),
            ("wrong aspect", "fZytfe", submit_body(aspect_idx=1), False),
            ("wrong seconds", "fZytfe", submit_body(key="macaron_cookie_extend_5s"), False),
            ("missing references", "fZytfe", submit_body(ref_ids=()), False),
            ("all fields match", "fZytfe", submit_body(), True),
        ]
        for name, rpcid, body, allowed in cases:
            assert (request_check(rpcid, body) is None) == allowed, name
            route = _Route()
            await page.routes[0](route, _Request(rpcid, body))
            assert route.calls == (["fallback"] if allowed else ["abort"]), name
        return _done_record()

    monkeypatch.setattr(mx, "_prepare_clip_extend", prepare)
    monkeypatch.setattr(mx.MigratedComposer, "submit_and_observe", observe)
    await mx.run_extend(
        _Page(_zzl0ze()),  # type: ignore[arg-type]
        project_id=PID,
        source_video=tmp_path / "source.mp4",
        reference_images=(tmp_path / "one.png", tmp_path / "two.png"),
        prompt="keep going",
        aspect="16:9",
        duration=6,
        poll_timeout_s=30.0,
    )
