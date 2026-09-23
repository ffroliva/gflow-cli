"""Recovering a stranded clip by media id (#865, #871).

The measured wire behaviour these tests stand in for lives in
``docs/superpowers/spikes/2026-09-17-stranded-clip-recovery.md``: the per-clip route
provokes ``as29s``, whose reply carries a signed ``flow-content.google`` URL, and the
bytes are checked against the size Flow reports for that clip.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.transports.migrated_recover import (
    MIGRATED_CLIP_URL,
    RecoveredClip,
    _verify,
    recover_clip,
)
from gflow_cli.exceptions import WireFormatError

MEDIA_ID = "23620c34-f2ec-419f-8819-3e60ff5b5767"
WORKFLOW_ID = "d8e72026-e629-4259-8d2b-bed42386d7d1"
PROJECT_ID = "339f65ee-fec3-436e-bdd1-fa2acdbf4afd"
SIGNED = (
    f"https://flow-content.google/video/{WORKFLOW_ID}"
    "?Expires=1789700000&KeyName=labs-flow-prod-cdn-key&Signature=redacted"
)

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 92  # 100 bytes, valid magic

#: The XSSI guard every batchexecute body starts with.
_XSSI = ")]}'\n\n"


def _as29s_frame(*, media_id: str = MEDIA_ID, url: str = SIGNED, size: int = len(MP4)) -> str:
    """One `batchexecute` envelope shaped like the status reply the app receives.

    Slots mirror `generation_record`: [0] workflow, [1] project, [2] media, 'CAE' at [3],
    status at [5][8][0], size at [5][13], signed url at [7][0][8].
    """
    details: list[Any] = [None] * 14
    details[8] = [3]
    details[13] = size
    generation = [None] * 9
    generation[8] = url
    record = [WORKFLOW_ID, PROJECT_ID, media_id, "CAE", None, details, None, [generation]]
    payload = json.dumps([["wrb.fr", "as29s", json.dumps(record)]])
    return _XSSI + str(len(payload)) + "\n" + payload


class _Response:
    def __init__(self, url: str, body: bytes, status: int = 200) -> None:
        self.url = url
        self.status = status
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _Frame:
    def __init__(self, text: str) -> None:
        self.url = "https://flow.google.com/_/FlowUi/data/batchexecute?rpcids=as29s"
        self._text = text

    async def text(self) -> str:
        return self._text


class _Other:
    """A non-`batchexecute` response — the listener must ignore it outright."""

    def __init__(self, url: str) -> None:
        self.url = url

    async def text(self) -> str:  # pragma: no cover - must never be called
        raise AssertionError("non-batchexecute traffic was read")


class _Unreadable:
    """A `batchexecute` response whose body cannot be read."""

    url = "https://flow.google.com/_/FlowUi/data/batchexecute?rpcids=as29s"

    async def text(self) -> str:
        raise RuntimeError("body aborted")


class _RequestApi:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def get(self, url: str, **_: Any) -> _Response:
        self._page.fetched.append(url)
        return _Response(url, self._page.body, self._page.http_status)


class FakePage:
    """Replays scripted `batchexecute` frames once the clip route is opened."""

    def __init__(
        self,
        *,
        frames: list[str] | None = None,
        body: bytes = MP4,
        http_status: int = 200,
    ) -> None:
        self.frames = frames if frames is not None else [_as29s_frame()]
        self.body = body
        self.http_status = http_status
        self.gotos: list[str] = []
        self.fetched: list[str] = []
        self._handlers: list[Any] = []
        self.request = _RequestApi(self)

    def on(self, event: str, handler: Any) -> None:
        if event == "response":
            self._handlers.append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        if event == "response" and handler in self._handlers:
            self._handlers.remove(handler)

    async def goto(self, url: str, **_: Any) -> None:
        self.gotos.append(url)
        for text in self.frames:
            for handler in list(self._handlers):
                await handler(_Frame(text))

    async def wait_for_timeout(self, _ms: float) -> None:
        await asyncio.sleep(0)


async def _recover(page: FakePage, tmp_path: Path, **kw: Any) -> RecoveredClip:
    # The wait is a real-clock budget, so tests must cap it or a failing case burns 45s.
    kw.setdefault("wait_s", 1.0)
    return await recover_clip(
        page,  # pyright: ignore[reportArgumentType] - structural fake
        project_id=PROJECT_ID,
        media_id=MEDIA_ID,
        out_dir=tmp_path,
        **kw,
    )


class TestVerify:
    def test_accepts_mp4_matching_the_recorded_size(self) -> None:
        _verify(MP4, expected=len(MP4), media_id=MEDIA_ID)

    def test_rejects_a_body_without_ftyp_magic(self) -> None:
        with pytest.raises(WireFormatError, match="did not return an MP4"):
            _verify(b"<!DOCTYPE html><html>", expected=None, media_id=MEDIA_ID)

    def test_rejects_a_valid_mp4_of_the_wrong_size(self) -> None:
        """The transcode trap: Flow also serves 360p/720p re-encodes of this clip.

        They carry correct `ftyp` magic and are NOT the asset the user paid for, so a
        magic-byte check alone would save the wrong file (the #281 failure class).
        """
        with pytest.raises(WireFormatError, match="lower-resolution transcode"):
            _verify(MP4, expected=2_410_295, media_id=MEDIA_ID)

    def test_allows_an_unknown_size(self) -> None:
        """A record without `size_bytes` still downloads — magic bytes are the floor."""
        _verify(MP4, expected=None, media_id=MEDIA_ID)


class TestRecoverClip:
    @pytest.mark.asyncio
    async def test_writes_the_clip_from_the_signed_url(self, tmp_path: Path) -> None:
        page = FakePage()
        clip = await _recover(page, tmp_path)

        assert clip.media_id == MEDIA_ID
        assert clip.workflow_id == WORKFLOW_ID
        assert clip.path == tmp_path / f"{MEDIA_ID}.mp4"
        assert clip.path.read_bytes() == MP4
        assert clip.bytes == len(MP4)

    @pytest.mark.asyncio
    async def test_opens_the_per_clip_route(self, tmp_path: Path) -> None:
        """No DOM is involved: both ids come from the catalog and build the URL."""
        page = FakePage()
        await _recover(page, tmp_path)

        assert page.gotos == [MIGRATED_CLIP_URL.format(project_id=PROJECT_ID, media_id=MEDIA_ID)]

    @pytest.mark.asyncio
    async def test_ignores_records_for_other_media_ids(self, tmp_path: Path) -> None:
        """A project load reports many clips; only this media id may be downloaded."""
        page = FakePage(
            frames=[
                _as29s_frame(
                    media_id="00000000-0000-0000-0000-000000000000", url="https://x/other"
                ),
                _as29s_frame(),
            ]
        )
        clip = await _recover(page, tmp_path)

        assert page.fetched == [SIGNED]
        assert clip.media_id == MEDIA_ID

    @pytest.mark.asyncio
    async def test_raises_when_no_signed_url_arrives(self, tmp_path: Path) -> None:
        page = FakePage(frames=[])
        with pytest.raises(WireFormatError, match="no signed media URL"):
            await _recover(page, tmp_path, wait_s=0.05)

    @pytest.mark.asyncio
    async def test_refuses_a_non_google_host(self, tmp_path: Path) -> None:
        """A redirected or spoofed URL must not be fetched, even from a real reply."""
        page = FakePage(frames=[_as29s_frame(url="https://evil.example.com/video/x")])
        with pytest.raises(WireFormatError, match="not an allowed Google host"):
            await _recover(page, tmp_path)

        assert page.fetched == []

    @pytest.mark.asyncio
    async def test_raises_on_an_http_error_from_the_cdn(self, tmp_path: Path) -> None:
        page = FakePage(http_status=403)
        with pytest.raises(WireFormatError, match="HTTP 403"):
            await _recover(page, tmp_path)

    @pytest.mark.asyncio
    async def test_writes_nothing_when_the_bytes_are_wrong(self, tmp_path: Path) -> None:
        """A failed verification must not leave a partial file behind."""
        page = FakePage(frames=[_as29s_frame(size=999_999)])
        with pytest.raises(WireFormatError):
            await _recover(page, tmp_path)

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_ignores_traffic_that_is_not_batchexecute(self, tmp_path: Path) -> None:
        """A project load pulls fonts, images and telemetry; none of it is a record."""

        class _Noise(FakePage):
            async def goto(self, url: str, **_: Any) -> None:
                self.gotos.append(url)
                for handler in list(self._handlers):
                    await handler(_Other("https://fonts.gstatic.com/s/x.woff2"))
                for text in self.frames:
                    for handler in list(self._handlers):
                        await handler(_Frame(text))

        clip = await _recover(_Noise(), tmp_path)
        assert clip.media_id == MEDIA_ID

    @pytest.mark.asyncio
    async def test_survives_a_body_that_cannot_be_read(self, tmp_path: Path) -> None:
        """An aborted or streamed body raises on `.text()` — that is not our frame."""

        class _Aborted(FakePage):
            async def goto(self, url: str, **_: Any) -> None:
                self.gotos.append(url)
                for handler in list(self._handlers):
                    await handler(_Unreadable())
                for text in self.frames:
                    for handler in list(self._handlers):
                        await handler(_Frame(text))

        clip = await _recover(_Aborted(), tmp_path)
        assert clip.media_id == MEDIA_ID

    @pytest.mark.asyncio
    async def test_skips_frames_that_are_not_generation_records(self, tmp_path: Path) -> None:
        """Most frames on a project load decode to something else entirely."""
        other = _XSSI + json.dumps([["wrb.fr", "Yizz8d", json.dumps({"unrelated": 1})]])
        clip = await _recover(FakePage(frames=[other, _as29s_frame()]), tmp_path)

        assert clip.media_id == MEDIA_ID

    @pytest.mark.asyncio
    async def test_removes_its_listener(self, tmp_path: Path) -> None:
        page = FakePage()
        await _recover(page, tmp_path)

        assert page._handlers == []  # noqa: SLF001 - asserting cleanup
