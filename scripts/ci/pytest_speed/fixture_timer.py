"""Pytest plugin: time every fixture setup. Load with `-p pytest_speed.fixture_timer`."""

import json
import os
import time

import pytest

_TOP = 15


def pytest_addoption(parser):
    parser.addoption("--fixture-times", default=None, help="write fixture setup times (JSON lines)")


def _target(config):
    path = config.getoption("--fixture-times")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker and getattr(config.option, "dist", "no") != "no":
        return None  # the xdist controller runs no tests; only PATH.gw* hold data
    return f"{path}.{worker}" if path and worker else path


def pytest_configure(config):
    path = _target(config)
    config._fixture_times = []
    config._fixture_times_file = open(path, "w", encoding="utf-8") if path else None  # noqa: SIM115


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    file = request.config._fixture_times_file
    if file is None:
        yield
        return
    start = time.perf_counter()
    yield
    row = {
        "fixture": fixturedef.argname,
        "scope": fixturedef.scope,
        "seconds": time.perf_counter() - start,
        "nodeid": request.node.nodeid,
    }
    request.config._fixture_times.append(row)
    file.write(json.dumps(row) + "\n")


def pytest_terminal_summary(terminalreporter, config):
    rows = sorted(config._fixture_times, key=lambda r: r["seconds"], reverse=True)[:_TOP]
    if not rows:
        return
    terminalreporter.section(f"{len(rows)} slowest fixture setups")
    for r in rows:
        terminalreporter.write_line(
            f"{r['seconds']:8.3f}s {r['scope']:8} {r['fixture']}  {r['nodeid']}"
        )


def pytest_unconfigure(config):
    file = getattr(config, "_fixture_times_file", None)
    if file is not None:
        file.close()
