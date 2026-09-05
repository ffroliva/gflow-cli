r"""Which duration / resolution / count options does the migrated flow.google.com
editor render per video model? ($0)

#650 unlocked --duration 4/6/8 on the Veo 3.1 models with the caveat that Flow's
duration control is cohort-dependent (labs cohorts differ; the maintainer's labs
cohort renders none for Veo). This spike selects each model in the migrated host's
model menu and records the option groups that remain, plus the cost line — so
the #650 semantics can be verified on the new host, where the driver now runs t2v.

Read-only: opens the settings pane, switches models, reads the DOM, closes the
pane. Nothing is typed or submitted.

    python scripts/dev/spike_migrated_duration_by_model.py --profile <name> --project <id>
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

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    VIDEO_MODEL_MENU_LABELS,
    MigratedComposer,
)

_GROUPS_JS = r"""() => {
  const pane = [...document.querySelectorAll('.cdk-overlay-pane')].find(p => p.querySelector("[role='radiogroup']"));
  if (!pane) return {groups: [], cost: null, model: null};
  const groups = [...pane.querySelectorAll("[role='radiogroup']")].map(g =>
    [...g.querySelectorAll("[role='radio']")].map(r => ({
      text: (r.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 24),
      checked: r.getAttribute('aria-checked'),
    })));
  const text = (pane.innerText || '').replace(/\s+/g, ' ');
  const cost = (text.match(/(\d+)\s*credits?/i) || [null, null])[1];
  const btn = [...pane.querySelectorAll('button')].find(b => (b.textContent || '').includes('arrow_drop_down'));
  return {groups, cost: cost ? Number(cost) : null, model: btn ? (btn.textContent || '').replace('arrow_drop_down', '').trim() : null};
}"""


async def _main(profile: str, project: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    out: dict[str, Any] = {"profile": profile, "project": project[:8] + "...", "models": {}}
    composer = MigratedComposer()
    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        assert context is not None
        page = await context.new_page()
        try:
            await composer.ensure_editor(page, project)
            for model, label in VIDEO_MODEL_MENU_LABELS.items():
                # Fresh editor per model: a re-opened pane after a menu switch did
                # not render its option groups on the first run of this spike.
                await page.reload(wait_until="domcontentloaded")
                await composer.ensure_editor(page, project)
                pane = await composer._open_pane(page)  # noqa: SLF001 - spike
                try:
                    await composer._select_model(page, pane, model)  # noqa: SLF001 - spike
                    await asyncio.sleep(0.8)
                    facts = await page.evaluate(_GROUPS_JS)
                except Exception as e:  # noqa: BLE001 - record, never abort
                    facts = {"error": str(e)[:200]}
                finally:
                    await composer._close_pane(page)  # noqa: SLF001 - spike
                    await asyncio.sleep(0.4)
                out["models"][model.value] = {"menu_label": label, **facts}
                groups = facts.get("groups") or []
                summary = [[r["text"] for r in g] for g in groups]
                step(
                    "model", f"{label}: cost={facts.get('cost')} groups={json.dumps(summary)[:170]}"
                )
            shot = default_out_path(f"migrated_duration_{profile}", ".png")
            await page.screenshot(path=str(shot))
            out["screenshot"] = shot.name
        finally:
            await page.close()
            path = default_out_path(f"migrated_duration_by_model_{profile}")
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
            step("out", str(path))
    # The pane may be left on the last model; the composer re-selects per request.
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(_main(a.profile, a.project)))
