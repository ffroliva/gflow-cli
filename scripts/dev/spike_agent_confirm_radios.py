r"""Which "Confirm before generating" radio is which, by something other than position? ($0)

Opens Agent settings, dumps every attribute of the two `mat-radio-button` inputs plus their
label text and checked state, then leaves through the back arrow WITHOUT saving.

    python scripts/dev/spike_agent_confirm_radios.py --profile <name> --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402

_JS = r"""
() => [...document.querySelectorAll('flow-settings-view mat-radio-button')].map(r => {
  const i = r.querySelector('input');
  const attrs = (e) => Object.fromEntries([...e.attributes].map(a => [a.name, a.value]));
  return { host: attrs(r), input: i ? attrs(i) : null, checked: i ? i.checked : null,
           text: (r.innerText || '').trim() };
})
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        await page.goto(f"https://flow.google.com/project/{args.project}", timeout=60_000)
        await page.wait_for_timeout(10_000)
        await page.locator("button:has(mat-icon:text-is('tune'))").last.click(timeout=8_000)
        await page.locator("flow-settings-view").wait_for(state="visible", timeout=8_000)
        await page.wait_for_timeout(1_000)
        radios = await page.evaluate(_JS)
        await page.locator(
            "flow-agent-panel button:has(mat-icon:text-is('arrow_back'))"
        ).last.click()
        out = default_out_path("spike_agent_confirm_radios")
        out.write_text(json.dumps(radios, indent=2), encoding="utf-8")
        print(json.dumps(radios, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
