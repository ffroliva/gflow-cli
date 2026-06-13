# Agentic Flow UI Cohort Recon Spike — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a credit-free recon harness that captures the forced agentic Flow UI (DOM + HAR + cookies/storage) across account × locale × profile/engine, plus a pre-flight probe answering "does gflow's own profile even render the agentic UI?", and a recon doc written from the captured evidence.

**Architecture:** One self-contained `scripts/dev/` spike that reuses `_spike_common` (auth/out-dir/page bootstrap) and the existing `VideoGenerationMixin` selectors/helpers. The script splits into **pure logic** (composer-state classifier, cookie/storage redaction, signal diff — unit-tested) and **impure browser orchestration** (P0 probe + scenarios S0–S9 — verified by supervised live runs). HAR is captured via a `FlowApiClient` subclass that injects `record_har_path` through the existing `_persistent_context_kwargs()` seam (same mechanism `RecordingFlowApiClient` uses for video).

**Tech Stack:** Python 3.12, Playwright (async), pytest. Reuses `gflow_cli.api.client.FlowApiClient`, `gflow_cli.api.routes`, `gflow_cli.api.transports.ui_automation_video.VideoGenerationMixin`.

**Conventions:** This is recon — **no `src/` changes**. All commits end with the repo's `Co-Authored-By` footer. Tests run with `.venv\Scripts\python.exe -m pytest` (not `uv run` — broken on Windows). Live spikes run with `PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\...`. Artifacts land in `scripts/dev/_spike_out/` (already gitignored) and are **never committed** (they carry account avatar/email).

**Source spec:** `docs/superpowers/specs/2026-06-13-agent-ui-cohort-recon-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `scripts/dev/spike_agent_ui_cohort.py` (create) | The harness: pure helpers + HAR client + P0 probe + scenarios + argparse `main()`. |
| `tests/scripts/test_spike_agent_ui_cohort.py` (create) | Unit tests for the pure helpers (classifier, redaction, diff). |
| `docs/AGENT_UI_RECON.md` (create) | Recon assessment, written from the captured evidence after the live runs. |
| `docs/INDEX.md` (modify) | Add a routing entry for `AGENT_UI_RECON.md`. |

---

## Task 1: Harness scaffold + HAR-capture client + argparse

**Files:**
- Create: `scripts/dev/spike_agent_ui_cohort.py`

- [ ] **Step 1: Write the module skeleton**

```python
#!/usr/bin/env python3
r"""Agentic Flow UI cohort recon (0 credits where possible).

Captures the forced agentic Flow UI — DOM, HAR, cookies/storage — to resolve
how Google gates the cohort (#183/#174). Image generation is free (captured
live); video-gen routes are route-aborted ($0). Output lands in
scripts/dev/_spike_out/ — LOCAL ONLY, never commit (carries account avatar).

Usage (headed, supervised; ONE disciplined run per account — WAF heat):

    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py \
        --profile ffroliva --project <uuid> --locale en --scenario p0
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    AGENT_CHAT_PANEL_CLOSE_SELECTOR,
    COMPOSER_AGENT_TOGGLE_SELECTOR,
    MODE_SWITCH_TRIGGER_SELECTORS,
    VideoGenerationMixin,
)

# Video-gen routes are aborted (credit guard); image-gen routes pass through
# (free) so the HAR captures the real request AND response.
_VIDEO_GEN_ROUTE_GLOBS = (
    "**/batchAsyncGenerateVideo*",
    "**/*GenerateVideo*",
)
```

- [ ] **Step 2: Add the HAR-capture client subclass**

```python
class _CaptureFlowApiClient(FlowApiClient):
    """FlowApiClient that records a HAR archive of its browser context.

    Injects record_har_path via the core _persistent_context_kwargs() seam —
    no capture concern leaks into core (same pattern as RecordingFlowApiClient).
    The HAR (request + embedded response bodies) is finalized when the context
    closes.
    """

    def __init__(self, *args: Any, har_path: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._har_path = har_path

    def _persistent_context_kwargs(self) -> dict[str, Any]:
        kwargs = super()._persistent_context_kwargs()
        kwargs["record_har_path"] = str(self._har_path)
        kwargs["record_har_content"] = "embed"
        return kwargs
```

- [ ] **Step 3: Add argparse `main()` and an empty async `_run`**

```python
async def _run(args: argparse.Namespace, out_path: Path) -> int:
    step("init", f"profile={args.profile} locale={args.locale} scenario={args.scenario}")
    return 0  # filled in later tasks


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Agentic Flow UI cohort recon")
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "ffroliva"))
    p.add_argument("--project", default=os.environ.get("GFLOW_CLI_PROJECT"), required=False)
    p.add_argument("--locale", default=os.environ.get("GFLOW_CLI_LOCALE", "en"))
    p.add_argument(
        "--scenario",
        default="p0",
        choices=["p0", "s0", "full"],
        help="p0 = pre-flight probe; s0 = cohort census; full = all scenarios",
    )
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    out_path = Path(args.out) if args.out else default_out_path("spike_agent_ui_cohort")
    return asyncio.run(_run(args, out_path))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verify it imports and `--help` works**

Run: `PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py --help`
Expected: argparse help text printed, exit 0. No ImportError.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/spike_agent_ui_cohort.py
git commit -m "feat(spike): scaffold agentic-UI cohort recon harness"
```

---

## Task 2: Pure composer-state classifier (TDD)

**Files:**
- Modify: `scripts/dev/spike_agent_ui_cohort.py`
- Test: `tests/scripts/test_spike_agent_ui_cohort.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_spike_agent_ui_cohort.py
import importlib.util
import sys
from pathlib import Path

_SPIKE = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "spike_agent_ui_cohort.py"
_spec = importlib.util.spec_from_file_location("spike_agent_ui_cohort", _SPIKE)
mod = importlib.util.module_from_spec(_spec)
sys.modules["spike_agent_ui_cohort"] = mod
_spec.loader.exec_module(mod)

ComposerSignals = mod.ComposerSignals
ComposerState = mod.ComposerState
classify_composer = mod.classify_composer


def _sig(**kw):
    base = dict(
        crop_present=False,
        agent_pill_present=False,
        agent_chat_panel_present=False,
        crop_recoverable=None,
    )
    base.update(kw)
    return ComposerSignals(**base)


def test_classify_crop_present_is_classic():
    assert classify_composer(_sig(crop_present=True)) is ComposerState.CLASSIC_MEDIA


def test_classify_agent_recoverable_is_over_classic():
    s = _sig(agent_pill_present=True, crop_recoverable=True)
    assert classify_composer(s) is ComposerState.AGENT_OVER_CLASSIC


def test_classify_agent_not_recoverable_is_forced():
    s = _sig(agent_chat_panel_present=True, crop_recoverable=False)
    assert classify_composer(s) is ComposerState.FORCED_AGENT


def test_classify_no_crop_no_agent_is_unknown():
    assert classify_composer(_sig()) is ComposerState.UNKNOWN


def test_classify_agent_recovery_not_attempted_is_unknown():
    # agent present but we have not yet tried to recover (crop_recoverable=None)
    assert classify_composer(_sig(agent_pill_present=True)) is ComposerState.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_spike_agent_ui_cohort.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'ComposerSignals'`.

- [ ] **Step 3: Implement the classifier** (add to `spike_agent_ui_cohort.py`, after the route globs)

```python
class ComposerState(str, Enum):
    CLASSIC_MEDIA = "classic_media"          # inline crop_* present
    AGENT_OVER_CLASSIC = "agent_over_classic"  # agent on top, crop_* recoverable
    FORCED_AGENT = "forced_agent"            # agent forced, crop_* unrecoverable
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ComposerSignals:
    crop_present: bool          # any MODE_SWITCH_TRIGGER_SELECTORS matched
    agent_pill_present: bool    # COMPOSER_AGENT_TOGGLE_SELECTOR matched
    agent_chat_panel_present: bool  # AGENT_CHAT_PANEL_CLOSE_SELECTOR matched
    crop_recoverable: bool | None   # after _exit_agent_mode: did crop_* return? None = not attempted


def classify_composer(s: ComposerSignals) -> ComposerState:
    """Map point-in-time composer signals to a cohort state.

    crop_present wins outright (media mode reachable now). Otherwise the
    recoverable-vs-forced split is decided by whether _exit_agent_mode brought
    crop_* back (crop_recoverable); None means recovery was not yet attempted,
    so we cannot tell -> UNKNOWN.
    """
    if s.crop_present:
        return ComposerState.CLASSIC_MEDIA
    agent_present = s.agent_pill_present or s.agent_chat_panel_present
    if not agent_present:
        return ComposerState.UNKNOWN
    if s.crop_recoverable is True:
        return ComposerState.AGENT_OVER_CLASSIC
    if s.crop_recoverable is False:
        return ComposerState.FORCED_AGENT
    return ComposerState.UNKNOWN
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_spike_agent_ui_cohort.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/spike_agent_ui_cohort.py tests/scripts/test_spike_agent_ui_cohort.py
git commit -m "feat(spike): add composer-state classifier with tests"
```

---

## Task 3: Pure redaction + signal-diff helpers (TDD)

**Files:**
- Modify: `scripts/dev/spike_agent_ui_cohort.py`
- Test: `tests/scripts/test_spike_agent_ui_cohort.py`

- [ ] **Step 1: Write the failing tests** (append to the test file)

```python
redact_cookies = mod.redact_cookies
fingerprint_map = mod.fingerprint_map
diff_signal_sets = mod.diff_signal_sets


def test_redact_cookies_drops_value_keeps_shape():
    out = redact_cookies([{"name": "X", "domain": ".labs.google", "path": "/", "value": "secret"}])
    assert out[0]["name"] == "X"
    assert out[0]["domain"] == ".labs.google"
    assert "value" not in out[0]
    assert out[0]["valueLen"] == len("secret")
    assert len(out[0]["valueSha8"]) == 8


def test_fingerprint_map_hashes_values():
    fm = fingerprint_map({"a": "hello", "b": ""})
    assert len(fm["a"]) == 8
    assert fm["b"] == ""  # empty stays empty (presence, not content)


def test_diff_signal_sets_reports_three_buckets():
    a = {"k1": "h1", "k2": "h2", "shared": "x"}
    b = {"k3": "h3", "shared": "y"}
    d = diff_signal_sets(a, b)
    assert d["onlyInA"] == ["k1", "k2"]
    assert d["onlyInB"] == ["k3"]
    assert d["changed"] == ["shared"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_spike_agent_ui_cohort.py -v`
Expected: FAIL with `AttributeError: ... 'redact_cookies'`.

- [ ] **Step 3: Implement the helpers** (add to `spike_agent_ui_cohort.py`)

```python
def _sha8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8] if value else ""


def redact_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep cookie shape (name/domain/path + value length/hash); drop the value.

    Lets us diff cookies across profiles by presence + value-hash without ever
    persisting a secret to disk.
    """
    out: list[dict[str, Any]] = []
    for c in cookies:
        value = str(c.get("value", ""))
        out.append(
            {
                "name": c.get("name"),
                "domain": c.get("domain"),
                "path": c.get("path"),
                "valueLen": len(value),
                "valueSha8": _sha8(value),
            }
        )
    return out


def fingerprint_map(kv: dict[str, Any]) -> dict[str, str]:
    """Reduce a {key: value} map (e.g. localStorage) to {key: sha8(value)}."""
    return {k: _sha8(str(v)) for k, v in kv.items()}


def diff_signal_sets(a: dict[str, str], b: dict[str, str]) -> dict[str, list[str]]:
    """Diff two {key: hash} maps into only-in-a / only-in-b / changed."""
    ka, kb = set(a), set(b)
    return {
        "onlyInA": sorted(ka - kb),
        "onlyInB": sorted(kb - ka),
        "changed": sorted(k for k in ka & kb if a[k] != b[k]),
    }
```

- [ ] **Step 4: Run to verify passing**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_spike_agent_ui_cohort.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/spike_agent_ui_cohort.py tests/scripts/test_spike_agent_ui_cohort.py
git commit -m "feat(spike): add cookie redaction and signal-diff helpers with tests"
```

---

## Task 4: Browser capture helpers (signals, gating, DOM dump, screenshot)

**Files:**
- Modify: `scripts/dev/spike_agent_ui_cohort.py`

These are impure (drive a live page). They are verified by code review + typecheck here; their real verification is the supervised run in Task 7.

- [ ] **Step 1: Add the page-signal reader and recovery probe**

```python
async def read_signals(page: Any) -> ComposerSignals:
    """Read point-in-time composer signals (recovery not yet attempted)."""
    crop = await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
    pill = await page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR).count() > 0
    chat = await page.locator(AGENT_CHAT_PANEL_CLOSE_SELECTOR).count() > 0
    return ComposerSignals(
        crop_present=crop,
        agent_pill_present=pill,
        agent_chat_panel_present=chat,
        crop_recoverable=None,
    )


async def probe_recoverable(page: Any) -> bool:
    """Attempt the existing agent-exit and report whether crop_* returned."""
    await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
    return await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
```

- [ ] **Step 2: Add the gating-signal capture (cookies/storage/__NEXT_DATA__)**

```python
async def capture_gating(page: Any) -> dict[str, Any]:
    """Capture redacted cohort signals: cookies, storage, Next.js data flags."""
    all_cookies = await page.context.cookies()
    flow_cookies = await page.context.cookies(["https://labs.google"])
    local = await page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")
    session = await page.evaluate("() => Object.fromEntries(Object.entries(sessionStorage))")
    next_raw = await page.evaluate(
        "() => document.getElementById('__NEXT_DATA__')?.textContent ?? null"
    )
    next_keys: list[str] = []
    if next_raw:
        try:
            parsed = json.loads(next_raw)
            # Surface experiment/flag-ish top-level keys without dumping payload.
            props = parsed.get("props", {}).get("pageProps", {})
            next_keys = sorted(props.keys()) if isinstance(props, dict) else []
        except (ValueError, AttributeError):
            next_keys = ["<unparseable>"]
    return {
        "cookiesAll": redact_cookies(all_cookies),
        "cookiesFlow": redact_cookies(flow_cookies),
        "localStorage": fingerprint_map(local),
        "sessionStorage": fingerprint_map(session),
        "nextDataPagePropKeys": next_keys,
    }
```

- [ ] **Step 3: Add the composer DOM dump and screenshot helper**

```python
_DUMP_COMPOSER_JS = r"""
() => {
  const pickLigatures = (root) =>
    Array.from(root.querySelectorAll('i.google-symbols, i.material-symbols-outlined'))
      .map((el) => (el.textContent || '').trim())
      .filter(Boolean);
  const composer = document.querySelector("div:has(div[role='textbox'])") || document.body;
  return {
    url: location.href,
    title: document.title,
    composerOuterHtmlLen: (composer.outerHTML || '').length,
    composerOuterHtml: (composer.outerHTML || '').slice(0, 20000),
    ligatures: Array.from(new Set(pickLigatures(document))).sort(),
    hasAgentText: !!document.querySelector("button:has(span)") &&
      /\bAgent\b/.test(document.body.innerText || ''),
  };
}
"""


async def dump_composer_dom(page: Any) -> dict[str, Any]:
    return await page.evaluate(_DUMP_COMPOSER_JS)


async def snap(page: Any, out_dir: Path, label: str) -> None:
    """Screenshot helper. NOTE: frames may carry account avatar/email — local only."""
    out_dir.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(out_dir / f"{label}.png"))
```

- [ ] **Step 4: Verify it typechecks and imports**

Run: `.venv\Scripts\python.exe -m pyright scripts/dev/spike_agent_ui_cohort.py`
Expected: 0 errors (Playwright `page: Any` is intentional — spikes use `Any` for the page, matching `spike_issue174_library_ui_recon.py`).
Then: `PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py --help` → exit 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/spike_agent_ui_cohort.py
git commit -m "feat(spike): add live capture helpers (signals, gating, DOM, screenshot)"
```

---

## Task 5: P0 pre-flight probe

**Files:**
- Modify: `scripts/dev/spike_agent_ui_cohort.py`

- [ ] **Step 1: Implement the P0 probe and a capture-client context manager**

```python
from contextlib import asynccontextmanager  # add to imports at top
from collections.abc import AsyncGenerator   # add to imports at top


@asynccontextmanager
async def build_capture_client(
    profile_dir: Path, har_path: Path, *, headless: bool = False
) -> AsyncGenerator[FlowApiClient, None]:
    async with _CaptureFlowApiClient(
        profile_dir=profile_dir, headless=headless, har_path=har_path
    ) as client:
        yield client


async def _classify_live(page: Any) -> dict[str, Any]:
    """Read signals, attempt recovery if no crop, and classify."""
    signals = await read_signals(page)
    state = classify_composer(signals)
    recoverable: bool | None = None
    if state in (ComposerState.UNKNOWN, ComposerState.FORCED_AGENT) and not signals.crop_present:
        recoverable = await probe_recoverable(page)
        signals = ComposerSignals(
            crop_present=await VideoGenerationMixin._media_panel_present(page),  # noqa: SLF001
            agent_pill_present=signals.agent_pill_present,
            agent_chat_panel_present=signals.agent_chat_panel_present,
            crop_recoverable=recoverable,
        )
        state = classify_composer(signals)
    return {"signals": asdict(signals), "state": state.value}


async def probe_p0(page: Any, out_dir: Path) -> dict[str, Any]:
    """Pre-flight: does gflow's own profile render the agentic UI on open?"""
    await snap(page, out_dir, "p0_on_open")
    result = await _classify_live(page)
    result["dom"] = await dump_composer_dom(page)
    await snap(page, out_dir, "p0_after_classify")
    step("p0", f"composer state = {result['state']}")
    return result
```

- [ ] **Step 2: Wire `_run` to drive the P0 path**

Replace the placeholder `_run` body with:

```python
async def _run(args: argparse.Namespace, out_path: Path) -> int:
    if not args.project:
        print("[spike] ERROR: --project <uuid> is required.", file=sys.stderr, flush=True)
        return 2
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    har_path = out_dir / f"{out_path.stem}.har"
    profile_dir = resolve_profile_dir(args.profile)

    result: dict[str, Any] = {
        "spike": "agent-ui-cohort",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": args.profile,
        "locale": args.locale,
        "project": args.project,
        "scenario": args.scenario,
        "scenarios": {},
    }

    async with build_capture_client(profile_dir, har_path) as client:
        page = await client._checkout_page()  # noqa: SLF001
        editor_url = routes.project_editor_url(args.locale, args.project)
        step("nav", f"goto {editor_url}")
        await page.goto(editor_url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(4_000)
        await page.keyboard.press("Escape")

        result["scenarios"]["p0"] = await probe_p0(page, out_dir)
        if args.scenario in ("s0", "full"):
            result["scenarios"].update(await run_scenarios(page, out_dir, args))

    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    step("done", f"wrote {out_path} (HAR: {har_path})")
    return 0
```

- [ ] **Step 3: Add a temporary `run_scenarios` stub so the module imports**

```python
async def run_scenarios(page: Any, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    return {}  # implemented in Task 6
```

- [ ] **Step 4: Verify import + typecheck + help**

Run: `.venv\Scripts\python.exe -m pyright scripts/dev/spike_agent_ui_cohort.py`
Expected: 0 errors.
Run: `PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py --help`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/spike_agent_ui_cohort.py
git commit -m "feat(spike): add P0 pre-flight probe and HAR capture client wiring"
```

---

## Task 6: Scenarios S0–S9 orchestration

**Files:**
- Modify: `scripts/dev/spike_agent_ui_cohort.py`

Recon scenarios capture broadly (DOM dump + screenshot + known-selector probes + gating snapshot) rather than clicking unknown agentic selectors — the agentic selectors are precisely what we are discovering, so we record candidates instead of hardcoding them.

- [ ] **Step 1: Add the route-abort credit guard**

```python
async def _install_credit_guard(page: Any, captured: dict[str, Any]) -> None:
    """Abort video-gen routes ($0); capture the first aborted payload."""

    async def _on_route(route: Any) -> None:
        req = route.request
        if "video" not in captured:
            try:
                captured["video"] = {"url": req.url, "postData": req.post_data}
            except Exception as e:  # noqa: BLE001
                captured["video"] = {"error": f"{type(e).__name__}: {e}"}
        await route.abort()

    for glob in _VIDEO_GEN_ROUTE_GLOBS:
        await page.route(glob, _on_route)
```

- [ ] **Step 2: Replace the `run_scenarios` stub with the real implementation**

```python
async def run_scenarios(page: Any, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    aborted: dict[str, Any] = {}
    await _install_credit_guard(page, aborted)

    # S0 cohort census (this run's profile) + S1 initial state
    out["s0_s1"] = await _classify_live(page)
    out["s0_s1"]["dom"] = await dump_composer_dom(page)
    await snap(page, out_dir, "s1_initial")

    # S2 Agent button: dump + click + record transition
    out["s2"] = {}
    pill = page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR).first
    out["s2"]["pillPresentBefore"] = await pill.count() > 0
    if out["s2"]["pillPresentBefore"]:
        await pill.click(force=True, timeout=2000)
        await page.wait_for_timeout(800)
        await snap(page, out_dir, "s2_after_pill_click")
        out["s2"]["after"] = await _classify_live(page)
        out["s2"]["domAfter"] = await dump_composer_dom(page)

    # S3/S4 agent expanded window: open settings / capture panel DOM (best-effort,
    # selectors unknown -> capture full ligature inventory + body text markers)
    out["s3_s4"] = await dump_composer_dom(page)
    await snap(page, out_dir, "s3_s4_expanded")

    # S5 wire (image, free): submit a prompt and let HAR capture the response.
    out["s5"] = await _capture_image_submit(page, out_dir)

    # S6 wire (entity, #174): video route-abort payload (if any fired)
    out["s6"] = {"abortedVideoPayload": aborted.get("video")}

    # S7 gating signals (redacted)
    out["s7_gating"] = await capture_gating(page)

    # S8 recoverable verdict — exhaust avenues, record each
    out["s8"] = await _recoverable_audit(page, out_dir)

    # S9 profile/engine axis is captured by RUNNING this spike per profile+engine;
    # the cross-profile diff is computed offline from the per-run JSONs (Task 7).
    out["s9_note"] = (
        f"axis sample: profile={args.profile} engine={os.environ.get('GFLOW_CLI_BROWSER_ENGINE', 'playwright')}"
    )
    return out
```

- [ ] **Step 3: Add the S5 image-submit and S8 recoverable-audit helpers**

```python
async def _capture_image_submit(page: Any, out_dir: Path) -> dict[str, Any]:
    """Type a prompt into the composer and submit. Image gen is free; HAR holds
    the real batchGenerateImages request+response. Returns what was attempted."""
    info: dict[str, Any] = {"attempted": False}
    box = page.locator("div[role='textbox']").first
    if await box.count() == 0:
        info["error"] = "no textbox found"
        return info
    await box.click()
    await box.type("a small red cube on white, product shot")
    await snap(page, out_dir, "s5_prompt_typed")
    # Submit via the arrow_forward button if present (locale-invariant ligature).
    submit = page.locator("button:has(i:text('arrow_forward'))").first
    if await submit.count() > 0:
        await submit.click(force=True, timeout=2000)
        info["attempted"] = True
        await page.wait_for_timeout(6_000)
        await snap(page, out_dir, "s5_after_submit")
    else:
        info["error"] = "no arrow_forward submit found"
    return info


async def _recoverable_audit(page: Any, out_dir: Path) -> dict[str, Any]:
    """Try every avenue to reach classic media mode; log each outcome."""
    audit: dict[str, Any] = {"avenues": []}

    async def _try(name: str, coro: Any) -> None:
        before = await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            audit["avenues"].append({"name": name, "error": f"{type(e).__name__}: {e}"})
            return
        await page.wait_for_timeout(600)
        after = await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
        audit["avenues"].append({"name": name, "cropBefore": before, "cropAfter": after})

    await _try("exit_agent_mode", VideoGenerationMixin._exit_agent_mode(page))  # noqa: SLF001
    await _try("escape_key", page.keyboard.press("Escape"))
    await snap(page, out_dir, "s8_after_recovery_attempts")
    audit["cropReachable"] = await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
    audit["verdict"] = "recoverable" if audit["cropReachable"] else "forced"
    return audit
```

- [ ] **Step 4: Verify typecheck + import + help**

Run: `.venv\Scripts\python.exe -m pyright scripts/dev/spike_agent_ui_cohort.py`
Expected: 0 errors.
Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_spike_agent_ui_cohort.py -v`
Expected: 8 passed (pure helpers unaffected).
Run: `PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py --help` → exit 0.

- [ ] **Step 5: Lint/format and commit**

Run: `.venv\Scripts\python.exe -m ruff check scripts/dev/spike_agent_ui_cohort.py` then `.venv\Scripts\python.exe -m ruff format scripts/dev/spike_agent_ui_cohort.py`
Expected: no lint errors; formatted clean.

```bash
git add scripts/dev/spike_agent_ui_cohort.py
git commit -m "feat(spike): implement scenarios S0-S9 with credit-free capture"
```

---

## Task 7: Supervised live runs (USER-DRIVEN)

**This task cannot be auto-executed.** It requires the user at a headed browser, signed into `ffroliva` and `denon82`. The agent prepares the exact commands; the user runs them (or runs them via `! <command>` in-session) and reports the artifact paths. One disciplined run per (profile, scenario) — WAF heat.

- [ ] **Step 1: P0 pre-flight on `ffroliva` (the priority question)**

Run:
```
PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py --profile ffroliva --project <ffroliva-project-uuid> --locale en --scenario p0
```
Capture: the printed `composer state` and the JSON/screenshots under `scripts/dev/_spike_out/`.
**Decision gate:** if state is `classic_media`, gflow's own profile does NOT see the agentic UI → record this prominently (it reorders the whole feature priority) before continuing.

- [ ] **Step 2: Full spike on `ffroliva` (en)**

Run:
```
PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py --profile ffroliva --project <uuid> --locale en --scenario full
```

- [ ] **Step 3: Full spike on `denon82` (pt-BR)**

Run:
```
PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py --profile denon82 --project <uuid> --locale pt --scenario full
```

- [ ] **Step 4: Engine axis — repeat P0 under patchright (both profiles)**

Run (PowerShell):
```
$env:GFLOW_CLI_BROWSER_ENGINE="patchright"; PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_agent_ui_cohort.py --profile ffroliva --project <uuid> --locale en --scenario p0; Remove-Item Env:\GFLOW_CLI_BROWSER_ENGINE
```

- [ ] **Step 5: Verify capture completeness (no commit — artifacts are gitignored)**

For each run confirm: a `.har` exists and is non-empty; `s7_gating` has cookies/storage; screenshots are present; and (privacy) spot-check that no screenshot or stored value leaks an email/secret. List the artifact paths for the recon doc.

---

## Task 8: Recon doc + index + close-out

**Files:**
- Create: `docs/AGENT_UI_RECON.md`
- Modify: `docs/INDEX.md`

- [ ] **Step 1: Write `docs/AGENT_UI_RECON.md` from the captured evidence**

Use this skeleton; fill every section from the Task 7 artifacts (cite the JSON/HAR filenames). **No claim without an artifact reference.**

```markdown
# Agentic Flow UI — Recon

> Reverse-engineered from `scripts/dev/spike_agent_ui_cohort.py` runs on
> ffroliva (en) and denon82 (pt-BR), 2026-06-13. Source artifacts cited inline.

## 1. Does gflow's own profile hit the agentic UI? (P0)
- ffroliva (gflow profile): <state> — evidence: <p0 json + screenshot>
- Conclusion: <does gflow hit it? does it match the primary-profile screenshots?>

## 2. Gating mechanism
- Cookie/localStorage/flag delta (forced vs classic, across locales): <from s7 diff>
- Per-account (server) vs per-profile/fingerprint (client): <verdict + evidence>
- Engine effect (playwright vs patchright): <verdict>
- Steerable? <yes/no + how>

## 3. The Agent button & expanded window (DOM)
- Pill selector: <confirmed/new> — evidence: <s2 domAfter>
- Click behaviour: <restores classic | opens chat panel>
- Agent settings (aspect/model/upscale) location + selectors: <s3_s4 dump>

## 4. The wire
- Agent image submit endpoint + payload/response shape: <from HAR>
- Reference entity ride-the-wire (#174): <result>
- Confirm-before-generating effect: <result>

## 5. Recoverable vs forced (S8)
- Avenues tried + outcomes: <s8 audit>
- Verdict: <recoverable | forced>

## 6. Recommendation for the feature plan
- Detection signal(s) to use: <DOM / cookie / flag>
- Disambiguation rule (recoverable vs forced): <rule>
- Fail-cleanly vs drive-the-agent: <call, with rationale>
```

- [ ] **Step 2: Add the routing entry to `docs/INDEX.md`**

Add under the recon/feature docs section:
```markdown
- [AGENT_UI_RECON.md](AGENT_UI_RECON.md) — agentic Flow UI: gating mechanism, detection signals, wire protocol (#183/#174)
```

- [ ] **Step 3: Final quality gate**

Run: `.venv\Scripts\python.exe -m pytest tests/scripts/test_spike_agent_ui_cohort.py -v`
Run: `.venv\Scripts\python.exe -m pyright scripts/dev/spike_agent_ui_cohort.py`
Run: `.venv\Scripts\python.exe -m ruff check scripts/dev/spike_agent_ui_cohort.py`
Expected: tests pass, 0 pyright errors, no lint errors.

- [ ] **Step 4: Commit (code + docs only; never the `_spike_out/` artifacts)**

```bash
git add docs/AGENT_UI_RECON.md docs/INDEX.md
git commit -m "docs: add agentic Flow UI recon findings (#183 #174)"
```

- [ ] **Step 5: Open the PR to `develop`**

```bash
git push -u origin docs/agent-ui-recon
gh pr create --base develop --title "Agentic Flow UI cohort recon (#183 #174)" --body "<plain-string summary + evidence links; never a heredoc>"
```

---

## Self-review

**Spec coverage:** §2 objective → Tasks 4–8. §3.1 profile/engine axis → Task 6 S9 + Task 7 Step 4 + recon §2. §4 unknowns (gating/DOM/wire) → S7 / S2–S4 / S5–S6. §5 methodology (credit-free, HAR, redaction, three-axis) → Tasks 1,3,4,6,7. §6 scenarios P0+S0–S9 → Tasks 5,6,7. §8 verification → Task 7 Step 5 + Task 8 Step 1 "no claim without artifact". §7 deliverables → all four files. Covered.

**Placeholder scan:** No "TBD/TODO/handle edge cases" in code steps; every code step shows complete code. The recon doc skeleton intentionally has `<...>` fill-ins because its content IS the spike's output (cannot be known pre-run) — this is data-entry, not a code placeholder.

**Type consistency:** `ComposerSignals` / `ComposerState` / `classify_composer` signatures match across Tasks 2, 4, 5, 6. `redact_cookies` / `fingerprint_map` / `diff_signal_sets` match Tasks 3 and their callers in `capture_gating` (Task 4). `build_capture_client` / `_CaptureFlowApiClient` consistent (Tasks 1, 5). `run_scenarios` stub (Task 5) → real impl (Task 6) same signature.
