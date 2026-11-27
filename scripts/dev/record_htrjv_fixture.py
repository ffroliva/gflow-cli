"""Re-record Flow's model catalog fixture (``HTrJv``) used by the model-retirement tests (#1375).

$0, read-only, no browser: ``flow_wire.read_model_catalog`` (the credits direct-read path:
the profile's saved cookies, the app shell's ``at``/``f.sid``/``bl``, one ``batchexecute``
POST, and the same refusals: direct reads off, a proxy configured) sends ``HTrJv`` with empty
arguments, as the page does on every project load. Measured 2026-11-19: the reply equals the
browser's own copy. The catalog holds model names, usage keys, costs and limits, and no
account data; the fixture keeps less still (below).

The direct read works only within about 1.5–2.5 h of a browser visit to Flow on that profile.
Open any Flow project with ``gflow`` first if this script reports no families.

The fixture keeps only model-family fields and usage keys:
``[display_name, [[usage_key], ...], enabled, family_key, alias, unknown, retirement_date]``.
No cookie, token, account id, cost or limit is written.

    uv run python scripts/dev/record_htrjv_fixture.py --profile NAME [--out PATH]

The script prints each difference from gflow's mapping (``model_catalog.catalog_drift``, the
same comparison ``gflow doctor`` makes). After re-recording, run
``pytest tests/api/test_model_catalog.py``: an added or removed family, a dropped image wire key
or a new retirement date fails there until ``api/model_catalog.py`` is updated (#1550).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gflow_cli.api import flow_wire
from gflow_cli.api.model_catalog import classify_catalog, parse_htrjv_families
from gflow_cli.config import get_settings
from gflow_cli.errors import NetworkError, RateLimitError

DEFAULT_OUT = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "flow_htrjv_model_families.json"
)


def trim(payload: object) -> list[list[Any]]:
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
    settings = get_settings()
    if not settings.wire_reads:
        sys.exit(
            "direct reads are off (the macOS default, or GFLOW_CLI_WIRE_READS=0);"
            " re-run with GFLOW_CLI_WIRE_READS=1"
        )
    flow_wire._TOTAL_S = 15.0  # pyright: ignore[reportPrivateUsage] - a one-off read may be slow
    try:
        payload = asyncio.run(flow_wire.read_model_catalog(settings.profile_subdir(args.profile)))
    except flow_wire.WireReadUnavailable as exc:  # the reason only: it never carries detail
        sys.exit(f"HTrJv read unavailable: {exc.reason}")
    except (RateLimitError, NetworkError) as exc:
        sys.exit(f"HTrJv read refused ({exc}): Flow is throttling this account; wait, then re-run")
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
    today = datetime.now(UTC).date()
    for diff in classify_catalog(parse_htrjv_families(families), today=today):
        print(f"drift ({diff.kind}): {diff.line}")


if __name__ == "__main__":
    main()
