r"""Gating spike for wiring manifest references (#913, PLAN Task 0). flow.google.com only.

Four claims the transport work (PLAN Task 5) rests on. One mode per claim:

  scope     $0. In a NEW project, @-search a caption that exists only in ANOTHER project.
            offered -> the picker searches the account library (a re-run of a manifest
            can bind yesterday's same caption, SCENARIO #40). not offered -> project-scoped.
  negative  1 image. Row 1 @-mentions an UNRELATED image but declares row 0's media id.
            Production's _image_body_problem must refuse (WireFormatError). Refused ->
            the body check discriminates. Accepted -> it does not; the design needs another
            binder (SCENARIO #16).
  chain     N+1 images. Row 0, then rows 1..N each referencing the row before through the
            real path (attach swapped to "mention existing, no upload"). Per row: mention
            attempts (migrated.mention_miss count), success, and whether the ogiZ0b body
            carries the parent id. Measures search lag on a fresh generation (SCENARIO #17).
  enter     $0 unless it submits. @-type a caption no asset has, then press Enter the way
            _mention_by_name does. ogiZ0b fired -> Enter on an empty picker submits a
            generation (SCENARIO #39). No ogiZ0b -> it does not.

Pre-registered readings are the arrows above, written before the first run.

    python scripts/dev/spike_ref_gate.py --profile ci-probe --mode scope \
        --project <uuid> --foreign-caption "<caption from another project>"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model  # noqa: E402
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    PICKER_OPTION,
    MigratedComposer,
)

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip


def _rpcids(url: str) -> list[str]:
    if "batchexecute" not in url:
        return []
    return parse_qs(urlsplit(url).query).get("rpcids", [""])[0].split(",")


def _req(prompt: str, ref_file: Path | None = None) -> GenerateImageRequest:
    return GenerateImageRequest(
        prompt=prompt,
        aspect=Aspect.from_cli("1:1"),
        model=Model.from_cli("nano2"),
        ref_paths=(ref_file,) if ref_file else (),
    )


async def _offered(page: Any, composer: MigratedComposer, query: str) -> list[str]:
    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2200)
    await page.keyboard.type(query, delay=100)
    await page.wait_for_timeout(3000)
    offered = [t.strip() for t in await page.locator(PICKER_OPTION).all_text_contents()]
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)
    await composer.clear_composer(page)
    return offered


async def _row_with_existing_ref(
    client: Any, project: str, prompt: str, mention: str, media_id: str, ref_file: Path
) -> dict[str, Any]:
    """Run one row through the real path, attaching an existing image by mention."""
    page = client._context.pages[0]  # noqa: SLF001
    bodies: list[str] = []
    rpcs: list[str] = []

    def on_request(r: Any) -> None:
        ids = _rpcids(r.url)
        rpcs.extend(ids)
        if "ogiZ0b" in ids:
            bodies.append(r.post_data or "")

    async def mention_existing(
        self: MigratedComposer, page: Any, project_id: str, paths: tuple[Path, ...]
    ) -> tuple[str, ...]:
        await self.clear_composer(page)
        await self._mention_by_name(page, mention, expect_chips=1)  # noqa: SLF001
        return (media_id,)

    original = MigratedComposer.attach_references
    MigratedComposer.attach_references = mention_existing  # type: ignore[method-assign]
    page.on("request", on_request)
    rec: dict[str, Any] = {"mention": mention, "declared_parent": media_id}
    try:
        with structlog.testing.capture_logs() as logs:
            try:
                img = await client.generate_image(project_id=project, req=_req(prompt, ref_file))
                rec.update(ok=True, media_id=img.media_name, display_name=img.display_name)
            except Exception as exc:  # noqa: BLE001 - the refusal IS a finding
                rec.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:400])
        rec["mention_misses"] = sum(1 for e in logs if e.get("event") == "migrated.mention_miss")
    finally:
        MigratedComposer.attach_references = original  # type: ignore[method-assign]
        page.remove_listener("request", on_request)
    rec["body_has_declared_parent"] = any(media_id in b for b in bodies)
    rec["upload_rpc"] = "maseQ" in rpcs
    return rec


async def main(a: argparse.Namespace) -> int:
    findings: dict[str, Any] = {"mode": a.mode, "project": a.project}
    out = default_out_path(f"ref_gate_{a.mode}")

    def save() -> None:
        out.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    async with build_client(resolve_profile_dir(a.profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        composer = MigratedComposer()

        if a.mode == "scope":
            await composer.ensure_editor(page, a.project)
            findings["offered"] = await _offered(page, composer, a.foreign_caption)
            findings["foreign_caption_offered"] = any(
                o.startswith(a.foreign_caption) for o in findings["offered"]
            )
            step("scope", json.dumps(findings))

        elif a.mode == "negative":
            row0 = await client.generate_image(project_id=a.project, req=_req("a single red apple"))
            findings["row0"] = {"media_id": row0.media_name, "display_name": row0.display_name}
            findings["row1"] = await _row_with_existing_ref(
                client, a.project, "the same apple, now green",
                a.unrelated_caption, row0.media_name, a.ref_file,
            )
            step("negative", json.dumps(findings))

        elif a.mode == "chain":
            row = await client.generate_image(project_id=a.project, req=_req("a single red apple"))
            findings["rows"] = [{"media_id": row.media_name, "display_name": row.display_name}]
            save()
            for k in range(1, a.length + 1):
                prev = findings["rows"][-1]
                if not prev.get("display_name"):
                    findings["rows"].append({"skipped": "parent has no display_name"})
                    break
                rec = await _row_with_existing_ref(
                    client, a.project, f"the same apple, variation {k}",
                    prev["display_name"], prev["media_id"], a.ref_file,
                )
                findings["rows"].append(rec)
                step(f"row{k}", json.dumps(rec))
                save()
                if not rec.get("ok"):
                    break

        elif a.mode == "enter":
            await composer.ensure_editor(page, a.project)
            rpcs: list[str] = []
            page.on("request", lambda r: rpcs.extend(_rpcids(r.url)))
            try:
                await page.locator(COMPOSER).first.click(timeout=5000)
            except Exception as exc:  # noqa: BLE001 - what blocks the composer IS the finding
                shot = out.with_suffix(".png")
                await page.screenshot(path=str(shot))
                findings["composer_click_failed"] = f"{type(exc).__name__}; screenshot {shot}"
                findings["element_at_composer"] = await page.evaluate(
                    "(s) => { const c = document.querySelector(s); if (!c) return 'no composer';"
                    " const r = c.getBoundingClientRect();"
                    " const e = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);"
                    " return e ? e.outerHTML.slice(0, 300) : 'nothing'; }",
                    COMPOSER,
                )
                save()
                raise
            await page.keyboard.type("@", delay=120)
            await page.wait_for_timeout(2200)
            await page.keyboard.type("zqxv no asset is called this", delay=100)
            await page.wait_for_timeout(2500)
            findings["offered"] = len(await page.locator(PICKER_OPTION).all())
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(8000)
            findings["rpcids_after_enter"] = sorted(set(rpcs))
            findings["submitted"] = "ogiZ0b" in rpcs
            await page.screenshot(path=str(out.with_suffix(".png")))
            findings["composer_text"] = await page.locator(COMPOSER).first.inner_text()
            save()
            step("enter", json.dumps(findings))

        save()
    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--mode", required=True, choices=["scope", "negative", "chain", "enter"])
    ap.add_argument("--project", required=True)
    ap.add_argument("--foreign-caption", default="")
    ap.add_argument("--unrelated-caption", default="")
    ap.add_argument("--ref-file", type=Path, help="any local image; routes the row, never uploaded")
    ap.add_argument("--length", type=int, default=5)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
