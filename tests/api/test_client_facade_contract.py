"""Characterization (golden master) of the ``FlowApiClient`` facade (#1249).

Pinned BEFORE the class is split, so the split can prove it changed nothing a caller sees:

* the method set and every signature, public and the private helpers tests patch, and the
  instance attributes ``__init__`` sets;
* every name other code reads from ``gflow_cli.api.client``, with its value or signature;
* for calls across every responsibility of the class: the HTTP traffic (verb, URL, body,
  header values with the bearer token redacted, other request options), the result or the
  exception with its problem details, the files written, and the log events with fields.

The expected values live in ``fixtures/flow_client_facade.json``, recorded on develop at
b7fc3b3f. A change to that file is a behaviour change and must be justified. Re-record it
(same hermetic setup as the tests) with:

    RECORD_FACADE_CONTRACT=1 uv run pytest tests/api/test_client_facade_contract.py -k record
"""

from __future__ import annotations

import base64
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import structlog

import gflow_cli.api.client as client_mod
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.scene import ConcatInput, SceneWorkflow, SceneWorkflowMetadata
from gflow_cli.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "flow_client_facade.json"
LISTING = Path(__file__).parent / "fixtures" / "project_initial_data_extend_models.json"

# Private helpers that tests patch on the instance or the class. Moved code must keep calling
# them through ``self`` (see docs/superpowers/plans/2026-11-11-1249-split-god-classes).
PATCHED_PRIVATE = (
    "_checkin_page",
    "_checkout_page",
    "_fetch_access_token",
    "_get_json",
    "_handle_account_chooser",
    "_launch_persistent_context",
    "_log_and_guard_launch",
    "_mint_recaptcha_token",
    "_patch_json",
    "_persistent_context_kwargs",
    "_post_json",
    "_preread_flow_session_cookies",
    "_resolve_account_locale",
    "_run_with_retry",
)

# Names read from gflow_cli.api.client by src/, tests/ and scripts/: imported, or read off the
# module object (grep, 2026-11-11).
MODULE_NAMES = (
    "FlowApiClient",
    "FlowApiError",
    "MAX_CONCAT_B64_LEN",
    "MAX_IMAGE_BYTES",
    "MAX_UPSAMPLE_B64_LEN",
    "ProfileLease",
    "WafRejectionError",
    "_AISANDBOX_CONTENT_TYPE",
    "_default_project_title",
    "_extract_provider_error_message",
    "_is_profile_access_denied",
    "_is_supported_image_header",
    "_is_target_closed",
    "_raise_for_non_retryable",
    "_storage_key_from_path",
    "_unwrap_trpc",
    "async_playwright",
    "validate_image_file",
)

# Settings tests may set on purpose, kept (conftest pins them); every other GFLOW_CLI_* is removed
# so a developer's environment cannot change the outcome.
_KEEP_ENV = {
    "GFLOW_CLI_HOME",
    "GFLOW_CLI_DB_PATH",
    "GFLOW_CLI_UPDATE_CHECK",
    "GFLOW_CLI_TELEMETRY",
    "GFLOW_CLI_WIRE_READS",
}

PROJECT = "0b5e0b5e-0000-4000-8000-000000000001"  # UUID-shaped: some routes validate it
MEDIA = "0b5e0b5e-0000-4000-8000-0000000000aa"
SCENE = "0b5e0b5e-0000-4000-8000-0000000000bb"
MP4_HEAD = bytes([0, 0, 0, 24]) + b"ftypmp42" + bytes(16)
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
)


def _trpc(payload: dict[str, Any]) -> str:
    return json.dumps({"result": {"data": {"json": payload}}})


# (verb, URL fragment) -> a reply shaped like Flow's, so success parsing is pinned too.
# Anything else gets an empty tRPC envelope.
_REPLIES: list[tuple[str, str, str | bytes]] = [
    ("GET", "flow-content.google/image/", PNG_1PX),
    ("GET", "media.getMediaUrlRedirect", MP4_HEAD),
    ("POST", ":runVideoFxConcatenation", json.dumps({"operation": {"name": "op-1"}})),
    (
        "POST",
        "project.createProject",
        _trpc({"result": {"projectId": PROJECT, "projectInfo": {"projectTitle": "first title"}}}),
    ),
    ("POST", f"/projects/{PROJECT}/scenes", json.dumps({"scene": {"sceneId": SCENE}})),
    ("POST", "flow.createEntity", _trpc({"entityId": "e1"})),
    (
        "POST",
        "batchCheckAsyncVideoGenerationStatus",
        json.dumps(
            {
                "media": [
                    {
                        "name": MEDIA,
                        "mediaMetadata": {
                            "mediaStatus": {
                                "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_FAILED"
                            }
                        },
                    }
                ]
            }
        ),
    ),
    (
        "POST",
        "upsampleImage",
        json.dumps({"encodedImage": base64.b64encode(PNG_1PX).decode()}),
    ),
    ("POST", "ExtendVideo", json.dumps({"media": [{"name": MEDIA}]})),
    (
        "POST",
        "uploadImage",
        json.dumps(
            {
                "media": {
                    "name": MEDIA,
                    "projectId": PROJECT,
                    "workflowId": "w1",
                    "image": {"dimensions": {"width": 1, "height": 1}},
                },
                "workflow": {"metadata": {"displayName": "in.png"}},
            }
        ),
    ),
    (
        "POST",
        "CheckConcatenationStatus",
        json.dumps(
            {
                "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                "encodedVideo": base64.b64encode(MP4_HEAD).decode(),
            }
        ),
    ),
]
_TRPC_EMPTY = _trpc({})


def _reply(verb: str, url: str) -> str | bytes:
    return next((body for v, frag, body in _REPLIES if v == verb and frag in url), _TRPC_EMPTY)


def _describe(obj: Any) -> str:
    if isinstance(obj, property):
        return "property"
    kind = "async" if inspect.iscoroutinefunction(obj) else "sync"
    return f"{kind} {inspect.signature(obj)}"


def method_contract() -> dict[str, str]:
    names = [n for n in dir(FlowApiClient) if not n.startswith("_")]
    names += ["__init__", "__aenter__", "__aexit__", *PATCHED_PRIVATE]
    return {n: _describe(inspect.getattr_static(FlowApiClient, n)) for n in sorted(set(names))}


def _describe_name(obj: Any) -> str:
    """A moved function keeps its signature; a class or module keeps its identity; a constant
    keeps its value. ``__module__`` of a moved function is allowed to change."""
    if inspect.isclass(obj) or inspect.ismodule(obj):
        return f"{type(obj).__name__} {getattr(obj, '__module__', '')}.{obj.__name__}"
    if inspect.isfunction(obj):
        return f"function {obj.__name__}{inspect.signature(obj)}"
    return f"{type(obj).__name__} {obj!r}"


def names_contract() -> dict[str, str]:
    return {n: _describe_name(getattr(client_mod, n)) for n in MODULE_NAMES}


class _Resp:
    def __init__(self, payload: str | bytes, url: str) -> None:
        self.url = url
        self.status = 200
        self._payload = payload if isinstance(payload, bytes) else payload.encode()

    async def text(self) -> str:
        return self._payload.decode("utf-8", errors="replace")

    async def body(self) -> bytes:
        return self._payload


# Per-request values Flow expects to differ on every call. Their SHAPE is pinned, not their value.
_VOLATILE = {
    "batchId": (
        re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}"),
        "<uuid4>",
    ),
    "sessionId": (re.compile(r";\d{13}"), "<;epoch-ms>"),
}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():  # pyright: ignore[reportUnknownVariableType]
            shape = _VOLATILE.get(k)  # pyright: ignore[reportUnknownArgumentType]
            if shape and isinstance(v, str) and shape[0].fullmatch(v):
                out[k] = shape[1]
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, str):  # a body logged as (possibly truncated) text
        for key, (shape, label) in _VOLATILE.items():
            # the log line may be cut mid-value, so match a prefix; parsed bodies get the full check
            loose = shape.pattern.replace("{13}", "{1,13}").replace("{12}", "{1,12}")
            value = re.sub(f'("{key}": "){loose}', rf"\g<1>{label}", value)
    return value


def _body(data: Any) -> Any:
    if not isinstance(data, str):
        return data
    try:
        return _scrub(json.loads(data))
    except json.JSONDecodeError:
        return _scrub(data)


def _headers(headers: dict[str, str] | None) -> dict[str, str]:
    return {
        k: ("<redacted>" if k.lower() in {"authorization", "cookie"} else v)
        for k, v in (headers or {}).items()
    }


class _Request:
    def __init__(self) -> None:
        self.log: list[dict[str, Any]] = []

    def _record(self, verb: str, url: str, kw: dict[str, Any]) -> _Resp:
        options = {k: v for k, v in kw.items() if k not in ("data", "headers")}
        self.log.append(
            {
                "verb": verb,
                "url": url,
                "data": _body(kw.get("data")),
                "headers": _headers(kw.get("headers")),
                "options": options,
            }
        )
        return _Resp(_reply(verb, url), url)

    async def get(self, url: str, **kw: Any) -> _Resp:
        return self._record("GET", url, kw)

    async def post(self, url: str, **kw: Any) -> _Resp:
        return self._record("POST", url, kw)

    async def patch(self, url: str, **kw: Any) -> _Resp:
        return self._record("PATCH", url, kw)


class _Page:
    url = "https://labs.google/fx/tools/flow"

    def __init__(self) -> None:
        self.request = _Request()


def _image() -> GeneratedImage:
    return GeneratedImage(
        media_name="m1",
        workflow_id="w1",
        seed=1,
        prompt="p",
        model_name_type=None,
        aspect_ratio="IMAGE_ASPECT_RATIO_SQUARE",
        fife_url="https://flow-content.google/image/m1",
        dimensions=(1, 1),
    )


def _cases(tmp: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """(responsibility, method, kwargs): calls across every surface of the class."""
    png = tmp / "in.png"
    png.write_bytes(PNG_1PX)
    poll = {"initial_delay_s": 0, "poll_interval": 0, "timeout_s": 0}
    return [
        ("workspace", "create_project", {"title": "first title"}),
        ("workspace", "rename_project", {"project_id": PROJECT, "new_title": "second title"}),
        ("workspace", "patch_agent_info", {"project_id": PROJECT, "enabled": True}),
        ("workspace", "get_agent_info", {"project_id": PROJECT}),
        ("workspace", "upload_image", {"project_id": PROJECT, "image_path": png}),
        ("workspace", "archive_workflow", {"workflow_id": "w1", "project_id": PROJECT}),
        (
            "workspace",
            "commit_workflow",
            {"workflow_id": "w1", "project_id": PROJECT, "primary_media_id": "m1"},
        ),
        ("workspace", "create_scene", {"project_id": PROJECT, "workflow_ids": ["w1"]}),
        (
            "workspace",
            "update_scene_workflows",
            {
                "scene_id": SCENE,
                "project_id": PROJECT,
                "workflows": [
                    SceneWorkflow(
                        workflow_id="w1",
                        metadata=SceneWorkflowMetadata(
                            position=0, start_time=0.0, end_time=8.0, total_duration=8.0
                        ),
                    )
                ],
            },
        ),
        ("workspace", "get_scene_workflows", {"scene_id": SCENE, "project_id": PROJECT}),
        (
            "workspace",
            "concatenate_scene",
            {
                "inputs": [ConcatInput(media_id=MEDIA, length=8.0, start=0.0, end=8.0)],
                "out_path": tmp / "concat.mp4",
                "poll_interval": 0,
            },
        ),
        ("generation", "poll_video_status", {"media_id": MEDIA, "project_id": PROJECT, **poll}),
        ("generation", "download_video", {"media_id": MEDIA, "out_path": tmp / "v.mp4"}),
        ("generation", "download_image", {"image": _image(), "out_path": tmp / "i.png"}),
        ("generation", "generate_image", {"req": None}),
        ("generation", "generate_video", {"req": None}),
        (
            "media_edit",
            "extend_video",
            {
                "media_id": MEDIA,
                "project_id": PROJECT,
                "scene_id": SCENE,
                "position": 0,
                "prompt": "go",
            },
        ),
        (
            "media_edit",
            "upsample_image",
            {
                "media_id": MEDIA,
                "project_id": PROJECT,
                "target_resolution": TargetResolution.RES_2K,
                "out_path": tmp / "u.png",
            },
        ),
        ("characters", "create_entity", {"project_id": PROJECT}),
        ("characters", "list_characters", {"project_id": PROJECT}),
        ("characters", "get_character", {"project_id": PROJECT, "name": "Kael"}),
        (
            "characters",
            "patch_entity",
            {
                "project_id": PROJECT,
                "entity_id": "e1",
                "display_name": "Kael",
                "workflow_ids": ["w1"],
            },
        ),
        ("characters", "delete_characters", {"project_id": PROJECT, "entity_ids": ["e1"]}),
    ]


_DEFAULT_TITLE = re.compile(r"gflow-cli [A-Z][a-z]{2} \d{1,2}, \d{2}:\d{2} [AP]M")


def _normalise(value: Any, tmp: Path) -> Any:
    text = json.dumps(value, default=repr, sort_keys=True, ensure_ascii=False)
    for form in (str(tmp), tmp.as_posix(), json.dumps(str(tmp), ensure_ascii=False)[1:-1]):
        text = text.replace(form, "<TMP>")
    # generate_image without a project names one after the clock; pin the shape, not the minute
    text = _DEFAULT_TITLE.sub("gflow-cli <Mon D, HH:MM AM>", text)
    # a result repr names the platform's Path class; the contract is the path, not the OS
    return json.loads(text.replace("WindowsPath(", "Path(").replace("PosixPath(", "Path("))


def _outcome(exc: Exception) -> dict[str, Any]:
    out: dict[str, Any] = {"raise": type(exc).__name__, "message": str(exc)}
    problem = getattr(exc, "to_problem_details", None)
    if callable(problem):
        out["problem"] = problem()
    return out


def _files(tmp: Path) -> list[str]:
    return sorted(
        f"{p.relative_to(tmp).as_posix()}:{p.stat().st_size}" for p in tmp.rglob("*") if p.is_file()
    )


def _client(tmp: Path) -> FlowApiClient:
    return FlowApiClient(tmp / "profile", settings=Settings(_env_file=None))  # pyright: ignore[reportCallIssue]


async def traffic_contract(tmp: Path) -> dict[str, Any]:
    client = _client(tmp)
    page = _Page()
    client._page = page  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    client._page_queue = None  # pyright: ignore[reportPrivateUsage]
    client._context = None  # pyright: ignore[reportPrivateUsage]
    client._access_token = "ya29.test"  # noqa: S105  # pyright: ignore[reportPrivateUsage]
    client._access_token_exp = 9_999_999_999  # pyright: ignore[reportPrivateUsage]
    # extend reads the model catalogue once per session; seed it so no browser context is needed
    client._extend_listing_cache[PROJECT] = json.loads(LISTING.read_text(encoding="utf-8"))  # pyright: ignore[reportPrivateUsage]

    async def _token(*_a: Any, **_k: Any) -> str:
        return "recaptcha-test"

    # The instance patch every reCAPTCHA-gated test relies on (S1 in the scenario table).
    client._mint_recaptcha_token = _token  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

    out: dict[str, Any] = {}
    for surface, name, kwargs in _cases(tmp):
        page.request.log.clear()
        before = set(_files(tmp))
        with structlog.testing.capture_logs() as events:
            try:
                result: Any = {"ok": repr(await getattr(client, name)(**kwargs))}
            except Exception as exc:  # noqa: BLE001 - the exception IS the observed outcome
                result = _outcome(exc)
        out[f"{surface}.{name}"] = {
            "traffic": page.request.log[:],
            "result": result,
            "files_written": [f for f in _files(tmp) if f not in before],
            "events": [{k: _body(v) for k, v in e.items()} for e in events],
        }
    return _normalise(out, tmp)


def instance_attributes(tmp: Path) -> list[str]:
    return sorted(vars(_client(tmp)))


async def snapshot(tmp: Path) -> dict[str, Any]:
    return {
        "methods": method_contract(),
        "instance_attributes": instance_attributes(tmp),
        "module_names": names_contract(),
        "calls": await traffic_contract(tmp),
    }


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("GFLOW_CLI_") and key not in _KEEP_ENV:
            monkeypatch.delenv(key)


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_method_set_and_signatures_are_unchanged(expected: dict[str, Any]) -> None:
    assert method_contract() == expected["methods"]


def test_instance_attributes_are_unchanged(expected: dict[str, Any], tmp_path: Path) -> None:
    assert instance_attributes(tmp_path) == expected["instance_attributes"]


def test_every_name_read_from_the_old_path_is_still_there(expected: dict[str, Any]) -> None:
    assert names_contract() == expected["module_names"]


async def test_facade_calls_send_the_same_traffic_and_return_the_same_outcome(
    expected: dict[str, Any], tmp_path: Path
) -> None:
    got = await traffic_contract(tmp_path)
    assert list(got) == list(expected["calls"])
    for key in got:
        assert got[key] == expected["calls"][key], key


@pytest.mark.skipif(not os.environ.get("RECORD_FACADE_CONTRACT"), reason="re-records on request")
async def test_record_the_fixture(tmp_path: Path) -> None:
    text = json.dumps(await snapshot(tmp_path), indent=1, sort_keys=True, ensure_ascii=False)
    FIXTURE.write_text(text + "\n", encoding="utf-8", newline="\n")
