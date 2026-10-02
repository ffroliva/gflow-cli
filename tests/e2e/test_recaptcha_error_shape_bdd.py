"""E2E: a reCAPTCHA mint failure is typed, and its retry flag matches the live page (#915).

Binds ``tests/features/recaptcha_error_shape.feature`` (``-m e2e_auth``, zero credits).

**Why an e2e.** The retry flag is a claim about Flow: that a page with no reCAPTCHA
script fails the same way every time, and that a mint which lost a race with a
navigation succeeds once the page settles. A mocked ``evaluate`` can only assert what we
told it; only a real page can falsify either half. Measured first by
``scripts/dev/spike_recaptcha_error_shape.py``
(``docs/superpowers/spikes/2026-10-01-recaptcha-error-shape.md``).

**What the race arm proves, and what it does not.** It drives ``TokenMinter`` directly on a
flow.google.com project page. Through the client, a flow.google.com page is refused before
the mint (``raise_if_migrated`` -> exit 36), so a user only meets the retryable shape on a
page Flow served from labs -- and no profile here is served labs (it answers 308). A
navigation destroying the page's context is Playwright behaviour, not a host's, so the
minter's flag is measured where it can be; the labs arm itself is unobserved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api._engine import mint_evaluate_kwargs
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.recaptcha import RecaptchaError, TokenMinter
from gflow_cli.errors import GFlowError, is_retryable

scenarios("../features/recaptcha_error_shape.feature")

_ROOT = "https://flow.google.com"


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


async def _settle(page: Any, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:  # noqa: BLE001 - settle is best-effort
        pass
    await page.wait_for_timeout(3000)


async def _mint(page: Any) -> str | BaseException:
    try:
        return await TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs()).mint(
            "IMAGE_GENERATION"
        )
    except Exception as exc:  # noqa: BLE001 - the failure IS the observation
        return exc


@given("a live client whose pool page is parked at about:blank", target_fixture="world")
def _blank(e2e_profile_dir: Path) -> dict[str, Any]:
    return {"profile": e2e_profile_dir, "arm": "blank"}


@given("a live client on a Flow project page", target_fixture="world")
def _project(e2e_profile_dir: Path) -> dict[str, Any]:
    return {"profile": e2e_profile_dir, "arm": "race"}


@when("the client mints a token on it")
def _mint_on_blank(world: dict[str, Any]) -> None:
    async def run() -> None:
        async with FlowApiClient(profile_dir=world["profile"], headless=False) as client:
            # Park EVERY pool page: the pool is FIFO, so with GFLOW_CLI_CONCURRENCY > 1 the
            # mint would otherwise be handed a healthy page and never fail.
            queue = client._page_queue  # noqa: SLF001
            pages = [await client._checkout_page() for _ in range(queue.qsize())]  # noqa: SLF001
            for page in pages:
                await page.goto("about:blank")
                client._checkin_page(page)  # noqa: SLF001
            try:
                await client._mint_recaptcha_token("IMAGE_GENERATION")  # noqa: SLF001
                world["failure"] = None
            except Exception as exc:  # noqa: BLE001
                world["failure"] = exc

    asyncio.run(run())


@when("a mint races a navigation of that page")
def _race(world: dict[str, Any]) -> None:
    async def run() -> None:
        async with FlowApiClient(profile_dir=world["profile"], headless=False) as client:
            project = await client.create_project(title="e2e recaptcha race (#915)")
            url = f"{_ROOT}/project/{project.project_id}"
            page = await client._checkout_page()  # noqa: SLF001
            await _settle(page, url)
            # One setup retry: the race is timing-based (3/3 in the spike, not guaranteed).
            for _ in range(2):
                mint = asyncio.create_task(_mint(page))
                await asyncio.sleep(0.05)
                nav = asyncio.create_task(
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                )
                world["failure"] = await mint
                try:
                    await nav
                except Exception:  # noqa: BLE001
                    pass
                await _settle(page, url)
                if isinstance(world["failure"], BaseException):
                    break
            world["re_mint"] = await _mint(page)
            client._checkin_page(page)  # noqa: SLF001

    asyncio.run(run())


def _typed(world: dict[str, Any]) -> RecaptchaError:
    failure = world["failure"]
    assert isinstance(failure, BaseException), f"the mint did not fail: {failure!r}"
    assert isinstance(failure, GFlowError), f"untyped: {type(failure).__name__}: {failure}"
    assert isinstance(failure, RecaptchaError), f"{type(failure).__name__}: {failure}"
    assert failure.problem_type == "https://gflow-cli.dev/errors/recaptcha-mint"
    print(f"\n[e2e] {type(failure).__name__} retryable={is_retryable(failure)}: {failure}")
    return failure


@then("the failure is a typed reCAPTCHA error that is not retryable")
def _not_retryable(world: dict[str, Any]) -> None:
    assert is_retryable(_typed(world)) is False


@then("the failure is a typed reCAPTCHA error that is retryable")
def _retryable(world: dict[str, Any]) -> None:
    assert is_retryable(_typed(world)) is True


@then("a mint on the settled page succeeds")
def _re_mint(world: dict[str, Any]) -> None:
    token = world["re_mint"]
    assert isinstance(token, str) and len(token) > 100, f"re-mint failed: {token!r}"
