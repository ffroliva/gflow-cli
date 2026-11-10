"""The canary's RPC-DRIFT check (#1218) and the per-build inventory archive S3 reads (#1223).

Offline: the shell and bundle are fixture strings. The live arm is the module's own
``__main__``, run by hand and pasted into the PR.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.canary import rpc_drift
from scripts.canary.rpc_drift import (
    ID_GONE,
    INCONCLUSIVE,
    MODULES_CHANGED,
    OK,
    Drift,
    archive,
    assess,
    bundle_url,
    catalog_ids,
    check,
    render,
)

_IDS = ("YhhmEf", "as29s", "Zzl0ze")
_BL = "boq_labs-ai-sandbox-frontend_20261108.05_p0"
_SCRIPT = "/_/mss/boq-labs/_/js/k=boq.X.en.abc/d=1/ct=zgms/rs=AA/m=_b"
_SHELL = (
    f'<html><script src="{_SCRIPT}"></script>'
    f'<script>WIZ_global_data = {{"cfb2h":"{_BL}","SNlM0e":"tok"}};</script></html>'
)


def _bundle(*ids: str) -> str:
    return ";".join(f'var a{i}=new _.x({{Xa:"{rpc}",Ya:1}})' for i, rpc in enumerate(ids))


def test_catalog_ids_come_from_the_wire_table() -> None:
    doc = (Path(__file__).resolve().parents[2] / "docs" / "FLOW_WIRE_RPCS.md").read_text(
        encoding="utf-8"
    )
    ids = catalog_ids(doc)
    assert {"YhhmEf", "as29s", "nzlxg", "p0UkFb"} <= set(ids)
    # The "seen but unused" list below the table is not catalogued.
    assert "UpteDb" not in ids


def test_only_live_rows_are_checked() -> None:
    # A row the maintainer marked gone or renamed stays in the table; it must not read
    # ID-GONE every night after that.
    table = (
        "| RPC id | Action | Status | Renamed from |\n"
        "|---|---|---|---|\n"
        "| `aaaaaa` | x | live | - |\n"
        "| `bbbbbb` | x | gone | - |\n"
        "| `cccccc` | x | renamed -> dddddd | - |\n"
        "| `dddddd` | x | live | `cccccc` |\n"
    )
    assert catalog_ids(table) == ("aaaaaa", "dddddd")


def test_bundle_url_swaps_the_module_segment_for_the_feature_modules() -> None:
    url = bundle_url(_SHELL)
    assert url is not None
    assert url.startswith("https://flow.google.com/_/mss/boq-labs/_/js/k=boq.X.en.abc/")
    assert url.endswith("/m=XRV0Af,wO1vlb,wj3Fuc,zxMy9c")
    assert "m=_b" not in url


def test_every_catalogued_id_present_is_ok() -> None:
    drift = assess(_IDS, _SHELL, 200, _bundle(*_IDS, "other1"))
    assert drift.state == OK
    assert drift.build == _BL
    assert drift.missing == ()
    assert drift.inventory == ("YhhmEf", "Zzl0ze", "as29s", "other1")


def test_one_id_gone_is_named_and_is_not_a_module_change() -> None:
    drift = assess(_IDS, _SHELL, 200, _bundle("YhhmEf", "Zzl0ze", "other1"))
    assert drift.state == ID_GONE
    assert drift.missing == ("as29s",)


def test_all_ids_missing_reads_as_module_list_changed() -> None:
    drift = assess(_IDS, _SHELL, 200, _bundle("other1", "other2"))
    assert drift.state == MODULES_CHANGED


def test_a_failed_bundle_fetch_reads_as_module_list_changed() -> None:
    drift = assess(_IDS, _SHELL, 404, "")
    assert drift.state == MODULES_CHANGED
    assert drift.build == _BL


@pytest.mark.parametrize(
    ("ids", "shell"),
    [
        ((), _SHELL),  # catalog parsed to nothing: 0/0 must not read as green
        (_IDS, "<html>no scripts</html>"),  # no /_/js/ script to build a URL from
        (_IDS, _SHELL.replace("cfb2h", "other")),  # no build label
    ],
)
def test_nothing_to_measure_is_inconclusive(ids: tuple[str, ...], shell: str) -> None:
    assert assess(ids, shell, 200, _bundle(*_IDS)).state == INCONCLUSIVE


def test_the_first_build_is_archived_with_no_diff(tmp_path: Path) -> None:
    drift = archive(assess(_IDS, _SHELL, 200, _bundle(*_IDS)), tmp_path, "2026-11-10T00:00:00Z")
    saved = json.loads((tmp_path / f"{_BL}.json").read_text(encoding="utf-8"))
    assert saved["build"] == _BL
    assert saved["ids"] == ["YhhmEf", "Zzl0ze", "as29s"]
    assert saved["modules"] == list(rpc_drift.FEATURE_MODULES)
    assert saved["first_seen"] == "2026-11-10T00:00:00Z"
    assert drift.previous_build is None


def test_a_new_build_reports_ids_added_and_removed_against_the_latest_one(
    tmp_path: Path,
) -> None:
    older = _SHELL.replace(_BL, "bl_old")
    newer = _SHELL.replace(_BL, "bl_mid")
    archive(assess(_IDS, older, 200, _bundle(*_IDS, "gone1")), tmp_path, "2026-11-01T00:00:00Z")
    archive(assess(_IDS, newer, 200, _bundle(*_IDS, "gone2")), tmp_path, "2026-11-05T00:00:00Z")

    drift = archive(
        assess(_IDS, _SHELL, 200, _bundle(*_IDS, "new1")), tmp_path, "2026-11-10T00:00:00Z"
    )
    # Compared with the most recently FIRST-SEEN build, not the alphabetically last one.
    assert drift.previous_build == "bl_mid"
    assert drift.added == ("new1",)
    assert drift.removed == ("gone2",)
    saved = json.loads((tmp_path / f"{_BL}.json").read_text(encoding="utf-8"))
    assert saved["previous_build"] == "bl_mid"
    assert saved["added"] == ["new1"]
    assert saved["removed"] == ["gone2"]


def test_a_build_already_archived_is_never_overwritten(tmp_path: Path) -> None:
    archive(assess(_IDS, _SHELL, 200, _bundle(*_IDS)), tmp_path, "2026-11-10T00:00:00Z")
    drift = archive(
        assess(_IDS, _SHELL, 200, _bundle(*_IDS, "late")), tmp_path, "2026-11-11T00:00:00Z"
    )
    saved = json.loads((tmp_path / f"{_BL}.json").read_text(encoding="utf-8"))
    assert saved["first_seen"] == "2026-11-10T00:00:00Z"
    assert "late" not in saved["ids"]
    assert drift.added == ()


def test_an_empty_inventory_is_never_archived(tmp_path: Path) -> None:
    # A renamed descriptor key would otherwise diff as ~261 ids removed.
    archive(assess(_IDS, _SHELL, 200, '"YhhmEf" "as29s" "Zzl0ze"'), tmp_path, "now")
    archive(assess(_IDS, _SHELL, 404, ""), tmp_path, "now")
    assert list(tmp_path.iterdir()) == []


def test_check_fetches_shell_then_bundle_without_cookies_and_archives(tmp_path: Path) -> None:
    seen: list[str] = []

    def get(url: str) -> tuple[int, str]:
        seen.append(url)
        return (200, _SHELL) if len(seen) == 1 else (200, _bundle(*_IDS))

    drift = check(tmp_path, get=get, ids=_IDS)
    assert drift.state == OK
    assert seen[0] == rpc_drift.SHELL
    assert seen[1].endswith("/m=XRV0Af,wO1vlb,wj3Fuc,zxMy9c")
    assert (tmp_path / f"{_BL}.json").exists()


def test_check_never_raises_and_publishes_only_the_error_class(tmp_path: Path) -> None:
    def get(url: str) -> tuple[int, str]:
        raise ConnectionError(f"failed to reach {url}?secret=1")

    drift = check(tmp_path, get=get, ids=_IDS)
    assert drift.state == INCONCLUSIVE
    assert drift.note == "ConnectionError"


def test_render_publishes_the_build_ids_and_diff_and_nothing_else() -> None:
    drift = Drift(
        state=ID_GONE,
        build=_BL,
        found=("YhhmEf",),
        missing=("as29s",),
        inventory=("YhhmEf", "x"),
        previous_build="bl_old",
        added=("x",),
        removed=("as29s",),
    )
    text = render(drift)
    assert "RPC-DRIFT" in text and ID_GONE in text
    assert _BL in text and "`as29s`" in text and "bl_old" in text
    assert "1/2" in text  # found / catalogued
    assert "http" not in text


def test_the_canary_appends_the_rpc_drift_section(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.canary import run_canary

    monkeypatch.setattr(rpc_drift, "check", lambda: Drift(state=ID_GONE, build=_BL))
    assert run_canary.rpc_drift_section().startswith(f"#### RPC-DRIFT {ID_GONE}")


def test_a_broken_rpc_drift_check_never_takes_the_canary_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.canary import run_canary

    def boom() -> Drift:
        raise RuntimeError("https://flow.google.com/_/js/secret")

    monkeypatch.setattr(rpc_drift, "check", boom)
    section = run_canary.rpc_drift_section()
    assert f"RPC-DRIFT {INCONCLUSIVE}" in section
    assert "RuntimeError" in section and "http" not in section


def test_a_module_change_is_never_archived(tmp_path: Path) -> None:
    # A wrong bundle can still hold descriptors; a build's file is never rewritten, so
    # archiving it would fix a wrong inventory into S3's data for good.
    drift = assess(_IDS, _SHELL, 200, _bundle("other1", "other2"))
    assert drift.state == MODULES_CHANGED
    archive(drift, tmp_path, "now")
    assert list(tmp_path.iterdir()) == []


def test_only_a_404_bundle_means_the_modules_moved() -> None:
    drift = assess(_IDS, _SHELL, 503, "")
    assert drift.state == INCONCLUSIVE
    assert "503" in drift.note


def test_a_corrupt_prior_archive_does_not_hide_the_verdict(tmp_path: Path) -> None:
    (tmp_path / "bl_old.json").write_text(
        json.dumps({"build": "bl_old", "first_seen": "2026-11-01", "ids": 5}), encoding="utf-8"
    )

    def get(url: str) -> tuple[int, str]:
        return (200, _SHELL) if url == rpc_drift.SHELL else (200, _bundle(*_IDS))

    assert check(tmp_path, get=get, ids=_IDS).state == OK


def test_main_publishes_the_section_on_an_auth_expired_night(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.canary import run_canary

    monkeypatch.setattr(run_canary, "probe_auth", lambda profile: False)
    monkeypatch.setattr(rpc_drift, "check", lambda: Drift(state=ID_GONE, build=_BL))
    monkeypatch.setattr("sys.argv", ["run_canary.py", "--profile", "x", "--dry-run"])
    assert run_canary.main() == 0
    out = capsys.readouterr().out
    assert "[AUTH-EXPIRED] gflow nightly canary" in out
    assert f"#### RPC-DRIFT {ID_GONE}" in out
