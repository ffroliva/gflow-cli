"""Measure migrated Flow's pre-submit generation quotes at $0.

PRE-REGISTERED READINGS (written before the live run):

* A quote is a number structurally contained in the active settings pane and paired
  with a credit unit in its rendered line: record its DOM path and value. No match is
  UNMEASURED for that setting, not evidence that Flow has no quote.
* Per-model, duration, and output-count quote changes are recorded as observed; an
  unavailable model/control is marked unavailable and is not inferred from another.
* Equal quote values in en and pt-BR support locale invariance for those matched
  selections only; missing locale override or quote leaves that comparison unmeasured.
* HTrJv-first and settings-change batchexecute replies are recorded as shape plus
  integer positional paths only. An integer is a price candidate only if it matches
  the DOM quote; positional coincidence alone is not a finding.
* The image composer is recorded as quoted zero, quoted nonzero, no quote, or
  unmeasured. No quote is not the same as a zero quote.
* Any billed RPC or generation POST is aborted before transmission. A balance
  difference is a safety failure and terminates the run.

The survey only navigates, opens settings, and selects controls. It never types a
prompt, presses Enter, submits, or approves a gate. Run only after `gflow auth status`
has verified the selected profile. Outputs are gitignored and contain no tokens,
cookies, account email, prompts, or raw response bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

from playwright.async_api import Page, Request, Route

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from _spike_common import build_client, resolve_profile_dir  # noqa: E402

from gflow_cli.api.image import GenerateImageRequest  # noqa: E402
from gflow_cli.api.image import Model as ImageModel  # noqa: E402
from gflow_cli.api.transports._mc_common import MIGRATED_ROOT_URL  # noqa: E402
from gflow_cli.api.transports._mc_settings_pane import SettingsPaneMixin  # noqa: E402
from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402
from gflow_cli.api.video import GenerateVideoRequest, VideoModel  # noqa: E402

OUT_ROOT = Path(__file__).parent / "_spike_out"
BILLED = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif", "fZytfe", "ogiZ0b")
MODELS = {
    "veo-lite": VideoModel.VEO_3_1_LITE,
    "veo-fast": VideoModel.VEO_3_1_FAST,
    "veo-quality": VideoModel.VEO_3_1_QUALITY,
    "omni-flash": VideoModel.OMNI_FLASH,
    "omni-1.2": VideoModel.OMNI_1_2_FLASH,
}


def is_billed(url: str, body: str) -> bool:
    """Match billed ids in rpcids URL parameters or decoded request form content."""
    decoded = unquote_plus(body)
    url_ids = {
        rpc for value in parse_qs(urlsplit(url).query).get("rpcids", []) for rpc in value.split(",")
    }
    return bool(url_ids.intersection(BILLED) or any(rpc in decoded for rpc in BILLED))


def self_test() -> None:
    assert is_billed("https://x/batchexecute?rpcids=YhhmEf", "")
    assert is_billed("https://x/batchexecute", "f.req=%5B%5B%5B%22nprQif%22%5D%5D%5D")
    assert not is_billed("https://x/batchexecute?rpcids=HTrJv", "f.req=%5B%5D")
    print("guard self-test: URL billed RPC, body-only nprQif, harmless RPC: PASS")


def run_cli(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed executable; arguments are constructed here
        ["uv", "run", "gflow", *args],  # noqa: S607 - uv is intentionally PATH-resolved
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"gflow {' '.join(args[:2])} failed (exit {result.returncode})")
    return result.stdout


def verified_session(profile: str) -> None:
    output = run_cli("auth", "status", "--profile", profile)
    if "Flow session verified" not in output:
        raise RuntimeError(
            "BLOCKED: auth status did not verify the Flow session; no login attempted"
        )


def credits(profile: str) -> int:
    output = run_cli("credits", "user", "--profile", profile, "--json")
    data = json.loads(output)
    value = data.get("credits") if isinstance(data, dict) else None
    if not isinstance(value, int):
        raise RuntimeError("credits output had no integer balance")
    return value


def shape(value: Any) -> Any:
    if isinstance(value, list):
        return [shape(item) for item in value]
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
    return "string"


def integers(value: Any, path: str = "$") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, bool):
        return found
    if isinstance(value, int):
        return [{"path": path, "value": value}]
    if isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(integers(child, f"{path}[{index}]"))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(integers(child, f"{path}.{key}"))
    return found


def rpc_payloads(text: str) -> list[tuple[str, Any]]:
    """Decode response frames but retain only parsed payloads for shape extraction."""
    return parse_frames(text)


async def dom_quotes(page: Page) -> dict[str, Any]:
    return await page.evaluate("""() => {
      const pane = [...document.querySelectorAll('.cdk-overlay-pane')]
        .filter(e => e.getClientRects().length).at(-1);
      if (!pane) return {pane: false, quotes: [], candidates: []};
      const path = e => {
        const parts = [];
        for (let n=e; n && n!==pane; n=n.parentElement) {
          let p=n.tagName.toLowerCase();
          if (n.id) p += '#' + n.id;
          if (typeof n.className === 'string' && n.className.trim())
            p += '.' + n.className.trim().split(/\\s+/).slice(0,3).join('.');
          parts.unshift(p);
        }
        return '.cdk-overlay-pane > ' + parts.join(' > ');
      };
      const candidates = [...pane.querySelectorAll(
        '[class*="credit" i], [class*="cost" i], [data-testid*="credit" i], [aria-label]')]
        .map(e => ({path:path(e), tag:e.tagName.toLowerCase(),
          attrs:[...e.attributes].map(a=>a.name).sort()})).slice(0,80);
      const quotes = [];
      const walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        for (let e=walker.currentNode.parentElement; e && e!==pane; e=e.parentElement) {
          const text = (e.innerText || '').trim();
          if (/\\d/.test(text) && /credit|crédito|créditos/i.test(text)) {
            const key = path(e) + '|' + text;
            if (!quotes.some(q => q.key === key)) quotes.push({key, path:path(e), text});
            break;
          }
        }
      }
      for (const quote of quotes) delete quote.key;
      return {pane:true, quotes, candidates};
    }""")


async def main(profile: str, locales: list[str], out: Path) -> int:
    verified_session(profile)
    before = credits(profile)
    print(f"credits before={before}", flush=True)
    report: dict[str, Any] = {
        "profile": profile,
        "credits_before": before,
        "locale_runs": [],
        "aborted_billed": [],
        "wire": [],
    }
    profile_dir = resolve_profile_dir(profile)
    out.mkdir(parents=True, exist_ok=True)
    async with build_client(profile_dir) as client:
        try:
            page = client._page  # noqa: SLF001 - spike inspects the live page
            context = client._context  # noqa: SLF001 - route guard belongs on its context
            assert page is not None and context is not None
            pending: set[asyncio.Task[None]] = set()
            htrjv_seen = asyncio.Event()

            async def record_response(response: Any) -> None:
                request = response.request
                if "batchexecute" not in request.url:
                    return
                try:
                    body = await response.text()
                except Exception:  # noqa: BLE001 - class only; never retain raw error text
                    return
                for rpc, payload in rpc_payloads(body):
                    report["wire"].append(
                        {
                            "rpc": rpc,
                            "shape": shape(payload),
                            "integers": integers(payload),
                            "phase": phase[0],
                        }
                    )
                    if rpc == "HTrJv":
                        htrjv_seen.set()

            phase = ["load"]

            def schedule_response(response: Any) -> None:
                task = asyncio.create_task(record_response(response))
                pending.add(task)
                task.add_done_callback(pending.discard)

            page.on("response", schedule_response)

            async def guard(route: Route) -> None:
                request: Request = route.request
                body = request.post_data or ""
                blocked_rpc = "batchexecute" in request.url and is_billed(request.url, body)
                blocked_api = (
                    "aisandbox-pa.googleapis.com" in request.url
                    and request.method == "POST"
                    and re.search(r"generate|batchAsync", urlsplit(request.url).path, re.I)
                )
                if blocked_rpc or blocked_api:
                    ids = [rpc for rpc in BILLED if rpc in request.url or rpc in unquote_plus(body)]
                    item = {
                        "url_host": urlsplit(request.url).hostname,
                        "rpc_ids": ids,
                        "api_generation_post": bool(blocked_api),
                    }
                    report["aborted_billed"].append(item)
                    print(f"ABORTED billed request: {item}", flush=True)
                    await route.abort()
                    return
                await route.continue_()

            # Only the two carriers a billed submit can use; a catch-all route made the
            # driver fail `Route.continue_` on unrelated requests (measured 2026-11-20).
            await page.route("**/batchexecute**", guard)
            await page.route("https://aisandbox-pa.googleapis.com/**", guard)
            for locale in locales:
                locale_result: dict[str, Any] = {"locale": locale, "models": {}}
                language = "pt-BR,pt;q=0.9" if locale.lower() == "pt-br" else "en-US,en;q=0.9"
                await context.set_extra_http_headers({"Accept-Language": language})
                await page.goto(
                    f"{MIGRATED_ROOT_URL}?hl={locale}",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                await page.wait_for_timeout(3000)
                if locale == locales[0]:
                    try:
                        await asyncio.wait_for(htrjv_seen.wait(), timeout=15)
                    except TimeoutError:
                        report["htrjv_load_reply"] = "unmeasured: no HTrJv reply observed"
                if "flow.google.com" not in page.url or "/about" in page.url:
                    raise RuntimeError(
                        f"UNMEASURED: Flow did not serve the app for locale {locale}"
                    )
                await page.locator("a[href*='/project/']").first.wait_for(timeout=20_000)
                href = await page.locator("a[href*='/project/']").first.get_attribute("href")
                if not href:
                    raise RuntimeError("UNMEASURED: no existing project link was found")
                target = href if href.startswith("http") else f"https://flow.google.com{href}"
                target = f"{target}{'&' if '?' in target else '?'}hl={locale}"
                await page.goto(target, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(2500)
                pane_driver = SettingsPaneMixin()
                # Read existing project, open its settings pane; no prompt is typed.
                for model_name, model in MODELS.items():
                    model_result: dict[str, Any] = {}
                    for duration in (4, 6, 8):
                        for count in range(1, 5):
                            key = f"{duration}s_x{count}"
                            try:
                                req = GenerateVideoRequest(
                                    prompt="probe", model=model, duration=duration, count=count
                                )
                                phase[0] = f"settings:{locale}:{model_name}:{key}"
                                await pane_driver.apply_video_settings(page, req)
                                await pane_driver._open_pane(page)  # noqa: SLF001
                                await page.wait_for_timeout(600)
                                model_result[key] = await dom_quotes(page)
                                await pane_driver._close_pane(page, strict=False)  # noqa: SLF001
                            except Exception as exc:  # noqa: BLE001 - record miss, do not infer
                                model_result[key] = {
                                    "error": type(exc).__name__,
                                    "quotes": [],
                                    "verdict": "unmeasured",
                                }
                    locale_result["models"][model_name] = model_result
                phase[0] = f"settings:{locale}:image"
                try:
                    # The prompt is never typed; the request type only requires one.
                    await pane_driver.apply_image_settings(
                        page, GenerateImageRequest(prompt="probe", model=ImageModel.NARWHAL)
                    )
                    await pane_driver._open_pane(page)  # noqa: SLF001
                    locale_result["image"] = await dom_quotes(page)
                    await pane_driver._close_pane(page, strict=False)  # noqa: SLF001
                except Exception as exc:  # noqa: BLE001 - record miss, do not infer
                    locale_result["image"] = {"error": type(exc).__name__, "verdict": "unmeasured"}
                report["locale_runs"].append(locale_result)
                (out / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                (out / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            await context.set_extra_http_headers({})
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await context.set_extra_http_headers({})
    after = credits(profile)
    report["credits_after"] = after
    report["credits_unchanged"] = before == after
    (out / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("locale | model | duration/count | quote")
    for run in report["locale_runs"]:
        for model, matrix in run["models"].items():
            for setting, result in matrix.items():
                quote = result.get("quotes", [])
                print(f"{run['locale']} | {model} | {setting} | {quote or 'none/unmeasured'}")
    print(f"credits before={before} after={after}")
    if before != after:
        raise RuntimeError("SAFETY FAILURE: credit balance changed during the spike")
    return 0


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="ffroliva")
    parser.add_argument("--locales", default="en,pt-BR")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    out = args.out or OUT_ROOT / f"spike_1355_{timestamp}"
    locales = [item.strip() for item in args.locales.split(",") if item.strip()]
    if not locales:
        parser.error("--locales must include at least one locale")
    return asyncio.run(main(args.profile, locales, out))


if __name__ == "__main__":
    raise SystemExit(cli())
