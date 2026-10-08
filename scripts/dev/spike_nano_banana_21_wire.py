r"""Nano Banana 2.1 on flow.google.com: alongside 2, or instead of it? (PR #958)

PR #958 says some accounts now show ``🍌 Nano Banana 2.1`` in the image model picker, and
that the ``ogiZ0b`` submit then carries ``BELUGA`` where it used to carry ``NARWHAL``. It
makes the submit guard accept ``BELUGA`` for ``--model nano-banana-2``. Whether that is
safe depends on one fact this spike measures: whether the menu offers BOTH entries.

Cost: the default run reads the menu and the project's model catalogue (``HTrJv``) and
costs nothing. ``--submit`` adds ONE nano2 image: 0 Flow credits, daily image quota only.
Created projects are left behind (named ``spike-nb21-*``). The body is never stored; only
the model tokens that appear in it are counted.

Pre-registered readings, written before the first run:

| Menu (image model entries)       | Reading                                                  |
|----------------------------------|----------------------------------------------------------|
| "Nano Banana 2" only             | this account has no 2.1; PR #958's premise unmeasured   |
| "Nano Banana 2.1" only, no 2     | 2.1 REPLACED 2 here; #958's alias is safe on this account |
| both "2" and "2.1"               | COEXIST: #958's alias lets nano-banana-2 run 2.1 silently |

| ``--submit`` body tokens (nano-banana-2 requested) | Reading                         |
|----------------------------------------------------|---------------------------------|
| NARWHAL                                            | 2 still serializes as NARWHAL   |
| BELUGA                                             | #958's wire claim reproduced    |
| neither                                            | the token moved; #958 settles nothing |

The catalogue tokens are corroboration only: a name in ``HTrJv`` says Flow knows a model,
not that this account's picker offers it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
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

from gflow_cli.api.image import Aspect, GenerateImageRequest  # noqa: E402
from gflow_cli.api.image import Model as ImageModel  # noqa: E402
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    IMAGE_SUBMIT_RPC,
    MENU_ITEM,
    MigratedComposer,
    _ligature,  # pyright: ignore[reportPrivateUsage]
    _post_data,  # pyright: ignore[reportPrivateUsage]
)

_TOKENS = ("NARWHAL", "BELUGA", "GEM_PIX_2", "HARBOR_SEAL", "Nano Banana 2.1", "Nano Banana")
CATALOGUE_RPC = "HTrJv"


def _hits(text: str) -> dict[str, int]:
    return {t: len(re.findall(re.escape(t), text)) for t in _TOKENS if t in text}


async def read_menu(page: Any, project_id: str) -> dict[str, Any]:
    """Open the settings pane in Image mode and list the image model entries ($0)."""
    composer = MigratedComposer()
    await composer.ensure_editor(page, project_id)
    pane = await composer._open_pane(page)  # noqa: SLF001
    try:
        await composer._select(page, pane, axis="mode", lig="image")  # noqa: SLF001
        button = pane.locator("button").filter(has=_ligature(page, "arrow_drop_down")).first
        await button.wait_for(state="visible", timeout=8000)
        current = (await button.text_content() or "").strip()
        await button.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        await items.first.wait_for(state="visible", timeout=5000)
        offered = [t.strip() for t in await items.all_text_contents()]
        await page.keyboard.press("Escape")
    finally:
        await composer._close_pane(page, strict=False)  # noqa: SLF001
    return {"current_button": current, "offered": offered}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--submit", action="store_true", help="also make ONE nano2 image (quota)")
    args = ap.parse_args()

    out: dict[str, Any] = {"profile": args.profile, "question": "PR #958 nano banana 2.1"}
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = client.transport._page  # noqa: SLF001
        catalogue: list[dict[str, int]] = []
        bodies: list[dict[str, int]] = []

        def on_response(resp: Any) -> None:
            if CATALOGUE_RPC not in str(getattr(resp, "url", "")):
                return

            async def _read() -> None:
                try:
                    catalogue.append(_hits(await resp.text()))
                except Exception:  # noqa: BLE001 - a listener must never break the run
                    pass

            asyncio.ensure_future(_read())  # noqa: RUF006

        def on_request(req: Any) -> None:
            if IMAGE_SUBMIT_RPC in str(getattr(req, "url", "")):
                bodies.append(_hits(_post_data(req)))

        page.on("response", on_response)
        page.on("request", on_request)

        project = await client.create_project(f"spike-nb21-{time.strftime('%H%M%S')}")
        step("project", project.project_id)
        try:
            out["menu"] = await read_menu(page, project.project_id)
        except Exception as exc:  # noqa: BLE001 - a failed read is a datum, not absence
            out["menu_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        step("menu", json.dumps(out.get("menu") or out.get("menu_error"), ensure_ascii=False))

        if args.submit:
            try:
                await client.generate_image(
                    project_id=project.project_id,
                    req=GenerateImageRequest(
                        prompt="a small red cube on a white table",
                        model=ImageModel.NARWHAL,
                        aspect=Aspect.SQUARE,
                        count=1,
                    ),
                )
                out["submit_error"] = None
            except Exception as exc:  # noqa: BLE001
                out["submit_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            step("submit", f"error={out['submit_error']}")

        await asyncio.sleep(1.0)
        out["catalogue_tokens"] = catalogue
        out["submit_body_tokens"] = bodies

    path = default_out_path(f"spike_nano_banana_21_{args.profile}", ".json")
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {path}")
    step("catalogue", json.dumps(out["catalogue_tokens"], ensure_ascii=False))
    step("body", json.dumps(out["submit_body_tokens"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
