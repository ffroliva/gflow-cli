"""Golden master characterization for ``direct_http`` before package split.

Pins every name and signature of ``gflow_cli.api.transports.direct_http`` that
the package split (Task 2) must preserve.
"""

from __future__ import annotations

import inspect
from typing import Any

from gflow_cli.api.transports import direct_http

# Every name read from gflow_cli.api.transports.direct_http by src/ and tests/
EXPECTED_NAMES = frozenset(
    {
        "DirectHttpTransport",
        "direct_can_serve",
        "run_direct_images",
        "run_direct_upload",
        "build_ogiz0b_body",
        "build_maseq_body",
        "read_project",
        "_MINT_RETRY_S",
        "_MINT_BUDGET_S",
        "_CHARACTER_READ_S",
        "_WIRE_MODEL",
    }
)

EXPECTED_SIGNATURES: dict[str, str] = {
    "DirectHttpTransport": (
        "(*, http_transport: 'httpx.AsyncBaseTransport | None' = None) -> 'None'"
    ),
    "direct_can_serve": "(request: 'GenerateImageRequest') -> 'bool'",
    # Task 10 (headless direct transport): the token source is injected, not minted here.
    "run_direct_images": (
        "(*, tokens: 'RecaptchaTokenSource', page: 'Page', profile_dir: 'Path', "
        "project_id: 'str', request: 'GenerateImageRequest', "
        "http_transport: 'httpx.AsyncBaseTransport | None' = None, "
        "page_lock: 'asyncio.Lock | None' = None) -> 'list[GeneratedImage]'"
    ),
    "run_direct_upload": (
        "(*, tokens: 'RecaptchaTokenSource', page: 'Page', profile_dir: 'Path', "
        "project_id: 'str', "
        "path: 'Path', http_transport: 'httpx.AsyncBaseTransport | None' = None) -> 'ImageRef'"
    ),
    "build_ogiz0b_body": (
        "(*, prompt: 'str', model: 'str', project_id: 'str', "
        "recaptcha_token: 'str', at_token: 'str', batch_id: 'str', "
        "entry_seeds: 'tuple[int, ...]', aspect: 'Aspect' = <Aspect.PORTRAIT: "
        "'IMAGE_ASPECT_RATIO_PORTRAIT'>, references: 'tuple[tuple[str, str], ...]' = (), "
        "characters: 'tuple[tuple[str, str], ...]' = ()) -> 'str'"
    ),
    "build_maseq_body": (
        "(*, project_id: 'str', recaptcha_token: 'str', at_token: 'str', "
        "image_bytes: 'bytes', mime: 'str', display_name: 'str', "
        "request_ids: 'tuple[str, str]') -> 'str'"
    ),
    "read_project": (
        "(page: 'Page', project_id: 'str', *, timeout_s: 'float' = 45.0) -> 'ProjectRead'"
    ),
}


def test_direct_http_reexports_every_name_callers_read() -> None:
    missing = [name for name in sorted(EXPECTED_NAMES) if not hasattr(direct_http, name)]
    assert missing == [], f"direct_http is missing expected names: {missing}"


def test_direct_http_signatures_unchanged() -> None:
    mismatches: dict[str, dict[str, Any]] = {}
    for name, expected_sig in EXPECTED_SIGNATURES.items():
        assert hasattr(direct_http, name), f"direct_http is missing callable {name}"
        obj = getattr(direct_http, name)
        actual_sig = str(inspect.signature(obj))
        if actual_sig != expected_sig:
            mismatches[name] = {"expected": expected_sig, "actual": actual_sig}
    assert mismatches == {}, f"Signature mismatches found: {mismatches}"
