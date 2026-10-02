r"""Batch-shaped: can row 1 reference row 0's generated image by its handle, with no upload? (#913)

Cost: 2 images of daily quota, 0 Veo credits. flow.google.com project required.

Design under test (owner, 2026-10-01): when row 0 of a batch finishes, Flow's reply
already names the new image (``media_id`` + workflow ``display_name``). Row 1 should
reference THAT image (already in the project, no re-upload), using the reply as the
handle. Earlier spike (spike_mention_existing_media.py) measured on this host:
generated images ARE mentionable in the composer's @ picker, under a caption Flow
writes (not our prompt); the mention chip carries no media id; mentioning uploads
nothing.

Method: both rows run through gflow's REAL image path (``FlowApiClient.generate_image``
→ ``migrated_composer.run_images``). For row 1 only, ``MigratedComposer.attach_references``
is swapped to "mention row 0 by its reply display_name, return row 0's media id"
instead of "upload + mention". Production then applies its own wire check,
``_image_body_problem``: the ``ogiZ0b`` submit body must carry every returned reference
id, or the run is refused. Every batchexecute rpcid fired during row 1 is recorded.

Pre-registered reading (written before the first run):

  Q5 row 0's reply display_name is offered in the @ picker
      -> the reply is a usable handle for the mention.
  Q6 row 1 completes (production's body check passed) and the captured ogiZ0b body
     contains row 0's media id
      -> the reference reached Flow by id; identity is proven on the wire.
  Q7 no upload rpc fired during row 1 (only search/submit/poll rpcids)
      -> nothing was added to the project.
  All three -> "reference the generated image by its reply handle" is buildable here.
  Row 1 refused with "missing ... reference(s)" -> the caption bound some OTHER image
      (or none): name-based binding is not trustworthy; the design needs another binder.
  Row 1 ReferenceNotFoundError -> the fresh image was not mentionable in time (indexing
      lag); measure with a wait before concluding.

    python scripts/dev/spike_batch_ref_existing_media.py --profile ci-probe --project <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model  # noqa: E402
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    PICKER_OPTION,
    MigratedComposer,
)

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip


def _rpcids(url: str) -> list[str]:
    q = parse_qs(urlsplit(url).query)
    return q.get("rpcids", [""])[0].split(",") if "batchexecute" in url else []


async def main(profile: str, project: str, ref_file: Path) -> int:
    findings: dict[str, Any] = {"project": project}
    out = default_out_path("batch_ref_existing_media")

    def save() -> None:
        out.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001 - spike reads the live context
        assert ctx is not None

        # Row 0: a plain generation through the real path.
        req0 = GenerateImageRequest(
            prompt="a single red apple on a wooden table, soft daylight",
            aspect=Aspect.from_cli("1:1"),
            model=Model.from_cli("nano2"),
        )
        img0 = await client.generate_image(project_id=project, req=req0)
        findings["row0"] = {"media_id": img0.media_name, "display_name": img0.display_name}
        step("row0", json.dumps(findings["row0"]))
        save()

        page = ctx.pages[0]
        composer = MigratedComposer()
        await composer.ensure_editor(page, project)

        # Q5: is row 0's reply display_name what the @ picker offers?
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.type("@", delay=120)
        await page.wait_for_timeout(2500)
        offered = [t.strip() for t in await page.locator(PICKER_OPTION).all_text_contents()]
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
        await composer.clear_composer(page)
        name0 = img0.display_name or ""
        findings["q5"] = {
            "offered": offered,
            "row0_name_offered": any(o.startswith(name0) for o in offered) if name0 else False,
        }
        step("Q5", json.dumps(findings["q5"]))
        save()

        # Row 1: real path, attach swapped to "mention row 0, no upload".
        rpcs: list[str] = []
        bodies: list[str] = []

        def on_request(r: Any) -> None:
            ids = _rpcids(r.url)
            rpcs.extend(ids)
            if "ogiZ0b" in ids:
                bodies.append(r.post_data or "")

        page.on("request", on_request)

        async def mention_existing(
            self: MigratedComposer, page: Any, project_id: str, paths: tuple[Path, ...]
        ) -> tuple[str, ...]:
            await self.clear_composer(page)
            await self._mention_by_name(page, name0, expect_chips=1)  # noqa: SLF001
            return (img0.media_name,)

        original = MigratedComposer.attach_references
        MigratedComposer.attach_references = mention_existing  # type: ignore[method-assign]
        try:
            req1 = GenerateImageRequest(
                prompt="the same apple, now green",
                aspect=Aspect.from_cli("1:1"),
                model=Model.from_cli("nano2"),
                ref_paths=(ref_file,),  # routes through attach_references; never uploaded
            )
            try:
                img1 = await client.generate_image(project_id=project, req=req1)
                findings["row1"] = {"ok": True, "media_id": img1.media_name}
            except Exception as exc:  # noqa: BLE001 - the refusal IS a finding
                findings["row1"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
        finally:
            MigratedComposer.attach_references = original  # type: ignore[method-assign]
            page.remove_listener("request", on_request)

        findings["q6_body_has_row0_id"] = any(img0.media_name in b for b in bodies)
        findings["q7_rpcids_during_row1"] = sorted(set(rpcs))
        step("row1", json.dumps(findings["row1"]))
        step("Q6", f"ogiZ0b bodies={len(bodies)} carry row0 id={findings['q6_body_has_row0_id']}")
        step("Q7", json.dumps(findings["q7_rpcids_during_row1"]))
        save()
    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    ap.add_argument("--ref-file", type=Path, required=True,
                    help="any existing local image; it routes row 1 but is never uploaded")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.profile, a.project, a.ref_file)))
