"""E2E: ``gflow video extend`` on flow.google.com (design B, #882 takeover).

Three $0 ``e2e_auth`` tests drive the PRODUCTION driver (``migrated_extend.run_extend``)
against a live page and keep every ``fZytfe`` from reaching Flow:

* the submit contract — any ``rqZuUc`` carries the media id (since 2026-11-08 the page
  on our account sends none before ``fZytfe``, #1180) and ``fZytfe`` slot 1 the
  workflow id the passive ``Zzl0ze`` read maps it to, for an extended clip the newest one
  (#1346) (the release hook is replaced so the
  checked ``fZytfe`` is aborted instead of let through);
* the refusal matrix on a live page — a media id that is not in the project exits 11
  before any click, and an ``--aspect`` the source does not have is aborted at the
  production route guard (exit 36);
* on an account where Flow renders the Extend menu item disabled (declare it with
  ``GFLOW_CLI_E2E_EXTEND_DISABLED=1``), the run ends in a typed refusal before any click
  on that item instead of a 30 s click timeout (#1334). The tests that need an enabled
  item skip on such a profile.

Two more $0 ``e2e_auth`` tests cover the local-upload arm (#1468): ``--max-credits 0``
through the CLI exits 45 with ``charged: no`` and no submit click, and a local clip with
``--duration 5`` and one ``--ref`` must reach the checked ``fZytfe`` as the 5 s key, the
uploaded ref id in ``entry[8]`` and the uploaded clip's workflow (the upload itself is free
and stays in the project). Its paid twin is an ``e2e_video`` test.

Belt and braces: a context-level route aborts ``fZytfe`` for the whole test, so even a
fall-through in the driver cannot bill.

**Side effect:** the contract and aspect tests let ``rqZuUc`` through (it is free), so
a run that sends one leaves an empty "Untitled Scene" in the project. gflow has no scene
delete; remove them from Flow's Scenes tab if wanted.

The ``e2e_video`` tests run one real extend, then two in a row on the same clip (#1346:
the second continues the workflow the first created), and spend Veo credits — opt in with
``GFLOW_CLI_E2E_RUN_VIDEO=1`` and only with the account owner's consent.

Env: ``GFLOW_CLI_E2E_PROFILE`` (profile served flow.google.com), ``GFLOW_CLI_E2E_PROJECT``,
``GFLOW_CLI_E2E_EXTEND_MEDIA`` (a finished video in that project) and
``GFLOW_CLI_E2E_EXTEND_ASPECT`` (that clip's aspect, default ``16:9``).
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import uuid
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.transports import migrated_composer
from gflow_cli.api.transports import migrated_extend as mx
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import reset_settings
from gflow_cli.errors import (
    ConfigurationError,
    ExtendUnavailableError,
    FlowHostMigratedError,
    InsufficientCreditsError,
    TransportTimeoutError,
)

pytestmark = pytest.mark.e2e

_PROMPT = "the camera keeps drifting forward, same light"
#: The page sends fZytfe only after rqZuUc, uwAyfb and its own reCAPTCHA mint; a 15 s
#: wait missed it once (2026-10-17). The observer caps its submit wait at 60 s anyway.
_SUBMIT_WAIT_S = 60.0


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, "").strip() or default
    if not value:
        pytest.skip(f"{name} must be set (see module doc)")
    return value


def _ids() -> tuple[str, str]:
    return _env("GFLOW_CLI_E2E_PROJECT"), _env("GFLOW_CLI_E2E_EXTEND_MEDIA")


def _extend_disabled() -> bool:
    return os.environ.get("GFLOW_CLI_E2E_EXTEND_DISABLED") == "1"


def _need_enabled_extend() -> None:
    if _extend_disabled():
        pytest.skip("Flow disables Extend on this profile (GFLOW_CLI_E2E_EXTEND_DISABLED=1)")


async def _block_fzytfe(route: Any, request: Any) -> None:
    """Context-level: whatever the driver does, ``fZytfe`` never leaves the browser."""
    body = migrated_composer.request_body(request)
    rpcid = migrated_composer.url_rpcid(str(request.url)) or migrated_composer.body_rpcid(body)
    if rpcid == mx.EXTEND_SUBMIT_RPC:
        await route.abort()
        return
    await route.fallback()


async def _live_page(profile: Path) -> tuple[UiAutomationTransport, Any]:
    transport = UiAutomationTransport()
    await transport.setup(profile)
    page = transport._page  # noqa: SLF001 - the e2e reads the live page
    assert page is not None
    await page.context.route("**/batchexecute*", _block_fzytfe)
    return transport, page


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_extend_submit_contract_at_zero_cost(
    e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _need_enabled_extend()
    project, media = _ids()
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    seen: dict[str, Any] = {"rqZuUc": [], "fZytfe": [], "workflow": None}

    real_require = mx.require_video_source

    def spy_require(clip: Any, **kw: Any) -> str:
        seen["workflow"] = real_require(clip, **kw)
        return seen["workflow"]

    async def capture_release(route: Any, rpcid: str | None) -> None:
        if rpcid == mx.EXTEND_SUBMIT_RPC:
            body = migrated_composer.request_body(route.request)
            entry = mx._at(mx._inner(rpcid, body), 0, 0)  # noqa: SLF001
            seen["fZytfe"].append({"slot1": mx._at(entry, 0, 1), "facts": mx.submit_facts(body)})  # noqa: SLF001
            await route.abort()  # the checked submit is kept at $0 too
            return
        if rpcid == mx.SCENE_CREATE_RPC:
            body = migrated_composer.request_body(route.request)
            seen["rqZuUc"].append(media in body)
        await route.fallback()

    monkeypatch.setattr(mx, "require_video_source", spy_require)
    monkeypatch.setattr(mx, "_release", capture_release)
    transport, page = await _live_page(e2e_profile_dir)
    try:
        with pytest.raises(TransportTimeoutError):
            await mx.run_extend(
                page,
                project_id=project,
                media_id=media,
                prompt=_PROMPT,
                aspect=None,
                poll_timeout_s=_SUBMIT_WAIT_S,
            )
    finally:
        await transport.teardown()

    # #1180: the scene create is no longer sent on our account; when it is, it names the clip.
    assert all(seen["rqZuUc"]) and len(seen["rqZuUc"]) <= 1, seen
    assert len(seen["fZytfe"]) == 1, seen
    hit = seen["fZytfe"][0]
    assert hit["slot1"] == seen["workflow"], hit
    model_key, aspect = hit["facts"]
    # veo_3_1_extension_* (labs-era) or *_extend_* (Omni, #1180)
    assert model_key and ("extension" in model_key or "_extend_" in model_key), hit
    assert aspect in ("9:16", "16:9"), hit


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_a_media_not_in_the_project_is_refused_before_any_click(
    e2e_profile_dir: Path,
) -> None:
    project, _ = _ids()
    sent: list[str] = []
    transport, page = await _live_page(e2e_profile_dir)

    def on_request(request: Any) -> None:
        rpcid = migrated_composer.url_rpcid(str(request.url))
        if rpcid in (mx.SCENE_CREATE_RPC, mx.EXTEND_SUBMIT_RPC):
            sent.append(rpcid)

    page.on("request", on_request)
    try:
        with pytest.raises(ConfigurationError, match="--project"):
            await mx.run_extend(
                page,
                project_id=project,
                media_id=str(uuid.uuid4()),
                prompt=_PROMPT,
                aspect=None,
                poll_timeout_s=_SUBMIT_WAIT_S,
            )
    finally:
        await transport.teardown()
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_a_foreign_aspect_is_aborted_by_the_production_guard(
    e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _need_enabled_extend()
    project, media = _ids()
    source = _env("GFLOW_CLI_E2E_EXTEND_ASPECT", "16:9")
    foreign = "9:16" if source == "16:9" else "16:9"
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    transport, page = await _live_page(e2e_profile_dir)
    try:
        with pytest.raises(FlowHostMigratedError, match=foreign):
            await mx.run_extend(
                page,
                project_id=project,
                media_id=media,
                prompt=_PROMPT,
                aspect=foreign,
                poll_timeout_s=_SUBMIT_WAIT_S,
            )
    finally:
        await transport.teardown()


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_a_disabled_extend_item_is_a_typed_refusal_not_a_timeout(
    e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1334: on a profile where Flow disables Extend, exit 35 (or 37), nothing sent."""
    if not _extend_disabled():
        pytest.skip("needs a profile where Flow disables Extend (GFLOW_CLI_E2E_EXTEND_DISABLED=1)")
    project, media = _ids()
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    sent: list[str] = []
    transport, page = await _live_page(e2e_profile_dir)

    def on_request(request: Any) -> None:
        body = migrated_composer.request_body(request)
        rpcids = mx.batch_rpcids(mx.url_rpcids(str(request.url)), body)
        sent.extend(r for r in rpcids if r in (mx.SCENE_CREATE_RPC, mx.EXTEND_SUBMIT_RPC))

    page.on("request", on_request)
    try:
        with pytest.raises((ExtendUnavailableError, InsufficientCreditsError)) as info:
            await mx.run_extend(
                page,
                project_id=project,
                media_id=media,
                prompt=_PROMPT,
                aspect=None,
                poll_timeout_s=_SUBMIT_WAIT_S,
            )
    finally:
        await transport.teardown()
    assert info.value.charged == "no"
    assert sent == []


def _mp4_seconds(data: bytes) -> float:
    """Duration from the ``mvhd`` box (version 0 or 1)."""
    at = data.find(b"mvhd")
    assert at > 0, "no mvhd box"
    version = data[at + 4]
    if version == 1:
        scale, duration = struct.unpack(">IQ", data[at + 24 : at + 36])
    else:
        scale, duration = struct.unpack(">II", data[at + 16 : at + 24])
    return duration / scale


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_e2e_one_paid_extend_downloads_an_mp4(
    e2e_profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPENDS VEO CREDITS. One segment, end to end, through the client entry the CLI uses."""
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO") != "1":
        pytest.skip("spends Veo credits — set GFLOW_CLI_E2E_RUN_VIDEO=1 (owner consent)")
    _need_enabled_extend()
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.transports.migrated_extend import extend_key_seconds
    from gflow_cli.api.transports.migrated_recover import read_clip

    project, media = _ids()
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    # Read the source through Flow itself: the per-test catalog is empty, so a catalog
    # lookup (download_media) would raise before the extend.
    transport, page = await _live_page(e2e_profile_dir)
    try:
        _wf, source = await read_clip(page, project_id=project, media_id=media)
    finally:
        await transport.teardown()
    source_seconds = _mp4_seconds(source)
    started: list[str] = []
    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        outcome, target = await client.extend_video_migrated(
            media_id=media,
            project_id=project,
            prompt=_PROMPT,
            aspect=None,
            out_path=tmp_path / "extend.mp4",
            on_started=lambda s, _a: started.append(s.media_id),
        )
    assert started == [outcome.record.media_id]
    assert target is not None
    data = Path(str(target)).read_bytes()
    assert data[4:8] == b"ftyp"
    seconds = _mp4_seconds(data)
    if outcome.record.media_id == media:
        # #1184, measured 2026-11-08 with macaron_cookie_extend_4s: the extend grows the
        # source in place, so the same id now holds the source plus the extension.
        added = extend_key_seconds(outcome.model_key)
        grown = source_seconds + (added or 0) - 0.5  # a key without a length token: just longer
        assert seconds >= grown and seconds > source_seconds, (
            outcome.model_key,
            source_seconds,
            seconds,
        )
    else:
        assert 6.5 <= seconds <= 8.5  # a separate Veo segment: Flow bills 8 s, ~7 s content


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_e2e_a_clip_extended_once_extends_again(
    e2e_profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPENDS VEO CREDITS (two extends). #1346: the second extend of the same clip is billed
    and downloaded, not aborted by the route guard as a request for another workflow."""
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO") != "1":
        pytest.skip("spends Veo credits — set GFLOW_CLI_E2E_RUN_VIDEO=1 (owner consent)")
    _need_enabled_extend()
    from gflow_cli.api.client import FlowApiClient

    project, media = _ids()
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    seconds: list[float] = []
    workflows: list[str] = []
    expected: list[str] = []  # the workflow the route guard expects, per run
    real_require = mx.require_video_source

    def spy_require(clip: Any, **kw: Any) -> str:
        expected.append(real_require(clip, **kw))
        return expected[-1]

    monkeypatch.setattr(mx, "require_video_source", spy_require)
    for run in (1, 2):
        async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
            outcome, target = await client.extend_video_migrated(
                media_id=media,
                project_id=project,
                prompt=_PROMPT,
                aspect=None,
                out_path=tmp_path / f"extend-{run}.mp4",
                on_started=lambda s, _a: workflows.append(s.workflow_id),
            )
        assert outcome.record.is_done and target is not None, run
        if outcome.record.media_id != media:
            pytest.skip("Flow made a separate clip, not an in-place extend (#1184): not #1346")
        seconds.append(_mp4_seconds(Path(str(target)).read_bytes()))
    assert len(workflows) == 2 and workflows[0] != workflows[1], workflows
    # The second run continued the workflow the first one created, and its file is longer.
    assert expected[1] == workflows[0], (expected, workflows)
    assert seconds[1] > seconds[0], seconds


# --- local upload, --duration, --ref and the quote cap (#1468) -----------------


def _ref_png(path: Path) -> Path:
    """A normal-sized image: Flow declines degenerate ones (#755)."""
    from PIL import Image

    Image.new("RGB", (512, 512), (40, 120, 200)).save(path)
    return path


async def _source_clip_file(profile: Path, path: Path) -> Path:
    """The env clip's bytes, read through Flow itself, as a local file to upload ($0)."""
    from gflow_cli.api.transports.migrated_recover import read_clip

    project, media = _ids()
    transport, page = await _live_page(profile)
    try:
        _wf, data = await read_clip(page, project_id=project, media_id=media)
    finally:
        await transport.teardown()
    path.write_bytes(data)
    return path


@pytest.mark.e2e_auth
def test_e2e_cli_a_zero_cap_is_exit_45_and_nothing_is_submitted(e2e_env: dict[str, str]) -> None:
    """$0: every extend quotes above 0 credits, so the cap refuses before the click."""
    from .test_image_uuid_ref_e2e import _events, _run_gflow

    _need_enabled_extend()
    project, media = _ids()
    proc = _run_gflow(
        ["video", "extend", media, _PROMPT, "--project", project, "--max-credits", "0"]
        + ["--yes", "--json"],
        e2e_env,
    )
    assert proc.returncode == 45, proc.stderr[-800:]
    error = json.loads(proc.stdout)["error"]
    assert error["charged"] == "no", error
    assert "migrated.submit_clicked" not in _events(proc.stderr)


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_local_upload_duration_and_ref_reach_the_submit_at_zero_cost(
    e2e_profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """$0 route-abort: a local clip and one ref are uploaded (free, they stay in the
    project), the slider is set to 5 s, and the checked ``fZytfe`` must carry the 5 s key,
    the uploaded ref id in ``entry[8]`` and the uploaded clip's workflow — then it is
    aborted before Flow bills it."""
    _need_enabled_extend()
    project, _ = _ids()
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    source = await _source_clip_file(e2e_profile_dir, tmp_path / "source.mp4")
    ref = _ref_png(tmp_path / "ref.png")
    seen: dict[str, Any] = {"fZytfe": [], "workflow": None, "refs": None}
    real_require, real_upload = mx.require_video_source, mx._upload_extend_inputs  # noqa: SLF001

    def spy_require(clip: Any, **kw: Any) -> str:
        seen["workflow"] = real_require(clip, **kw)
        return seen["workflow"]

    async def spy_upload(*args: Any, **kw: Any) -> Any:
        uploaded, refs = await real_upload(*args, **kw)
        seen["refs"] = [ref_id for ref_id, _ in refs]
        return uploaded, refs

    async def capture_release(route: Any, rpcid: str | None) -> None:
        if rpcid == mx.EXTEND_SUBMIT_RPC:
            body = migrated_composer.request_body(route.request)
            entry = mx._at(mx._inner(rpcid, body), 0, 0)  # noqa: SLF001
            seen["fZytfe"].append(
                {
                    "slot1": mx._at(entry, 0, 1),  # noqa: SLF001
                    "refs": mx._extract_reference_ids(entry),  # noqa: SLF001
                    "key": mx.submit_facts(body)[0],
                }
            )
            await route.abort()
            return
        await route.fallback()

    monkeypatch.setattr(mx, "require_video_source", spy_require)
    monkeypatch.setattr(mx, "_upload_extend_inputs", spy_upload)
    monkeypatch.setattr(mx, "_release", capture_release)
    transport, page = await _live_page(e2e_profile_dir)
    try:
        with pytest.raises(TransportTimeoutError):
            await mx.run_extend(
                page,
                project_id=project,
                source_video=source,
                reference_images=(ref,),
                duration=5,
                prompt=_PROMPT,
                aspect=None,
                poll_timeout_s=_SUBMIT_WAIT_S,
            )
    finally:
        await transport.teardown()

    assert len(seen["fZytfe"]) == 1, seen
    hit = seen["fZytfe"][0]
    assert mx.extend_key_seconds(hit["key"]) == 5, hit
    assert hit["refs"] == seen["refs"] and len(hit["refs"]) == 1, (hit, seen["refs"])
    assert hit["slot1"] == seen["workflow"], (hit, seen["workflow"])


@pytest.mark.e2e_video
def test_e2e_one_paid_local_upload_extend_with_a_ref(
    e2e_env: dict[str, str], e2e_profile_dir: Path, tmp_path: Path
) -> None:
    """SPENDS VEO CREDITS. A local clip at --duration 5 with one --ref, through the CLI:
    the output is an mp4 and the catalog lists the extended clip."""
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO") != "1":
        pytest.skip("spends Veo credits — set GFLOW_CLI_E2E_RUN_VIDEO=1 (owner consent)")
    _need_enabled_extend()
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore

    from .test_image_uuid_ref_e2e import _run_gflow

    project, _ = _ids()
    source = asyncio.run(_source_clip_file(e2e_profile_dir, tmp_path / "source.mp4"))
    ref = _ref_png(tmp_path / "ref.png")
    proc = _run_gflow(
        ["video", "extend", str(source), _PROMPT, "--project", project, "--duration", "5"]
        + ["--ref", str(ref), "--yes", "--json"],
        e2e_env,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    payload = json.loads(proc.stdout)
    assert Path(payload["local_path"]).read_bytes()[4:8] == b"ftyp", payload
    assert payload["request"]["duration"] in (5, None), payload  # None: a separate Veo clip
    with DataStore.open(Path(e2e_env["GFLOW_CLI_DB_PATH"])) as store:
        row = DataRepository(store).get_asset_by_flow_media_id(
            e2e_env["GFLOW_CLI_PROFILE"], payload["media_id"]
        )
    assert row is not None, payload
