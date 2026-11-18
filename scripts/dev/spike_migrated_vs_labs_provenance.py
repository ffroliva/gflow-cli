r"""Is flow.google.com the same app on a new DNS name, or a different frontend? ($0)

The working hypothesis to test: *"this is just a DNS redirect — the system is the
same, only the root DNS and the namespace changed; the internals barely moved."*

That hypothesis makes three checkable predictions:

  P1  the hop is a server redirect (30x) from labs.google to flow.google.com,
      not a client-side hand-off by a different app
  P2  the same frontend framework renders both (same markers in the DOM)
  P3  the page talks to the same backend hosts and routes
      (``aisandbox-pa.googleapis.com`` REST + labs tRPC)

Each is recorded separately, because they can disagree — a shared BACKEND with a
rewritten FRONTEND would satisfy P3 and fail P2, and that distinction decides
whether gflow needs a new driver, a new transport, or only a new selector.

Every request is logged (method, host, path, status) so the answer is the
network's, not a guess. Credit-free: navigation and DOM reads only; nothing is
typed and nothing is submitted.

    python scripts/dev/spike_migrated_vs_labs_provenance.py \
        --profile ci-probe --project 1e4efe0d-afcf-4e0d-ae4d-b4431f2d73de
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.routes import character_editor_url  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

# Framework fingerprints. Each is a build-tool artefact, not a display string,
# so none of them translate — safe to compare across locales.
_MARKERS_JS = r"""() => {
  const has = (sel) => document.querySelectorAll(sel).length;
  const root = document.documentElement;
  const reactKeyed = [...document.querySelectorAll('div')].slice(0, 400)
    .some(e => Object.keys(e).some(k => k.startsWith('__react')));
  return {
    angular_ngcontent: [...document.querySelectorAll('*')].slice(0, 2000)
      .filter(e => [...e.attributes].some(a => a.name.startsWith('_ngcontent'))).length,
    angular_version: root.getAttribute('ng-version'),
    angular_app_root: has('app-root, [ng-version]'),
    react_root_attr: has('[data-reactroot]'),
    react_fiber_keys: reactKeyed,
    next_data: has('#__NEXT_DATA__'),
    slate_editors: has('[data-slate-editor]'),
    prosemirror: has('.ProseMirror'),
    mat_icon: has('mat-icon'),
    google_symbols_i: has('i.google-symbols'),
    scripts: [...document.querySelectorAll('script[src]')]
      .map(s => (s.getAttribute('src') || '').split('/').pop()).slice(0, 25),
  };
}"""


def _bucket(url: str) -> str:
    """Group a URL into the coarse backend it belongs to."""
    host = urlsplit(url).netloc
    path = urlsplit(url).path
    if "aisandbox" in host:
        return f"aisandbox-pa{path.rsplit('/', 1)[0][:40]}"
    if "batchexecute" in path:
        return f"{host} batchexecute"
    if "/trpc/" in path:
        return f"{host} tRPC"
    if "recaptcha" in host or "recaptcha" in path:
        return "recaptcha"
    return host


async def _main(profile: str, project: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    findings: dict[str, Any] = {"profile": profile, "project": project}

    async with build_client(profile_dir) as client:
        entity_id = await client.create_entity(project)
        step("entity", f"created {entity_id} (free tRPC)")
        findings["entity_id"] = entity_id
        try:
            context = client._context  # noqa: SLF001 - spike reads the live context
            page = await context.new_page()

            requests: list[dict[str, Any]] = []
            page.on(
                "request",
                lambda r: requests.append(
                    {"method": r.method, "url": r.url, "type": r.resource_type}
                ),
            )
            redirects: list[dict[str, Any]] = []
            page.on(
                "response",
                lambda r: (
                    redirects.append(
                        {"status": r.status, "url": r.url, "location": r.headers.get("location")}
                    )
                    if 300 <= r.status < 400
                    else None
                ),
            )
            frame_navs: list[str] = []
            page.on("framenavigated", lambda f: frame_navs.append(f.url) if not f.parent_frame else None)

            # --- P1: how does the labs URL become the flow URL? --------------
            labs_url = character_editor_url("en", project, entity_id)
            step("goto", f"LABS {labs_url}")
            mark = len(requests)
            await page.goto(labs_url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=25_000)
            except Exception:  # noqa: BLE001 - settle is best-effort
                pass
            await page.wait_for_timeout(3000)

            doc_redirects = [
                r for r in redirects if "labs.google" in r["url"] or "flow.google" in r["url"]
            ]
            findings["P1_hop"] = {
                "requested": labs_url,
                "landed": page.url,
                "server_redirects": doc_redirects,
                "top_frame_navigations": frame_navs,
                "verdict": (
                    "server-redirect"
                    if any(
                        r.get("location") and "flow.google.com" in (r.get("location") or "")
                        for r in doc_redirects
                    )
                    else "client-side handoff"
                ),
            }
            step("P1", f"{findings['P1_hop']['verdict']}  landed={page.url}")
            step("P1", f"top-frame navigations: {frame_navs}")
            for r in doc_redirects:
                step("P1", f"  {r['status']} {r['url'][:70]} -> {(r['location'] or '')[:70]}")

            # --- P2: which frontend rendered it? -----------------------------
            findings["P2_markers"] = await page.evaluate(_MARKERS_JS)
            m = findings["P2_markers"]
            step(
                "P2",
                f"angular_ngcontent={m['angular_ngcontent']} ng-version={m['angular_version']} "
                f"react_fiber={m['react_fiber_keys']} next_data={m['next_data']} "
                f"slate={m['slate_editors']} prosemirror={m['prosemirror']}",
            )

            # --- P3: which backends does it call? ----------------------------
            after = requests[mark:]
            buckets = Counter(_bucket(r["url"]) for r in after if r["type"] in {"xhr", "fetch"})
            findings["P3_backends"] = dict(buckets)
            findings["P3_sample_api_urls"] = [
                r["url"][:150]
                for r in after
                if r["type"] in {"xhr", "fetch"}
                and ("aisandbox" in r["url"] or "batchexecute" in r["url"] or "/trpc/" in r["url"])
            ][:20]
            step("P3", f"xhr/fetch by backend: {dict(buckets)}")
            for u in findings["P3_sample_api_urls"][:8]:
                step("P3", f"  {u[:120]}")
        finally:
            try:
                await client.delete_characters(project, [entity_id])
                step("cleanup", f"deleted {entity_id}")
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
                step("cleanup", f"FAILED to delete {entity_id}: {exc}")
            out = default_out_path("migrated_vs_labs_provenance")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project)))
