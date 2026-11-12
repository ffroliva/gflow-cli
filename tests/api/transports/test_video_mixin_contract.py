"""Characterization (golden master) of ``VideoGenerationMixin`` before it is split (#1249 wave 2).

Pinned BEFORE the split, so the split can prove it moved code and changed nothing a caller sees:

* every attribute ``UiAutomationTransport`` resolves through its class chain, with its
  signature and a hash of its source. A move keeps the body byte-identical, so the hash holds;
  any edit to a body, a decorator or a signature changes it;
* every global name a method reads still resolves where the method now lives. A moved method
  that still says ``VideoGenerationMixin._x(...)`` or uses a helper its new module never
  imported fails here, not at runtime on a live account;
* the transport's class chain starts with ``VideoGenerationMixin``, so the 26 tests that patch
  a private method with ``setattr(VideoGenerationMixin, ...)`` still reach the transport;
* every name src/, tests/ and scripts/ read from ``ui_automation_video`` (AST scan,
  2026-11-12), with its value or signature.

Behaviour (DOM calls, traffic, outcomes) stays pinned by the existing transport suites, which
drive the transport against fake pages. The expected values live in
``fixtures/video_mixin_contract.json``, recorded on develop at 4143e83c. A change to that file
is a behaviour change and must be justified. Re-record it with:

    RECORD_VIDEO_MIXIN_CONTRACT=1 uv run pytest -k record
        tests/api/transports/test_video_mixin_contract.py
"""

from __future__ import annotations

import builtins
import dis
import hashlib
import importlib
import inspect
import json
import os
import re
import types
from pathlib import Path
from typing import Any

import pytest

import gflow_cli.api.transports.ui_automation_video as video_mod
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

FIXTURE = Path(__file__).parent / "fixtures" / "video_mixin_contract.json"
PARTS = ("network", "editor_mode", "settings", "picker_project", "references", "frames")

MODULE_NAMES = (
    "ADD_MEDIA_BUTTON",
    "AGENTIC_UI_INDICATORS",
    "AGENT_CHAT_PANEL_CLOSE_SELECTOR",
    "COMPOSER_AGENT_TOGGLE_SELECTOR",
    "DIALOG_ANY",
    "ENTITY_ATTACH_DRIFT_HINT",
    "FRAME_SLOTS_STRUCT",
    "LIBRARY_UI_INDICATORS",
    "MODEL_PICKER_TRIGGER",
    "MODE_SWITCH_TRIGGER_SELECTORS",
    "NON_CLASSIC_COHORT_INDICATORS",
    "PICKER_CONTEXT_INCLUDE",
    "PICKER_GRID_SCROLL_ATTEMPTS",
    "PICKER_GRID_SCROLL_STALL_LIMIT",
    "PICKER_INCLUDE_BUTTON",
    "PICKER_PERSONAGENS_TAB",
    "PICKER_PROJECT_MENU_OPEN",
    "PICKER_PROJECT_MENU_POLLS",
    "PICKER_PROJECT_MENU_POLL_MS",
    "PICKER_PROJECT_SELECTOR_TRIGGERS",
    "PICKER_SEARCH_INPUT",
    "PICKER_VOZES_TAB",
    "PINNED_PLAYWRIGHT",
    "SUPPORTED_PLAYWRIGHT_RANGE",
    "VIDEO_ASPECT_TAB_SELECTORS",
    "VIDEO_GENERATE_ROUTES",
    "VIDEO_MODEL_OPTION_SELECTORS",
    "VIDEO_SUBMODE_SELECTORS",
    "VIDEO_TAB_IN_MENU_SELECTORS",
    "VideoGenerationMixin",
    "VideoModel",
    "_EDITOR_READY_ANCHOR",
    "_GENERATION_ROUTE_RE",
    "_PICKER_PROJECT_OPTION_MATCH_JS",
    "_PICKER_PROJECT_TRIGGER_ACTIVE_JS",
    "_PROBE_TIMEOUT_MS",
    "_capture_debug_screenshot",
    "_summarize_request_image_inputs",
    "_upload_rejection_message",
    "capture_ui_diagnostics",
    "migrated_route",
    "screenshot_clause",
    "selector_drift_detail",
    "write_asset_async",
    "zip_entity_refs",
)

_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def _function(obj: Any) -> types.FunctionType | None:
    """The plain function behind a method, staticmethod, classmethod or property getter."""
    if isinstance(obj, (staticmethod, classmethod)):
        obj = obj.__func__
    if isinstance(obj, property):
        obj = obj.fget
    return obj if isinstance(obj, types.FunctionType) else None


def _describe(obj: Any) -> str:
    func = _function(obj)
    if func is None:
        return f"{type(obj).__name__}: {_ADDRESS.sub('', repr(obj))}"
    source = inspect.getsource(func).replace("\r\n", "\n")
    digest = hashlib.sha256(source.encode()).hexdigest()[:16]
    kind = type(obj).__name__
    return f"{kind} {inspect.signature(func)} sha={digest}"


def _resolved() -> dict[str, Any]:
    """Every attribute ``VideoGenerationMixin`` resolves through its class chain, first wins.

    The chain only: ``UiAutomationTransport``'s own methods are not part of the move, so an
    edit to them must not force a re-record here.
    """
    found: dict[str, Any] = {}
    for klass in VideoGenerationMixin.__mro__[:-1]:  # everything but ``object``
        for name, value in vars(klass).items():
            # Class bookkeeping (`__firstlineno__`, `__static_attributes__` exist on 3.13 only)
            # is not behaviour; dunder methods such as `__init__` are.
            if name.startswith("__") and _function(value) is None:
                continue
            found.setdefault(name, value)
    return found


def method_contract() -> dict[str, str]:
    return {name: _describe(value) for name, value in sorted(_resolved().items())}


VIDEO_MODULES = frozenset(
    {
        "gflow_cli.api.transports.ui_automation_video",
        "gflow_cli.api.transports._video_common",
        *(f"gflow_cli.api.transports._video_{p}" for p in PARTS),
    }
)


def _describe_name(obj: Any) -> str:
    """A name's value or signature; source only for functions the video modules define.

    A function re-exported from elsewhere (``storage.write_asset_async``) is pinned by
    signature, so an edit in its own module does not break this contract.
    """
    func = _function(obj)
    if func is not None and func.__module__ not in VIDEO_MODULES:
        return f"{type(obj).__name__} {inspect.signature(func)}"
    return _describe(obj)


def names_contract() -> dict[str, str]:
    return {name: _describe_name(getattr(video_mod, name)) for name in MODULE_NAMES}


def _global_reads(code: types.CodeType) -> set[str]:
    reads = {i.argval for i in dis.get_instructions(code) if i.opname == "LOAD_GLOBAL"}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            reads |= _global_reads(const)
    return reads


def snapshot() -> dict[str, Any]:
    return {"methods": method_contract(), "module_names": names_contract()}


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_every_resolved_method_keeps_its_signature_and_source(expected: dict[str, Any]) -> None:
    assert method_contract() == expected["methods"]


def test_every_name_read_from_ui_automation_video_is_still_there(
    expected: dict[str, Any],
) -> None:
    assert names_contract() == expected["module_names"]


def _functions_to_resolve() -> list[types.FunctionType]:
    """The chain's methods and every function the video modules define, decorators unwrapped."""
    found = [f for f in map(_function, _resolved().values()) if f is not None]
    for name in VIDEO_MODULES:
        module = importlib.import_module(name)
        found += [
            f
            for f in vars(module).values()
            if isinstance(f, types.FunctionType) and f.__module__ == name
        ]
    # ``@asynccontextmanager`` and friends hide the body behind a wrapper from contextlib.
    return [inspect.unwrap(f) for f in found]  # pyright: ignore[reportReturnType]


def test_every_global_a_method_reads_still_resolves_where_it_lives() -> None:
    missing = []
    for func in _functions_to_resolve():
        for read in sorted(_global_reads(func.__code__)):
            if read not in func.__globals__ and not hasattr(builtins, read):
                missing.append(f"{func.__module__}.{func.__qualname__} reads {read}")
    assert missing == []


def test_tests_that_patch_the_mixin_class_still_reach_the_transport() -> None:
    # ``setattr(VideoGenerationMixin, name, fake)`` shadows a method defined on any base of the
    # mixin, but not one the transport inherits from a class placed before the mixin.
    assert UiAutomationTransport.__mro__[1] is VideoGenerationMixin
    assert set(UiAutomationTransport.__mro__[2:]) <= set(VideoGenerationMixin.__mro__)


@pytest.mark.skipif(
    not os.environ.get("RECORD_VIDEO_MIXIN_CONTRACT"), reason="re-records on request"
)
def test_record_the_fixture() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
