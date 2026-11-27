"""The HTrJv fixture recorder keeps family fields and usage keys only (#1375, #1550)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api import flow_wire
from gflow_cli.config import get_settings
from gflow_cli.errors import NetworkError, RateLimitError
from scripts.dev import record_htrjv_fixture
from scripts.dev.record_htrjv_fixture import trim


def test_trim_keeps_family_fields_and_usage_keys_only() -> None:
    usage = ["veo_3_1_t2v_fast", 20, [8], "a cost and limits gflow does not record"]
    family = ["Veo 3.1 - Fast", [usage], True, "veo_3_1_fast", None, None, [2026, 12, 5], "x"]

    assert trim([[family], "other reply data"]) == [
        ["Veo 3.1 - Fast", [["veo_3_1_t2v_fast"]], True, "veo_3_1_fast", None, None, [2026, 12, 5]]
    ]


def test_trim_of_a_reply_with_no_families_is_empty() -> None:
    assert trim([[1, 2], None]) == []


def test_reads_off_names_the_variable_that_turns_them_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["record", "--profile", "p"])
    off = get_settings().model_copy(update={"wire_reads": False})
    monkeypatch.setattr(record_htrjv_fixture, "get_settings", lambda: off)

    with pytest.raises(SystemExit, match="GFLOW_CLI_WIRE_READS=1"):
        record_htrjv_fixture.main()


@pytest.mark.parametrize(
    "error",
    [
        RateLimitError("HTTP 429", status=429, route="credits_wire", retry_after=60.0),
        NetworkError("HTTP 503", status=503, route="credits_wire"),
    ],
)
def test_a_throttled_read_exits_with_the_status_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    async def raises(_profile_dir: Path) -> object:
        raise error

    monkeypatch.setattr("sys.argv", ["record", "--profile", "p", "--out", str(tmp_path / "f")])
    on = get_settings().model_copy(update={"wire_reads": True})
    monkeypatch.setattr(record_htrjv_fixture, "get_settings", lambda: on)
    monkeypatch.setattr(flow_wire, "read_model_catalog", raises)
    monkeypatch.setattr(flow_wire, "_TOTAL_S", flow_wire._TOTAL_S)  # restored after the test

    with pytest.raises(SystemExit, match=str(error)):
        record_htrjv_fixture.main()
    assert flow_wire._TOTAL_S == 15.0  # the one-off read keeps its 15 s budget
