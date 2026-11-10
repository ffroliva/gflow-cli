"""Spike #1223 / #1224: how do Flow's RPC descriptors change between builds, and does anything
in a descriptor survive a rename?

$0, read-only, no cookies, no browser, no RPC call. Every request is a GET of public JavaScript
that Flow's own page load makes: the flow.google.com shell, and its ``/_/js/`` script URL with
the ``m=`` segment set to feature modules the page itself loads (the #1217 ``construct_arm``
method, the same request the nightly canary sends). The OLD-build arm replays script URLs that
Flow's pages requested in September 2026 (captured in ``scripts/dev/_spike_out/*.json``), one
combined GET plus one GET per module per build. That is the edge of "traffic the app sends":
the app sent these exact URLs, but on an older page.

Observed BEFORE this reading was written (orientation, 2026-11-10, not part of the test):
the issue text quotes a ``20261108.05`` descriptor as ``{Xa:"as29s", hb:..., Ya:_.sC, kb:_.pD}``.
Today's ``20261110.00_p0`` bundle has zero ``{Xa:"`` matches; the same ids sit under
``{Wa:"as29s",hb:void 0,Za:!0,Xa:_.tC,kb:_.GDa}``. So the id KEY was renamed by one build, and
the canary (`scripts/canary/rpc_drift.py`) reads PARSE-CHANGED today. That is one pair from a
quoted example, n=1; the arms below do not rely on it. The descriptor key is learned per build
from where the catalogued ids sit, never hard-coded.

Pre-registered reading (written before the arms ran):

| Arm / measure | Outcome | Reading |
|---|---|---|
| A. 5 shell GETs | one label | one build served now |
|  | two labels | a rollout is in progress; both bundles are measured |
| B. Old script URLs | 200 with descriptors | old builds are recoverable: real cross-build data |
|  | 404 / no descriptors | old builds are gone after the fact; #1223 needs the nightly |
| C. Id set, per consecutive pair | identical | ids did not churn across that pair |
|  | <5% added+removed | additive churn: new methods, old ones kept |
|  | >=5% | real churn |
| C. Catalogued ids (live rows) | all present in every build | no id gflow uses was renamed |
| D. Fingerprint candidate: share of common ids | >=95% | stable: can re-find a renamed id |
|  that keep it across every measured pair | | |
|  | 50-95% | weak: a tiebreaker only |
|  | <50% | not a fingerprint |
| D. any of the above with fewer than 2 builds recovered | — | **unmeasured**; settles nothing |

Fingerprint candidates (D): the descriptor's field-key tuple, its boolean flag value, its
response-class reference (a minified name), the variable it is assigned to, its feature module,
its previous and next descriptor ids in bundle order, and the quoted non-id literals within 400
characters.

Output: ``--out`` (default: the MAIN checkout's ``scripts/dev/_spike_out/rpc_churn/``,
gitignored) holds one ``<build>.json`` record and one ``<build>.js`` bundle per build. It never
writes to ``tmp/rpc-inventory/``, which belongs to the nightly. Only build labels, ids, counts
and minified names are printed; the JS is public and carries no account data.

Usage: ``uv run python scripts/dev/spike_rpc_churn.py [--out DIR] [--captures DIR]``
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "canary"))
import rpc_drift as rd  # noqa: E402

MAIN = Path(r"C:/development/github/gflow-cli")
_BL = r"boq_labs-ai-sandbox-frontend_\d{8}\.\d{2}_p\d"
_URL_RE = re.compile(r'[^"\s]*/_/js/k=boq-labs-ai-sandbox[^"\s\\]*')
_KEY = r"[A-Za-z$_]{1,3}"
_LIT_RE = re.compile(r'"([A-Za-z][A-Za-z0-9_ .:-]{3,40})"')
_ID_RE = re.compile(r"^[A-Za-z0-9]{4,7}$")


def get(url: str) -> tuple[int, str]:
    status, body, _ = rd._http_get(url)  # noqa: SLF001 - the canary's own cookie-free GET
    return status, body


def with_modules(script_url: str, modules: list[str]) -> str:
    kept = "/".join(s for s in script_url.split("/") if not s.startswith("m="))
    return f"{kept}/m={','.join(modules)}"


def learn_key(text: str, known: tuple[str, ...]) -> str | None:
    """The object key that holds the id, learned from where the catalogued ids sit."""
    keys: Counter[str] = Counter()
    for rpc in known:
        keys.update(re.findall(rf'\{{({_KEY}):"{rpc}",', text))
    return keys.most_common(1)[0][0] if keys else None


def descriptors(text: str, key: str) -> list[dict[str, object]]:
    pat = re.compile(
        rf'([\w$.]+)=\{{{re.escape(key)}:"([A-Za-z0-9]{{4,7}})"((?:,{_KEY}:[^,{{}}]*)*)\}}'
    )
    out: list[dict[str, object]] = []
    for m in pat.finditer(text):
        fields = re.findall(rf",({_KEY}):([^,{{}}]*)", m.group(3))
        window = text[max(0, m.start() - 400) : m.end() + 400]
        lits = sorted({s for s in _LIT_RE.findall(window) if not _ID_RE.match(s)})
        out.append(
            {
                "id": m.group(2),
                "var": m.group(1),
                "field_keys": [k for k, _ in fields],
                "fields": dict(fields),
                "marked": bool(
                    re.match(r"[;,]\s*[\w$.]+\[[\w$.]+\]=!0", text[m.end() : m.end() + 40])
                ),
                "literals": lits,
            }
        )
    for i, d in enumerate(out):
        d["prev"] = out[i - 1]["id"] if i else None
        d["next"] = out[i + 1]["id"] if i + 1 < len(out) else None
    return out


def measure(build: str, script_url: str, known: tuple[str, ...], out: Path) -> dict[str, object]:
    rec: dict[str, object] = {"build": build, "k": re.search(r"k=([^/]*)", script_url).group(1)}
    mods = list(rd.FEATURE_MODULES)
    status, text = get(with_modules(script_url, mods))
    if status != 200:  # September pages loaded only these two
        mods = ["XRV0Af", "wO1vlb"]
        status, text = get(with_modules(script_url, mods))
    rec.update(modules=mods, status=status, bytes=len(text))
    key = learn_key(text, known) if status == 200 else None
    rec["key"] = key
    if not key:
        rec["descriptors"] = []
        return rec
    descs = descriptors(text, key)
    placement: dict[str, list[str]] = {}
    statuses: dict[str, int] = {}
    for mod in mods:
        s, t = get(with_modules(script_url, [mod]))
        statuses[mod] = s
        for d in descriptors(t, key) if s == 200 else []:
            placement.setdefault(str(d["id"]), []).append(mod)
    for d in descs:
        d["module"] = ",".join(placement.get(str(d["id"]), [])) or None
    rec.update(module_status=statuses, descriptors=descs)
    rec["known_found"] = sorted(k for k in known if re.search(rf"""["']{k}["']""", text))
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", build)
    (out / f"{safe}.js").write_text(text, encoding="utf-8")
    (out / f"{safe}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def live_builds(n: int = 5) -> tuple[dict[str, str], list[tuple[str, int, str]]]:
    seen: dict[str, str] = {}
    log: list[tuple[str, int, str]] = []
    for _ in range(n):
        status, html, final = rd._http_get(rd.SHELL)  # noqa: SLF001
        m = rd._BL_RE.search(html)  # noqa: SLF001
        label = m.group(1) if m else "?"
        log.append((label, len(html), final.split("/")[2] if "//" in final else final))
        url = rd._script_url(html, rd.SHELL) if status == 200 else None  # noqa: SLF001
        if m and url:
            seen.setdefault(label, url)
    return seen, log


def captured_builds(captures: Path) -> dict[str, str]:
    """One recorded script URL per build label, from old spike captures (labels with one k=)."""
    found: dict[str, str] = {}
    for f in sorted(captures.glob("*.json")):
        t = f.read_text(encoding="utf-8", errors="replace")
        labels = set(re.findall(_BL, t))
        urls = _URL_RE.findall(t)
        if len(labels) != 1 or not urls:
            continue
        url = urljoin(rd.SHELL, urls[0].replace("&amp;", "&"))
        found.setdefault(labels.pop(), url)
    return found


def share(pairs: list[tuple[object, object]]) -> str:
    return f"{sum(a == b for a, b in pairs)}/{len(pairs)}" if pairs else "n/a"


def compare(a: dict[str, object], b: dict[str, object], known: tuple[str, ...]) -> None:
    da = {str(d["id"]): d for d in a["descriptors"]}
    db = {str(d["id"]): d for d in b["descriptors"]}
    common = sorted(da.keys() & db.keys())
    added, removed = sorted(db.keys() - da.keys()), sorted(da.keys() - db.keys())
    print(f"\n## {a['build']} -> {b['build']}")
    print(f"ids {len(da)} -> {len(db)}: common {len(common)}", end="")
    print(f", added {len(added)}, removed {len(removed)}")
    print(f"  catalogued missing after: {sorted(set(known) - set(b.get('known_found', [])))}")
    print(f"  added: {added[:30]}")
    print(f"  removed: {removed[:30]}")
    print(f"  id key: {a['key']} -> {b['key']}")

    def same(f: str) -> str:
        return share([(da[i].get(f), db[i].get(f)) for i in common])

    def field_pos(pos: int) -> str:
        def at(d: dict[str, object]) -> object:
            vals = list(d["fields"].values())
            return vals[pos] if len(vals) > pos else None

        return share([(at(da[i]), at(db[i])) for i in common])

    shape_a = Counter(tuple(d["field_keys"]) for d in da.values())
    shape_b = Counter(tuple(d["field_keys"]) for d in db.values())
    print(f"  field-key shapes: {dict(shape_a)} -> {dict(shape_b)}")
    print(f"  same field_keys {same('field_keys')} · var {same('var')} · module {same('module')}")
    print(f"  same prev {same('prev')} · next {same('next')} · marked {same('marked')}")
    for pos in range(4):
        print(f"  same field #{pos + 1} value {field_pos(pos)}")

    def lits(d: dict[str, object]) -> set[str]:
        return set(d["literals"])

    lit = [(len(lits(da[i]) & lits(db[i])) * 2 >= len(lits(da[i])), True) for i in common]
    print(f"  literals overlap >=50% {share(lit)}")
    order_a = [i for i in (str(d["id"]) for d in a["descriptors"]) if i in db]
    order_b = [i for i in (str(d["id"]) for d in b["descriptors"]) if i in da]
    print(f"  bundle order of common ids identical: {order_a == order_b}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", type=Path, default=MAIN / "scripts" / "dev" / "_spike_out" / "rpc_churn"
    )
    ap.add_argument("--captures", type=Path, default=MAIN / "scripts" / "dev" / "_spike_out")
    args = ap.parse_args()
    known = rd.catalog_ids(rd.CATALOG.read_text(encoding="utf-8"))
    live, log = live_builds()
    print("A. shell GETs (label, bytes, host):", log)
    old = captured_builds(args.captures)
    print("B. captured builds:", sorted(old))
    recs = []
    for build, url in sorted({**old, **live}.items()):
        rec = measure(build, url, known, args.out)
        ndesc = len(rec["descriptors"])
        distinct = len({d["id"] for d in rec["descriptors"]})
        found = len(rec.get("known_found", []))
        print(
            f"   {build}: k={rec['k']} status={rec['status']} bytes={rec['bytes']} "
            f"key={rec['key']} descriptors={ndesc} distinct={distinct} "
            f"modules={rec.get('module_status')} known={found}/{len(known)}"
        )
        if ndesc:
            recs.append(rec)
    for a, b in itertools.pairwise(recs):
        compare(a, b, known)
    if len(recs) > 2:
        compare(recs[0], recs[-1], known)


if __name__ == "__main__":
    main()
