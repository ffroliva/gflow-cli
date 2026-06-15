#!/usr/bin/env python3
r"""Offline analyzer for gflow-agent-browser-spike captures.

Reads the sandbox's agentui-capture-*.json files, classifies each composer
state, redacts + fingerprints the gating signals, diffs them across runs
(account / locale / engine), and emits a consolidated, redacted findings JSON.
No browser driving — pure file processing. Raw captures stay in the sandbox.

Usage:
    .venv\Scripts\python.exe scripts\dev\analyze_agent_ui_capture.py \
        path\to\agentui-capture-ffroliva-en-*.json \
        path\to\agentui-capture-denon82-pt-*.json --out findings.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ComposerState(StrEnum):
    CLASSIC_MEDIA = "classic_media"
    AGENT_OVER_CLASSIC = "agent_over_classic"
    FORCED_AGENT = "forced_agent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ComposerSignals:
    crop_present: bool
    agent_pill_present: bool
    agent_chat_panel_present: bool
    crop_recoverable: bool | None  # None = recovery not attempted


def classify_composer(s: ComposerSignals) -> ComposerState:
    if s.crop_present:
        return ComposerState.CLASSIC_MEDIA
    if not (s.agent_pill_present or s.agent_chat_panel_present):
        return ComposerState.UNKNOWN
    if s.crop_recoverable is True:
        return ComposerState.AGENT_OVER_CLASSIC
    if s.crop_recoverable is False:
        return ComposerState.FORCED_AGENT
    return ComposerState.UNKNOWN


def _sha8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8] if value else ""


def fingerprint_map(kv: dict[str, Any]) -> dict[str, str]:
    """Reduce a {key: value} map (localStorage etc.) to {key: sha8(value)} —
    lets us diff by presence + value-hash without persisting secrets."""
    return {k: _sha8(str(v)) for k, v in kv.items()}


def diff_signal_sets(a: dict[str, str], b: dict[str, str]) -> dict[str, list[str]]:
    ka, kb = set(a), set(b)
    return {
        "onlyInA": sorted(ka - kb),
        "onlyInB": sorted(kb - ka),
        "changed": sorted(k for k in ka & kb if a[k] != b[k]),
    }


def load_capture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _signals_from_capture(capture: dict[str, Any]) -> ComposerSignals:
    s = capture.get("signals", {})
    rec = s.get("cropRecoverable")
    return ComposerSignals(
        crop_present=bool(s.get("cropPresent")),
        agent_pill_present=bool(s.get("agentPill")),
        agent_chat_panel_present=bool(s.get("chatPanel")),
        crop_recoverable=None if rec is None else bool(rec),
    )


def summarize_capture(capture: dict[str, Any]) -> dict[str, Any]:
    gating = capture.get("gating", {})
    return {
        "key": f"{capture.get('profile')}/{capture.get('locale')}",
        "profile": capture.get("profile"),
        "locale": capture.get("locale"),
        "engine": capture.get("engine"),
        "navigatorWebdriver": capture.get("navigatorWebdriver"),
        "state": classify_composer(_signals_from_capture(capture)).value,
        "localStorageFp": fingerprint_map(gating.get("localStorage", {})),
        "sessionStorageFp": fingerprint_map(gating.get("sessionStorage", {})),
        "cookieNames": sorted(gating.get("documentCookieNames", [])),
        "nextDataPagePropKeys": sorted(gating.get("nextDataPagePropKeys", [])),
    }


def build_findings(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Consolidate per-run summaries; diff the first agentic run against the
    first classic run to surface candidate gating signals."""
    states = {s["key"]: s["state"] for s in summaries}
    agentic = next(
        (s for s in summaries if s["state"] in ("forced_agent", "agent_over_classic")), None
    )
    classic = next((s for s in summaries if s["state"] == "classic_media"), None)
    out: dict[str, Any] = {"runs": summaries, "states": states}
    if agentic and classic:
        out["localStorageDiff"] = diff_signal_sets(
            agentic["localStorageFp"], classic["localStorageFp"]
        )
        out["sessionStorageDiff"] = diff_signal_sets(
            agentic["sessionStorageFp"], classic["sessionStorageFp"]
        )
        out["cookieNameDiff"] = diff_signal_sets(
            {k: "1" for k in agentic["cookieNames"]}, {k: "1" for k in classic["cookieNames"]}
        )
        out["nextDataKeyDiff"] = diff_signal_sets(
            {k: "1" for k in agentic["nextDataPagePropKeys"]},
            {k: "1" for k in classic["nextDataPagePropKeys"]},
        )
    else:
        out["note"] = "need at least one agentic and one classic capture to diff gating signals"
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Analyze agentic-UI captures")
    p.add_argument("captures", nargs="+", help="paths to agentui-capture-*.json")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    summaries = [summarize_capture(load_capture(Path(c))) for c in args.captures]
    findings = build_findings(summaries)
    text = json.dumps(findings, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
