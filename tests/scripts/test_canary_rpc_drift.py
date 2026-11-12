"""The canary's RPC-DRIFT check (#1218) and the per-build inventory archive S3 reads (#1223).

Offline: the shell and bundle are fixture strings. The live arm is the module's own
``__main__``, run by hand and pasted into the PR.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from scripts.canary import rpc_drift
from scripts.canary.rpc_drift import (
    ID_GONE,
    INCONCLUSIVE,
    MODULES_CHANGED,
    OK,
    PARSE_CHANGED,
    CatalogError,
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
    assert "eAenfb" not in ids


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


@pytest.mark.parametrize("key", ["Wa", "W1", "$a"])
def test_a_renamed_descriptor_key_still_parses(key: str) -> None:
    # Build 20261110.00_p0 renamed the id key Xa -> Wa (#1224): the minified key is not a
    # contract, so the check learns it from the bundle instead of hard-coding it. A minified
    # name may carry a digit after its first character.
    bundle = ";".join(
        f'var b{i}={{{key}:"{rpc}",hb:void 0,Za:!0,Xa:_.tC,kb:_.GDa}};b{i}[_.QB]=!0'
        for i, rpc in enumerate((*_IDS, "other1"))
    )
    drift = assess(_IDS, _SHELL, 200, bundle + ';var z={De:"noise1",x:1}')
    assert drift.state == OK
    assert drift.inventory == ("YhhmEf", "Zzl0ze", "as29s", "other1")


def test_ids_held_in_another_shape_are_parse_changed_not_ok() -> None:
    # Build 20260929.10_p1 held ids in a constructor; unrelated {xc:"…",} objects must not be
    # taken for the descriptor list (measured: the old fallback read OK with 1 "descriptor").
    bundle = ";".join(
        f'var c{i}=new _.Gx("{rpc}",_.Mx,H,[_.dm,!0,_.cm,"/FlowService.GetMedia"])'
        for i, rpc in enumerate(_IDS)
    )
    drift = assess(_IDS, _SHELL, 200, bundle + ';var z={xc:"noise1",y:2}')
    assert drift.state == PARSE_CHANGED


def test_one_id_gone_is_named_and_is_not_a_module_change() -> None:
    drift = assess(_IDS, _SHELL, 200, _bundle("YhhmEf", "Zzl0ze", "other1"))
    assert drift.state == ID_GONE
    assert drift.missing == ("as29s",)


def test_all_ids_missing_from_a_valid_bundle_is_id_gone() -> None:
    # Descriptors parsed, so the bundle is the right one: the ids really are not in it.
    drift = assess(_IDS, _SHELL, 200, _bundle("other1", "other2"))
    assert drift.state == ID_GONE
    assert drift.missing == _IDS


def test_a_bundle_with_no_descriptors_is_never_ok() -> None:
    drift = assess(_IDS, _SHELL, 200, '"YhhmEf" "as29s" "Zzl0ze"')
    assert drift.state == PARSE_CHANGED


def test_an_id_found_in_the_base_bundle_moved_and_is_not_gone() -> None:
    drift = assess(_IDS, _SHELL, 200, _bundle("YhhmEf", "Zzl0ze"), base_text='x={Xa:"as29s"}')
    assert drift.state == OK
    assert drift.missing == ()
    assert drift.moved == ("as29s",)
    assert "not in the feature modules" in render(drift)


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
    drift = assess(_IDS, _SHELL, 200, '"YhhmEf" "as29s" "Zzl0ze"')
    assert drift.state == PARSE_CHANGED
    archive(drift, tmp_path, "now")
    archive(assess(_IDS, _SHELL, 404, ""), tmp_path, "now")
    assert list(tmp_path.iterdir()) == []


def test_check_fetches_shell_then_bundle_without_cookies_and_archives(tmp_path: Path) -> None:
    seen: list[str] = []

    def get(url: str) -> tuple[int, str, str]:
        seen.append(url)
        return (200, _SHELL, url) if len(seen) == 1 else (200, _bundle(*_IDS), url)

    drift = check(tmp_path, get=get, ids=_IDS, write=True)
    assert drift.state == OK
    assert seen[0] == rpc_drift.SHELL
    assert seen[1].endswith("/m=XRV0Af,wO1vlb,wj3Fuc,zxMy9c")
    assert (tmp_path / f"{_BL}.json").exists()


def test_check_never_raises_and_publishes_only_the_error_class(tmp_path: Path) -> None:
    def get(url: str) -> tuple[int, str, str]:
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

    monkeypatch.setattr(rpc_drift, "check", lambda **_: Drift(state=ID_GONE, build=_BL))
    assert run_canary.rpc_drift_section().startswith(f"#### RPC-DRIFT {ID_GONE}")


def test_a_broken_rpc_drift_check_never_takes_the_canary_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.canary import run_canary

    def boom(**_: object) -> Drift:
        raise RuntimeError("https://flow.google.com/_/js/secret")

    monkeypatch.setattr(rpc_drift, "check", boom)
    section = run_canary.rpc_drift_section()
    assert f"RPC-DRIFT {INCONCLUSIVE}" in section
    assert "RuntimeError" in section and "http" not in section


def test_a_module_change_is_never_archived(tmp_path: Path) -> None:
    # A wrong bundle can still hold descriptors; a build's file is never rewritten, so
    # archiving it would fix a wrong inventory into S3's data for good.
    drift = assess(_IDS, _SHELL, 404, "")
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

    def get(url: str) -> tuple[int, str, str]:
        return (200, _SHELL, url) if url == rpc_drift.SHELL else (200, _bundle(*_IDS), url)

    assert check(tmp_path, get=get, ids=_IDS, write=True).state == OK


def test_main_publishes_the_section_on_an_auth_expired_night(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.canary import run_canary

    monkeypatch.setattr(run_canary, "probe_auth", lambda profile: False)
    writes: list[bool] = []

    def spy(*, write: bool) -> Drift:
        writes.append(write)
        return Drift(state=ID_GONE, build=_BL)

    monkeypatch.setattr(rpc_drift, "check", spy)
    monkeypatch.setattr("sys.argv", ["run_canary.py", "--profile", "x", "--dry-run"])
    assert run_canary.main() == 0
    assert writes == [False]  # a dry run must not use up a new build's diff
    out = capsys.readouterr().out
    assert "[AUTH-EXPIRED] gflow nightly canary" in out
    assert f"#### RPC-DRIFT {ID_GONE}" in out


def test_live_status_matches_case_insensitively() -> None:
    table = "| RPC id | Status |\n|---|---|\n| `aaaaaa` | Live |\n| `bbbbbb` | LIVE |\n"
    assert catalog_ids(table) == ("aaaaaa", "bbbbbb")


@pytest.mark.parametrize(
    "table",
    [
        "| RPC id | Status |\n|---|---|\n| `aaaaaa` | gone |\n",  # no live row at all
        "| RPC id | Status |\n|---|---|\n| `aaaaaa` | live |\n| `bbbbbb` |\n",  # row dropped
    ],
)
def test_a_table_that_drops_rows_fails_loudly(table: str) -> None:
    with pytest.raises(CatalogError):
        catalog_ids(table)


def test_a_catalog_problem_is_inconclusive_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rpc_drift, "CATALOG", tmp_path / "t.md")
    (tmp_path / "t.md").write_text(
        "| RPC id | Status |\n|---|---|\n| `aaaaaa` | gone |\n", encoding="utf-8"
    )
    drift = check(tmp_path, get=lambda url: (200, _SHELL, url))
    assert drift.state == INCONCLUSIVE
    assert "live" in drift.note


def test_a_shell_http_error_is_inconclusive_not_a_page_change() -> None:
    drift = assess(_IDS, _SHELL, 200, _bundle(*_IDS), shell_status=429)
    assert drift.state == INCONCLUSIVE
    assert drift.note == "shell HTTP 429"


def test_a_redirect_to_a_page_without_the_app_is_inconclusive(tmp_path: Path) -> None:
    consent = "https://consent.google.com/ml?continue=https://flow.google.com/&gl=GB"

    def get(url: str) -> tuple[int, str, str]:
        return 200, "<html><form>consent</form></html>", consent

    drift = check(tmp_path, get=get, ids=_IDS)
    assert drift.state == INCONCLUSIVE
    assert "consent.google.com" in drift.note and "continue" not in drift.note


def test_script_urls_resolve_against_the_final_shell_url(tmp_path: Path) -> None:
    seen: list[str] = []
    final = "https://flow.google.com/fx/app/"
    relative = _SHELL.replace(f'src="{_SCRIPT}"', 'src="static/_/js/k=x/m=_b"')

    def get(url: str) -> tuple[int, str, str]:
        seen.append(url)
        return (200, relative, final) if len(seen) == 1 else (200, _bundle(*_IDS), url)

    assert check(tmp_path, get=get, ids=_IDS).state == OK
    assert seen[1] == "https://flow.google.com/fx/app/static/_/js/k=x/m=XRV0Af,wO1vlb,wj3Fuc,zxMy9c"


def test_the_check_has_a_total_time_budget(tmp_path: Path) -> None:
    def get(url: str) -> tuple[int, str, str]:
        if url == rpc_drift.SHELL:
            return 200, _SHELL, url
        time.sleep(2)
        return 200, _bundle(*_IDS), url

    started = time.monotonic()
    drift = check(tmp_path, get=get, ids=_IDS, budget_s=0.3)
    assert time.monotonic() - started < 1.5
    assert drift.state == INCONCLUSIVE
    assert "budget" in drift.note


def test_only_a_real_nightly_writes_the_archive(tmp_path: Path) -> None:
    prior = {"build": "bl_old", "first_seen": "2026-11-01", "ids": ["YhhmEf", "gone1"]}
    (tmp_path / "bl_old.json").write_text(json.dumps(prior), encoding="utf-8")

    def get(url: str) -> tuple[int, str, str]:
        return (200, _SHELL, url) if url == rpc_drift.SHELL else (200, _bundle(*_IDS), url)

    drift = check(tmp_path, get=get, ids=_IDS)  # a manual or dry run
    assert not (tmp_path / f"{_BL}.json").exists()
    assert drift.removed == ("gone1",)  # the diff is still shown
    check(tmp_path, get=get, ids=_IDS, write=True)
    assert (tmp_path / f"{_BL}.json").exists()


def test_a_failed_archive_write_is_reported(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    drift = archive(assess(_IDS, _SHELL, 200, _bundle(*_IDS)), blocked, "now")
    assert "archive not written" in drift.note


@pytest.mark.sharpit(allow_swallowed=True)  # the swallow is the contract under test
def test_an_rpc_drift_module_that_fails_to_import_never_loses_the_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.canary as pkg
    from scripts.canary import run_canary

    monkeypatch.delattr(pkg, "rpc_drift")
    monkeypatch.setitem(sys.modules, "scripts.canary.rpc_drift", None)
    section = run_canary.rpc_drift_section()
    assert f"RPC-DRIFT {INCONCLUSIVE}" in section and "ModuleNotFoundError" in section
