"""Capture the changelog announcement modal as an offline-rehearsable fixture (#593).

Companion to ``spike_changelog_modal.py``. That one *measures* and prints; this one
*records* — full DOM, every frame, the dialog subtree, a screenshot, the actionability
probe, and a HAR of the whole session — so the modal can be replayed offline after the
one shot at dismissing it is spent.

The dismissal persists server-side: once clicked, the announcement never comes back on
that account. So the click is gated behind ``--dismiss`` and everything is captured on
BOTH sides of it in one run (one HAR spans both).

    # 1. read-only — safe to repeat, modal survives
    uv run python scripts/dev/spike_changelog_capture.py ffroliva --project <id>

    # 2. one shot — captures before, clicks via the PRODUCTION dismissal path, captures after
    uv run python scripts/dev/spike_changelog_capture.py ffroliva --project <id> --dismiss

Artifacts land in ``scripts/dev/_spike_out/changelog_capture_<ts>/`` (gitignored).
SECURITY: the HAR carries auth cookies — never attach it to a public issue.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from _spike_common import default_out_path, resolve_profile_dir  # noqa: E402, isort: skip

# Probe: what is on the page, is the app behind it reachable, and does anything
# text-independent already match it. Mirrors spike_changelog_modal.py's _PROBE.
_PROBE = """
() => {
  const out = {dialogs: [], iframes: [], body: {}, blockers: []};
  const bs = getComputedStyle(document.body);
  out.body = {
    overflow: bs.overflow,
    pointerEvents: bs.pointerEvents,
    ariaHidden: document.body.getAttribute('aria-hidden'),
    inert: document.body.hasAttribute('inert'),
  };
  for (const d of document.querySelectorAll("[role='dialog'],[role='alertdialog'],dialog")) {
    const cs = getComputedStyle(d);
    out.dialogs.push({
      role: d.getAttribute('role') || d.tagName.toLowerCase(),
      modal: d.getAttribute('aria-modal'),
      label: (d.getAttribute('aria-label') || '').slice(0, 120),
      changelogLink: !!d.querySelector("a[href*='changelog']"),
      links: Array.from(d.querySelectorAll('a')).map(a => a.getAttribute('href')).slice(0, 10),
      buttons: Array.from(d.querySelectorAll('button')).map(b => ({
        text: (b.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 40),
        icons: Array.from(b.querySelectorAll('i')).map(i => (i.innerText || '').trim()),
        aria: b.getAttribute('aria-label'),
        cls: (b.className || '').toString().slice(0, 120),
      })).slice(0, 12),
      z: cs.zIndex,
      pointerEvents: cs.pointerEvents,
      html: d.outerHTML,
    });
  }
  for (const f of document.querySelectorAll('iframe')) {
    out.iframes.push({src: f.getAttribute('src'), title: f.getAttribute('title')});
  }
  const top = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
  out.topAtCentre = top ? {
    tag: top.tagName.toLowerCase(),
    cls: (top.className || '').toString().slice(0, 120),
    inDialog: !!top.closest("[role='dialog'],[role='alertdialog'],dialog"),
  } : null;
  for (const el of document.querySelectorAll('body > *')) {
    if (el.hasAttribute('inert') || el.getAttribute('aria-hidden') === 'true') {
      out.blockers.push({
        tag: el.tagName.toLowerCase(),
        inert: el.hasAttribute('inert'),
        ariaHidden: el.getAttribute('aria-hidden'),
      });
    }
  }
  return out;
}
"""

_HIT_TEST = (
    "el => { const r = el.getBoundingClientRect();"
    " const t = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);"
    " return t === el || el.contains(t) ? 'REACHABLE' : 'COVERED by <' +"
    " (t ? t.tagName.toLowerCase() : 'null') + '>'; }"
)


async def _selector_table(page, groups) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, sels in groups:
        for sel in sels:
            try:
                n = await page.locator(sel).count()
                vis = await page.locator(sel).first.is_visible() if n else False
            except Exception as exc:  # noqa: BLE001 — a bad selector is data, not a crash
                n, vis = -1, f"ERR {type(exc).__name__}"
            rows.append({"group": name, "selector": sel, "count": n, "visible": vis})
    return rows


async def _actionability(page, selectors) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            count = await page.locator(sel).count()
            visible = await loc.is_visible() if count else False
            enabled = await loc.is_enabled() if count else False
            hit = await loc.evaluate(_HIT_TEST) if count and visible else "n/a"
        except Exception as exc:  # noqa: BLE001
            count, visible, enabled, hit = -1, False, False, f"ERR {type(exc).__name__}"
        rows.append(
            {"selector": sel, "count": count, "visible": visible, "enabled": enabled, "hit": hit}
        )
    return rows


async def _capture(page, out: Path, tag: str) -> dict[str, object]:
    """Write every artifact for one side of the click; return the probe summary."""
    from gflow_cli.api.transports.ui_automation import (
        CHANGELOG_IFRAME_SELECTORS,
        MODE_SWITCH_TRIGGER_SELECTORS,
        OVERLAY_CLOSE_BUTTON_SELECTORS,
        TOP_BANNER_SELECTORS,
        WELCOME_SCREEN_SELECTORS,
    )

    d = out / tag
    d.mkdir(parents=True, exist_ok=True)

    probe = await page.evaluate(_PROBE)
    (d / "page.html").write_text(await page.content(), encoding="utf-8")

    # Every frame separately — the changelog body lives in a cross-origin iframe,
    # which page.content() does not include.
    frames: list[dict[str, object]] = []
    for i, frame in enumerate(page.frames):
        entry: dict[str, object] = {"index": i, "url": frame.url, "name": frame.name}
        try:
            html = await frame.content()
            fp = d / f"frame_{i}.html"
            fp.write_text(html, encoding="utf-8")
            entry["file"] = fp.name
            entry["bytes"] = len(html)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
        frames.append(entry)

    # Dialog subtrees, isolated — this is the offline fixture the detector runs against.
    for i, dlg in enumerate(probe["dialogs"]):
        (d / f"dialog_{i}.html").write_text(str(dlg.pop("html", "")), encoding="utf-8")

    await page.screenshot(path=str(d / "screenshot.png"), full_page=False)

    report = {
        "tag": tag,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "url": page.url,
        "probe": probe,
        "frames": frames,
        "selectors": await _selector_table(
            page,
            (
                ("CHANGELOG_IFRAME", CHANGELOG_IFRAME_SELECTORS),
                ("TOP_BANNER", TOP_BANNER_SELECTORS),
                ("WELCOME_SCREEN", WELCOME_SCREEN_SELECTORS),
                ("CLOSE_BUTTONS", OVERLAY_CLOSE_BUTTON_SELECTORS),
            ),
        ),
        "actionability": await _actionability(page, MODE_SWITCH_TRIGGER_SELECTORS[:3]),
    }
    (d / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _summarise(report: dict[str, object]) -> None:
    probe = report["probe"]
    body = probe["body"]
    print(f"\n--- {report['tag']} --- {report['url']}")
    print(f"  body.pointerEvents={body['pointerEvents']} ariaHidden={body['ariaHidden']}")
    print(f"  dialogs={len(probe['dialogs'])} topAtCentre={probe['topAtCentre']}")
    for dlg in probe["dialogs"]:
        print(
            f"    role={dlg['role']} modal={dlg['modal']} changelogLink={dlg['changelogLink']}"
            f" buttons={[b['text'] for b in dlg['buttons']]}"
        )
    for row in report["selectors"]:
        if row["count"]:
            print(f"    MATCH {row['group']:<17} n={row['count']!s:<3} {row['selector']}")
    for row in report["actionability"]:
        print(f"    app-control hit={row['hit']} visible={row['visible']} {row['selector'][:60]}")


async def _run(profile: str, project_id: str | None, dismiss: bool, out: Path) -> int:
    from gflow_cli.api import routes
    from gflow_cli.api.client import FlowApiClient

    pdir = resolve_profile_dir(profile)
    async with FlowApiClient(profile_dir=pdir) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        if project_id:
            url = routes.project_editor_url(client._account_locale, project_id)  # noqa: SLF001
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(6000)

        before = await _capture(page, out, "before")
        _summarise(before)

        if not dismiss:
            print("\n[capture] read-only run — nothing clicked; the modal survives.")
            return 0

        fn = getattr(client.transport, "_dismiss_blocking_overlays", None)
        if fn is None:
            print("[capture] ERROR: transport has no _dismiss_blocking_overlays", file=sys.stderr)
            return 3

        # Narrow network window around the click. The HAR has everything, but the
        # request that PERSISTS the dismissal server-side is the one artifact worth
        # being able to find without grepping 85 MB.
        traffic: list[dict[str, object]] = []

        def _on_request(req) -> None:  # noqa: ANN001 — playwright Request
            try:
                traffic.append(
                    {
                        "kind": "request",
                        "t": round(time.time() - t0, 3),
                        "method": req.method,
                        "url": req.url,
                        "post_data": (req.post_data or "")[:4000],
                    }
                )
            except Exception:  # noqa: BLE001, S110 — instrumentation must never break the run
                pass

        def _on_response(resp) -> None:  # noqa: ANN001 — playwright Response
            traffic.append(
                {
                    "kind": "response",
                    "t": round(time.time() - t0, 3),
                    "status": resp.status,
                    "url": resp.url,
                }
            )

        clicked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        t0 = time.time()
        client._context.on("request", _on_request)  # noqa: SLF001 — dev instrument
        client._context.on("response", _on_response)  # noqa: SLF001
        acted = await fn(page, out)
        await page.wait_for_timeout(5000)
        client._context.remove_listener("request", _on_request)  # noqa: SLF001
        client._context.remove_listener("response", _on_response)  # noqa: SLF001
        (out / "dismiss_traffic.json").write_text(json.dumps(traffic, indent=2), encoding="utf-8")
        (out / "click.json").write_text(
            json.dumps(
                {
                    "clicked_at": clicked_at,
                    "monotonic_offset_s": round(time.time() - t0, 3),
                    "dismissal_returned": acted,
                    "path": "production _dismiss_blocking_overlays",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[capture] dismissal ran at {clicked_at}; returned {acted}")

        await page.wait_for_timeout(4000)
        _summarise(await _capture(page, out, "after"))

        # Does the dismissal survive a reload? The one shot is spent either way, so
        # prove persistence now rather than inferring it from a later run.
        if project_id:
            await page.goto(
                routes.project_editor_url(client._account_locale, project_id),  # noqa: SLF001
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(6000)
            _summarise(await _capture(page, out, "after_reload"))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("--project", default=None, help="project id to open (else the bootstrap page)")
    ap.add_argument("--dismiss", action="store_true", help="ONE SHOT: click the real dismissal")
    args = ap.parse_args()

    out_dir = default_out_path("changelog_capture", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Must be set before FlowApiClient builds its Settings — hence the late import in _run.
    os.environ["GFLOW_CLI_HAR_PATH"] = str(out_dir / "session.har")
    print(f"[capture] artifacts -> {out_dir}")
    raise SystemExit(asyncio.run(_run(args.profile, args.project, args.dismiss, out_dir)))
