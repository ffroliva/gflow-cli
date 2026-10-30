"""Pytest plugin: count non-function fixture setups. Load with `-p pytest_speed.rebuild_log`."""

import json
import os
from collections import Counter, defaultdict

import pytest


def pytest_addoption(parser):
    parser.addoption("--rebuild-log", default=None, help="write non-function fixture setup counts")


def _target(config):
    path = config.getoption("--rebuild-log")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker and getattr(config.option, "dist", "no") != "no":
        return None  # the xdist controller runs no tests; only PATH.gw* hold data
    return f"{path}.{worker}" if path and worker else path


def pytest_configure(config):
    config._rebuild_setups = Counter()
    config._rebuild_users = defaultdict(set)
    config._rebuild_per_file = Counter()


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    yield
    scope = fixturedef.scope
    if scope == "function" or not request.config.getoption("--rebuild-log"):
        return
    key = f"{fixturedef.argname}@{scope}"
    request.config._rebuild_setups[key] += 1
    if scope == "module":
        request.config._rebuild_per_file[(key, request.node.nodeid.split("::")[0])] += 1


def pytest_runtest_call(item):
    config = item.config
    for name in getattr(item, "fixturenames", ()):
        for fixturedef in item._fixtureinfo.name2fixturedefs.get(name, ())[-1:]:
            if fixturedef.scope != "function":
                config._rebuild_users[f"{name}@{fixturedef.scope}"].add(item.nodeid)


def pytest_sessionfinish(session):
    config = session.config
    path = _target(config)
    if not path:
        return
    data = {
        key: {"setups": count, "tests": len(config._rebuild_users[key])}
        for key, count in sorted(config._rebuild_setups.items())
    }
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=1)


def pytest_terminal_summary(terminalreporter, config):
    repeated = [(k, n) for k, n in config._rebuild_per_file.items() if n > 1]
    if not repeated:
        return
    terminalreporter.section("module fixtures set up more than once in one file")
    for (key, file), count in sorted(repeated):
        terminalreporter.write_line(f"{count:4}x {key}  {file}")
