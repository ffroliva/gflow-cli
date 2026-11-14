"""Characterization (golden master) of ``UiAutomationTransport`` before it is split (#1249 wave 2).

Pinned BEFORE the split, so the split can prove it moved code and changed nothing a caller sees:

* every attribute ``UiAutomationTransport`` resolves through its class chain, with its
  signature and a hash of its source. A move keeps the body byte-identical, so the hash holds;
  any edit to a body, a decorator or a signature changes it;
* every global name a method reads still resolves where the method now lives. A moved method
  that still says ``UiAutomationTransport._x(...)`` or uses a helper its new module never
  imported fails here, not at runtime on a live account;
* the MRO keeps ``VideoGenerationMixin`` ahead of the bases that receive moved methods;
* every name src/, tests/ and scripts/ read from ``ui_automation``, with its value or signature;
* Reach guard (Scenario S1): every method that reads a patched module global resolves it from
  the exact module that tests patch.

Behaviour (DOM calls, traffic, outcomes) stays pinned by the existing transport suites, which
drive the transport against fake pages. The expected values live in
``fixtures/ui_transport_contract.json``, recorded on develop. Re-record it with:

    RECORD_UI_TRANSPORT_CONTRACT=1 uv run pytest -k record
        tests/api/transports/test_ui_transport_contract.py
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

import gflow_cli.api.transports.ui_automation as ui_mod
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

FIXTURE = Path(__file__).parent / "fixtures" / "ui_transport_contract.json"
PARTS = ("session", "navigation", "prompt", "settings", "capture", "character", "image")

MODULE_NAMES = (
    "Aspect",
    "AuthExpiredError",
    "BatchPartialError",
    "BatchSubmissionResult",
    "CHANGELOG_IFRAME_SELECTORS",
    "CharacterImageRequest",
    "ConfigurationError",
    "ContentPolicyError",
    "ENTITY_ATTACH_DRIFT_HINT",
    "FLOW_URL",
    "FlowAppError",
    "GEN_SETTINGS_BUTTON_SELECTORS",
    "GFlowError",
    "GenerateImageRequest",
    "GeneratedImage",
    "IMAGE_MODEL_OPTION_SELECTORS",
    "IMAGE_MODEL_PICKER_TRIGGER",
    "IMAGE_TAB_IN_MENU_SELECTORS",
    "ImageRef",
    "MENU_ITEM",
    "MODE_SWITCH_TRIGGER_SELECTORS",
    "MentionIndexUnavailableError",
    "Model",
    "ModelMenuMatcher",
    "NEW_PROJECT_SELECTORS",
    "ONBOARDING_SELECTORS",
    "OVERLAY_CLOSE_BUTTON_SELECTORS",
    "PROMPT_FORMAT_SELECTORS",
    "PROMPT_INPUT_SELECTORS",
    "ProfileLease",
    "RateLimitError",
    "SUBMIT_BUTTON_SELECTORS",
    "TOP_BANNER_SELECTORS",
    "UiAutomationTransport",
    "UiSelectorDriftError",
    "VideoGenerationMixin",
    "WELCOME_SCREEN_SELECTORS",
    "WafRejectionError",
    "WireFormatError",
    "_ALLOWED_DOWNLOAD_HOST_SUFFIXES",
    "_ASPECT_TAB_CANDIDATES",
    "_AnyList",
    "_BLOCK_PROBE_JS",
    "_BODY_SLOT_MOUNT_POLL_MS",
    "_BODY_SLOT_MOUNT_TIMEOUT_S",
    "_BODY_TRIPTYCH_PREAMBLE",
    "_CHANGELOG_SCOPED_CLOSE",
    "_CLI_FROM_ASPECT",
    "_COUNT_TAB_COUNT",
    "_COUNT_TAB_TEXT_RE",
    "_DIAGNOSTICS_SUBDIR",
    "_EVT_COUNT_SETTER_COMPLETED",
    "_EVT_OVERLAY_DISMISSED",
    "_FORMAT_REWRITE_MIN_DELTA",
    "_FORMAT_REWRITE_POLL_MS",
    "_FORMAT_REWRITE_TIMEOUT_S",
    "_GENERIC_CLOSE",
    "_JsonObj",
    "_NEW_PROJECT_PROBE_TIMEOUT_MS",
    "_ONBOARDING_STRUCTURAL_SELECTORS",
    "_ONBOARDING_TEXT_SELECTORS",
    "_PROJECT_URL_FRAGMENT",
    "_REF_VALUE_MAX_CHARS",
    "_SUPPORTED_COUNTS",
    "_VIEWPORT",
    "_capture_debug_screenshot",
    "_collect_images_from_body",
    "_count_tab_locator_for",
    "_count_tabs_locator",
    "_elide_large_value",
    "_entity_ids_from_request_body",
    "_extract_project_id",
    "_images_from_responses",
    "_is_allowed_download_host",
    "_jitter_ms",
    "_prompt_hash_stable",
    "_reference_field_count",
    "_summarize_batch_request_body",
    "_try_iframe_close_button",
    "aspect_cli_from_enum",
    "async_playwright",
    "await_url_settled",
    "close_menu",
    "count_visible",
    "extract_project_id",
    "flow_host_kind",
    "generation_error",
    "log",
    "migrated_images_prefer",
    "migrated_route",
    "offered_menu_labels",
    "parse_retry_after",
    "raise_if_known_landing",
    "raise_if_migrated",
    "screenshot_clause",
    "selector_drift_detail",
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
    """Every attribute ``UiAutomationTransport`` resolves through its class chain, first wins."""
    found: dict[str, Any] = {}
    for klass in UiAutomationTransport.__mro__[:-1]:  # everything but ``object``
        for name, value in vars(klass).items():
            if name.startswith("__") and _function(value) is None:
                continue
            found.setdefault(name, value)
    return found


def method_contract() -> dict[str, str]:
    return {name: _describe(value) for name, value in sorted(_resolved().items())}


UI_MODULES = frozenset(
    {
        "gflow_cli.api.transports.ui_automation",
        "gflow_cli.api.transports._ui_common",
        *(f"gflow_cli.api.transports._ui_{p}" for p in PARTS),
    }
)


def _describe_name(obj: Any) -> str:
    """A name's value or signature; source only for functions the UI modules define."""
    func = _function(obj)
    if func is not None and func.__module__ not in UI_MODULES:
        return f"{type(obj).__name__} {inspect.signature(func)}"
    return _describe(obj)


def names_contract() -> dict[str, str]:
    return {name: _describe_name(getattr(ui_mod, name)) for name in MODULE_NAMES}


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


def test_every_name_read_from_ui_automation_is_still_there(
    expected: dict[str, Any],
) -> None:
    assert names_contract() == expected["module_names"]


def _functions_to_resolve() -> list[types.FunctionType]:
    """The transport's methods and every function the UI modules define, decorators unwrapped."""
    found = [f for f in map(_function, _resolved().values()) if f is not None]
    for name in UI_MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        found += [
            f
            for f in vars(module).values()
            if isinstance(f, types.FunctionType) and f.__module__ == name
        ]
    return [inspect.unwrap(f) for f in found]  # pyright: ignore[reportReturnType]


def test_every_global_a_method_reads_still_resolves_where_it_lives() -> None:
    missing = []
    for func in _functions_to_resolve():
        for read in sorted(_global_reads(func.__code__)):
            if read not in func.__globals__ and not hasattr(builtins, read):
                missing.append(f"{func.__module__}.{func.__qualname__} reads {read}")
    assert missing == []


def test_mro_keeps_video_generation_mixin_ahead_of_moved_bases() -> None:
    assert UiAutomationTransport.__mro__[1] is VideoGenerationMixin
    video_idx = UiAutomationTransport.__mro__.index(VideoGenerationMixin)
    for cls in UiAutomationTransport.__mro__:
        if cls.__module__ in UI_MODULES and cls is not UiAutomationTransport:
            assert video_idx < UiAutomationTransport.__mro__.index(cls)


# Scenario S1 (Reach guard):
# Maps each patched (method, global_name) pair to the module whose namespace the patch targets.
# When a reader moves to a new module, its patch string is retargeted AND this map is updated.
PATCHED_GLOBAL_READERS: dict[tuple[str, str], str] = {
    ("setup", "async_playwright"): "gflow_cli.api.transports.ui_automation",
    ("_is_settings_panel_open", "_count_tabs_locator"): "gflow_cli.api.transports.ui_automation",
    ("_enter_editor", "_NEW_PROJECT_PROBE_TIMEOUT_MS"): "gflow_cli.api.transports._ui_navigation",
    ("_settle_if_redirecting", "await_url_settled"): "gflow_cli.api.transports._ui_navigation",
    (
        "_locate_body_prompt_box",
        "_BODY_SLOT_MOUNT_TIMEOUT_S",
    ): "gflow_cli.api.transports._ui_prompt",
    ("_submit_body_prompt", "log"): "gflow_cli.api.transports._ui_prompt",
    ("format_character_prompt", "_FORMAT_REWRITE_TIMEOUT_S"): "gflow_cli.api.transports._ui_prompt",
    ("format_character_prompt", "_FORMAT_REWRITE_POLL_MS"): "gflow_cli.api.transports._ui_prompt",
    ("format_character_prompt", "log"): "gflow_cli.api.transports._ui_prompt",
    (
        "_select_image_model",
        "IMAGE_MODEL_OPTION_SELECTORS",
    ): "gflow_cli.api.transports.ui_automation",
    ("_select_image_model", "log"): "gflow_cli.api.transports.ui_automation",
    ("_configure_generation_settings", "log"): "gflow_cli.api.transports.ui_automation",
}


def test_patched_module_globals_reach_their_readers() -> None:
    """Every method in the UI transport chain reading a patched global gets it from that module."""
    mismatches = []
    for klass in UiAutomationTransport.__mro__:
        if klass.__module__ not in UI_MODULES:
            continue
        for name, value in vars(klass).items():
            func = _function(value)
            if func is None:
                continue
            reads = _global_reads(func.__code__)
            for (
                target_func_name,
                global_name,
            ), expected_module_name in PATCHED_GLOBAL_READERS.items():
                if name == target_func_name:
                    assert global_name in reads, f"{name} does not read {global_name}"
                    actual_module_name = func.__globals__.get("__name__")
                    if actual_module_name != expected_module_name:
                        mismatches.append(
                            f"{klass.__name__}.{name} reads '{global_name}' from "
                            f"'{actual_module_name}', but patch targets '{expected_module_name}'"
                        )
    assert mismatches == []


@pytest.mark.skipif(
    not os.environ.get("RECORD_UI_TRANSPORT_CONTRACT"), reason="re-records on request"
)
def test_record_the_fixture() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
