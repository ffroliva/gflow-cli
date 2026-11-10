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

Amended after run 1 (the table above is unchanged): run 1 got HTTP 404 on the 20260903 and
20260909 URLs and 200 on 20260929, whose 6.7 MB bundle held no ``{K:"id",`` descriptor at all.
By hand, its ids sit in a constructor, ``new _.Gx("as29s",_.Mx,HWa,[_.dm,!0,_.cm,
"/FlowService.GetMedia"])``, which names the backend method. Run 2 parses that shape too, adds
the flag and the method path as candidates, and sends the old page's exact URL once as a
control when the module-swapped URL 404s.

Amended after run 2: its constructor regex matched only 37 of the 219 ``new _.Gx("`` calls
(the args can hold inline classes), so its "230 ids added" was a parser artifact and is
discarded. Run 3 finds each constructor's options array by its method path instead.

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
    """How the ids are held, learned from where the catalogued ids sit.

    Two shapes are recognised: an object key, ``{Wa:"id",...}`` (returned as ``Wa``), and a
    constructor, ``new _.Gx("id",...,[...,"/Service.Method"])`` (returned as ``new _.Gx``).
    The constructor shape was added after the first run met it on the 20260929 build.
    """
    keys: Counter[str] = Counter()
    for rpc in known:
        keys.update(re.findall(rf'\{{({_KEY}):"{rpc}",', text))
        keys.update(f"new _.{c}" for c in re.findall(rf'new _\.([\w$]+)\("{rpc}",', text))
    return keys.most_common(1)[0][0] if keys else None


_CTOR_RE = re.compile(r'(?:([\w$.]+)=)?new _\.([\w$]+)\("([A-Za-z0-9]{4,7})",')
#: The options array that closes a constructor descriptor; the args before it can hold
#: inline classes, so the array is found by its method path, not by bracket counting.
_OPTS_RE = re.compile(r'\[([^\[\]]*?"(/[A-Z]\w*\.[A-Z]\w*)"[^\[\]]*)\]\)')


def _ctor_descriptors(text: str) -> list[dict[str, object]]:
    """Every ``new _.X("id",...,[...,"/Service.Method"])``, whichever constructor X is
    (20260929 had ``Gx`` for unary and ``Hx`` for server-streaming methods)."""
    out: list[dict[str, object]] = []
    for m in _CTOR_RE.finditer(text):
        opts = _OPTS_RE.search(text, m.end(), m.end() + 1200)
        nxt = _CTOR_RE.search(text, m.end())
        if not opts or (nxt and nxt.start() < opts.start()):
            continue  # not a descriptor: no method path before the next constructor
        flag = re.search(r"!([01])", opts.group(1))
        out.append(
            {
                "id": m.group(3),
                "var": m.group(1),
                "ctor": m.group(2),
                "field_keys": ["ctor", "options"],
                "fields": {"ctor": m.group(2), "options": opts.group(1)},
                "flag": flag.group(1) if flag else None,
                "method": opts.group(2),
                "marked": None,
                "literals": _literals(text, m.start(), opts.end()),
            }
        )
    return out


def _literals(text: str, start: int, end: int) -> list[str]:
    window = text[max(0, start - 400) : end + 400]
    return sorted({s for s in _LIT_RE.findall(window) if not _ID_RE.match(s)})


def descriptors(text: str, key: str) -> list[dict[str, object]]:
    if key.startswith("new _."):
        out = _ctor_descriptors(text)
    else:
        out = _object_descriptors(text, key)
    for i, d in enumerate(out):
        d["prev"] = out[i - 1]["id"] if i else None
        d["next"] = out[i + 1]["id"] if i + 1 < len(out) else None
    return out


def _object_descriptors(text: str, key: str) -> list[dict[str, object]]:
    pat = re.compile(
        rf'([\w$.]+)=\{{{re.escape(key)}:"([A-Za-z0-9]{{4,7}})"((?:,{_KEY}:[^,{{}}]*)*)\}}'
    )
    out: list[dict[str, object]] = []
    for m in pat.finditer(text):
        fields = re.findall(rf",({_KEY}):([^,{{}}]*)", m.group(3))
        flag = next((v[1] for _, v in fields if v in ("!0", "!1")), None)
        out.append(
            {
                "id": m.group(2),
                "var": m.group(1),
                "field_keys": [k for k, _ in fields],
                "fields": dict(fields),
                "flag": flag,
                "method": None,
                "marked": bool(
                    re.match(r"[;,]\s*[\w$.]+\[[\w$.]+\]=!0", text[m.end() : m.end() + 40])
                ),
                "literals": _literals(text, m.start(), m.end()),
            }
        )
    return out


def measure(build: str, script_url: str, known: tuple[str, ...], out: Path) -> dict[str, object]:
    rec: dict[str, object] = {"build": build, "k": re.search(r"k=([^/]*)", script_url).group(1)}
    mods = list(rd.FEATURE_MODULES)
    status, text = get(with_modules(script_url, mods))
    if status != 200:  # September pages loaded only these two
        mods = ["XRV0Af", "wO1vlb"]
        status, text = get(with_modules(script_url, mods))
    rec.update(modules=mods, status=status, bytes=len(text))
    if status != 200:
        # Control: the exact URL the old page sent. A 404 here too means the build is gone,
        # not that a module name moved.
        rec["control_status"] = get(script_url)[0]
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
    rec["method_paths"] = sorted(set(re.findall(r'"(/[A-Z]\w*\.[A-Z]\w*)"', text)))
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
    print(f"  same flag (!0/!1) {same('flag')} · same method {same('method')}")
    paths_b = set(b.get("method_paths", []))
    with_path = [i for i in common if da[i].get("method")]
    kept = sum(da[i]["method"] in paths_b for i in with_path)
    print(f"  old method path still a string in new bundle: {kept}/{len(with_path)}")
    print(f"  method-path strings: {len(a.get('method_paths', []))} -> {len(paths_b)}")
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
            f"modules={rec.get('module_status')} known={found}/{len(known)} "
            f"control={rec.get('control_status')}"
        )
        if ndesc:
            recs.append(rec)
    for a, b in itertools.pairwise(recs):
        compare(a, b, known)
    if len(recs) > 2:
        compare(recs[0], recs[-1], known)


if __name__ == "__main__":
    main()
