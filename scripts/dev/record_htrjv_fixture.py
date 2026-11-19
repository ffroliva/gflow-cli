"""Re-record Flow's model catalog fixture (``HTrJv``) used by the model-retirement tests (#1375).

$0, read-only, no browser: the credits direct-read path (``api/flow_wire.py``: the profile's
saved cookies, the app shell's ``at``/``f.sid``/``bl``, one ``batchexecute`` POST) sends
``HTrJv`` with empty arguments, as the page does on every project load. Measured 2026-11-19:
the reply equals the browser's own copy.

The direct read works only within about 1.5–2.5 h of a browser visit to Flow on that profile.
Open any Flow project with ``gflow`` first if this script reports no families.

The fixture keeps only model-family fields and usage keys:
``[display_name, [[usage_key], ...], enabled, family_key, alias, unknown, retirement_date]``.
No cookie, token, account id, cost or limit is written.

    uv run python scripts/dev/record_htrjv_fixture.py --profile NAME [--out PATH]

After re-recording, run ``pytest tests/api/test_model_catalog.py``: a moved retirement date or
a renamed family key fails there until ``api/model_catalog.py`` is updated.
"""

# ruff: noqa: SLF001 - reuses flow_wire's private steps rather than duplicating them

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from gflow_cli.api import flow_wire as fw
from gflow_cli.api.model_catalog import parse_htrjv_families
from gflow_cli.api.transports.batchexecute import parse_frames
from gflow_cli.config import get_settings

RPC = "HTrJv"
DEFAULT_OUT = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "flow_htrjv_model_families.json"
)


async def read_htrjv(profile_dir: Path) -> Any:
    ua = fw._user_agent(profile_dir)
    if ua is None:
        sys.exit(
            "no Chrome version recorded for this profile; sign in with `gflow auth login` first"
        )
    cookies = await fw._read_cookies(profile_dir)
    headers = {
        "user-agent": ua,
        "accept-language": "en-US,en;q=0.9",
        "cookie": "; ".join(f"{name}={value}" for name, value in cookies),
    }
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=15) as client:
        tokens = await fw._shell_tokens(client, profile_dir, headers)
        query = {
            "rpcids": RPC,
            "source-path": "/",
            "bl": tokens.bl,
            "f.sid": tokens.fsid,
            "hl": "en",
            "rt": "c",
        }
        body = urlencode({"f.req": json.dumps([[[RPC, "[]", None, "generic"]]]), "at": tokens.at})
        response = await client.post(
            f"{fw._BATCHEXECUTE}?{urlencode({k: v for k, v in query.items() if v is not None})}",
            content=body,
            headers={
                **headers,
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                "x-same-domain": "1",
                "origin": fw._ORIGIN,
                "referer": f"{fw._ORIGIN}/",
            },
        )
    response.raise_for_status()
    frames = [payload for rid, payload in parse_frames(response.text) if rid == RPC]
    return frames[0] if frames else None


def trim(payload: Any) -> list[list[Any]]:
    """Family entries with usages reduced to their keys, in catalog order."""
    keys = {family.key for family in parse_htrjv_families(payload)}
    trimmed: list[list[Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, list):
            return
        if (
            len(node) >= 4
            and isinstance(node[3], str)
            and node[3] in keys
            and isinstance(node[0], str)
        ):
            usages = [
                [u[0]] for u in node[1] if isinstance(u, list) and u and isinstance(u[0], str)
            ]
            trimmed.append([node[0], usages, *node[2:7]])
            return
        for child in node:
            walk(child)

    walk(payload)
    return trimmed


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-record the HTrJv model-family fixture (#1375)")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    payload = asyncio.run(read_htrjv(get_settings().profile_subdir(args.profile)))
    families = trim(payload)
    if not families:
        sys.exit(
            "HTrJv returned no model families (outside the direct-read window? see the docstring)"
        )
    recorded = datetime.now(UTC).date().isoformat()
    doc = {
        "_comment": (
            "Flow's HTrJv model catalog, trimmed to model families: [display_name, "
            "[[usage_key], ...], enabled, family_key, alias, unknown, retirement_date]. "
            f"Recorded {recorded} by scripts/dev/record_htrjv_fixture.py. Usage entries keep "
            "only their key: costs, durations and limits are not asserted (#1375)."
        ),
        "recorded": recorded,
        "families": families,
    }
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    dated = [(f[0], f[6]) for f in families if len(f) > 6 and f[6]]
    print(f"wrote {len(families)} families to {args.out}; with a retirement date: {dated}")


if __name__ == "__main__":
    main()
