"""Tests for scripts/dev/spike_selected_video_model.py (Task 28).

Verifies the candidate carrier search, source categorization, and findings
recording logic offline without opening a browser.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load scripts/dev/spike_selected_video_model.py dynamically
_MOD_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "dev" / "spike_selected_video_model.py"
)
_spec = importlib.util.spec_from_file_location("spike_selected_video_model", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["spike_selected_video_model"] = _mod
_spec.loader.exec_module(_mod)


def test_find_model_carrier_in_storage() -> None:
    """Detects model identifiers in localStorage or sessionStorage."""
    storage_data = {
        "user_pref_theme": "dark",
        "flow-prompt-box-settings": (
            '{"aspectRatio":"LANDSCAPE","videoModelFamilyId":"veo_3_1_lite",'
            '"videoDuration":4,"videoResolution":4}'
        ),
        "unrelated": "12345",
    }
    candidates = _mod.find_carrier_candidates(
        storage={"localStorage": storage_data},
        network_replies=[],
    )
    assert len(candidates) == 1
    c = candidates[0]
    assert c["source_type"] == "storage"
    assert "flow-prompt-box-settings" in c["location"]
    assert "videoModelFamilyId" in c["location"]
    assert c["value"] == "veo_3_1_lite"
    assert c["model_match"] == "veo_3_1_lite"


def test_find_model_carrier_in_network_replies() -> None:
    """Detects model identifiers in batchexecute or JSON network responses."""
    url = "https://flow.google.com/data/batchexecute?rpcids=XYZ123"
    raw_payload = '[[null, "veo_3_1_lite"]]'
    replies = [
        {
            "url": url,
            "rpcids": ["XYZ123"],
            "data": [[["wrb.fr", "XYZ123", raw_payload, None, None, None, "generic"]]],
        }
    ]
    candidates = _mod.find_carrier_candidates(
        storage={},
        network_replies=replies,
    )
    assert any(c["value"] == "veo_3_1_lite" for c in candidates)
    match = next(c for c in candidates if c["value"] == "veo_3_1_lite")
    assert match["source_type"] == "network"
    assert "XYZ123" in match["location"]


def test_empty_candidates_recorded_as_not_found_not_unreadable() -> None:
    """When no carrier is found, the summary must state 'not found in the listed sources'."""
    candidates = _mod.find_carrier_candidates(
        storage={"localStorage": {"theme": "dark"}},
        network_replies=[],
    )
    assert candidates == []
    summary = _mod.summarize_findings(candidates)
    assert "not found in the listed sources" in summary["status"]
    assert "unreadable" not in summary["status"].lower()


def test_spike_script_structure_and_safety_guards() -> None:
    """Script text must include abort_submits guard and profile leasing."""
    text = _MOD_PATH.read_text(encoding="utf-8")
    assert "_abort_submits" in text or "abort" in text
    assert "build_client" in text or "ProfileLease" in text
    assert "YhhmEf" in text and "eb1hJf" in text
