"""Tests for `gflow video extend` — the extend primitive at the CLI seam.

Driven through `CliRunner`, with the async runner patched so no browser, no
network and no credits are involved.

Two things are deliberately pinned as behaviour, not implementation detail,
because the predict council identified both as the difference between a safe
command and an expensive one:

* nothing is spent before the user has seen the cost, and
* `1:1` is refused at the Click boundary — Flow has no square extend model in
  either family, so accepting it could only ever produce a late failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main as cli

MEDIA = "b9458021-fc2d-4d95-ab53-cf844c6f1079"
PROJECT = "7d3d6bd9-a39f-4c2d-b772-146e73e539cf"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_rejects_square_aspect_at_the_boundary(runner: CliRunner) -> None:
    """No SQUARE key exists in either extend family, so this can never succeed.
    Refusing in Click means it costs nothing and says so immediately."""
    result = runner.invoke(
        cli, ["video", "extend", MEDIA, "keep going", "--aspect", "1:1", "--yes"]
    )
    assert result.exit_code == 2
    assert "1:1" in result.output or "aspect" in result.output.lower()


def test_dry_run_spends_nothing(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """--dry-run must not construct a client, let alone submit. The council's
    rule: the cost gate comes before anything billable exists."""
    called: list[str] = []

    def _boom(*_a: Any, **_k: Any) -> None:
        called.append("ran")
        raise AssertionError("dry-run must not reach the runner")

    monkeypatch.setattr("gflow_cli.cli_video.run_with_handlers", _boom)
    result = runner.invoke(
        cli, ["video", "extend", MEDIA, "keep going", "--project", PROJECT, "--dry-run"]
    )
    assert called == []
    assert result.exit_code == 0
    assert "extend" in result.output.lower()


def test_rejects_a_malformed_media_id(runner: CliRunner) -> None:
    """Fail before any network call rather than after a token has been minted."""
    result = runner.invoke(
        cli, ["video", "extend", "not-a-uuid", "keep going", "--yes", "--dry-run"]
    )
    assert result.exit_code != 0
    assert "uuid" in result.output.lower() or "invalid" in result.output.lower()


def test_help_names_the_cost_and_the_ceiling(runner: CliRunner) -> None:
    """`--help` is the only documentation most callers read. It must say that
    this spends credits and that a segment is 8s, because both drive the
    decision to run it."""
    result = runner.invoke(cli, ["video", "extend", "--help"])
    assert result.exit_code == 0
    low = result.output.lower()
    assert "credit" in low
    assert "8" in result.output


def test_resume_flag_exists_if_the_banner_promises_it(runner: CliRunner) -> None:
    """The interrupt banner tells the user to re-run with `--resume-from <id>`.
    A flag that is advertised but absent is worse than saying nothing, so the
    two must not drift apart."""
    result = runner.invoke(cli, ["video", "extend", "--help"])
    assert result.exit_code == 0
    assert "--resume-from" in result.output


def test_resume_requires_a_scene(runner: CliRunner) -> None:
    """Resuming means continuing an existing scene, so the id it takes is a
    scene id — the same one the banner prints."""
    result = runner.invoke(
        cli,
        [
            "video",
            "extend",
            MEDIA,
            "onwards",
            "--project",
            PROJECT,
            "--resume-from",
            "not-a-uuid",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0


# --------------------------------------------------------------------------
# _extend_session — the orchestration between the CLI boundary and the client
# --------------------------------------------------------------------------


class _FakeScene:
    def __init__(self, scene_id: str, clips: list[Any] | None = None) -> None:
        self.scene_id = scene_id
        self.workflows = clips or []

    def to_concat_inputs(self) -> tuple[Any, ...]:
        return tuple(self.workflows)


class _FakeClient:
    """Stands in for FlowApiClient as an async context manager."""

    def __init__(self, listing: dict[str, Any]) -> None:
        self._listing = listing
        self.submitted: list[dict[str, Any]] = []
        self.concat_calls = 0
        self.downloads: list[str] = []
        self.created_scene = False
        self.scene_clips: list[Any] = []
        self.served = "labs"
        self.listed = False

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def served_route(self, *, prefer_migrated: bool) -> str:
        return self.served

    async def capability_listing(self, _project_id: str) -> dict[str, Any]:
        self.listed = True
        return self._listing

    async def create_scene(self, *, project_id: str, workflow_ids: list[str]) -> _FakeScene:
        self.created_scene = True
        return _FakeScene("aaaaaaaa-0000-0000-0000-000000000000")

    async def get_scene_workflows(self, scene_id: str, *, project_id: str) -> _FakeScene:
        return _FakeScene(scene_id, list(self.scene_clips))

    async def extend_video(self, **kwargs: Any) -> Any:
        from gflow_cli.api.video_extend import ExtendStarted

        self.submitted.append(kwargs)
        n = len(self.submitted)
        return ExtendStarted(
            media_id=f"media-{n}",
            workflow_id=f"wf-{n}",
            model_key="veo_3_1_extension_lite",
            unit_cost=10,
        )

    async def poll_video_status(self, media_id: str, *, project_id: str) -> object:
        return object()

    async def concatenate_scene(self, inputs: Any, *, out_path: Any) -> Any:
        self.concat_calls += 1
        return out_path

    async def download_video(self, media_id: str, out_path: Path) -> Path:
        self.downloads.append(media_id)
        return out_path


def _listing_fixture() -> dict[str, Any]:
    import json as _json
    from pathlib import Path as _Path

    return _json.loads(
        (
            _Path(__file__).parents[1]
            / "api"
            / "fixtures"
            / "project_initial_data_extend_models.json"
        ).read_text(encoding="utf-8")
    )


@pytest.fixture
def _patched_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient(_listing_fixture())
    monkeypatch.setattr("gflow_cli.cli_video.FlowApiClient", lambda **_kw: fake)
    return fake


@pytest.mark.asyncio
async def test_session_creates_a_scene_and_chains(
    _patched_client: _FakeClient, tmp_path: Path
) -> None:
    """Flow's own UI creates the scene before extending into it, so we do too."""
    from gflow_cli.cli_video import _extend_session

    await _extend_session(
        profile_name="p",
        profile_dir=tmp_path,
        media_id=MEDIA,
        prompts=("a", "b"),
        segments=2,
        aspect="16:9",
        jitter_range=(0.0, 0.0),
        output_file=None,
        project_id=PROJECT,
        scene_id=None,
        seed=None,
        as_json=False,
        recorder=None,
    )
    assert _patched_client.created_scene
    assert len(_patched_client.submitted) == 2


@pytest.mark.asyncio
async def test_session_refuses_when_the_balance_cannot_finish_the_run(
    _patched_client: _FakeClient, tmp_path: Path
) -> None:
    """Stopping before segment 1 beats stopping at segment 6 holding a
    half-length video and a spent balance."""
    from gflow_cli.cli_video import _extend_session
    from gflow_cli.errors import ConfigurationError

    listing = _patched_client._listing
    listing["result"]["data"]["json"]["userData"]["credits"] = 5  # < 10 * 2

    with pytest.raises(ConfigurationError, match="insufficient credits"):
        await _extend_session(
            profile_name="p",
            profile_dir=tmp_path,
            media_id=MEDIA,
            prompts=("a",),
            segments=2,
            aspect="16:9",
            jitter_range=(0.0, 0.0),
            output_file=None,
            project_id=PROJECT,
            scene_id=None,
            seed=None,
            as_json=False,
            recorder=None,
        )
    assert _patched_client.submitted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("segments", [1, 2])
async def test_session_renders_when_output_is_given(
    _patched_client: _FakeClient, tmp_path: Path, segments: int
) -> None:
    # labs: the scene render is "original + N extensions" (KNOWN_ISSUES), even for N=1;
    # downloading the lone new segment would drop the source clip from --output.
    from gflow_cli.cli_video import _extend_session

    await _extend_session(
        profile_name="p",
        profile_dir=tmp_path,
        media_id=MEDIA,
        prompts=("a",),
        segments=segments,
        aspect="16:9",
        jitter_range=(0.0, 0.0),
        output_file=tmp_path / "out.mp4",
        project_id=PROJECT,
        scene_id=None,
        seed=None,
        as_json=False,
        recorder=None,
    )
    assert _patched_client.concat_calls == 1
    assert _patched_client.downloads == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("segments", "scene_id", "seed"), [(2, None, None), (1, MEDIA, None), (1, None, 7)]
)
async def test_an_unported_form_on_a_page_served_flow_google_com_is_exit_36(
    _patched_client: _FakeClient,
    tmp_path: Path,
    segments: int,
    scene_id: str | None,
    seed: int | None,
) -> None:
    # Default host `auto` routes an unported form to the labs arm; if Flow then serves
    # flow.google.com, the retired projectInitialData read would be exit 7. Refuse first.
    from gflow_cli.cli_video import _extend_session
    from gflow_cli.errors import FlowHostMigratedError
    from gflow_cli.json_output import exit_code_for

    _patched_client.served = "migrated"
    with pytest.raises(FlowHostMigratedError, match="not ported") as info:
        await _extend_session(
            profile_name="p",
            profile_dir=tmp_path,
            media_id=MEDIA,
            prompts=("a",),
            segments=segments,
            aspect="16:9",
            jitter_range=(0.0, 0.0),
            output_file=None,
            project_id=PROJECT,
            scene_id=scene_id,
            seed=seed,
            as_json=False,
            recorder=None,
        )
    assert exit_code_for(info.value) == 36
    assert not _patched_client.listed and _patched_client.submitted == []


@pytest.mark.asyncio
async def test_session_requires_a_project(tmp_path: Path) -> None:
    """Extend must know which project owns MEDIA_ID before anything else."""
    from gflow_cli.cli_video import _run_extend
    from gflow_cli.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="--project is required"):
        await _run_extend(
            profile_name="p",
            profile_dir=tmp_path,
            media_id=MEDIA,
            prompts=("a",),
            segments=1,
            aspect="16:9",
            jitter=None,
            output_file=None,
            project_id=None,
            scene_id=None,
            seed=None,
            as_json=False,
        )


# --------------------------------------------------------------------------
# Resume, recording, and the paths a partial run takes
# --------------------------------------------------------------------------


def _clip(position: int, media_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(metadata=SimpleNamespace(position=position), media_id=media_id)


@pytest.fixture
def _chain_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replaces run_extend_chain so the session's own decisions are visible
    without submitting anything."""
    from gflow_cli.api.extend_chain import ExtendChainResult

    seen: dict[str, Any] = {}

    async def _fake(_client: Any, **kwargs: Any) -> ExtendChainResult:
        seen.update(kwargs)
        on_submitted = kwargs.get("on_submitted")
        if on_submitted is not None:
            for started in seen.get("_emit", []):
                on_submitted(started)
        return seen.get(
            "_result",
            ExtendChainResult(scene_id=kwargs["scene_id"], completed_media_ids=["m1"]),
        )

    monkeypatch.setattr("gflow_cli.cli_video.run_extend_chain", _fake)
    return seen


@pytest.mark.asyncio
async def test_resume_appends_after_the_last_clip_not_at_its_count(
    _patched_client: _FakeClient, _chain_spy: dict[str, Any], tmp_path: Path
) -> None:
    """Positions go non-contiguous when clips are deleted in Flow's UI, so the
    next slot is `last position + 1` — `len(clips)` would collide with an
    occupied one. The seed is the scene's real tail, not the original media."""
    from gflow_cli.cli_video import _extend_session

    scene = "bbbbbbbb-0000-0000-0000-000000000000"
    _patched_client.scene_clips = [_clip(1, "first"), _clip(5, "tail-media")]

    await _extend_session(
        profile_name="p",
        profile_dir=tmp_path,
        media_id=MEDIA,
        prompts=("a",),
        segments=1,
        aspect="16:9",
        jitter_range=(0.0, 0.0),
        output_file=None,
        project_id=PROJECT,
        scene_id=scene,
        seed=None,
        as_json=False,
        recorder=None,
    )

    assert _chain_spy["start_position"] == 6
    assert _chain_spy["media_id"] == "tail-media"
    assert _chain_spy["scene_id"] == scene
    assert not _patched_client.created_scene


@pytest.mark.asyncio
async def test_a_failed_catalog_write_never_sinks_a_paid_segment(
    _patched_client: _FakeClient, _chain_spy: dict[str, Any], tmp_path: Path
) -> None:
    """Flow bills on acceptance. A DataStore failure at submit is logged and
    swallowed — the user has already paid for that segment."""
    from gflow_cli.api.video_extend import ExtendStarted
    from gflow_cli.cli_video import _extend_session
    from gflow_cli.errors import DataStoreError

    calls: list[str] = []

    class _Recorder:
        def record_started_extend(self, *, started: Any, **_kw: Any) -> None:
            calls.append(started.media_id)
            if len(calls) == 2:
                msg = "disk full"
                raise DataStoreError(msg)

    _chain_spy["_emit"] = [
        ExtendStarted(media_id="m1", workflow_id="w1", model_key="k", unit_cost=10),
        ExtendStarted(media_id="m2", workflow_id="w2", model_key="k", unit_cost=10),
    ]

    await _extend_session(
        profile_name="p",
        profile_dir=tmp_path,
        media_id=MEDIA,
        prompts=("a",),
        segments=2,
        aspect="16:9",
        jitter_range=(0.0, 0.0),
        output_file=None,
        project_id=PROJECT,
        scene_id=None,
        seed=None,
        as_json=False,
        recorder=_Recorder(),
    )

    assert calls == ["m1", "m2"]


@pytest.mark.asyncio
async def test_an_aborted_chain_does_not_exit_zero(
    _patched_client: _FakeClient, _chain_spy: dict[str, Any], tmp_path: Path
) -> None:
    """A run that abandoned paid work must surface its error, not look complete."""
    from gflow_cli.api.extend_chain import ExtendChainResult
    from gflow_cli.cli_video import _extend_session
    from gflow_cli.errors import ConfigurationError

    boom = ConfigurationError("refused at segment 2")
    _chain_spy["_result"] = ExtendChainResult(
        scene_id="s", completed_media_ids=["m1"], credits_spent=10, error=boom
    )

    with pytest.raises(ConfigurationError, match="refused at segment 2"):
        await _extend_session(
            profile_name="p",
            profile_dir=tmp_path,
            media_id=MEDIA,
            prompts=("a",),
            segments=2,
            aspect="16:9",
            jitter_range=(0.0, 0.0),
            output_file=None,
            project_id=PROJECT,
            scene_id=None,
            seed=None,
            as_json=False,
            recorder=None,
        )


@pytest.mark.asyncio
async def test_json_mode_emits_exactly_one_document_when_the_chain_aborts(
    _patched_client: _FakeClient,
    _chain_spy: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-raising in JSON mode would print a SECOND document and make
    json.loads(stdout) fail with "Extra data", so the payload is emitted once
    and the failure is carried by the exit code alone."""
    from gflow_cli.api.extend_chain import ExtendChainResult
    from gflow_cli.cli_video import _extend_session
    from gflow_cli.errors import ConfigurationError

    _chain_spy["_result"] = ExtendChainResult(
        scene_id="s",
        completed_media_ids=["m1"],
        credits_spent=10,
        error=ConfigurationError("refused"),
    )

    with pytest.raises(SystemExit) as exit_info:
        await _extend_session(
            profile_name="p",
            profile_dir=tmp_path,
            media_id=MEDIA,
            prompts=("a",),
            segments=2,
            aspect="16:9",
            jitter_range=(0.0, 0.0),
            output_file=None,
            project_id=PROJECT,
            scene_id=None,
            seed=None,
            as_json=True,
            recorder=None,
        )

    assert exit_info.value.code != 0
    payload = json.loads(capsys.readouterr().out)  # one document, or this raises
    assert payload["aborted"] is True
    assert payload["segments_completed"] == 1
    assert payload["credits_spent"] == 10
    # The video_result shape t2v/i2v emit, so one parser reads all three (#882, @stgmt).
    assert payload["status"] == "fail"
    assert payload["succeeded"] is False
    assert payload["media_id"] == "m1"
    assert payload["request"]["mode"] == "extend"


# --------------------------------------------------------------------------
# flow.google.com arm (#882 takeover): route chosen before any browser
# --------------------------------------------------------------------------

SCENE = "cccccccc-0000-4000-8000-000000000000"


@pytest.fixture
def _host(monkeypatch: pytest.MonkeyPatch) -> Any:
    from gflow_cli.config import reset_settings

    def set_host(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
        else:
            monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", value)
        reset_settings()

    yield set_host
    reset_settings()


@pytest.fixture
def _run_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def _fake(**kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr("gflow_cli.cli_video._run_extend", _fake)
    monkeypatch.setattr("gflow_cli.cli_video._make_provider_dir", lambda _p: Path("."))
    monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", lambda _p: "p")
    return seen


@pytest.mark.parametrize(
    "extra",
    [
        ["-n", "2"],
        ["--scene", SCENE],
        ["--resume-from", SCENE],
        ["--seed", "7"],
    ],
)
def test_unported_forms_exit_36_before_anything_on_flow_google_com(
    runner: CliRunner, _host: Any, _run_spy: dict[str, Any], extra: list[str]
) -> None:
    _host("flow.google.com")
    result = runner.invoke(
        cli, ["video", "extend", MEDIA, "go", "--project", PROJECT, "--yes", *extra]
    )
    assert result.exit_code == 36, result.output
    assert _run_spy == {}  # no client, no browser
    assert "Extend plan" not in result.output


@pytest.mark.parametrize(
    ("host", "extra", "migrated"),
    [
        (None, [], True),  # auto: the default host serves a single segment
        ("flow.google.com", [], True),
        ("labs.google", [], False),  # the kill switch keeps the labs arm
        # auto + unported form: the labs arm, which refuses (exit 36) once the page
        # shows Flow served flow.google.com — see the session test above.
        (None, ["-n", "2"], False),
    ],
)
def test_the_arm_is_chosen_before_the_browser(
    runner: CliRunner,
    _host: Any,
    _run_spy: dict[str, Any],
    host: str | None,
    extra: list[str],
    migrated: bool,
) -> None:
    _host(host)
    result = runner.invoke(
        cli, ["video", "extend", MEDIA, "go", "--project", PROJECT, "--yes", *extra]
    )
    assert result.exit_code == 0, result.output
    assert _run_spy["migrated"] is migrated
    assert _run_spy["aspect"] is None  # no hidden 9:16 sent to the migrated arm


def test_the_migrated_plan_claims_no_price(runner: CliRunner, _host: Any) -> None:
    _host("flow.google.com")
    result = runner.invoke(cli, ["video", "extend", MEDIA, "go", "--project", PROJECT, "--dry-run"])
    assert result.exit_code == 0
    assert "does not report the price" in result.output
    assert "exact amount" not in result.output
    flat = " ".join(result.output.split())  # Rich wraps at the terminal width
    assert "grows the source clip in place" in flat  # #1184, a dated observation
    assert "measured 2026-11-08" in flat
    assert "Flow bills 8s" not in flat  # the length is the key's, not a fixed Veo 8 s


class _MigratedClient:
    def __init__(self, *, done: bool) -> None:
        self.done = done
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _MigratedClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def extend_video_migrated(self, **kwargs: Any) -> Any:
        from gflow_cli.api.transports.batchexecute import GenerationRecord
        from gflow_cli.api.transports.migrated_extend import ExtendOutcome
        from gflow_cli.api.video_extend import ExtendStarted

        self.calls.append(kwargs)
        kwargs["on_started"](
            ExtendStarted(media_id="new-media", workflow_id="new-wf", model_key="k"), "16:9"
        )
        record = GenerationRecord(
            workflow_id="new-wf",
            project_id=PROJECT,
            media_id="new-media",
            status=3 if self.done else 4,
            video_url=None,
            poster_url=None,
            size_bytes=None,
        )
        outcome = ExtendOutcome(
            record=record, video=None, model_key="k", aspect="16:9", scene_id=SCENE
        )
        return outcome, (kwargs["out_path"] if self.done else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("done", [True, False])
async def test_the_migrated_session_reports_one_video_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    done: bool,
) -> None:
    from gflow_cli.cli_video import _extend_migrated_session

    fake = _MigratedClient(done=done)
    monkeypatch.setattr("gflow_cli.cli_video.FlowApiClient", lambda **_kw: fake)
    rows: list[tuple[str, str]] = []

    class _Recorder:
        def record_started_extend(self, *, started: Any, aspect: str, **_kw: Any) -> None:
            rows.append((started.media_id, aspect))

    async def _go() -> None:
        await _extend_migrated_session(
            profile_name="p",
            profile_dir=tmp_path,
            media_id=MEDIA,
            prompt="go",
            aspect=None,
            output_file=tmp_path / "out.mp4",
            project_id=PROJECT,
            as_json=True,
            recorder=_Recorder(),  # type: ignore[arg-type]
        )

    if done:
        await _go()
    else:
        with pytest.raises(SystemExit) as info:
            await _go()
        assert info.value.code != 0
    payload = json.loads(capsys.readouterr().out)  # exactly one document
    assert payload["succeeded"] is done
    assert payload["media_id"] == "new-media"
    assert payload["request"]["mode"] == "extend"
    assert payload["scene_id"] == SCENE
    assert rows == [("new-media", "16:9")]  # recorded at submit, with the page's aspect
    assert fake.calls[0]["aspect"] is None


@pytest.mark.parametrize(
    ("key", "added", "billed"),
    [("macaron_cookie_extend_4s", 4, 4), ("veo_3_1_extension_lite", 7, 8)],
)
def test_the_migrated_payload_reports_the_key_length(key: str, added: int, billed: int) -> None:
    """#1184: an Omni key names its length (+3.9 s measured for ``_4s``); a Veo key keeps
    the measured 7 s added / 8 s billed."""
    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.transports.migrated_extend import ExtendOutcome
    from gflow_cli.cli_video import _migrated_extend_payload

    record = GenerationRecord(
        workflow_id="wf",
        project_id=PROJECT,
        media_id=MEDIA,
        status=3,
        video_url=None,
        poster_url=None,
        size_bytes=None,
    )
    outcome = ExtendOutcome(record=record, video=None, model_key=key, aspect="9:16", scene_id=None)
    payload = _migrated_extend_payload(
        outcome, "out.mp4", profile_name="p", project_id=PROJECT, aspect=None
    )
    assert (payload["seconds_added"], payload["seconds_billed"]) == (added, billed)
    assert payload["request"]["duration"] == added


def test_json_mode_prints_no_human_plan(runner: CliRunner) -> None:
    """The plan preamble on stdout corrupted the JSON document (#882, @stgmt)."""
    result = runner.invoke(
        cli, ["video", "extend", MEDIA, "go", "--project", PROJECT, "--dry-run", "--json"]
    )
    assert result.exit_code == 0
    assert "Extend plan" not in result.output


@pytest.mark.asyncio
async def test_the_store_is_closed_even_when_the_session_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stray handle blocks a later `gflow data` call in the same process on
    Windows, so the close has to survive the failure path too."""
    from gflow_cli.cli_video import _run_extend

    closed: list[bool] = []

    class _Store:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr("gflow_cli.cli_video.DataStore.open", lambda _p: _Store())
    monkeypatch.setattr("gflow_cli.cli_video.DataRepository", lambda _s: object())
    monkeypatch.setattr("gflow_cli.cli_video.OperationRecorder", lambda *_a, **_kw: object())

    async def _boom(**_kw: Any) -> None:
        msg = "browser died"
        raise RuntimeError(msg)

    monkeypatch.setattr("gflow_cli.cli_video._extend_session", _boom)

    with pytest.raises(RuntimeError, match="browser died"):
        await _run_extend(
            profile_name="p",
            profile_dir=tmp_path,
            media_id=MEDIA,
            prompts=("a",),
            segments=1,
            aspect="16:9",
            jitter=None,
            output_file=None,
            project_id=PROJECT,
            scene_id=None,
            seed=None,
            as_json=False,
        )

    assert closed == [True]


@pytest.mark.asyncio
async def test_an_unavailable_catalog_does_not_block_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The catalog is a convenience, never a gate."""
    from gflow_cli.cli_video import _run_extend
    from gflow_cli.errors import DataStoreError

    def _unavailable(_path: Any) -> None:
        msg = "locked"
        raise DataStoreError(msg)

    monkeypatch.setattr("gflow_cli.cli_video.DataStore.open", _unavailable)

    seen: dict[str, Any] = {}

    async def _session(**kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr("gflow_cli.cli_video._extend_session", _session)

    await _run_extend(
        profile_name="p",
        profile_dir=tmp_path,
        media_id=MEDIA,
        prompts=("a",),
        segments=1,
        aspect="16:9",
        jitter=None,
        output_file=None,
        project_id=PROJECT,
        scene_id=None,
        seed=None,
        as_json=False,
    )

    assert seen["recorder"] is None


@pytest.mark.asyncio
async def test_a_media_id_no_workflow_owns_names_the_project_flag(
    _patched_client: _FakeClient, _chain_spy: dict[str, Any], tmp_path: Path
) -> None:
    """A scene can only be created from the workflow that owns the media, so a
    media id from another project fails here — and the message says which flag
    to fix rather than surfacing later as an opaque scene error."""
    from gflow_cli.cli_video import _extend_session
    from gflow_cli.errors import ConfigurationError

    stranger = "00000000-dead-4000-8000-000000000000"

    with pytest.raises(ConfigurationError, match="no workflow owns it"):
        await _extend_session(
            profile_name="p",
            profile_dir=tmp_path,
            media_id=stranger,
            prompts=("a",),
            segments=1,
            aspect="16:9",
            jitter_range=(0.0, 0.0),
            output_file=None,
            project_id=PROJECT,
            scene_id=None,
            seed=None,
            as_json=False,
            recorder=None,
        )

    assert not _patched_client.created_scene


def test_declining_the_confirmation_submits_nothing(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --yes the cost prompt is the last gate, and answering no must
    abort before a profile is resolved or a browser is opened."""
    called: list[object] = []
    monkeypatch.setattr("gflow_cli.cli_video._run_extend", lambda **kw: called.append(kw))

    result = runner.invoke(
        cli,
        ["video", "extend", MEDIA, "a wave recedes", "--project", PROJECT],
        input="n\n",
    )

    assert result.exit_code != 0
    assert called == []


def test_segments_defaults_to_one_per_prompt(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--segments` is optional: two prompts mean two segments."""
    seen: dict[str, Any] = {}

    async def _run(**kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr("gflow_cli.cli_video._run_extend", _run)
    # Profile resolution happens after the confirm gate; stubbing it keeps this
    # test about argument shaping rather than about the machine's auth state.
    monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", lambda _p: "default")
    monkeypatch.setattr("gflow_cli.cli_video._make_provider_dir", lambda _n: Path("."))

    result = runner.invoke(
        cli,
        [
            "video",
            "extend",
            MEDIA,
            "the wave recedes",
            "the gull lands",
            "--project",
            PROJECT,
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["segments"] == 2
    assert seen["prompts"] == ("the wave recedes", "the gull lands")
