r"""Is the migrated composer's settings trigger ever VISIBLE BUT UNCLICKABLE? ($0)

Settles the mechanism question behind #776.

A reporter on Windows / `flow.google.com` reaches `migrated.editor_ready` and then dies
5.039 s later with a bare Playwright `TimeoutError` and no exit code. By elimination that
is `migrated_composer.py:884` — `await trigger.click(timeout=5000)`, the one unguarded
5000 ms call between `editor_ready` and the next log line. Its sibling one line above
(`trigger.wait_for(state="visible", timeout=5000)`, `:870`) IS guarded and would have
raised `UiSelectorDriftError` (exit 23) instead, so the trigger was *visible* and the
*click* is what expired.

    A CLICK THAT DOES NOT LAND IS EVIDENCE ABOUT ACTIONABILITY.
    IT IS NEVER EVIDENCE THAT THE CONTROL IS MISSING.

`ui_automation.py:1210` (`_probe_page_block`, from #593) records the mechanism that
produces exactly this shape, measured live on 2026-08-27:

    while Flow's announcement modal is up the body carries `pointer-events: none` and is
    neither `aria-hidden` nor `inert` — so every control reads visible and enabled yet
    never receives a click.

`_require_unblocked` guards against it in the labs driver at four call sites. The migrated
driver (`migrated_composer.py`, PR #664, later than #593's audit) calls it **zero** times,
and its only overlay handling — `_dismiss_dialog`, `:743` — matches `[role='dialog']`
alone and runs immediately after `goto(wait_until="domcontentloaded")`, which the module's
own comment at `:607` says is seconds before Angular mounts the composer.

So there are two separate unknowns, and this probe measures both:

1. **Does the block state occur on THIS host?** #593 measured labs.google. Whether
   flow.google.com's Angular frontend blocks the body the same way has never been read.
2. **Is the driver's `_dismiss_dialog` timed to miss it?** Sampling from `goto` through
   composer mount shows when overlays actually appear relative to when the driver looks.

## Pre-registered readings — written before the run, so the result cannot be respun

| Outcome | Reading |
|---|---|
| body `pointer-events:none` observed at/after mount in >=1 of N | the #593 mechanism reaches the migrated host; porting the guard is the fix |
| trigger present+visible but `hit_testable:false` in >=1 of N | occlusion WITHOUT a body block; the guard needs the hit-test too, not just the body probe |
| replayed click expires while the trigger reads visible | #776 reproduced locally — the strongest possible result |
| 0/N, click always lands | **does not reproduce on this profile; settles nothing** about the reporter's machine |

That last row is the one worth pre-writing. A 0/N here does NOT clear
`migrated_composer.py:884`: the confirmed defect in #776 is that the failure arrives
**unattributable**, and that is true whatever covers the trigger. A disappearance is not
evidence of transience — it is equally consistent with this account never having been
served the announcement. Report it as *unmeasured*, and say what would settle it.

## Cost

Zero. Navigation, DOM reads, one click on the settings trigger (which opens Flow's own
settings pane and changes no setting), and Escape to close it. Nothing is typed, nothing
submitted, nothing created or deleted. No credits, no daily quota.

    python scripts/dev/spike_migrated_click_blocked.py \
        --profile ci-probe --project <project-id> --samples 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

# Copied from migrated_composer.py:82/123/83 so the probe and the driver cannot disagree
# about which controls are under discussion.
READY_ANCHOR = ".settings-trigger-button"
DIALOG = "[role='dialog']"
OVERLAY = ".cdk-overlay-pane"

#: The driver's own click budget (migrated_composer.py:884). Replayed exactly: a probe
#: that waited longer would "succeed" on a page the real run would have abandoned.
DRIVER_CLICK_TIMEOUT_MS = 5000

#: `_probe_page_block`'s reading, plus the hit-test it does NOT do. The body property is
#: selector-free and locale-invariant — a property of BEING blocked rather than of any
#: particular announcement — which is why it survives whatever Flow ships next.
_BLOCK_JS = r"""
() => {
  const body = getComputedStyle(document.body);
  const html = getComputedStyle(document.documentElement);
  const trig = document.querySelector('.settings-trigger-button');
  let t = null;
  if (trig) {
    const box = trig.getBoundingClientRect();
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    const top = (box.width && box.height) ? document.elementFromPoint(cx, cy) : null;
    const cs = getComputedStyle(trig);
    // Present, visible, enabled and CLICKABLE are four different things. Playwright's
    // actionability wait fails on the fourth while the first three all read fine, which
    // is the entire reason #776 arrives with no message.
    t = {
      hidden_attr: trig.hasAttribute('hidden'),
      disabled: trig.hasAttribute('disabled'),
      display: cs.display,
      visibility: cs.visibility,
      pointer_events: cs.pointerEvents,
      w: Math.round(box.width), h: Math.round(box.height),
      hit_testable: !!top && (top === trig || trig.contains(top) || top.contains(trig)),
      // What is actually on top, named structurally. This is the line that turns
      // "a timeout" into "a <flow-changelog-dialog> was over it".
      occluded_by: top && !(top === trig || trig.contains(top) || top.contains(trig))
        ? top.tagName.toLowerCase() +
          (top.classList.length ? '.' + [...top.classList].slice(0, 3).join('.') : '')
        : null,
    };
  }
  const overlays = [...document.querySelectorAll('.cdk-overlay-pane')].map(o => {
    const box = o.getBoundingClientRect();
    return {
      tag: o.tagName.toLowerCase(),
      classes: [...o.classList].slice(0, 4),
      // Component boundaries inside the overlay identify WHAT it is without reading
      // a single translated label.
      custom_tags: [...new Set([...o.querySelectorAll('*')]
        .map(e => e.tagName.toLowerCase()).filter(x => x.includes('-')))].slice(0, 8),
      has_changelog_link: !!o.querySelector("a[href*='changelog']"),
      visible: box.width > 0 && box.height > 0,
    };
  });
  return {
    t_ms: Math.round(performance.now()),
    url: location.href,
    ready_state: document.readyState,
    // THE #593 signal.
    body_pointer_events: body.pointerEvents,
    html_pointer_events: html.pointerEvents,
    body_aria_hidden: document.body.getAttribute('aria-hidden'),
    body_inert: document.body.hasAttribute('inert'),
    counts: {
      settings_trigger: document.querySelectorAll('.settings-trigger-button').length,
      dialog: document.querySelectorAll("[role='dialog']").length,
      overlay: document.querySelectorAll('.cdk-overlay-pane').length,
      iframe: document.querySelectorAll('iframe').length,
      contenteditable: document.querySelectorAll("[contenteditable='true']").length,
    },
    // A changelog iframe is the #593 carrier on labs; recorded by href, not by text.
    changelog_iframes: [...document.querySelectorAll('iframe')]
      .map(f => f.getAttribute('src') || '')
      .filter(s => s.includes('changelog')),
    trigger: t,
    overlays,
  };
}
"""


class ProbeFailedError(RuntimeError):
    """A step this probe cannot complete. Never downgraded to a verdict."""


def _blocked(sample: dict[str, Any]) -> bool:
    """True when the app behind is unclickable — `_overlay_blocks_page`'s reading."""
    return sample.get("body_pointer_events") == "none"


def _occluded(sample: dict[str, Any]) -> bool:
    """True when the trigger is rendered but something else answers a hit-test on it."""
    t = sample.get("trigger")
    return bool(t and t.get("w") and not t.get("hit_testable"))


async def _timeline(page: Any, seconds: float, every_ms: int) -> list[dict[str, Any]]:
    """Sample from now until `seconds`, so WHEN a block appears is visible, not just IF.

    The driver looks for a dialog once, immediately after `goto` returns. If overlays
    mount later than that single read, the miss is structural and no amount of retrying
    the same call site fixes it.
    """
    out: list[dict[str, Any]] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            out.append(await page.evaluate(_BLOCK_JS))
        except Exception as exc:  # noqa: BLE001 - a mid-navigation read is not a failure
            out.append({"error": str(exc)[:200], "t_ms": None})
        await page.wait_for_timeout(every_ms)
    return out


def _summarise(tag: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    good = [s for s in samples if "error" not in s]
    blocked = [s for s in good if _blocked(s)]
    occluded = [s for s in good if _occluded(s)]
    first_trigger = next((s for s in good if (s["counts"]["settings_trigger"] or 0) > 0), None)
    summary = {
        "samples": len(samples),
        "readable": len(good),
        "blocked_samples": len(blocked),
        "occluded_samples": len(occluded),
        "first_trigger_t_ms": first_trigger["t_ms"] if first_trigger else None,
        "max_overlays": max((s["counts"]["overlay"] for s in good), default=0),
        "max_dialogs": max((s["counts"]["dialog"] for s in good), default=0),
        "changelog_iframes": sorted({i for s in good for i in s["changelog_iframes"]}),
        "occluders": sorted({s["trigger"]["occluded_by"] for s in occluded if s["trigger"]}),
    }
    step(
        tag,
        f"readable={summary['readable']}/{summary['samples']} "
        f"blocked={summary['blocked_samples']} occluded={summary['occluded_samples']} "
        f"trigger_at={summary['first_trigger_t_ms']}ms "
        f"overlays<={summary['max_overlays']} dialogs<={summary['max_dialogs']}",
    )
    if summary["occluders"]:
        step(f"{tag}.occluders", str(summary["occluders"]))
    return summary


async def _replay_driver_click(page: Any) -> dict[str, Any]:
    """Do exactly what `_open_pane` does, and time it.

    This is the A/B that matters: the same two calls, same timeouts, same order. A click
    that lands here on a page the probe just read as unblocked is a control result; one
    that expires while `wait_for(visible)` passed IS #776, reproduced.
    """
    trigger = page.locator(READY_ANCHOR).first
    out: dict[str, Any] = {}
    t0 = time.monotonic()
    try:
        await trigger.wait_for(state="visible", timeout=DRIVER_CLICK_TIMEOUT_MS)
        out["wait_for_visible_ms"] = round((time.monotonic() - t0) * 1000)
        out["wait_for_visible"] = "passed"
    except Exception as exc:  # noqa: BLE001 - this branch is exit 23 in the driver
        out["wait_for_visible_ms"] = round((time.monotonic() - t0) * 1000)
        out["wait_for_visible"] = "TIMED OUT"
        out["wait_error"] = str(exc)[:300]
        step("replay", "wait_for(visible) TIMED OUT — this run is the exit-23 branch, not #776")
        return out

    # State captured BEFORE the click, so a block can be attributed to the click that
    # follows rather than inferred from the wreckage afterwards.
    out["before"] = await page.evaluate(_BLOCK_JS)
    t1 = time.monotonic()
    try:
        await trigger.click(timeout=DRIVER_CLICK_TIMEOUT_MS)
        out["click_ms"] = round((time.monotonic() - t1) * 1000)
        out["click"] = "landed"
        step("replay", f"click LANDED in {out['click_ms']}ms")
    except Exception as exc:  # noqa: BLE001 - the whole point of the probe
        out["click_ms"] = round((time.monotonic() - t1) * 1000)
        out["click"] = "TIMED OUT"
        out["click_error"] = str(exc)[:400]
        step("replay", f"click TIMED OUT after {out['click_ms']}ms — #776 REPRODUCED")
    out["after"] = await page.evaluate(_BLOCK_JS)
    # Leave the account exactly as found: the pane changes no setting, but an open
    # overlay would poison a later sample in this same run.
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    except Exception:  # noqa: BLE001 - cleanup is best-effort
        pass
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--samples", type=int, default=3, help="independent navigations")
    ap.add_argument("--watch-s", type=float, default=14.0, help="timeline length per sample")
    ap.add_argument("--every-ms", type=int, default=250)
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    findings: dict[str, Any] = {
        "profile": args.profile,
        "project": args.project,
        "question": (
            "is .settings-trigger-button ever visible-but-unclickable on flow.google.com, "
            "and does body{pointer-events:none} (#593) occur on the migrated host? (#776)"
        ),
        "cost": "credit-free: navigation, DOM reads, one settings-pane click, Escape",
        "driver_click_timeout_ms": DRIVER_CLICK_TIMEOUT_MS,
        "runs": [],
    }
    url = f"https://flow.google.com/project/{args.project}"

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        for i in range(args.samples):
            page = await context.new_page()
            run: dict[str, Any] = {"n": i + 1}
            try:
                step(f"run{i + 1}.goto", url)
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

                # The driver's ONE look for a dialog happens right here, before any
                # settle. Recorded separately so "what _dismiss_dialog could have seen"
                # is a measurement rather than an argument about the code.
                run["at_dismiss_dialog_time"] = await page.evaluate(_BLOCK_JS)
                step(
                    f"run{i + 1}.dismiss_window",
                    f"trigger={run['at_dismiss_dialog_time']['counts']['settings_trigger']} "
                    f"dialogs={run['at_dismiss_dialog_time']['counts']['dialog']} "
                    f"body_pe={run['at_dismiss_dialog_time']['body_pointer_events']}",
                )

                samples = await _timeline(page, args.watch_s, args.every_ms)
                run["timeline_summary"] = _summarise(f"run{i + 1}.timeline", samples)
                run["timeline"] = samples

                if not run["timeline_summary"]["readable"]:
                    raise ProbeFailedError(
                        f"run {i + 1}: every DOM read failed — this run says NOTHING"
                    )
                landed = page.url
                run["landed_url"] = landed
                if "flow.google.com/project/" not in landed:
                    # Same discipline as ensure_editor: a landing page cannot answer a
                    # question about the composer, and must not be read as one.
                    run["verdict"] = "NOT ON A PROJECT PAGE — says nothing about the click"
                    step(f"run{i + 1}.landed", f"{landed} — skipped")
                    continue

                run["replay"] = await _replay_driver_click(page)
                shot = default_out_path(f"spike_click_blocked_run{i + 1}", ".png")
                await page.screenshot(path=str(shot))
                run["screenshot"] = shot.name
            finally:
                findings["runs"].append(run)
                await page.close()

    # ---- the question, answered from the read -------------------------------
    scored = [r for r in findings["runs"] if "replay" in r]
    blocked_runs = [r for r in scored if r["timeline_summary"]["blocked_samples"]]
    occluded_runs = [r for r in scored if r["timeline_summary"]["occluded_samples"]]
    failed_clicks = [r for r in scored if r["replay"].get("click") == "TIMED OUT"]
    late_trigger = [
        r
        for r in scored
        if not r["at_dismiss_dialog_time"]["counts"]["settings_trigger"]
        and r["timeline_summary"]["first_trigger_t_ms"] is not None
    ]

    findings["verdict"] = {
        "scored_runs": len(scored),
        "runs_with_body_block": len(blocked_runs),
        "runs_with_occluded_trigger": len(occluded_runs),
        "runs_where_the_click_expired": len(failed_clicks),
        "runs_where_dismiss_dialog_ran_before_the_composer_existed": len(late_trigger),
        # Stated as an observation, never as a conclusion about the reporter's machine.
        "reading": (
            "#776 REPRODUCED — the driver's own click sequence expired here"
            if failed_clicks
            else "the block/occlusion state was observed, but the click still landed"
            if blocked_runs or occluded_runs
            else "NOT REPRODUCED on this profile — unmeasured, settles nothing about #776; "
            "the confirmed defect (an unattributable timeout at migrated_composer.py:884) "
            "is independent of which overlay causes it"
        ),
    }

    out = default_out_path("spike_migrated_click_blocked")
    out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    step("verdict", json.dumps(findings["verdict"], indent=2))
    step("out", str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
