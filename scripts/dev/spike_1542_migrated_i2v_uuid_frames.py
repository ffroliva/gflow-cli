r"""Live $0 DOM + wire spike for #1542: in-project image UUIDs as video i2v Start/End frames.

Cost: $0 — zero credits, zero uploads (no ``maseQ``), every submit aborted in-browser via
Playwright ``page.route("**/batchexecute*", ...)`` before it leaves Chrome.

Question:
  Can ``flow.google.com``'s Frames picker bind an existing in-project image UUID onto the
  Start and End chips with zero upload — using the project grid tile's ``/asb/<token>``
  identity (mirroring #913/#995 on ``image i2i``) — and does the outgoing ``batchexecute``
  submit carry the exact media UUID(s) on ``eb1hJf`` (Start-only) and ``nprQif`` (Start+End)?

What this probe measures (all at $0):
  Q0 Catalog rows: verify ``flow_project_id`` and ``display_name`` for Start, End, and a
     truncated-ellipsis (``…``) image in the target project.
  Q1 Grid tokens: verify ``img[data-media-id="<uuid>"]`` exposes ``/asb/<token>`` for all
     target UUIDs (and returns ``""`` for a random negative-control UUID).
  Q2 Frame picker options: open Start picker, inspect ``button.asset-item[role='option']``
     options, verify each option's ``<img src="/asb/<token>">`` matches Q1's grid tokens.
  Q3 Truncated ``…`` captions: compare searching raw caption ending in ``…`` vs stripped
     caption (trailing ``…`` removed) in the frame picker search box.
  Q4 Angular search-input reset: after typing a non-matching (renamed/stale) query, test
     three reset strategies on the live Angular input:
       (a) ``search.fill("")``
       (b) ``Control+A`` + ``Backspace``
       (c) ``Escape`` + reopen picker
     Record option counts, token visibility, and input value after each.
  Q5 Start-only route-aborted submit: bind Start UUID by ``/asb/<token>``, assert
     ``BOUND_CHIP == 1``, click submit under route-abort, check ``rpcid == "eb1hJf"``,
     ``_i2v_body_problem(...) is None``, and ``maseQ`` count == 0.
  Q6 Start+End route-aborted submit: bind End UUID by ``/asb/<token>``, assert
     ``BOUND_CHIP == 2``, click submit under route-abort, check ``rpcid == "nprQif"``,
     ``_interpolation_body_problem(...) is None``, and ``maseQ`` count == 0.

Pre-registered reading (written before the first run, 2026-11-28):
  - Q1 all target UUIDs return distinct non-empty ``/asb/<token>`` and random UUID returns ""
      -> grid token identity is available for video i2v on this host.
  - Q2 picker options carry matching ``/asb/<token>`` values
      -> exact token-based option selection works in the Frames picker (locale-invariant,
         immune to colliding captions).
  - Q3 raw ``…`` fails (0 matches) while stripped succeeds (token present)
      -> stripping trailing ``…`` / ``.`` is empirically required; if both fail, inspect
         why (e.g. search-prefix length or punctuation).
  - Q4 (a)/(b)/(c) comparison
      -> whichever method(s) reliably restore the unfiltered picker list and surface the
         target token become the production fallback for stale/renamed captions; any method
         that leaves the filtered list stale is rejected.
  - Q5 ``eb1hJf`` passes ``_i2v_body_problem`` with 0 ``maseQ`` uploads AND
    Q6 ``nprQif`` passes ``_interpolation_body_problem`` with 0 ``maseQ`` uploads
      -> in-place zero-upload Start and Start+End frame binding is confirmed on the wire.
  - 0/1 on any arm -> does not confirm; record DOM/wire evidence before changing code.

Usage:
    python scripts/dev/spike_1542_migrated_i2v_uuid_frames.py \
        --profile <profile> --project <project-uuid> \
        --start-media <uuid> --end-media <uuid> [--ellipsis-media <uuid>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports import migrated_composer as mc  # noqa: E402
from gflow_cli.api.transports._mc_common import (  # noqa: E402
    _GRID_TOKEN_JS,
    BATCHEXECUTE_GLOB,
    BOUND_CHIP,
    EMPTY_CHIP,
    FRAME_COMMIT_GRACE_S,
    FRAME_COMMIT_HIDDEN_S,
    FRAME_PICKER_OPEN_S,
    FRAMES_LIGATURE,
    INTERPOLATION_SUBMIT_RPC,
    MODEL_KEY,
    PICKER_CONFIRM,
    PICKER_OPTION,
    PICKER_SEARCH,
    SUBMIT_RPCS,
    UPLOAD_RPC,
    _i2v_body_problem,
    _interpolation_body_problem,
    _ligature,
    _picker_pane,
    body_rpcid,
    request_body,
    url_rpcid,
)
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel  # noqa: E402
from gflow_cli.config import get_settings  # noqa: E402
from gflow_cli.data.repository import DataRepository  # noqa: E402
from gflow_cli.data.store import DataStore  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

_PICKER_OPTION_PAIRS_JS = (
    "(els) => els.map(o => {"
    " const img = o.querySelector('img');"
    " const m = ((img && img.getAttribute('src')) || '').match(/\\/asb\\/([A-Za-z0-9_-]+)/);"
    " const t = o.querySelector('.asset-title');"
    " return [m ? m[1] : '', ((t && t.textContent) || '').trim()]; })"
)


def _catalog_row(profile: str, media_id: str) -> dict[str, Any]:
    with DataStore.open(get_settings().resolved_db_path()) as store:
        asset = DataRepository(store).get_asset_by_flow_media_id(profile, media_id)
    if asset is None:
        return {"found": False}
    dn = str(asset.metadata_json.get("display_name") or "")
    return {
        "found": True,
        "kind": str(asset.kind),
        "flow_project_id_matches": asset.flow_project_id,
        "display_name_len": len(dn),
        "ends_with_ellipsis": dn.endswith("\u2026"),
        "display_name": dn,
    }


async def _read_picker_pairs(picker: Any) -> list[tuple[str, str]]:
    raw = await picker.locator(PICKER_OPTION).evaluate_all(_PICKER_OPTION_PAIRS_JS)
    return [(str(tok), str(title)) for tok, title in raw]


async def _wait_for_token(
    picker: Any, target_token: str, timeout_s: float = 5.0
) -> tuple[int | None, list[tuple[str, str]]]:
    import time

    deadline = time.monotonic() + timeout_s
    pairs: list[tuple[str, str]] = []
    while True:
        pairs = await _read_picker_pairs(picker)
        tokens = [tok for tok, _ in pairs]
        if target_token in tokens:
            return tokens.index(target_token), pairs
        if time.monotonic() >= deadline:
            return None, pairs
        await asyncio.sleep(0.25)


async def _close_picker(page: Any) -> None:
    await page.keyboard.press("Escape")
    try:
        await _picker_pane(page).wait_for(state="hidden", timeout=3000)
    except Exception:
        await page.wait_for_timeout(500)


async def _open_picker(composer: mc.MigratedComposer, page: Any, label: str) -> Any:
    return await composer._open_frame_picker(page, label)  # noqa: SLF001


async def _commit_option_by_token(
    composer: mc.MigratedComposer,
    page: Any,
    picker: Any,
    token: str,
    expect_bound: int,
) -> dict[str, Any]:
    idx, pairs = await _wait_for_token(picker, token, timeout_s=FRAME_PICKER_OPEN_S)
    if idx is None:
        raise RuntimeError(f"Token not found in picker ({len(pairs)} options)")
    await picker.locator(PICKER_OPTION).nth(idx).click(timeout=4000)
    used_confirm = False
    try:
        await _picker_pane(page).wait_for(state="hidden", timeout=int(FRAME_COMMIT_GRACE_S * 1000))
    except Exception:
        confirm = _picker_pane(page).locator(PICKER_CONFIRM).first
        if await confirm.is_visible():
            await confirm.click(timeout=4000)
            used_confirm = True
        await _picker_pane(page).wait_for(state="hidden", timeout=int(FRAME_COMMIT_HIDDEN_S * 1000))
    await page.locator(BOUND_CHIP).nth(expect_bound - 1).wait_for(state="visible", timeout=5000)
    bound_count = await page.locator(BOUND_CHIP).count()
    empty_count = await page.locator(EMPTY_CHIP).count()
    return {
        "option_index": idx,
        "offered": len(pairs),
        "used_confirm": used_confirm,
        "bound_chips": bound_count,
        "empty_chips": empty_count,
    }


async def _probe_truncated_caption(
    composer: mc.MigratedComposer,
    page: Any,
    caption: str,
    token: str,
) -> dict[str, Any]:
    """Q3: compare raw trailing-ellipsis query vs stripped query."""
    stripped = caption.strip().rstrip("\u2026").rstrip(".").strip()
    out: dict[str, Any] = {
        "raw_len": len(caption),
        "ends_with_ellipsis": caption.endswith("\u2026"),
        "stripped_len": len(stripped),
    }
    for arm_name, query in (("raw", caption), ("stripped", stripped)):
        picker = await _open_picker(composer, page, "Start")
        search = picker.locator(PICKER_SEARCH).first
        await search.click(timeout=4000)
        await page.keyboard.insert_text(query)
        idx, pairs = await _wait_for_token(picker, token, timeout_s=4.0)
        out[arm_name] = {
            "found": idx is not None,
            "index": idx,
            "offered": len(pairs),
        }
        await _close_picker(page)
    return out


async def _probe_search_clear_methods(
    composer: mc.MigratedComposer,
    page: Any,
    target_token: str,
) -> dict[str, Any]:
    """Q4: compare fill(''), Control+A+Backspace, and Escape+reopen after a stale query."""
    stale = "zq spike 1542 nonexistent renamed caption"
    results: dict[str, Any] = {}

    for method in ("fill_empty", "ctrl_a_backspace", "escape_reopen"):
        uptedb_calls = 0

        def _on_req(req: Any) -> None:
            nonlocal uptedb_calls
            if "batchexecute" in str(req.url) and url_rpcid(str(req.url)) == "UpteDb":
                uptedb_calls += 1

        page.on("request", _on_req)
        try:
            picker = await _open_picker(composer, page, "Start")
            search = picker.locator(PICKER_SEARCH).first
            await search.click(timeout=4000)
            await page.keyboard.insert_text(stale)
            await page.wait_for_timeout(1500)
            before_pairs = await _read_picker_pairs(picker)
            uptedb_before = uptedb_calls

            if method == "fill_empty":
                await search.fill("")
                idx, after_pairs = await _wait_for_token(picker, target_token, timeout_s=4.0)
                val_after = await search.input_value()
                await _close_picker(page)
            elif method == "ctrl_a_backspace":
                await search.click(timeout=4000)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                idx, after_pairs = await _wait_for_token(picker, target_token, timeout_s=4.0)
                val_after = await search.input_value()
                await _close_picker(page)
            else:
                await _close_picker(page)
                picker = await _open_picker(composer, page, "Start")
                search = picker.locator(PICKER_SEARCH).first
                idx, after_pairs = await _wait_for_token(picker, target_token, timeout_s=4.0)
                val_after = await search.input_value()
                await _close_picker(page)

            results[method] = {
                "before_offered": len(before_pairs),
                "after_offered": len(after_pairs),
                "found_target_token": idx is not None,
                "target_index": idx,
                "input_value_after": val_after,
                "uptedb_after_clear": uptedb_calls - uptedb_before,
            }
        finally:
            page.remove_listener("request", _on_req)
    return results


async def _route_aborted_submit(
    page: Any,
    *,
    expect_start: str,
    expect_end: str | None,
) -> dict[str, Any]:
    """Click submit with every SUBMIT_RPC aborted before leaving Chrome ($0)."""
    captured: list[dict[str, Any]] = []
    uploads: list[str] = []

    async def _guard(route: Any, request: Any) -> None:
        url = str(request.url)
        body = request_body(request)
        rpcid = url_rpcid(url) or body_rpcid(body)
        if rpcid == UPLOAD_RPC:
            uploads.append(url)
        if rpcid in SUBMIT_RPCS:
            key_m = MODEL_KEY.search(body)
            key = key_m.group(0) if key_m else None
            if rpcid == INTERPOLATION_SUBMIT_RPC:
                problem = _interpolation_body_problem(body, rpcid, expect_start, expect_end)
            else:
                problem = _i2v_body_problem(body, rpcid, expect_start, end_media_id=expect_end)
            captured.append(
                {
                    "rpcid": rpcid,
                    "rpcid_in_url": url_rpcid(url) == rpcid,
                    "rpcid_in_body": body_rpcid(body) == rpcid,
                    "model_key": key,
                    "carries_start_id": expect_start in body,
                    "carries_end_id": (expect_end in body) if expect_end else None,
                    "body_bytes": len(body),
                    "guard_problem": problem,
                }
            )
            await route.abort()
            return
        await route.continue_()

    await page.route(BATCHEXECUTE_GLOB, _guard)
    try:
        submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
        # Wait briefly for submit button to become enabled.
        for _ in range(30):
            if await submit.is_enabled():
                break
            await page.wait_for_timeout(100)
        await submit.click(timeout=10_000)
        for _ in range(40):
            if captured:
                break
            await page.wait_for_timeout(250)
    finally:
        await page.unroute(BATCHEXECUTE_GLOB, _guard)

    return {
        "captured": captured,
        "maseQ_uploads": len(uploads),
    }


async def main(
    profile: str,
    project: str,
    start_media: str,
    end_media: str,
    ellipsis_media: str | None,
) -> int:
    findings: dict[str, Any] = {
        "cost": "$0 (route-aborted submits, zero uploads)",
        "has_ellipsis_arm": bool(ellipsis_media),
    }
    out = default_out_path("spike_1542_migrated_i2v_uuid_frames")

    def save() -> None:
        out.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")

    # Q0: catalog rows
    start_row = _catalog_row(profile, start_media)
    end_row = _catalog_row(profile, end_media)
    ell_row = _catalog_row(profile, ellipsis_media) if ellipsis_media else None
    findings["q0"] = {
        "start_found": start_row.get("found"),
        "start_project_match": start_row.get("flow_project_id_matches") == project,
        "start_caption_len": start_row.get("display_name_len"),
        "end_found": end_row.get("found"),
        "end_project_match": end_row.get("flow_project_id_matches") == project,
        "end_caption_len": end_row.get("display_name_len"),
        "ellipsis_found": ell_row.get("found") if ell_row else None,
        "ellipsis_project_match": (
            ell_row.get("flow_project_id_matches") == project if ell_row else None
        ),
        "ellipsis_ends_with_ellipsis": ell_row.get("ends_with_ellipsis") if ell_row else None,
    }
    step("Q0", json.dumps(findings["q0"]))
    save()

    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001 - spike reads the live context
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        composer = mc.MigratedComposer()
        editor = await composer.ensure_editor(page, project)
        findings["editor"] = editor

        # Q1: Grid tokens
        grid_count = await page.evaluate(
            "() => document.querySelectorAll('img[data-media-id]').length"
        )
        start_tok = str(await page.evaluate(_GRID_TOKEN_JS, start_media) or "")
        end_tok = str(await page.evaluate(_GRID_TOKEN_JS, end_media) or "")
        ell_tok = (
            str(await page.evaluate(_GRID_TOKEN_JS, ellipsis_media) or "") if ellipsis_media else ""
        )
        neg_tok = str(await page.evaluate(_GRID_TOKEN_JS, str(uuid.uuid4())) or "")
        findings["q1"] = {
            "grid_tile_count": grid_count,
            "start_token_present": bool(start_tok),
            "end_token_present": bool(end_tok),
            "tokens_distinct": bool(start_tok and end_tok and start_tok != end_tok),
            "ellipsis_token_present": bool(ell_tok) if ellipsis_media else None,
            "negative_control_empty": neg_tok == "",
        }
        step("Q1", json.dumps(findings["q1"]))
        save()
        if not start_tok or not end_tok:
            step("STOP", "Start or End image token missing from project grid")
            return 2

        # Enter Video + Frames submode via production apply_video_settings
        dummy_req = GenerateVideoRequest(
            prompt="slow camera push",
            mode=Mode.I2V,
            aspect=Aspect.PORTRAIT,
            model=VideoModel.VEO_3_1_LITE,
            start_image_ref_id=start_media,
        )
        await composer.apply_video_settings(page, dummy_req)
        step("settings", "applied Video + Frames + veo-lite + 9:16")

        # Q2: Inspect unfiltered Start picker options and /asb/<token> matching
        picker = await _open_picker(composer, page, "Start")
        await page.wait_for_timeout(1500)
        unfiltered_pairs = await _read_picker_pairs(picker)
        picker_tokens = [tok for tok, _ in unfiltered_pairs]
        findings["q2"] = {
            "unfiltered_option_count": len(unfiltered_pairs),
            "options_with_asb_token": sum(1 for tok in picker_tokens if tok),
            "start_in_unfiltered": start_tok in picker_tokens,
            "start_unfiltered_index": (
                picker_tokens.index(start_tok) if start_tok in picker_tokens else None
            ),
            "end_in_unfiltered": end_tok in picker_tokens,
            "end_unfiltered_index": (
                picker_tokens.index(end_tok) if end_tok in picker_tokens else None
            ),
        }
        step("Q2", json.dumps(findings["q2"]))
        await _close_picker(page)
        save()

        # Q3: Truncated ellipsis caption probe (if provided and in grid)
        if ellipsis_media and ell_row and ell_row.get("display_name") and ell_tok:
            findings["q3"] = await _probe_truncated_caption(
                composer, page, str(ell_row["display_name"]), ell_tok
            )
            step("Q3", json.dumps(findings["q3"]))
            save()

        # Q4: Search-input clear strategies on stale/renamed query
        findings["q4"] = await _probe_search_clear_methods(composer, page, start_tok)
        step("Q4", json.dumps(findings["q4"]))
        save()

        # Q5: Bind Start by token (with sanitized caption query) -> $0 route-aborted eb1hJf
        start_query = (
            str(start_row.get("display_name") or "").strip().rstrip("\u2026").rstrip(".").strip()
        )
        picker = await _open_picker(composer, page, "Start")
        if start_query:
            search = picker.locator(PICKER_SEARCH).first
            await search.click(timeout=4000)
            await page.keyboard.insert_text(start_query)
        start_bind = await _commit_option_by_token(composer, page, picker, start_tok, 1)
        await composer.send_prompt(page, "slow camera push")
        start_submit = await _route_aborted_submit(page, expect_start=start_media, expect_end=None)
        findings["q5_start_only"] = {
            "bind": start_bind,
            "submit": start_submit,
        }
        step("Q5", json.dumps(findings["q5_start_only"]))
        save()

        # Q6: Reload cleanly, bind BOTH Start and End by token -> $0 route-aborted nprQif
        await page.reload(wait_until="domcontentloaded", timeout=45_000)
        await composer.ensure_editor(page, project)
        await composer.apply_video_settings(page, dummy_req)
        picker = await _open_picker(composer, page, "Start")
        if start_query:
            search = picker.locator(PICKER_SEARCH).first
            await search.click(timeout=4000)
            await page.keyboard.insert_text(start_query)
        q6_start_bind = await _commit_option_by_token(composer, page, picker, start_tok, 1)
        end_query = (
            str(end_row.get("display_name") or "").strip().rstrip("\u2026").rstrip(".").strip()
        )
        picker = await _open_picker(composer, page, "End")
        if end_query:
            search = picker.locator(PICKER_SEARCH).first
            await search.click(timeout=4000)
            await page.keyboard.insert_text(end_query)
        end_bind = await _commit_option_by_token(composer, page, picker, end_tok, 2)
        await composer.send_prompt(page, "slow camera transition")
        end_submit = await _route_aborted_submit(
            page, expect_start=start_media, expect_end=end_media
        )
        findings["q6_start_and_end"] = {
            "start_bind": q6_start_bind,
            "end_bind": end_bind,
            "submit": end_submit,
        }
        step("Q6", json.dumps(findings["q6_start_and_end"]))
        save()

    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--start-media", required=True)
    ap.add_argument("--end-media", required=True)
    ap.add_argument("--ellipsis-media", default=None)
    a = ap.parse_args()
    raise SystemExit(
        asyncio.run(main(a.profile, a.project, a.start_media, a.end_media, a.ellipsis_media))
    )
