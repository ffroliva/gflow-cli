"""Is gflow ACTUALLY language-agnostic? Drive the REAL code, per locale.

Language agnosticism is a core design claim: the selector cascades try
structural / ARIA / icon-ligature tiers FIRST and fall back to human-language
text only when those miss. If the real editor-entry + mode-switch path succeeds
in a locale, that path is agnostic for that locale. If it raises, it is not.

**Why this drives the transport instead of reimplementing it.** A first version
of this probe did `goto` + `wait` + `is_visible` and reported MISS for every
selector group — including on the account's OWN locale, a path that provably
works in production. Reimplementing the entry sequence got it wrong (missing
overlay dismissal, mount waits, and the fact that the mode tabs live inside a
dropdown that must be opened first). Calling the real methods cannot be wrong
about its own preconditions.

Flow routes locale by URL PATH SEGMENT, so ONE account can be served every
locale — this does not need 14 Google accounts.

Note only the BARE URL redirects to the account locale — Flow serves whatever
segment it is asked for and never corrects a wrong-but-valid one
(scripts/dev/spike_locale_poison.py, #587). So `/fx/en/` is served as asked on
any account, and en is only meaningfully testable on an en account.

Navigation and menu interaction only. Never submits a prompt, never generates,
spends no credits.

    uv run python scripts/dev/verify_language_agnostic.py \
        --profile denon82 --project <existing-project-id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, resolve_profile_dir  # noqa: E402

LOCALES = ["pt", "es", "fr", "de", "it", "nl", "ja", "zh", "ko", "pl", "ru", "tr", "id"]


async def probe(client: Any, page: Any, locale: str, project_id: str) -> dict[str, Any]:
    """Run the REAL editor-entry + image-mode-switch path under one locale."""
    transport = client.transport
    transport._account_locale = locale  # force the URL this run builds

    row: dict[str, Any] = {"locale": locale}
    try:
        await transport._enter_editor(page, None, project_id=project_id)
        row["served"] = f"/fx/{locale}/" in page.url
        row["html_lang"] = await page.evaluate("document.documentElement.lang")
        row["entered_editor"] = True
    except Exception as exc:  # noqa: BLE001
        row.update(
            entered_editor=False,
            served=None,
            error=f"{type(exc).__name__}: {str(exc)[:120]}",
        )
        return row

    # The real cascade: exits agent mode, probes the mode-switch trigger, opens
    # the dropdown, probes the image tab. Raises if any tier fails to resolve.
    try:
        await transport._switch_to_image_mode(page)
        row["image_mode_switch"] = "OK"
    except Exception as exc:  # noqa: BLE001
        row["image_mode_switch"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return row


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--locales", default=",".join(LOCALES))
    args = ap.parse_args()

    rows: list[dict[str, Any]] = []
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = client._page
        for loc in [x.strip() for x in args.locales.split(",") if x.strip()]:
            rows.append(await probe(client, page, loc, args.project))
            print(f"  probed {loc}: {rows[-1].get('image_mode_switch', rows[-1].get('error'))}")

    print(f"\n{'locale':<8}{'served':<9}{'lang':<7}{'editor':<9}image-mode-switch")
    print("-" * 88)
    for r in rows:
        print(
            f"{r['locale']:<8}{str(r.get('served')):<9}{str(r.get('html_lang')):<7}"
            f"{str(r.get('entered_editor')):<9}{str(r.get('image_mode_switch', r.get('error')))[:44]}"
        )

    served = [r for r in rows if r.get("served")]
    broken = [r for r in served if r.get("image_mode_switch") != "OK"]
    print("\n=== VERDICT ===")
    print(f"  locales served      : {len(served)}/{len(rows)}")
    print(f"  image-mode failures : {len(broken)} {[r['locale'] for r in broken]}")
    if served and not broken:
        print("\n  LANGUAGE-AGNOSTIC CONFIRMED on the editor-entry + image-mode path")
        print("  for every locale Flow served. The locale-invariant tiers held.")
    else:
        print("\n  NOT AGNOSTIC — the path fails in the locales listed above.")

    out = Path("scripts/dev/_spike_out/verify_language_agnostic.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nevidence: {out}")
    return 0 if served and not broken else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
