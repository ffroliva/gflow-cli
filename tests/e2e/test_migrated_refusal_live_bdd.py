"""E2E: a refusal flow.google.com states on the wire is reported by name (#906, #873).

Binds ``tests/features/migrated_refusal_live.feature``. Selected by ``-m e2e_image``;
see ``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e.** The offline scenarios pin the envelope we captured on 2026-09-27. Only a
real submit proves Flow still refuses that way — a mocked page asserts our parser, never
Google's reply.

**How a refusal is provoked on demand.** The ``ogiZ0b`` submit goes out with its reCAPTCHA
Enterprise token corrupted in flight, which Google refuses exactly as it refuses a
low-scoring browser: HTTP 200, gRPC 7, ``PUBLIC_ERROR_UNUSUAL_ACTIVITY`` (spike
2026-09-27-migrated-refusal-is-on-the-wire). Any retry is aborted in the browser, so one
bad token reaches Google per run.

**Cost and opt-in.** Zero credits; at most one image of daily quota if the token were
somehow accepted. But a refused token can raise the profile's WAF score for hours and make
the NEXT image test in the same session fail, so this runs only with
``GFLOW_CLI_E2E_RUN_REFUSAL=1`` — use a probe profile, not your working one.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

import pytest
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.errors import WafRejectionError

scenarios("../features/migrated_refusal_live.feature")

_RUN_REFUSAL_ENV = "GFLOW_CLI_E2E_RUN_REFUSAL"
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{1500,}")


def _corrupt_token(body: str) -> str | None:
    """``body`` with the reCAPTCHA token in ``f.req`` corrupted, or ``None`` if absent."""
    pairs = parse_qsl(body, keep_blank_values=True)
    for i, (key, value) in enumerate(pairs):
        tokens = sorted(_TOKEN.findall(value), key=len, reverse=True) if key == "f.req" else []
        if tokens:
            tok = tokens[0]
            pairs[i] = (key, value.replace(tok, tok[:20] + tok[20:][::-1]))
            return urlencode(pairs)
    return None


@pytest.fixture
def world(e2e_profile_dir: Path) -> dict[str, Any]:
    if os.environ.get(_RUN_REFUSAL_ENV) != "1":
        pytest.skip(
            f"Set {_RUN_REFUSAL_ENV}=1 to run: it sends one corrupted reCAPTCHA token, "
            "which can raise the profile's WAF score for later image tests."
        )
    return {"profile": e2e_profile_dir, "submits": []}


@given("an image submit whose reCAPTCHA token is corrupted on its way to Flow")
def _armed(world: dict[str, Any]) -> None:
    async def route(r: Any, req: Any) -> None:
        body = req.post_data or ""
        if (mc.url_rpcid(req.url) or mc.body_rpcid(body)) != mc.IMAGE_SUBMIT_RPC:
            await r.continue_()
            return
        corrupted = _corrupt_token(body) if not world["submits"] else None
        world["submits"].append("corrupted" if corrupted else "aborted")
        if corrupted is None:
            await r.abort()
            return
        await r.continue_(post_data=corrupted)

    world["route"] = route


@when("gflow generates the image on the migrated host")
def _generate(world: dict[str, Any]) -> None:
    async def run() -> None:
        async with FlowApiClient(
            profile_dir=world["profile"], visibility=BrowserVisibility.HEADED
        ) as client:
            assert client._context is not None  # noqa: SLF001
            await client._context.route("**/batchexecute*", world["route"])  # noqa: SLF001
            try:
                await client.generate_image(
                    req=GenerateImageRequest(prompt="a calm lake at dawn, gentle mist")
                )
            except Exception as exc:  # noqa: BLE001 — the raised error is what Then checks
                world["error"] = exc

    asyncio.run(run())


@then("gflow raises a WAF rejection naming PUBLIC_ERROR_UNUSUAL_ACTIVITY")
def _refused(world: dict[str, Any]) -> None:
    err = world.get("error")
    if not world["submits"]:
        pytest.fail(f"no ogiZ0b submit was reached, so nothing was tested: {err!r}")
    assert isinstance(err, WafRejectionError), repr(err)
    assert mc.UNUSUAL_ACTIVITY_REASON in err.detail


@then("only one corrupted submit reached Flow")
def _one_bad_token(world: dict[str, Any]) -> None:
    assert world["submits"][0] == "corrupted", world["submits"]
    assert world["submits"].count("corrupted") == 1, world["submits"]
