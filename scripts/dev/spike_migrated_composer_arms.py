r"""Does the migrated composer have a SECOND arm, and what hides the settings trigger? ($0)

Settles the discriminating question behind #749.

A reporter on `flow.google.com` finds `READY_ANCHOR = ".settings-trigger-button"`
(`src/gflow_cli/api/transports/migrated_composer.py:73`) present in the DOM but carrying
a bare `hidden` attribute, so the `state="visible"` wait at `:466` can never pass. Their
DOM shows a different settings control living in an `agent-footer-actions` component:
a `button.agent-action-button` whose `mat-icon` ligature is `tune`.

`migrated_composer.py` knows nothing about arms — grep it for `mode_control`, `agent` or
`sidebar` and you get nothing. `mode_control.py`, which DOES handle the agentic/classic
split, refuses this host outright (`raise_if_migrated`, `mode_control.py:146`). So the
migrated driver assumes the classic composer is the only thing this host renders.

Our own capture from 2026-09-06 (`_spike_out/migrated_editor_dom_inventory_20260906_214316.json`)
shows the classic arm: `settings_trigger: 1`, `crop_16_9` among the button ligatures, and
NO `tune` and no `agent-*` anything. That proves which arm we were on. It does not say
whether the other arm is reachable from here — it never recorded `hidden`, component tag
names, or occlusion. Hence this probe.

    A SELECTOR THAT DOES NOT MATCH IS EVIDENCE ABOUT THE SELECTOR.
    IT IS NEVER EVIDENCE ABOUT THE FEATURE.

So this records a POSITIVE INVENTORY of what IS on the page — every custom element tag,
every ligature with its carrier, the trigger's own attributes and hit-testability — and
only then asks whether an agentic arm is present. An absent `agent-footer-actions` is
reported as "not rendered in this state on this profile", never as "does not exist".

Both sides of the transition: if a mode-toggle candidate is found (a `button[aria-pressed]`,
the labs shape, or a `tune` carrier), it is clicked once and the whole inventory retaken,
so a signal present in both states is visibly not a settle signal.

Credit-free: navigation, DOM reads, and at most ONE click on a mode toggle. Nothing is
typed, nothing is submitted, nothing is created or deleted.

    python scripts/dev/spike_migrated_composer_arms.py \
        --profile ffroliva --project c5550ed7-7b6e-43db-8cd3-4d56a74b1244
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

# Copied from migrated_composer.py:73 so the probe and the driver cannot disagree about
# which control is under discussion.
READY_ANCHOR = ".settings-trigger-button"

#: The reporter's replacement control, anchored structurally. `aria-label="Configuración"`
#: is their Spanish locale and is exactly what AGENTS.md forbids anchoring on; the
#: component class plus the Material Symbols ligature carry the same identity in every
#: locale. `mat-icon` is the migrated host's carrier — labs uses `i.google-symbols`, and
#: that mismatch alone looks identical to a missing feature.
AGENT_TUNE = "button:has(mat-icon:text-is('tune'))"

_INVENTORY_JS = r"""
() => {
  const vis = (el) => {
    const cs = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    const top = (box.width && box.height) ? document.elementFromPoint(cx, cy) : null;
    return {
      hidden_attr: el.hasAttribute('hidden'),
      display: cs.display,
      visibility: cs.visibility,
      w: Math.round(box.width), h: Math.round(box.height),
      // Present, visible and still not clickable are three different things.
      hit_testable: !!top && (top === el || el.contains(top) || top.contains(el)),
      occluded_by: top && !(top === el || el.contains(top) || top.contains(el))
        ? top.tagName.toLowerCase() + '.' + [...top.classList].slice(0, 2).join('.')
        : null,
    };
  };
  const ligs = (el) =>
    [...el.querySelectorAll('mat-icon, i')].map(i => (i.textContent || '').trim()).filter(Boolean);
  // The component the control lives in — the durable anchor. `agent-footer-actions` is
  // a component boundary; a class on a <div> three levels up is a layout accident.
  const chain = (el) => {
    const out = [];
    for (let n = el; n && n !== document.body && out.length < 8; n = n.parentElement) {
      if (n.tagName.includes('-')) out.push(n.tagName.toLowerCase());
    }
    return out;
  };

  const tally = (arr) => {
    const m = {};
    for (const k of arr) m[k] = (m[k] || 0) + 1;
    return Object.entries(m).sort((a, b) => b[1] - a[1]).map(([k, n]) => ({ k, n }));
  };

  const buttons = [...document.querySelectorAll('button')];
  return {
    url: location.href,
    html_lang: document.documentElement.lang,
    counts: {
      buttons: buttons.length,
      mat_icon: document.querySelectorAll('mat-icon').length,
      i_google_symbols: document.querySelectorAll('i.google-symbols').length,
      contenteditable: document.querySelectorAll("[contenteditable='true']").length,
      radiogroup: document.querySelectorAll("[role='radiogroup']").length,
      settings_trigger: document.querySelectorAll('.settings-trigger-button').length,
    },
    // Every custom element on the page. This is the arm fingerprint: the reporter has
    // `agent-footer-actions`, our 2026-09-06 capture has no `agent-*` at all.
    custom_tags: tally(
      [...document.querySelectorAll('*')]
        .map(e => e.tagName.toLowerCase())
        .filter(t => t.includes('-'))
    ),
    agent_elements: [...document.querySelectorAll('*')]
      .filter(e => e.tagName.toLowerCase().startsWith('agent-')
                || [...e.classList].some(c => c.startsWith('agent-')))
      .slice(0, 40)
      .map(e => ({
        tag: e.tagName.toLowerCase(),
        classes: [...e.classList].filter(c => c.startsWith('agent-')),
        ligatures: ligs(e),
        ...vis(e),
      })),
    // The control the driver waits on, with the state the reporter says it is in.
    settings_triggers: [...document.querySelectorAll('.settings-trigger-button')].map(e => ({
      tag: e.tagName.toLowerCase(),
      component_chain: chain(e),
      ligatures: ligs(e),
      ...vis(e),
    })),
    // The reporter's replacement, found by ligature not by label.
    tune_buttons: buttons
      .filter(b => ligs(b).includes('tune'))
      .map(b => ({
        classes: [...b.classList].filter(c => !c.startsWith('mat-') && !c.startsWith('mdc-')),
        component_chain: chain(b),
        ...vis(b),
      })),
    // The labs mode-toggle shape (mode_control.py:45). If the migrated host reuses it,
    // that is the arm switch and no new mechanism is needed.
    aria_pressed: buttons
      .filter(b => b.hasAttribute('aria-pressed'))
      .map(b => ({
        pressed: b.getAttribute('aria-pressed'),
        classes: [...b.classList].filter(c => !c.startsWith('mat-') && !c.startsWith('mdc-')),
        ligatures: ligs(b),
        // Decides whether mode_control.py:45's AGENT_TOGGLE_SELECTOR
        // (`button[aria-pressed]:has(span.content)`) would match this host's chip.
        has_span_content: !!b.querySelector('span.content'),
        component_chain: chain(b),
        ...vis(b),
      })),
    button_ligatures: tally(buttons.flatMap(ligs)),
  };
}
"""


class ProbeFailedError(RuntimeError):
    """A step this probe cannot complete. Never downgraded to a verdict."""


def _report(tag: str, inv: dict[str, Any]) -> None:
    c = inv["counts"]
    step(tag, f"buttons={c['buttons']} mat_icon={c['mat_icon']} "
              f"settings_trigger={c['settings_trigger']} radiogroup={c['radiogroup']}")
    for t in inv["settings_triggers"]:
        step(f"{tag}.trigger", f"hidden_attr={t['hidden_attr']} display={t['display']} "
                               f"{t['w']}x{t['h']} hit_testable={t['hit_testable']} "
                               f"chain={t['component_chain']}")
    step(f"{tag}.agent_elements", str(len(inv["agent_elements"])))
    for b in inv["tune_buttons"]:
        step(f"{tag}.tune", f"classes={b['classes']} chain={b['component_chain']} "
                            f"hidden_attr={b['hidden_attr']} hit_testable={b['hit_testable']}")
    for b in inv["aria_pressed"]:
        step(f"{tag}.aria_pressed", f"pressed={b['pressed']} ligatures={b['ligatures']} "
                                    f"classes={b['classes']}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument(
        "--settle-s",
        type=float,
        default=8.0,
        help="wait after goto before the first read (the SPA mounts the composer late)",
    )
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    findings: dict[str, Any] = {
        "profile": args.profile,
        "project": args.project,
        "question": "is an agentic arm reachable on the migrated composer, and what "
                    "state carries `hidden` on .settings-trigger-button? (#749)",
        "cost": "credit-free: navigation, DOM reads, at most one toggle click",
    }
    url = f"https://flow.google.com/project/{args.project}"

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()

        step("goto", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(int(args.settle_s * 1000))
        findings["landed_url"] = page.url
        step("landed", page.url)
        if "flow.google.com/project/" not in page.url:
            raise ProbeFailedError(
                f"did not land on a migrated project page (got {page.url}) — the account "
                "is signed out, not migrated, or was redirected, so this run says NOTHING "
                "about the composer arms"
            )

        findings["state_a"] = await page.evaluate(_INVENTORY_JS)
        _report("A", findings["state_a"])
        shot_a = default_out_path("spike_migrated_arms_A", ".png")
        await page.screenshot(path=str(shot_a))
        findings["screenshot_a"] = shot_a.name

        # --- both sides of the transition ------------------------------------
        # Only a toggle is clicked, and only if one is present. No text is typed: the
        # reporter's "the composer must be touched first" claim is a SEPARATE question,
        # and mixing it in here would leave neither answered.
        toggle_sel = None
        if findings["state_a"]["aria_pressed"]:
            toggle_sel = "button[aria-pressed]"
        elif findings["state_a"]["tune_buttons"]:
            toggle_sel = AGENT_TUNE
        findings["toggle_selector"] = toggle_sel

        if toggle_sel is None:
            step("transition", "no toggle candidate on this page — state B not taken")
        else:
            step("transition", f"clicking {toggle_sel}")
            try:
                await page.locator(toggle_sel).first.click(timeout=8_000)
                await page.wait_for_timeout(3_000)
                findings["state_b"] = await page.evaluate(_INVENTORY_JS)
                _report("B", findings["state_b"])
                shot_b = default_out_path("spike_migrated_arms_B", ".png")
                await page.screenshot(path=str(shot_b))
                findings["screenshot_b"] = shot_b.name
            except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
                findings["transition_error"] = str(exc)[:400]
                step("transition", f"FAILED: {str(exc)[:200]}")

    # ---- the question, answered from the read -------------------------------
    a = findings["state_a"]
    b = findings.get("state_b") or {}

    def arm(inv: dict[str, Any]) -> str:
        """Which arm is rendered.

        NOT `agent_elements` — the first run of this probe (2026-09-08, ffroliva) found
        `button.agent-mode-chip` present in BOTH states, so an `agent-*` class is a
        component that exists either way, not an arm signal, and classifying on it
        labelled the plain classic composer "agentic". The discriminators are the two
        things that actually swap: the chip's own `aria-pressed`, and whether the
        classic settings trigger got `hidden`.
        """
        if not inv:
            return "unknown"
        chip = next((p for p in inv["aria_pressed"] if "agent-mode-chip" in p["classes"]), None)
        if chip is not None:
            return "agentic" if chip["pressed"] == "true" else "classic"
        triggers = inv["settings_triggers"]
        if triggers and all(t["hidden_attr"] for t in triggers):
            return "agentic"
        return "classic" if triggers else "unknown"

    findings["answer"] = {
        "arm_in_state_a": arm(a),
        "arm_in_state_b": arm(b),
        "settings_trigger_count": a["counts"]["settings_trigger"],
        "settings_trigger_hidden": [t["hidden_attr"] for t in a["settings_triggers"]],
        "agent_elements_present": bool(a["agent_elements"]),
        "tune_button_present": bool(a["tune_buttons"]),
        "labs_style_toggle_present": bool(a["aria_pressed"]),
        # A signal in BOTH states is not a settle signal, and this is what says so.
        "agent_elements_after_toggle": bool(b.get("agent_elements")) if b else None,
        "trigger_hidden_after_toggle": (
            [t["hidden_attr"] for t in b["settings_triggers"]] if b else None
        ),
    }

    out = default_out_path("spike_migrated_composer_arms")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    step("ANSWER", json.dumps(findings["answer"]))
    step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except ProbeFailedError as exc:
        print(f"[spike] PROBE FAILED: {exc}", file=sys.stderr, flush=True)
        sys.exit(3)
