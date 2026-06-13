# Agentic Flow UI Cohort Recon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the forced agentic Flow UI (DOM + HAR + cookies/storage) in the isolated `gflow-agent-browser-spike` sandbox via CDP-attached real Chrome + manual drive, then analyze the artifacts offline in `gflow-cli` (classify composer state, diff gating signals across account × locale × engine) and write `docs/AGENT_UI_RECON.md` from the evidence.

**Architecture:** **Two tools, split by responsibility.** *Capture* lives in `C:\development\github\gflow-agent-browser-spike` (separate, non-git sandbox; `agent-browser@0.27.0` over CDP, no Node deps in gflow-cli). It attaches real Chrome to a gflow profile and the operator manually drives the agentic UI while `agent-browser eval` / `snapshot` / `network har` capture evidence. *Analysis* lives in `gflow-cli` as an **offline** Python tool that consumes the sandbox JSON/HAR — pure logic (classifier, redaction, diff), fully unit-tested, no browser driving.

**Tech Stack:** PowerShell 7 + `agent-browser@0.27.0` (sandbox capture); Python 3.12 + pytest (gflow-cli analysis). No Playwright/Node in gflow-cli for this spike.

**Conventions:** No `src/` changes. All commits end with the repo's `Co-Authored-By` footer. Python tests run with `.venv\Scripts\python.exe -m pytest`. Raw sandbox artifacts (HAR, eval JSON, screenshots) are **local only, never committed** — they carry auth cookies/tokens/prompts. The gflow-cli analyzer redacts before writing any finding.

**Source spec:** `docs/superpowers/specs/2026-06-13-agent-ui-cohort-recon-design.md`

---

## File structure

| File | Repo | Responsibility |
|---|---|---|
| `scripts/capture-agent-ui.ps1` (create) | gflow-agent-browser-spike | Per-run capture: composer signals + gating `eval` + DOM/snapshot + HAR around one manual image gen, on a CDP-attached gflow profile. Writes one combined `agentui-capture-*.json`. |
| `scripts/dev/analyze_agent_ui_capture.py` (create) | gflow-cli | Offline analyzer: load capture JSONs → classify composer → redact + fingerprint gating → cross-run diff → consolidated findings JSON. |
| `tests/scripts/test_analyze_agent_ui_capture.py` (create) | gflow-cli | Unit tests for the pure helpers + the load/classify/diff pipeline (fixture capture JSON). |
| `docs/AGENT_UI_RECON.md` (create) | gflow-cli | Recon assessment, written from the captured evidence. |
| `docs/INDEX.md` (modify) | gflow-cli | Add a routing entry for `AGENT_UI_RECON.md`. |

**Capture JSON contract** (written by Task 1, consumed by Task 5 — keep these keys identical across both tasks):

```json
{
  "profile": "ffroliva", "locale": "en", "engine": "cdp-real-chrome",
  "capturedAt": "20260613-210500", "projectUrl": "https://labs.google/fx/...",
  "navigatorWebdriver": false,
  "signals": {"cropPresent": false, "agentPill": true, "chatPanel": false, "cropRecoverable": false},
  "gating": {"localStorage": {"k": "v"}, "sessionStorage": {}, "documentCookieNames": ["NID"], "nextDataPagePropKeys": ["..."]},
  "ligatures": ["add_2", "arrow_forward", "..."],
  "harFile": "flow-generation-20260613-210500.har"
}
```

---

## Task 1: Sandbox capture script (`capture-agent-ui.ps1`)

**Files:**
- Create: `C:\development\github\gflow-agent-browser-spike\scripts\capture-agent-ui.ps1`

Modeled on the sandbox's existing `run-cdp-smoke.ps1` (same `Invoke-AgentBrowser` helpers and `--cdp --json eval` pattern), adding a project URL, composer-signals + gating evals, a before/after recovery probe, and a combined output JSON.

- [ ] **Step 1: Write the script header, params, and agent-browser helpers**

```powershell
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectUrl,
    [string]$Profile = $env:GFLOW_CLI_PROFILE,
    [string]$Locale = "en",
    [string]$Engine = "cdp-real-chrome",
    [int]$Port = 9334,
    [string]$Session = "gflow-agent-ui",
    [string]$AgentBrowserVersion = "0.27.0"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"
$AgentStateDir = Join-Path $ProjectRoot ".agent-browser"
$NpmCacheDir = Join-Path $ProjectRoot ".npm-cache"
New-Item -ItemType Directory -Force $ArtifactsDir, $AgentStateDir, $NpmCacheDir | Out-Null
$env:npm_config_cache = $NpmCacheDir
$env:AGENT_BROWSER_SOCKET_DIR = Join-Path $AgentStateDir "sockets"
$env:AGENT_BROWSER_SESSION = $Session
New-Item -ItemType Directory -Force $env:AGENT_BROWSER_SOCKET_DIR | Out-Null

function Invoke-AgentBrowser {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)
    $package = "agent-browser@$AgentBrowserVersion"
    $output = & npx --yes --package $package agent-browser @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "agent-browser failed ($LASTEXITCODE): $($CommandArgs -join ' ')" }
    return ($output -join [Environment]::NewLine)
}
function Eval-Result {
    param([string]$Expr)
    $json = Invoke-AgentBrowser @("--cdp", "$Port", "--json", "eval", $Expr)
    return ($json | ConvertFrom-Json).data.result
}
```

- [ ] **Step 2: Add the eval expressions (composer signals + gating)**

```powershell
$signalsExpr = @'
(() => {
  const lig = (s) => Array.from(document.querySelectorAll(s)).map(e => (e.textContent||'').trim());
  const all = lig('i.google-symbols, i.material-symbols-outlined');
  const cropPresent = Array.from(document.querySelectorAll("button i.google-symbols"))
      .some(e => (e.textContent||'').trim().startsWith('crop_'));
  const agentPill = /\bAgent\b/.test(document.body.innerText||'') && !!document.querySelector("div[role='textbox']");
  const chatPanel = all.includes('edit_square') && all.includes('close');
  return { cropPresent, agentPill, chatPanel, url: location.href,
           ligatures: Array.from(new Set(all)).filter(Boolean).sort() };
})()
'@

$gatingExpr = @'
(() => {
  const next = document.getElementById('__NEXT_DATA__');
  let keys = [];
  try { const p = JSON.parse(next ? next.textContent : '{}');
        keys = Object.keys((p.props && p.props.pageProps) || {}).sort(); } catch (e) { keys = ['<unparseable>']; }
  return {
    localStorage: Object.fromEntries(Object.entries(localStorage)),
    sessionStorage: Object.fromEntries(Object.entries(sessionStorage)),
    documentCookieNames: (document.cookie || '').split('; ').map(c => c.split('=')[0]).filter(Boolean).sort(),
    nextDataPagePropKeys: keys
  };
})()
'@
```

- [ ] **Step 3: Add the capture flow (open → signals → manual drive → HAR → assemble JSON)**

```powershell
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
Invoke-AgentBrowser @("--cdp", "$Port", "--json", "open", $ProjectUrl) | Out-Null
Start-Sleep -Seconds 4
$webdriver = Eval-Result 'navigator.webdriver'
$signalsBefore = Eval-Result $signalsExpr
$gating = Eval-Result $gatingExpr
Invoke-AgentBrowser @("--cdp", "$Port", "snapshot", "-i") |
    Set-Content -LiteralPath (Join-Path $ArtifactsDir "agentui-snapshot-$ts.txt") -Encoding UTF8

Write-Host "Try to leave Agent mode in the Chrome window (click the Agent pill / close the chat panel)."
Read-Host "Press Enter after attempting to reach the classic image controls"
$signalsAfter = Eval-Result $signalsExpr

Invoke-AgentBrowser @("--cdp", "$Port", "--json", "network", "requests", "--clear") | Out-Null
Invoke-AgentBrowser @("--cdp", "$Port", "--json", "network", "har", "start") | Out-Null
Write-Host "Now trigger exactly ONE image generation through the agentic UI (image gen is free)."
Read-Host "Press Enter after the generation request has fired"
$harFile = "flow-generation-$ts.har"
Invoke-AgentBrowser @("--cdp", "$Port", "--json", "network", "har", "stop", (Join-Path $ArtifactsDir $harFile)) | Out-Null

$capture = [ordered]@{
    profile = $Profile; locale = $Locale; engine = $Engine
    capturedAt = $ts; projectUrl = $ProjectUrl
    navigatorWebdriver = $webdriver
    signals = [ordered]@{
        cropPresent = [bool]$signalsBefore.cropPresent
        agentPill   = [bool]$signalsBefore.agentPill
        chatPanel   = [bool]$signalsBefore.chatPanel
        cropRecoverable = [bool]$signalsAfter.cropPresent
    }
    gating = $gating
    ligatures = $signalsBefore.ligatures
    harFile = $harFile
}
$outPath = Join-Path $ArtifactsDir "agentui-capture-$Profile-$Locale-$ts.json"
$capture | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outPath -Encoding UTF8
Write-Host "capture written: $outPath"
```

- [ ] **Step 4: Verify the script parses (no live Chrome needed)**

Run:
```
pwsh -NoProfile -Command "$null=[System.Management.Automation.Language.Parser]::ParseFile('C:\development\github\gflow-agent-browser-spike\scripts\capture-agent-ui.ps1',[ref]$null,[ref]$e); if($e){$e;exit 1}else{'parse ok'}"
```
Expected: `parse ok` (no parser errors). Live behavior is verified in Task 6.

- [ ] **Step 5: Commit (in gflow-cli — sandbox is non-git, so nothing to commit there)**

No gflow-cli commit this task. Note in the run log that `capture-agent-ui.ps1` was created in the sandbox.

---

## Task 2: Analyzer scaffold (`analyze_agent_ui_capture.py`)

**Files:**
- Create: `scripts/dev/analyze_agent_ui_capture.py` (gflow-cli)

- [ ] **Step 1: Write the module skeleton**

```python
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
from enum import Enum
from pathlib import Path
from typing import Any


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
```

- [ ] **Step 2: Add stubs so the module imports (filled in Tasks 3–5)**

```python
def load_capture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_capture(capture: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError  # Task 5


def build_findings(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    raise NotImplementedError  # Task 5
```

- [ ] **Step 3: Verify import + help**

Run: `.venv\Scripts\python.exe scripts\dev\analyze_agent_ui_capture.py --help`
Expected: argparse help, exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/dev/analyze_agent_ui_capture.py
git commit -m "feat(spike): scaffold offline agentic-UI capture analyzer"
```

---

## Task 3: Composer-state classifier (TDD)

**Files:**
- Modify: `scripts/dev/analyze_agent_ui_capture.py`
- Test: `tests/scripts/test_analyze_agent_ui_capture.py`

- [ ] **Step 1: Write the failing test**

```python
import importlib.util
import sys
from pathlib import Path

_MOD = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "analyze_agent_ui_capture.py"
_spec = importlib.util.spec_from_file_location("analyze_agent_ui_capture", _MOD)
mod = importlib.util.module_from_spec(_spec)
sys.modules["analyze_agent_ui_capture"] = mod
_spec.loader.exec_module(mod)

ComposerSignals = mod.ComposerSignals
ComposerState = mod.ComposerState
classify_composer = mod.classify_composer


def _sig(**kw):
    base = dict(crop_present=False, agent_pill_present=False,
                agent_chat_panel_present=False, crop_recoverable=None)
    base.update(kw)
    return ComposerSignals(**base)


def test_crop_present_is_classic():
    assert classify_composer(_sig(crop_present=True)) is ComposerState.CLASSIC_MEDIA


def test_agent_recoverable_is_over_classic():
    assert classify_composer(_sig(agent_pill_present=True, crop_recoverable=True)) is ComposerState.AGENT_OVER_CLASSIC


def test_agent_not_recoverable_is_forced():
    assert classify_composer(_sig(agent_chat_panel_present=True, crop_recoverable=False)) is ComposerState.FORCED_AGENT


def test_no_crop_no_agent_is_unknown():
    assert classify_composer(_sig()) is ComposerState.UNKNOWN


def test_agent_recovery_not_attempted_is_unknown():
    assert classify_composer(_sig(agent_pill_present=True)) is ComposerState.UNKNOWN
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_analyze_agent_ui_capture.py -v`
Expected: FAIL — `AttributeError: ... 'ComposerSignals'`.

- [ ] **Step 3: Implement classifier** (add to `analyze_agent_ui_capture.py`)

```python
class ComposerState(str, Enum):
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
```

- [ ] **Step 4: Run to verify passing**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_analyze_agent_ui_capture.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/analyze_agent_ui_capture.py tests/scripts/test_analyze_agent_ui_capture.py
git commit -m "feat(spike): add composer-state classifier with tests"
```

---

## Task 4: Redaction + signal-diff helpers (TDD)

**Files:**
- Modify: `scripts/dev/analyze_agent_ui_capture.py`
- Test: `tests/scripts/test_analyze_agent_ui_capture.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
fingerprint_map = mod.fingerprint_map
diff_signal_sets = mod.diff_signal_sets


def test_fingerprint_map_hashes_values():
    fm = fingerprint_map({"a": "hello", "b": ""})
    assert len(fm["a"]) == 8
    assert fm["b"] == ""  # empty stays empty (presence, not content)


def test_diff_signal_sets_three_buckets():
    a = {"k1": "h1", "k2": "h2", "shared": "x"}
    b = {"k3": "h3", "shared": "y"}
    d = diff_signal_sets(a, b)
    assert d["onlyInA"] == ["k1", "k2"]
    assert d["onlyInB"] == ["k3"]
    assert d["changed"] == ["shared"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_analyze_agent_ui_capture.py -v`
Expected: FAIL — `AttributeError: ... 'fingerprint_map'`.

- [ ] **Step 3: Implement helpers** (add to `analyze_agent_ui_capture.py`)

```python
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
```

- [ ] **Step 4: Run to verify passing**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_analyze_agent_ui_capture.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/analyze_agent_ui_capture.py tests/scripts/test_analyze_agent_ui_capture.py
git commit -m "feat(spike): add fingerprint + signal-diff helpers with tests"
```

---

## Task 5: Wire the analyzer pipeline (summarize + findings, TDD)

**Files:**
- Modify: `scripts/dev/analyze_agent_ui_capture.py`
- Test: `tests/scripts/test_analyze_agent_ui_capture.py`

- [ ] **Step 1: Write the failing test with a fixture capture dict**

```python
summarize_capture = mod.summarize_capture
build_findings = mod.build_findings


def _capture(profile, locale, *, crop, pill, recoverable, ls):
    return {
        "profile": profile, "locale": locale, "engine": "cdp-real-chrome",
        "signals": {"cropPresent": crop, "agentPill": pill, "chatPanel": False,
                    "cropRecoverable": recoverable},
        "gating": {"localStorage": ls, "sessionStorage": {},
                   "documentCookieNames": ["NID"], "nextDataPagePropKeys": ["flags"]},
    }


def test_summarize_capture_classifies_and_fingerprints():
    cap = _capture("ffroliva", "en", crop=False, pill=True, recoverable=False, ls={"exp": "agentic"})
    s = summarize_capture(cap)
    assert s["state"] == "forced_agent"
    assert s["profile"] == "ffroliva"
    assert len(s["localStorageFp"]["exp"]) == 8  # hashed, not raw


def test_build_findings_diffs_two_runs():
    a = summarize_capture(_capture("ffroliva", "en", crop=False, pill=True, recoverable=False, ls={"exp": "agentic"}))
    b = summarize_capture(_capture("denon82", "pt", crop=True, pill=False, recoverable=None, ls={}))
    f = build_findings([a, b])
    assert f["states"] == {"ffroliva/en": "forced_agent", "denon82/pt": "classic_media"}
    # exp localStorage key present only in the forced-agent run -> candidate gating signal
    assert "exp" in f["localStorageDiff"]["onlyInA"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_analyze_agent_ui_capture.py -v`
Expected: FAIL — `NotImplementedError` from `summarize_capture`.

- [ ] **Step 3: Implement `summarize_capture` and `build_findings`** (replace the Task 2 stubs)

```python
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
    agentic = next((s for s in summaries if s["state"] in ("forced_agent", "agent_over_classic")), None)
    classic = next((s for s in summaries if s["state"] == "classic_media"), None)
    out: dict[str, Any] = {"runs": summaries, "states": states}
    if agentic and classic:
        out["localStorageDiff"] = diff_signal_sets(agentic["localStorageFp"], classic["localStorageFp"])
        out["sessionStorageDiff"] = diff_signal_sets(agentic["sessionStorageFp"], classic["sessionStorageFp"])
        out["cookieNameDiff"] = diff_signal_sets(
            {k: "1" for k in agentic["cookieNames"]}, {k: "1" for k in classic["cookieNames"]}
        )
        out["nextDataKeyDiff"] = diff_signal_sets(
            {k: "1" for k in agentic["nextDataPagePropKeys"]}, {k: "1" for k in classic["nextDataPagePropKeys"]}
        )
    else:
        out["note"] = "need at least one agentic and one classic capture to diff gating signals"
    return out
```

- [ ] **Step 4: Run full suite + lint + typecheck**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_analyze_agent_ui_capture.py -v`
Expected: 9 passed.
Run: `.venv\Scripts\python.exe -m pyright scripts/dev/analyze_agent_ui_capture.py`
Expected: 0 errors.
Run: `.venv\Scripts\python.exe -m ruff check scripts/dev/analyze_agent_ui_capture.py` then `... -m ruff format scripts/dev/analyze_agent_ui_capture.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/analyze_agent_ui_capture.py tests/scripts/test_analyze_agent_ui_capture.py
git commit -m "feat(spike): wire capture summarize + cross-run gating diff"
```

---

## Task 6: Supervised capture runs (USER-DRIVEN, in the sandbox)

**This task cannot be auto-executed.** It requires the operator at a headed Chrome, signed into the gflow profiles. The agent prepares commands; the operator runs them and reports artifact paths. One disciplined run per (profile, scenario) — WAF heat.

- [ ] **Step 1: P0 — does gflow's own `ffroliva` profile render the agentic UI? (the priority question)**

In `C:\development\github\gflow-agent-browser-spike`:
```
.\scripts\launch-flow-chrome.ps1 -ProfileName ffroliva -Port 9334
.\scripts\run-cdp-smoke.ps1 -Port 9334
```
Record: `navigator.webdriver` and the `flow-page-probe.json` / `snapshot-interactive.txt`. **Decision gate:** if the gflow `ffroliva` profile opens in the **classic** composer (crop_* present), gflow's own path does NOT hit the agentic UI — record this prominently; it reorders the feature priority.

- [ ] **Step 2: Full capture on `ffroliva` (en)**

Keep the launched Chrome open; open the project, then:
```
.\scripts\capture-agent-ui.ps1 -ProjectUrl "https://labs.google/fx/tools/flow/project/58c24049-c3bc-44fb-8615-852f84e5fd0f?hl=en" -ProfileName ffroliva -Locale en -Port 9334
```
Follow the prompts: attempt to leave Agent mode (records `cropRecoverable`), then trigger ONE free image generation (records the wire via HAR).

- [ ] **Step 3: Full capture on `denon82` (pt-BR)**

```
.\scripts\launch-flow-chrome.ps1 -ProfileName denon82 -Port 9335
.\scripts\capture-agent-ui.ps1 -ProjectUrl "https://labs.google/fx/tools/flow/project/<denon82-project-uuid>?hl=pt" -ProfileName denon82 -Locale pt -Port 9335
```

- [ ] **Step 4: Engine axis — compare CDP-real-Chrome vs gflow's Playwright path**

The sandbox capture above is CDP-attached real Chrome (`navigator.webdriver === false`). For the gflow engine path, note what `gflow image t2i` produces on the same `ffroliva` profile today (the #183 exit-23 = agentic, or a successful classic run). Optionally probe patchright:
```
$env:GFLOW_CLI_BROWSER_ENGINE="patchright"; gflow --verbose image t2i "engine axis probe"; Remove-Item Env:\GFLOW_CLI_BROWSER_ENGINE
```
Record each engine's observed composer state.

- [ ] **Step 5: Summarize HARs and verify capture completeness**

For each HAR, produce a redacted summary (identify the agentic gen endpoint — do not assume `aisandbox-pa`):
```
python .\scripts\extract_har_summary.py .\artifacts\flow-generation-<ts>.har --out .\artifacts\flow-generation-<ts>.summary.json
```
Confirm per run: a non-empty `.har`, an `agentui-capture-*.json` with `signals` + `gating`, a snapshot, screenshots. Privacy spot-check: no email/secret leaks in anything that will leave the sandbox. List the `agentui-capture-*.json` paths for Task 7.

---

## Task 7: Run analysis, write the recon doc, close out

**Files:**
- Create: `docs/AGENT_UI_RECON.md` (gflow-cli)
- Modify: `docs/INDEX.md` (gflow-cli)

- [ ] **Step 1: Run the analyzer over the captures**

```
.venv\Scripts\python.exe scripts\dev\analyze_agent_ui_capture.py "C:\development\github\gflow-agent-browser-spike\artifacts\agentui-capture-ffroliva-en-*.json" "C:\development\github\gflow-agent-browser-spike\artifacts\agentui-capture-denon82-pt-*.json" --out "C:\development\github\gflow-agent-browser-spike\artifacts\agentui-findings.json"
```
Expected: a findings JSON with per-run `states` and the gating diffs. The redacted findings JSON is safe to quote in the doc.

- [ ] **Step 2: Write `docs/AGENT_UI_RECON.md` from the evidence**

Fill every section from the Task 6 artifacts + the findings JSON. **No claim without an artifact reference.**

```markdown
# Agentic Flow UI — Recon

> Captured in gflow-agent-browser-spike (CDP real Chrome, manual drive) on
> ffroliva (en) and denon82 (pt-BR), 2026-06-13. Analyzed by
> gflow-cli/scripts/dev/analyze_agent_ui_capture.py. Raw artifacts stay local.

## 1. Does gflow's own profile hit the agentic UI? (P0)
- ffroliva (gflow profile, CDP real Chrome): <state> — evidence: <run-cdp-smoke + capture json>
- navigator.webdriver: <value>
- Conclusion: <does gflow hit it? does it match the primary-profile screenshots?>

## 2. Gating mechanism
- localStorage / sessionStorage / cookie-name / __NEXT_DATA__ diff (agentic vs classic): <from findings.json>
- Per-account (server) vs per-profile/fingerprint (client): <verdict + evidence>
- Stable across locales (en vs pt-BR)? <yes/no>
- Engine effect (CDP real Chrome vs gflow Playwright/patchright): <verdict>
- Steerable? <yes/no + how>

## 3. The Agent button & expanded window (DOM)
- Pill selector (confirmed/new): <from ligatures + snapshot>
- Click behaviour: <restores classic | opens chat panel>
- Agent settings (aspect/model/upscale) location: <from snapshot>

## 4. The wire
- Agent image submit endpoint + payload/response shape: <from HAR summary>
- Reference entity ride-the-wire (#174): <result>

## 5. Recoverable vs forced
- cropRecoverable per run + avenues tried: <from capture json>
- Verdict: <recoverable | forced>

## 6. Recommendation for the feature plan
- Detection signal(s): <DOM / cookie / flag>
- Disambiguation rule (recoverable vs forced): <rule>
- Fail-cleanly vs drive-the-agent: <call + rationale>
```

- [ ] **Step 3: Add the routing entry to `docs/INDEX.md`**

Add under the recon/feature docs section:
```markdown
- [AGENT_UI_RECON.md](AGENT_UI_RECON.md) — agentic Flow UI: gating mechanism, detection signals, wire protocol (#183/#174)
```

- [ ] **Step 4: Final quality gate**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_analyze_agent_ui_capture.py -v`
Run: `.venv\Scripts\python.exe -m pyright scripts/dev/analyze_agent_ui_capture.py`
Run: `.venv\Scripts\python.exe -m ruff check scripts/dev/analyze_agent_ui_capture.py`
Expected: tests pass, 0 pyright errors, no lint errors.

- [ ] **Step 5: Commit (code + docs only; never the sandbox artifacts)**

```bash
git add docs/AGENT_UI_RECON.md docs/INDEX.md
git commit -m "docs: add agentic Flow UI recon findings (#183 #174)"
```

- [ ] **Step 6: Open the PR to `develop`**

```bash
git push -u origin docs/agent-ui-recon
gh pr create --base develop --title "Agentic Flow UI cohort recon (#183 #174)" --body "<plain-string summary + evidence links; never a heredoc>"
```

---

## Self-review

**Spec coverage:** §5 two-tool split → Task 1 (sandbox capture) + Tasks 2–5 (gflow-cli analysis). §3.1 profile/engine axis → Task 6 Steps 1–4. §4 unknowns: gating → capture gating eval (Task 1) + diff (Task 5) + recon §2; DOM/button → Task 1 signals/snapshot + recon §3; wire → Task 1 HAR + Task 6 Step 5 + recon §4. §6 scenarios P0/S0–S9 → Task 6 (P0/S1 = Steps 1–2, S2–S4 = manual drive + snapshot, S5 = manual image gen + HAR, S7 = gating eval, S8 = cropRecoverable probe, S9 = Step 4). §8 verification → Task 6 Step 5 + Task 7 Step 2 "no claim without artifact". §7 deliverables → all five files. Covered.

**Placeholder scan:** No "TBD/handle edge cases" in code steps; every code step shows complete code. The recon-doc skeleton (Task 7 Step 2) uses `<...>` fill-ins **by design** — its content IS the spike's output, unknowable pre-run; that's data entry, not a code placeholder.

**Type consistency:** The **capture JSON contract** (file-structure section) uses keys `signals.{cropPresent,agentPill,chatPanel,cropRecoverable}` + `gating.{localStorage,sessionStorage,documentCookieNames,nextDataPagePropKeys}` — written by Task 1 Step 3, read by `_signals_from_capture`/`summarize_capture` in Task 5 Step 3. `ComposerSignals` / `ComposerState` / `classify_composer` consistent across Tasks 3 and 5. `fingerprint_map` / `diff_signal_sets` consistent across Tasks 4 and 5. `summarize_capture` / `build_findings` stubs (Task 2) → impls (Task 5) same signatures.
```
