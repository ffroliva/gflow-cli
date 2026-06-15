import importlib.util
import sys
from pathlib import Path

# Load the analyzer from scripts/dev/ by path. scripts/ is intentionally outside
# pyright's include (src, tests) and is not an importable package, so a plain
# `from scripts.dev...` import is unresolvable under the project config. The
# asserts narrow spec / spec.loader away from None to keep pyright clean.
_MOD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "analyze_agent_ui_capture.py"
_spec = importlib.util.spec_from_file_location("analyze_agent_ui_capture", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["analyze_agent_ui_capture"] = _mod
_spec.loader.exec_module(_mod)

ComposerSignals = _mod.ComposerSignals
ComposerState = _mod.ComposerState
build_findings = _mod.build_findings
classify_composer = _mod.classify_composer
diff_signal_sets = _mod.diff_signal_sets
fingerprint_map = _mod.fingerprint_map
summarize_capture = _mod.summarize_capture


def _sig(
    crop_present: bool = False,
    agent_pill_present: bool = False,
    agent_chat_panel_present: bool = False,
    crop_recoverable: bool | None = None,
) -> ComposerSignals:
    return ComposerSignals(
        crop_present=crop_present,
        agent_pill_present=agent_pill_present,
        agent_chat_panel_present=agent_chat_panel_present,
        crop_recoverable=crop_recoverable,
    )


def test_crop_present_is_classic():
    assert classify_composer(_sig(crop_present=True)) is ComposerState.CLASSIC_MEDIA


def test_agent_recoverable_is_over_classic():
    result = classify_composer(_sig(agent_pill_present=True, crop_recoverable=True))
    assert result is ComposerState.AGENT_OVER_CLASSIC


def test_agent_not_recoverable_is_forced():
    result = classify_composer(_sig(agent_chat_panel_present=True, crop_recoverable=False))
    assert result is ComposerState.FORCED_AGENT


def test_no_crop_no_agent_is_unknown():
    assert classify_composer(_sig()) is ComposerState.UNKNOWN


def test_agent_recovery_not_attempted_is_unknown():
    assert classify_composer(_sig(agent_pill_present=True)) is ComposerState.UNKNOWN


def test_fingerprint_map_hashes_values():
    fm = fingerprint_map({"a": "hello", "b": ""})
    assert len(fm["a"]) == 8
    assert fm["b"] == ""


def test_diff_signal_sets_three_buckets():
    a = {"k1": "h1", "k2": "h2", "shared": "x"}
    b = {"k3": "h3", "shared": "y"}
    d = diff_signal_sets(a, b)
    assert d["onlyInA"] == ["k1", "k2"]
    assert d["onlyInB"] == ["k3"]
    assert d["changed"] == ["shared"]


def _capture(profile, locale, *, crop, pill, recoverable, ls):
    return {
        "profile": profile,
        "locale": locale,
        "engine": "cdp-real-chrome",
        "signals": {
            "cropPresent": crop,
            "agentPill": pill,
            "chatPanel": False,
            "cropRecoverable": recoverable,
        },
        "gating": {
            "localStorage": ls,
            "sessionStorage": {},
            "documentCookieNames": ["NID"],
            "nextDataPagePropKeys": ["flags"],
        },
    }


def test_summarize_capture_classifies_and_fingerprints():
    cap = _capture(
        "ffroliva", "en", crop=False, pill=True, recoverable=False, ls={"exp": "agentic"}
    )
    s = summarize_capture(cap)
    assert s["state"] == "forced_agent"
    assert s["profile"] == "ffroliva"
    assert len(s["localStorageFp"]["exp"]) == 8


def test_build_findings_diffs_two_runs():
    a = summarize_capture(
        _capture("ffroliva", "en", crop=False, pill=True, recoverable=False, ls={"exp": "agentic"})
    )
    b = summarize_capture(_capture("denon82", "pt", crop=True, pill=False, recoverable=None, ls={}))
    f = build_findings([a, b])
    assert f["states"] == {"ffroliva/en": "forced_agent", "denon82/pt": "classic_media"}
    assert "exp" in f["localStorageDiff"]["onlyInA"]
