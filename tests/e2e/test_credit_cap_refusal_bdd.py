"""Live contract checks for #1355's Flow quote and pre-submit credit cap.

Binds ``tests/features/credit_cap_refusal.feature``. The two refusal cases cost $0;
the compliant case spends about four Veo credits and is explicitly opt-in.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli import config
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.cli import main
from gflow_cli.mcp import tools as mcp_tools

scenarios("../features/credit_cap_refusal.feature")

_PROJECT_ENV = "GFLOW_CLI_E2E_PROJECT"
_PROMPT = "a single red paper kite above a quiet beach"
_VIDEO_RPCS = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif")


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


def _balance(env: dict[str, str], profile: str) -> int:
    config.reset_settings()
    result = CliRunner().invoke(main, ["credits", "user", "--profile", profile, "--json"], env=env)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["credits"]


@given("a live classic Flow project served by flow.google.com")
def _live_classic_project(
    world: dict[str, Any], e2e_profile_dir: Path, e2e_env: dict[str, str]
) -> None:
    project = os.environ.get(_PROJECT_ENV, "").strip()
    if not project:
        pytest.skip(f"set {_PROJECT_ENV} to an existing flow.google.com project")
    profile = e2e_profile_dir.name.removeprefix("profile_")
    world.update(project=project, profile=profile, profile_dir=e2e_profile_dir, env=e2e_env)

    async def check_composer() -> None:
        transport = UiAutomationTransport()
        try:
            await transport.setup(e2e_profile_dir)
            page = transport._page  # noqa: SLF001 - confirm the live served composer
            assert page is not None
            if "flow.google.com" not in page.url:
                pytest.skip(f"Flow served {page.url!r}; this scenario needs flow.google.com")
            kind = await MigratedComposer().ensure_editor(page, project)
            if "flow.google.com" not in page.url:
                pytest.skip(f"Flow served {page.url!r}; this scenario needs flow.google.com")
            if kind != "classic":
                pytest.skip(
                    "the project is on the agent-only composer, not the measured classic pane"
                )
        finally:
            await transport.teardown()

    asyncio.run(check_composer())
    world["balance_before"] = _balance(e2e_env, profile)


@given("a Playwright route backstop that aborts video generation submissions")
def _install_route_backstop(monkeypatch: pytest.MonkeyPatch, world: dict[str, Any]) -> None:
    original = UiAutomationTransport.setup
    attempts: list[str] = []

    async def guarded_setup(
        transport: UiAutomationTransport, profile_dir: Path, *, page: Any = None
    ) -> None:
        await original(transport, profile_dir, page=page)
        active = transport._page  # noqa: SLF001 - install a real browser route guard
        assert active is not None

        async def guard(route: Any) -> None:
            request = route.request
            body = request.post_data or ""
            marker = next((rpc for rpc in _VIDEO_RPCS if rpc in request.url + body), None)
            if marker is not None:
                attempts.append(marker)
                await route.abort()
            else:
                await route.continue_()

        await active.route("**/batchexecute**", guard)

    monkeypatch.setattr(UiAutomationTransport, "setup", guarded_setup)
    world["submission_attempts"] = attempts


def _run_cli_video(world: dict[str, Any], cap: int, resolution: str | None = None) -> None:
    config.reset_settings()
    args: list[str] = [
        "video",
        "t2v",
        _PROMPT,
        "--project",
        world["project"],
        "--model",
        "omni-1.2",
        "--duration",
        "4",
        "--count",
        "1",
    ]
    if resolution is not None:
        args += ["--resolution", resolution]
    args += ["--max-credits", str(cap), "--json"]
    world["cli"] = CliRunner().invoke(main, args, env=world["env"])


@when(
    parsers.parse(
        "the operator requests an omni-1.2 4-second x1 360p video with max_credits {cap:d}"
    )
)
def _cli_video(world: dict[str, Any], cap: int) -> None:
    _run_cli_video(world, cap, resolution="360p")


@when(parsers.parse("MCP requests an omni-1.2 4-second x1 360p video with max_credits {cap:d}"))
def _mcp_video(monkeypatch: pytest.MonkeyPatch, world: dict[str, Any], cap: int) -> None:
    for key, value in world["env"].items():
        monkeypatch.setenv(key, value)
    config.reset_settings()
    world["mcp"] = asyncio.run(
        mcp_tools.gflow_generate_video(
            prompt=_PROMPT,
            mode="t2v",
            aspect="9:16",
            model="omni-1.2",
            duration=4,
            resolution="360p",
            count=1,
            max_credits=cap,
            profile=world["profile"],
            project=world["project"],
            wait=True,
        )
    )


@given("a live classic Flow project served by flow.google.com with paid video E2E enabled")
def _paid_video_enabled(
    world: dict[str, Any], e2e_profile_dir: Path, e2e_env: dict[str, str]
) -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend about four Veo credits")
    _live_classic_project(world, e2e_profile_dir, e2e_env)


@when("the operator requests an omni-1.2 4-second x1 video with max_credits 4")
def _cli_video_at_quote(world: dict[str, Any]) -> None:
    _run_cli_video(world, 4)


@then("the CLI returns CreditCapExceededError with exit code 45 and charged no")
def _cli_refusal(world: dict[str, Any]) -> None:
    result = world["cli"]
    assert result.exit_code == 45, result.output
    world["error"] = json.loads(result.stdout)["error"]
    assert world["error"]["class"] == "CreditCapExceededError"
    assert world["error"]["charged"] == "no"
    assert world["error"]["next_action"] == "fix_input"


@then("the MCP tool returns CreditCapExceededError with exit code 45 and charged no")
def _mcp_refusal(world: dict[str, Any]) -> None:
    result = world["mcp"]
    assert result["status"] == "failed", result
    world["error"] = result["error"]
    assert world["error"]["type"] == "https://gflow-cli.dev/errors/credit-cap-exceeded"
    assert world["error"]["exit_code"] == 45
    assert world["error"]["charged"] == "no"
    assert world["error"]["next_action"] == "fix_input"


@then(parsers.parse("the quote is {quoted:d} and the max_credits value is {cap:d}"))
def _quote_matches(world: dict[str, Any], quoted: int, cap: int) -> None:
    assert world["error"]["quoted"] == quoted
    assert world["error"]["max_credits"] == cap


@then("no video generation submission was attempted")
def _no_submit(world: dict[str, Any]) -> None:
    assert world["submission_attempts"] == []


@then("the profile credit balance is unchanged")
def _balance_unchanged(world: dict[str, Any]) -> None:
    assert _balance(world["env"], world["profile"]) == world["balance_before"]


@then("the CLI completes and downloads a non-empty MP4")
def _paid_success(world: dict[str, Any]) -> None:
    result = world["cli"]
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    output = Path(data["local_path"])
    assert output.is_file() and output.stat().st_size > 0
