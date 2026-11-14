"""Characterization (golden master) of ``MigratedComposer`` before it is split (#1249 wave 2).

Pinned BEFORE the split, so the split can prove it moved code and changed nothing a caller sees:

* every attribute ``MigratedComposer`` resolves through its class chain, with its
  signature and a hash of its source. A move keeps the body byte-identical, so the hash holds;
  any edit to a body, a decorator or a signature changes it;
* every public module-level function of ``migrated_composer`` keeps its signature and source hash;
* every global name a method or module function reads still resolves where it now lives;
* tests that patch a module global on ``migrated_composer`` reach the reader (reach guard S1);
* the composer's class chain starts with ``MigratedComposer``;
* every name src/, tests/ and scripts/ read from ``migrated_composer`` is still importable.

Behaviour (DOM calls, traffic, outcomes) stays pinned by the existing transport suites, which
drive the transport against fake pages. The expected values live in
``fixtures/migrated_composer_contract.json``, first recorded on develop; each later re-record
names its reason in the commit. A change to that file is a behaviour change and must be justified.
Re-record it with:

    RECORD_MIGRATED_COMPOSER_CONTRACT=1 uv run pytest -k record \
        tests/api/transports/test_migrated_composer_contract.py
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
import typing
from pathlib import Path
from typing import Any

import pytest

import gflow_cli.api.transports.migrated_composer as mc_mod
from gflow_cli.api.transports._mc_editor_gate import EditorGateMixin
from gflow_cli.api.transports._mc_media import MediaMixin
from gflow_cli.api.transports._mc_mention import MentionMixin
from gflow_cli.api.transports._mc_settings_pane import SettingsPaneMixin
from gflow_cli.api.transports._mc_submit_observe import SubmitObserveMixin
from gflow_cli.api.transports._mc_upload_frame import UploadFrameMixin
from gflow_cli.api.transports.migrated_composer import MigratedComposer

FIXTURE = Path(__file__).parent / "fixtures" / "migrated_composer_contract.json"
PARTS = (
    "common",
    "media",
    "editor_gate",
    "settings_pane",
    "upload_frame",
    "mention",
    "submit_observe",
)

MC_MODULES = frozenset(
    {
        "gflow_cli.api.transports.migrated_composer",
        *(f"gflow_cli.api.transports._mc_{p}" for p in PARTS),
    }
)

MODULE_NAMES = (
    "ABORTED_BEFORE_SEND",
    "AGENT_MODE_CHIP",
    "AGENT_MODE_CHIP_ANY",
    "AGENT_ONLY_ANCHOR",
    "AGENT_ONLY_EARLY_S",
    "AGENT_ONLY_RELOADS",
    "ANCHOR_TIMEOUT_S",
    "ASPECT_LIGATURE",
    "BATCHEXECUTE_GLOB",
    "BOUND_CHIP",
    "COMPOSER",
    "COOKIE_BAR",
    "COOKIE_BAR_ACCEPT",
    "COOKIE_BAR_REJECT",
    "CREATE_PROJECT_RPC",
    "CREDITS_RPC",
    "CREDITS_WARNING",
    "DIALOG",
    "EMPTY_CHIP",
    "EXISTING_REF_WAIT_S",
    "FRAMES_LIGATURE",
    "FRAME_SEARCH_ATTEMPTS",
    "IMAGE_ASPECT_LIGATURE",
    "IMAGE_ASPECT_LIGATURE_MEASURED",
    "IMAGE_MODEL_MENU_MATCHERS",
    "IMAGE_SUBMIT_RPC",
    "INTERPOLATION_SUBMIT_RPC",
    "LISTING_RPC",
    "MENTION_CHIP",
    "MENU_ITEM",
    "MIGRATED_PROJECT_URL",
    "MIGRATED_ROOT_URL",
    "MODEL_KEY",
    "MigratedComposer",
    "ModelMenuMatcher",
    "NEW_PROJECT_BUTTON",
    "OVERLAY",
    "PAGE_RPC_TIMEOUT_S",
    "PANE_CLOSE_ESCAPES",
    "PICKER",
    "PICKER_OPTION",
    "PICKER_SEARCH",
    "PROJECT_TITLE_INPUT",
    "PlaywrightTimeoutError",
    "RADIO",
    "RADIOGROUP",
    "READY_ANCHOR",
    "RENAME_PROJECT_RPC",
    "STATUS_RPCS",
    "SUBMIT_RPCS",
    "UNUSUAL_ACTIVITY_REASON",
    "UPLOAD_RPC",
    "UUID_RE",
    "VIDEO_MODEL_MENU_LABELS",
    "VIDEO_MODEL_MENU_MATCHERS",
    "_GRID_TOKEN_JS",
    "_I2V_FIRST_LAST_TOKEN",
    "_OPTION_TITLES_JS",
    "_OPTION_TOKENS_JS",
    "_duration",
    "_first_uuid",
    "_guard_image_submit",
    "_i2v_body_problem",
    "_image_body_problem",
    "_image_body_references",
    "_interpolation_body_problem",
    "_ligature",
    "_navigated_project_id",
    "_project_snapshot",
    "_r2v_body_problem",
    "_raise_if_out_of_credits",
    "_recovery_detail",
    "_rpc_reply",
    "_unique_display_name",
    "_unported_form",
    "_unported_image_form",
    "_wait_anchor",
    "body_rpcid",
    "create_project",
    "migrated_can_serve",
    "migrated_images_prefer",
    "read_credits",
    "rename_project",
    "request_body",
    "run_images",
    "run_video",
    "url_rpcid",
)

PATCHED_GLOBAL_READERS: dict[tuple[str, str], str] = {
    (
        "EditorGateMixin.ensure_editor",
        "AGENT_MODE_CHIP",
    ): "gflow_cli.api.transports._mc_editor_gate",
    (
        "EditorGateMixin.ensure_editor_for",
        "AGENT_ONLY_RELOAD_WAIT_S",
    ): "gflow_cli.api.transports._mc_editor_gate",
    (
        "EditorGateMixin._is_agent_only_composer",
        "AGENT_ONLY_SETTLE_S",
    ): "gflow_cli.api.transports._mc_editor_gate",
    (
        "MentionMixin.reference_existing",
        "EXISTING_REF_WAIT_S",
    ): "gflow_cli.api.transports._mc_mention",
    (
        "UploadFrameMixin._pick_frame_by_name",
        "FRAME_COMMIT_GRACE_S",
    ): "gflow_cli.api.transports._mc_upload_frame",
    (
        "UploadFrameMixin._pick_frame_by_name",
        "FRAME_COMMIT_HIDDEN_S",
    ): "gflow_cli.api.transports._mc_upload_frame",
    (
        "UploadFrameMixin._open_frame_picker",
        "FRAME_PICKER_OPEN_S",
    ): "gflow_cli.api.transports._mc_upload_frame",
    (
        "UploadFrameMixin._pick_frame_by_name",
        "FRAME_SEARCH_RETRY_PAUSE_S",
    ): "gflow_cli.api.transports._mc_upload_frame",
    (
        "UploadFrameMixin._pick_frame_by_name",
        "FRAME_THUMB_VISIBLE_S",
    ): "gflow_cli.api.transports._mc_upload_frame",
    (
        "UploadFrameMixin._upload_via_toolbar",
        "FRAME_UPLOAD_S",
    ): "gflow_cli.api.transports._mc_upload_frame",
    ("_recover_images", "IMAGE_RECOVERY_S"): "gflow_cli.api.transports._mc_common",
    ("_recover_images", "IMAGE_RECOVERY_SETTLE_S"): "gflow_cli.api.transports._mc_common",
    (
        "SubmitObserveMixin.submit_images_and_observe",
        "IMAGE_REPLY_BUDGET_S",
    ): "gflow_cli.api.transports._mc_submit_observe",
    ("run_video", "MigratedComposer"): "gflow_cli.api.transports.migrated_composer",
    ("run_images", "MigratedComposer"): "gflow_cli.api.transports.migrated_composer",
    ("MediaMixin.clip_bytes", "MigratedComposer"): "gflow_cli.api.transports._mc_media",
    (
        "SubmitObserveMixin._await_terminal",
        "RESULT_URL_GRACE_S",
    ): "gflow_cli.api.transports._mc_submit_observe",
    (
        "SubmitObserveMixin.submit_and_observe",
        "SUBMIT_ENABLE_BUDGET_S",
    ): "gflow_cli.api.transports._mc_submit_observe",
    (
        "SubmitObserveMixin.submit_and_observe",
        "SUBMIT_ENABLE_POLL_S",
    ): "gflow_cli.api.transports._mc_submit_observe",
    (
        "SubmitObserveMixin.submit_and_observe",
        "SUBMIT_REPLY_BUDGET_S",
    ): "gflow_cli.api.transports._mc_submit_observe",
    ("create_project", "_project_snapshot"): "gflow_cli.api.transports.migrated_composer",
    ("rename_project", "_project_snapshot"): "gflow_cli.api.transports.migrated_composer",
    (
        "UploadFrameMixin._upload_via_toolbar",
        "_project_snapshot",
    ): "gflow_cli.api.transports._mc_upload_frame",
    (
        "SubmitObserveMixin.submit_and_observe",
        "_submit_body_problem",
    ): "gflow_cli.api.transports._mc_submit_observe",
    (
        "UploadFrameMixin._upload_via_toolbar",
        "_unique_display_name",
    ): "gflow_cli.api.transports._mc_upload_frame",
    ("run_images", "_unported_image_form"): "gflow_cli.api.transports.migrated_composer",
    ("create_project", "_wait_anchor"): "gflow_cli.api.transports.migrated_composer",
    ("create_project", "rename_project"): "gflow_cli.api.transports.migrated_composer",
    ("_project_snapshot", "time"): "gflow_cli.api.transports._mc_common",
}

_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def _function(obj: Any) -> types.FunctionType | None:
    """The plain function behind a method, staticmethod, classmethod or property getter."""
    if isinstance(obj, (staticmethod, classmethod)):
        obj = obj.__func__
    if isinstance(obj, property):
        obj = obj.fget
    return obj if isinstance(obj, types.FunctionType) else None


def _clean_repr(obj: Any) -> str:
    if isinstance(obj, types.ModuleType):
        return f"module {obj.__name__}"
    if isinstance(obj, frozenset):
        return f"frozenset({sorted(obj)!r})"
    if isinstance(obj, set):
        return f"set({sorted(obj)!r})"
    if isinstance(obj, type):
        return f"type: {obj.__name__}"
    if type(obj).__name__ == "BoundLoggerLazyProxy":
        return "BoundLoggerLazyProxy"
    return _ADDRESS.sub("", repr(obj))


def _describe(obj: Any) -> str:
    func = _function(obj)
    if func is None:
        if isinstance(obj, types.ModuleType):
            return f"module {obj.__name__}"
        if (
            getattr(type(obj), "__module__", "") == "typing"
            or isinstance(obj, getattr(types, "GenericAlias", ()))
            or isinstance(obj, getattr(typing, "_SpecialForm", ()))
        ):
            name = (
                getattr(obj, "_name", None)
                or getattr(obj, "__name__", None)
                or str(obj).replace("typing.", "")
            )
            return f"typing.{name}"
        return f"{type(obj).__name__}: {_clean_repr(obj)}"
    source = inspect.getsource(func).replace("\r\n", "\n")
    source = re.sub(r"[ \t]*# pyright: ignore\[reportArgumentType\]", "", source)
    digest = hashlib.sha256(source.encode()).hexdigest()[:16]
    kind = type(obj).__name__
    sig_str = _ADDRESS.sub("", str(inspect.signature(func)))
    return f"{kind} {sig_str} sha={digest}"


def _resolved() -> dict[str, Any]:
    """Every attribute ``MigratedComposer`` resolves through its class chain, first wins."""
    found: dict[str, Any] = {}
    for klass in MigratedComposer.__mro__[:-1]:  # everything but ``object``
        for name, value in vars(klass).items():
            if name.startswith("__") and _function(value) is None:
                continue
            found.setdefault(name, value)
    return found


def method_contract() -> dict[str, str]:
    return {name: _describe(value) for name, value in sorted(_resolved().items())}


def _public_functions() -> dict[str, types.FunctionType]:
    """Every public module-level function of ``migrated_composer``."""
    funcs: dict[str, types.FunctionType] = {}
    for name, val in vars(mc_mod).items():
        if (
            not name.startswith("_")
            and isinstance(val, types.FunctionType)
            and val.__module__ in MC_MODULES
        ):
            funcs[name] = val
    return funcs


def public_functions_contract() -> dict[str, str]:
    return {name: _describe(func) for name, func in sorted(_public_functions().items())}


def _describe_name(obj: Any) -> str:
    """A name's value or signature; source only for functions the composer modules define."""
    func = _function(obj)
    if func is not None and func.__module__ not in MC_MODULES:
        sig_str = _ADDRESS.sub("", str(inspect.signature(func)))
        return f"{type(obj).__name__} {sig_str}"
    return _describe(obj)


def names_contract() -> dict[str, str]:
    return {name: _describe_name(getattr(mc_mod, name)) for name in MODULE_NAMES}


def _global_reads(code: types.CodeType) -> set[str]:
    reads = {i.argval for i in dis.get_instructions(code) if i.opname == "LOAD_GLOBAL"}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            reads |= _global_reads(const)
    return reads


def _functions_to_resolve() -> list[types.FunctionType]:
    """The chain's methods and every function the composer modules define, decorators unwrapped."""
    found = [f for f in map(_function, _resolved().values()) if f is not None]
    for name in MC_MODULES:
        module = importlib.import_module(name)
        found += [
            f
            for f in vars(module).values()
            if isinstance(f, types.FunctionType) and f.__module__ == name
        ]
    return [inspect.unwrap(f) for f in found]  # pyright: ignore[reportReturnType]


def snapshot() -> dict[str, Any]:
    return {
        "methods": method_contract(),
        "public_functions": public_functions_contract(),
        "module_names": names_contract(),
    }


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_every_resolved_method_keeps_its_signature_and_source(
    expected: dict[str, Any],
) -> None:
    assert method_contract() == expected["methods"]


def test_every_public_function_keeps_its_signature_and_source(
    expected: dict[str, Any],
) -> None:
    assert public_functions_contract() == expected["public_functions"]


def test_every_name_importable_from_migrated_composer_is_still_there(
    expected: dict[str, Any],
) -> None:
    assert names_contract() == expected["module_names"]


def test_every_global_a_method_reads_still_resolves_where_it_lives() -> None:
    missing = []
    for func in _functions_to_resolve():
        for read in sorted(_global_reads(func.__code__)):
            if read not in func.__globals__ and not hasattr(builtins, read):
                missing.append(f"{func.__module__}.{func.__qualname__} reads {read}")
    assert missing == []


def test_reach_guard_for_patched_module_globals() -> None:
    """Reach guard (S1): reading a patched global resolves from the patched module."""
    mismatches = []
    matched_keys = set()
    funcs = [(f, _global_reads(f.__code__)) for f in _functions_to_resolve()]
    for (target_func_name, global_name), expected_mod in PATCHED_GLOBAL_READERS.items():
        matched = False
        for func, reads in funcs:
            if func.__qualname__ == target_func_name or func.__name__ == target_func_name:
                if global_name in reads:
                    matched = True
                    matched_keys.add((target_func_name, global_name))
                    actual_mod = func.__globals__.get("__name__")
                    if actual_mod != expected_mod:
                        mismatches.append(
                            f"{func.__qualname__} reads {global_name} from {actual_mod}, "
                            f"expected {expected_mod}"
                        )
        if not matched:
            mismatches.append(
                f"Target reader {target_func_name} reading {global_name} did not match any function"
            )
    assert matched_keys == set(PATCHED_GLOBAL_READERS.keys()), (
        f"Unmatched keys: {set(PATCHED_GLOBAL_READERS.keys()) - matched_keys}"
    )
    assert mismatches == []


def test_tests_that_patch_the_composer_class_still_reach_the_methods() -> None:
    expected_mro = (
        MigratedComposer,
        SubmitObserveMixin,
        MentionMixin,
        UploadFrameMixin,
        SettingsPaneMixin,
        EditorGateMixin,
        MediaMixin,
        object,
    )
    assert MigratedComposer.__mro__ == expected_mro


@pytest.mark.skipif(
    not os.environ.get("RECORD_MIGRATED_COMPOSER_CONTRACT"), reason="re-records on request"
)
def test_record_the_fixture() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
