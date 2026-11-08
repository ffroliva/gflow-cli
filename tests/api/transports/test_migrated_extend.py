"""Unit tests for the flow.google.com extend driver (design B, #882 takeover).

Shapes are the ones measured in docs/superpowers/spikes/2026-10-17-migrated-extend-ui-path.md:
the passive ``Zzl0ze`` record (round 2, B3) and the page's own ``rqZuUc`` / ``fZytfe``
request bodies (round 1, A2/A3).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gflow_cli.api.transports import migrated_extend as mx
from gflow_cli.api.transports.batchexecute import GenerationRecord
from gflow_cli.api.video import VideoStarted
from gflow_cli.errors import (
    ConfigurationError,
    FlowAppError,
    FlowHostMigratedError,
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


class _Locator:
    def __init__(self, clicks: list[str], name: str) -> None:
        self._clicks, self._name = clicks, name

    async def click(self) -> None:
        self._clicks.append(self._name)


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
    seen: dict[str, Any] = {"clicks": [], "observe": None, "workflow": NEW_WF}

    async def anchor(_page: Any, selector: str, *, named: str) -> _Locator:
        return _Locator(seen["clicks"], selector)

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
    with pytest.raises(TransportTimeoutError, match="Zzl0ze"):
        await _run(page)
    assert driver["clicks"] == []
    assert page.listeners["response"] == []


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
    assert page.routes == [] and page.unrouted == 1
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


async def _guard_calls(monkeypatch: pytest.MonkeyPatch, *requests: Any) -> list[str]:
    calls: list[str] = []

    async def observe(_self: Any, page: Any, **_kwargs: Any) -> GenerationRecord:
        for request in requests:
            route = _Route()
            await page.routes[0](route, request)
            calls.append(route.calls[0])
        return _done_record()

    monkeypatch.setattr(mx.MigratedComposer, "submit_and_observe", observe)
    await _run(_Page(_zzl0ze()))
    return calls


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
