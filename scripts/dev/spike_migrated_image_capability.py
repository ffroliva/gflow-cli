r"""Can the migrated project composer generate an IMAGE at all? ($0)

This decides the shape of the fix for #692.

Established already: `flow.google.com` CAN mint a reCAPTCHA Enterprise token on
`/project/<id>` (`spike_migrated_recaptcha_mint.py`), so the mint guard refuses
the host when the pooled page is merely on the root grid. But `gflow image` is
UI-driven — mint, then drive the composer — so the mint is only worth routing if
there is an image mode to drive.

A first inventory of the migrated project page found `movie`, `apps_spark_2`,
`crop_16_9`, `[role=tab]` = 0 and no obvious image control, which suggests
video-only. That is NOT proof: the page carries two `<flow-add-menu>` elements
and a settings overlay built from `[role=radio]` rather than tabs, so an image
mode could sit inside either. The character editor demonstrably generates images
on this host, so the capability exists somewhere on the origin.

So: enumerate the closed surfaces instead of inferring from the default view.

  1. the composer's own controls and any radiogroup submodes
  2. every `<flow-add-menu>` — opened, then read
  3. the settings overlay — opened, then read

If an image submode exists, #692 becomes "route the mint and drive it" — images
would work on the migrated host. If none exists, #692 is a diagnostics fix: the
host genuinely cannot serve `gflow image` yet, and the honest outcome is exit 36
with a message that names the migration rather than a bare RecaptchaError.

Credit-free: navigation, clicks on menu/settings affordances, DOM reads. Nothing
typed, nothing submitted.

    python scripts/dev/spike_migrated_image_capability.py \
        --profile ci-probe --project <uuid>
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

_MIGRATED_ROOT = "https://flow.google.com"


class SpikeUnreachedError(RuntimeError):
    """A closed surface this spike exists to read could not be opened.

    Raised instead of printing a verdict. The distinction is the whole point of
    the class: "I looked and found nothing" and "I never got to look" are
    different results, and collapsing them is how an unopened settings panel
    became a shipped claim that the migrated composer has no image mode.
    """

# Anything that could plausibly name an image mode, by ligature or by role.
_SURFACE_JS = r"""() => {
  const LIG = '.google-symbols, .material-symbols-outlined, .material-icons, mat-icon';
  const ligOf = (el) => [...el.querySelectorAll(LIG)]
    .map(e => (e.textContent || '').trim())
    .filter(t => /^[a-z0-9_]{2,40}$/.test(t));
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role'),
    ligatures: ligOf(el).slice(0, 3),
    text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 48),
    checked: el.getAttribute('aria-checked'),
    visible: vis(el),
  });
  return {
    radios: [...document.querySelectorAll('[role="radio"]')].map(describe),
    radiogroups: [...document.querySelectorAll('[role="radiogroup"]')].map(describe),
    menuitems: [...document.querySelectorAll('[role="menuitem"]')].map(describe),
    add_menus: [...document.querySelectorAll('flow-add-menu')].map(describe),
    // The tell we actually care about: does anything name an image capability?
    image_ligatures: [...document.querySelectorAll(LIG)]
      .map(e => (e.textContent || '').trim())
      .filter(t => /(image|photo|picture|palette|brush|draw|frame|camera|art)/i.test(t)),
    all_ligatures: [...new Set([...document.querySelectorAll(LIG)]
      .map(e => (e.textContent || '').trim())
      .filter(t => /^[a-z0-9_]{2,40}$/.test(t)))],
  };
}"""


async def _snapshot(page: Any, label: str, findings: dict[str, Any]) -> dict[str, Any]:
    data = await page.evaluate(_SURFACE_JS)
    findings[label] = data
    step(
        label,
        f"radios={len(data['radios'])} menuitems={len(data['menuitems'])} "
        f"add_menus={len(data['add_menus'])} image_ligatures={data['image_ligatures']}",
    )
    for r in data["radios"][:8]:
        step("  radio", f"{r['ligatures']} checked={r['checked']} text={r['text']!r}")
    for m in data["menuitems"][:10]:
        step("  menuitem", f"{m['ligatures']} text={m['text']!r}")
    return data


async def _main(profile: str, project: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    findings: dict[str, Any] = {"profile": profile, "project": project}

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()
        try:
            await page.goto(
                f"{_MIGRATED_ROOT}/project/{project}",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:  # noqa: BLE001 - settle is best-effort
                pass
            await page.wait_for_timeout(3000)
            step("landed", page.url)

            await _snapshot(page, "default_view", findings)

            # Which closed surfaces we actually got INSIDE. A verdict may only be
            # printed once every one of them has been read — see _UNREACHED_MSG.
            reached: dict[str, bool] = {"settings_overlay": False, "add_menu": False}

            # Open each add menu in turn and read what it offers.
            menus = page.locator("flow-add-menu button")
            count = await menus.count()
            step("add_menus", f"{count} button(s) inside <flow-add-menu>")
            for i in range(min(count, 3)):
                try:
                    await menus.nth(i).click(timeout=4000)
                    await page.wait_for_timeout(1500)
                    await _snapshot(page, f"add_menu_{i}_open", findings)
                    reached["add_menu"] = True
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(600)
                except Exception as exc:  # noqa: BLE001,PERF203 - a menu may not open
                    step("add_menu", f"[{i}] could not open: {type(exc).__name__}")

            # The settings overlay is where the migrated composer keeps submodes —
            # and where the `mode` radiogroup carrying `imageImage` lives. This is
            # THE surface the spike exists to read, so failing to open it is a
            # failed run, never a finding.
            trig = page.locator(
                "button[aria-label='Settings trigger'], .settings-trigger-button, "
                "button:has(mat-icon:text-is('settings_2'))"
            ).first
            if await trig.count():
                try:
                    await trig.click(timeout=4000)
                    await page.wait_for_timeout(1800)
                    # Presence of the trigger is not entry to the panel: wait for a
                    # pane that actually contains radios before claiming to have read it.
                    await page.locator(".cdk-overlay-pane").filter(
                        has=page.locator("[role='radiogroup']")
                    ).last.wait_for(state="visible", timeout=10_000)
                    await _snapshot(page, "settings_open", findings)
                    reached["settings_overlay"] = True
                except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
                    step("settings", f"could not open: {type(exc).__name__}")
            else:
                step("settings", "no settings trigger found")

            findings["surfaces_reached"] = reached
            found = sorted(
                {
                    lig
                    for key, sec in findings.items()
                    if isinstance(sec, dict)
                    for lig in sec.get("image_ligatures", [])
                }
            )
            findings["image_ligatures_found"] = found

            # A probe that could not reach the surface it exists to read must FAIL,
            # never conclude. The original version printed "the migrated composer
            # looks VIDEO-ONLY" unconditionally, and that sentence — from a run whose
            # overlay-open had silently failed — reached a shipped code comment as
            # "measured, not assumed". It was falsified on 2026-09-07 by
            # spike_migrated_composer_mode_axis.py: the panel holds
            # `[imageImage, videocamVideo]`. Never restore the unconditional verdict.
            unreached = [name for name, ok in reached.items() if not ok]
            if unreached:
                step("VERDICT", "WITHHELD")
                step("unreached", ", ".join(unreached))
                raise SpikeUnreachedError(
                    "did not get inside: "
                    + ", ".join(unreached)
                    + " — this run cannot speak to what those surfaces contain"
                )
            step(
                "verdict",
                f"image-naming ligatures across all surfaces: {found or 'NONE'} -> "
                + (
                    "an image capability may be drivable — investigate"
                    if found
                    else "no image ligature on any surface READ (all surfaces were "
                    "opened) — still not proof of absence; confirm against "
                    "spike_migrated_composer_mode_axis.py"
                ),
            )
        finally:
            shot = default_out_path("migrated_image_capability", ".png")
            try:
                await page.screenshot(path=str(shot))
                findings["screenshot"] = shot.name
            except Exception:  # noqa: BLE001 - screenshot is a courtesy
                pass
            out = default_out_path("migrated_image_capability")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    try:
        raise SystemExit(asyncio.run(_main(args.profile, args.project)))
    except SpikeUnreachedError as exc:
        print(f"[spike] PROBE FAILED: {exc}", file=sys.stderr, flush=True)
        print(
            "[spike] This is a failed measurement, NOT evidence of absence.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(3) from exc
