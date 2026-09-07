"""The shared ``e2e_env`` fixture must not hand its subprocess a redirected home.

`tests/conftest.py::_isolate_settings` is autouse and points `GFLOW_CLI_HOME` at an
empty per-test tmp dir, so unit tests never touch real user data. `e2e_env` builds its
subprocess environment with `os.environ.copy()`, which inherits that redirect — and a
`gflow` child launched with it cannot see any real profile. It exits 2 with
``No session for profile '<name>'. Run gflow auth login first.`` while the profile is
demonstrably valid and in use by every other test in the same run.

Six e2e modules already work around this individually
(`test_asset_tagging_e2e.py`, `test_json_output_e2e.py`, `test_image_uuid_ref_e2e.py`,
`test_transports_e2e.py`, `test_chain_e2e.py`, `test_i2v_flags_e2e.py`), each popping
`GFLOW_CLI_HOME` themselves. `tests/e2e/conftest.py::e2e_env` was the one shared fixture
that did not, so any test depending on it alone could never pass —
`test_data_layer_e2e.py::test_t2i_records_full_provenance` was failing exactly this way
in the 2026-09-07 sweep.

Zero cost: no browser, no network, no generation. It reads a dict.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


def test_e2e_env_does_not_inherit_the_isolated_home(e2e_env: dict[str, str]) -> None:
    assert "GFLOW_CLI_HOME" not in e2e_env, (
        "e2e_env inherited GFLOW_CLI_HOME from the autouse _isolate_settings fixture; "
        "a gflow subprocess launched with this env resolves an empty home and cannot "
        "find any real profile (exit 2, 'No session for profile')"
    )


def test_e2e_env_still_isolates_the_things_it_is_supposed_to(
    e2e_env: dict[str, str],
) -> None:
    """Popping the home must not weaken the isolation the fixture exists for: the DB and
    the output directory still have to point into ``tmp_path``, or a subprocess test
    would write into the developer's real catalog."""
    assert "GFLOW_CLI_DB_PATH" in e2e_env
    assert "GFLOW_CLI_OUTPUT_DIR" in e2e_env
    assert e2e_env["GFLOW_CLI_PROFILE"]
