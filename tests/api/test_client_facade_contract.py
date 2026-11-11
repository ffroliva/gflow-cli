"""Characterization (golden master) of the ``FlowApiClient`` facade (#1249).

Pinned BEFORE the class is split, so the split can prove it changed nothing a caller sees:

* the method set and every signature, public and the private helpers tests patch;
* every name other modules import from ``gflow_cli.api.client``;
* for one call per responsibility: the HTTP traffic it sends (verb, URL, body, header
  names - never header values), its result or exception, and the log events it emits.

The expected values live in ``fixtures/flow_client_facade.json`` and were recorded on
develop at b7fc3b3f. A change to that file is a behaviour change and must be justified.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
import structlog

import gflow_cli.api.client as client_mod
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "flow_client_facade.json"

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

# Names imported from gflow_cli.api.client by src/, tests/ and scripts/ (grep, 2026-11-11).
MODULE_NAMES = (
    "FlowApiClient",
    "FlowApiError",
    "MAX_CONCAT_B64_LEN",
    "MAX_IMAGE_BYTES",
    "MAX_UPSAMPLE_B64_LEN",
    "ProfileLease",
    "_AISANDBOX_CONTENT_TYPE",
    "_default_project_title",
    "_extract_provider_error_message",
    "_is_profile_access_denied",
    "_is_supported_image_header",
    "_is_target_closed",
    "_raise_for_non_retryable",
    "_storage_key_from_path",
    "_unwrap_trpc",
    "validate_image_file",
)

_TRPC_EMPTY = '{"result":{"data":{"json":{}}}}'
PROJECT = "0b5e0b5e-0000-4000-8000-000000000001"  # UUID-shaped: some routes validate it
MEDIA = "0b5e0b5e-0000-4000-8000-0000000000aa"
SCENE = "0b5e0b5e-0000-4000-8000-0000000000bb"
LISTING = Path(__file__).parent / "fixtures" / "project_initial_data_extend_models.json"


def _describe(obj: Any) -> str:
    if isinstance(obj, property):
        return "property"
    kind = "async" if inspect.iscoroutinefunction(obj) else "sync"
    return f"{kind} {inspect.signature(obj)}"


def method_contract() -> dict[str, str]:
    names = [n for n in dir(FlowApiClient) if not n.startswith("_")]
    names += ["__init__", "__aenter__", "__aexit__", *PATCHED_PRIVATE]
    return {n: _describe(inspect.getattr_static(FlowApiClient, n)) for n in sorted(set(names))}


class _Resp:
    def __init__(self, text: str, url: str) -> None:
        self.url = url
        self.status = 200
        self.ok = True
        self.headers: dict[str, str] = {}
        self._text = text

    async def text(self) -> str:
        return self._text

    async def json(self) -> Any:
        return json.loads(self._text)

    async def body(self) -> bytes:
        return self._text.encode()


# Per-request values Flow expects to differ on every call: a fresh uuid4 and an epoch-ms stamp.
_VOLATILE = {"batchId": "<uuid4>", "sessionId": "<epoch-ms>"}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _VOLATILE.get(k) or _scrub(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return [_scrub(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _body(data: Any) -> Any:
    if not isinstance(data, str):
        return data
    try:
        return _scrub(json.loads(data))
    except json.JSONDecodeError:
        return data


class _Request:
    def __init__(self) -> None:
        self.log: list[dict[str, Any]] = []

    def _record(self, verb: str, url: str, kw: dict[str, Any]) -> _Resp:
        self.log.append(
            {
                "verb": verb,
                "url": url,
                "data": _body(kw.get("data")),
                "headers": sorted(kw.get("headers") or {}),
                "kwargs": sorted(kw),
            }
        )
        return _Resp(_TRPC_EMPTY, url)

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


def _cases(tmp: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """(responsibility, method, kwargs): one or more calls per surface of the class."""
    return [
        ("workspace", "create_project", {"title": "first title"}),
        ("workspace", "rename_project", {"project_id": PROJECT, "new_title": "second title"}),
        ("workspace", "patch_agent_info", {"project_id": PROJECT, "enabled": True}),
        ("workspace", "get_agent_info", {"project_id": PROJECT}),
        ("workspace", "archive_workflow", {"workflow_id": "w1", "project_id": PROJECT}),
        (
            "workspace",
            "commit_workflow",
            {"workflow_id": "w1", "project_id": PROJECT, "primary_media_id": "m1"},
        ),
        ("workspace", "create_scene", {"project_id": PROJECT, "workflow_ids": ["w1"]}),
        ("workspace", "get_scene_workflows", {"scene_id": SCENE, "project_id": PROJECT}),
        (
            "generation",
            "poll_video_status",
            {
                "media_id": MEDIA,
                "project_id": PROJECT,
                "initial_delay_s": 0,
                "poll_interval": 0,
                "timeout_s": 0,
            },
        ),
        ("generation", "download_video", {"media_id": MEDIA, "out_path": tmp / "v.mp4"}),
        (
            "generation",
            "download_image",
            {
                "image": GeneratedImage(
                    media_name="m1",
                    workflow_id="w1",
                    seed=1,
                    prompt="p",
                    model_name_type=None,
                    aspect_ratio="IMAGE_ASPECT_RATIO_SQUARE",
                    fife_url="https://flow-content.google/image/m1",
                    dimensions=(1, 1),
                ),
                "out_path": tmp / "i.png",
            },
        ),
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


def _normalise(value: Any, tmp: Path) -> Any:
    text = json.dumps(value, default=repr, sort_keys=True)
    for form in (str(tmp), tmp.as_posix(), json.dumps(str(tmp))[1:-1]):
        text = text.replace(form, "<TMP>")
    # a result repr names the platform's Path class; the contract is the path, not the OS
    return json.loads(text.replace("WindowsPath(", "Path(").replace("PosixPath(", "Path("))


async def traffic_contract(tmp: Path) -> dict[str, Any]:
    client = FlowApiClient(tmp / "profile", settings=Settings(_env_file=None))  # pyright: ignore[reportCallIssue]
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
        with structlog.testing.capture_logs() as events:
            try:
                result: Any = {"ok": repr(await getattr(client, name)(**kwargs))}
            except Exception as exc:  # noqa: BLE001 - the exception IS the observed outcome
                result = {"raise": type(exc).__name__, "message": str(exc)}
        out[f"{surface}.{name}"] = {
            "traffic": page.request.log[:],
            "result": result,
            "events": [e["event"] for e in events],
        }
    return _normalise(out, tmp)


async def snapshot(tmp: Path) -> dict[str, Any]:
    return {
        "methods": method_contract(),
        "module_names": {n: type(getattr(client_mod, n)).__name__ for n in MODULE_NAMES},
        "calls": await traffic_contract(tmp),
    }


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_method_set_and_signatures_are_unchanged(expected: dict[str, Any]) -> None:
    assert method_contract() == expected["methods"]


def test_every_imported_name_is_still_importable_from_the_old_path(
    expected: dict[str, Any],
) -> None:
    got = {n: type(getattr(client_mod, n)).__name__ for n in MODULE_NAMES}
    assert got == expected["module_names"]


async def test_facade_calls_send_the_same_traffic_and_return_the_same_outcome(
    expected: dict[str, Any], tmp_path: Path
) -> None:
    got = await traffic_contract(tmp_path)
    assert list(got) == list(expected["calls"])
    for key in got:
        assert got[key] == expected["calls"][key], key
