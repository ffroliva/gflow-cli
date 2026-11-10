# Spike: Flow's RPC ids are readable in the JavaScript it serves (#1217)

**Date:** 2026-11-10 · **Profile:** `denon82` · **Cost:** $0 (no generation, upload or RPC call
made by the spike; the page arm opened a project page and one clip editor without clicking)
· **Script:** [`scripts/dev/spike_rpc_ids_in_bundle.py`](../../../scripts/dev/spike_rpc_ids_in_bundle.py)
· **Build label seen:** `boq_labs-ai-sandbox-frontend_20261108.05_p0`

## Question

Can a check with no browser and no RPC call confirm that every `batchexecute` RPC id gflow uses
still exists, billed submits included? That needs the ids to appear as literal strings in the
JavaScript flow.google.com serves.

## Pre-registered reading

Written in the script's docstring before the first run:

| Outcome | Reading |
|---|---|
| every known id found, random control absent | a bundle check is a valid drift signal |
| some found | lazy-loaded chunks; record which are not |
| none found | ids are not literals in the shell's bundles |
| random control found | the search is broken; settles nothing |

## Results

The 16 known ids are gflow's code constants plus `nzlxg`. While the table was being built
(#1216), it found a 17th constant, `o8DA4` (project rename). Arm 3 was re-run with it and
found **17/17**, with 0/200 controls.

| Arm | Cookies | Scripts | Known ids found | Reading |
|---|---|---|---|---|
| 1. Shell bundles (`m=_b`) | with / without | 3 | **3/16** (`as29s`, `jHPbke`, `nzlxg`) | "some found": the rest are lazy |
| 2. Project page + clip editor (browser, no click) | profile | 10 | **16/16** | lazy feature modules hold them |
| 2b. Same 7 chunk URLs replayed | **none** | 7, all 200 | **16/16** | chunks are public |
| 3. Public shell URL with `m=XRV0Af,wO1vlb,wj3Fuc,zxMy9c` | **none**, no browser | 1 (7.0 MB) | **16/16** | **a cookie-free, browser-free check works** |

Supporting observations:
- **Controls:** a random 6-char control was absent in arm 1 (both cookie arms), and 0 of 200 random
  controls matched in arm 3's 7 MB bundle. A hit is therefore not a coincidence of size.
- **Calls:** every RPC id the page actually called (17 distinct ids, including `Zzl0ze`, `nzlxg` and
  `as29s`) is also named in the loaded scripts. None is built at runtime.
- **Descriptor shape:** RPC descriptors look like `{Xa:"as29s", …}`. Arm 3's bundle holds **261**
  distinct ids in that shape; gflow knows 16. The others are the candidate inventory for #1216.
- **`SNlM0e` was absent** in the cookie arm. This first read as the token flapping, which is
  wrong. Spike #1221 (PR #1231) found that this shell (39,716 bytes) matches the signed-out page
  (about 39.7k), not a signed-in one (42.5–42.8k). The cookies were not accepted at that moment,
  and every signed-in page carries `SNlM0e`. It doesn't affect this spike, which needs no token.

## Verdict

**GO for a bundle-based drift check.** With only the public shell, no cookies and no browser:
1. read the build label and the base script URL;
2. swap `m=` for the four feature modules;
3. search the 7 MB bundle for each catalogued id.

That confirms existence for all 16 ids, billed submits included, without calling any of them.

## Limits (not measured)

- **Module names can change.** The four names (`XRV0Af`, `wO1vlb`, `wj3Fuc`, `zxMy9c`) were seen
  on one build. If they change, arm 3 misses ids. The check must then fall back to arm 2 (a page
  load, still $0) to learn the new names, and must report "module list changed" rather than "id gone".
- **Presence is not behavior.** An id present in the bundle can still change its payload shape.
  Payload drift is caught by e2e and live-verify, not by this check.
- **Only flow.google.com** was observed. labs.google answers 308 to flow.google.com on our profiles
  (2026-09-14 survey).
- **One build, one profile, one run.** Repetition across builds is what #1218's nightly run will
  measure.
