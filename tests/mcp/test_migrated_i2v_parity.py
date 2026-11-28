"""The MCP queued path and the migrated-host routing agree on i2v (#639, slice 1).

`gflow_generate_video(mode="i2v", initial_frame=…)` writes a queue payload; the
worker decodes it with `worker/codec.py` and the transport asks
`migrated_can_serve` where to run it. No tool signature changed for the port, so
the only thing to pin is that the payload the tool writes decodes to a request the
routing gate answers the same way the CLI's request is answered: a local file is
served by flow.google.com, a media UUID is not (it keeps the labs driver on an
unmoved account and exits 36-equivalent on a moved one).
"""

from __future__ import annotations

from pathlib import Path

from gflow_cli.api.transports.migrated_composer import migrated_can_serve
from gflow_cli.mcp.tools import (
    _build_video_media_inputs,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.worker.codec import build_video_request

_UUID = "33333333-3333-4333-8333-333333333333"


def _payload(media: dict[str, object]) -> dict[str, object]:
    return {"prompt": "a crane", "mode": "i2v", "aspect": "16:9", **media}


def test_a_local_initial_frame_from_mcp_is_served_by_the_migrated_host(tmp_path: Path) -> None:
    png = tmp_path / "hero.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    media, err = _build_video_media_inputs(
        mode="i2v", initial_frame=str(png), end_frame=None, reference_images=None
    )
    assert err is None and media is not None
    request = build_video_request(_payload(media))
    assert request.start_image == png.resolve() or request.start_image == png
    assert migrated_can_serve(request, "p1") is True


def test_a_uuid_initial_frame_from_mcp_keeps_the_labs_routing() -> None:
    media, err = _build_video_media_inputs(
        mode="i2v", initial_frame=_UUID, end_frame=None, reference_images=None
    )
    assert err is None and media is not None
    request = build_video_request(_payload(media))
    assert request.start_image_ref_id == _UUID
    assert migrated_can_serve(request, "p1") is False


def test_a_local_end_frame_from_mcp_is_served_by_the_migrated_host(tmp_path: Path) -> None:
    png = tmp_path / "hero.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    media, err = _build_video_media_inputs(
        mode="i2v", initial_frame=str(png), end_frame=str(png), reference_images=None
    )
    assert err is None and media is not None
    request = build_video_request(_payload(media))
    assert request.end_image == png.resolve() or request.end_image == png
    assert migrated_can_serve(request, "p1") is True


def test_in_project_captioned_uuid_frames_from_mcp_are_served_by_the_migrated_host() -> None:
    """#1542: MCP enqueue -> _resolve_payload_refs -> worker codec -> migrated_can_serve."""
    from types import SimpleNamespace

    from gflow_cli.data.models import AssetKind
    from gflow_cli.mcp.tools import _resolve_payload_refs  # pyright: ignore[reportPrivateUsage]

    class _FakeRepo:
        def get_asset_by_flow_media_id(self, _profile: str, _ref_id: str) -> object:
            return SimpleNamespace(
                kind=AssetKind.IMAGE,
                flow_project_id="p1",
                metadata_json={"display_name": "Stone corridor"},
                local_files=[],
            )

    media, err = _build_video_media_inputs(
        mode="i2v", initial_frame=_UUID, end_frame=_UUID, reference_images=None
    )
    assert err is None and media is not None
    same_proj = {**_payload(media), "project_id": "p1"}
    assert _resolve_payload_refs(_FakeRepo(), "default", same_proj, "i2v") is None  # type: ignore[arg-type]
    req_same = build_video_request(same_proj)
    assert req_same.start_image_ref_in_project is True
    assert req_same.end_image_ref_in_project is True
    assert migrated_can_serve(req_same, "p1") is True

    other_proj = {**_payload(media), "project_id": "p2"}
    assert _resolve_payload_refs(_FakeRepo(), "default", other_proj, "i2v") is None  # type: ignore[arg-type]
    req_other = build_video_request(other_proj)
    assert req_other.start_image_ref_in_project is False
    assert migrated_can_serve(req_other, "p2") is False
