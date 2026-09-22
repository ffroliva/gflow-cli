# Live verification — v0.79.1

> Evidence for the user-facing changes in this release, run against real Flow.
> v0.79.1 is a fix-only release: its single user-facing change is the signed-media
> download path ([#895](https://github.com/ffroliva/gflow-cli/issues/895)). The other
> entries are test-only or error-text changes that carry their own e2e coverage.

## Environment

| | |
|---|---|
| Release branch | `chore/release-v0.79.1` (cut from `develop@142c4d95`) |
| PR merged | [#899](https://github.com/ffroliva/gflow-cli/pull/899) — all 16 checks green incl. SonarCloud |
| Profile | `ffroliva` (real-browser Chrome strategy) |
| Host Flow served | `flow.google.com` (migrated) |
| Date | 2026-09-22 |
| Transport | `ui_automation` → migrated composer |

## What changed, and what was verified

The release touches **three** signed-media download call sites, and all three route
through **one** new shared helper, `transports/_common.get_signed_media` — which is what
makes the coverage below meaningful rather than partial.

| Site | Reached by | Verified live? |
|---|---|---|
| **S2** `migrated_recover._fetch_verified` | `gflow data download` / `gflow_download_media` | ✅ **Yes — $0** |
| **S1** `migrated_composer._fetch_mp4` | every `gflow video t2v/i2v/r2v` | ⚠️ **Helper yes, call site no** — see blocker |
| **S3** `ui_automation_video._download_video` | labs `media.getMediaUrlRedirect` | ❌ **Blocked** — see blocker |

### ✅ S2 — verified live, zero credits

`pytest tests/e2e/test_data_download_e2e.py -m e2e_data` → **2 passed in 20.74 s**.

Five-layer ledger:

| Layer | Evidence |
|---|---|
| File count | 1 mp4 written per recovered clip |
| Magic bytes | `body[4:8] == b"ftyp"` asserted |
| Shape | byte length equals the size Flow reports for that clip; `> 100_000 B` sanity floor |
| structlog invariants | `migrated.recover_download` emitted with `media_id`, `workflow_id`, `bytes`; **no `url=` kwarg on any event** (the signed URL must never reach a log) |
| User-confirmable artifact | `tmp/…/<media_id>.mp4`, 2 702 168 B, plays |

This drove a **real** Flow clip route, a **real** `as29s` record, and a **real** signed
`flow-content.google` transfer — through the changed helper, with `max_retries` now on the
GET. It is the load-bearing live evidence for this release.

Hand-run confirmation of the same path, outside pytest: `gflow data download
9ad33c78-… --profile ffroliva --out tmp/reset-probe` → exit 0, 2 702 168 B, ~25 s.
Repeated **20×** with 20/20 successes (see blocker note on what that does *not* show).

### ⚠️ S1 — the helper is verified, the call site is not

S1 and S2 call the **same** `get_signed_media`, so the retry argument, the transport-error
predicate, the `NetworkError` translation and the URL-leak guards are all exercised by the
S2 run above. What S2 does **not** exercise is S1's own call-site wiring — that it passes
`recoverable_clip_hint(record.media_id)` rather than the recovery hint, and that the
`>= 300` branch carries `expired_link_hint`.

Covered offline instead, in `tests/api/transports/test_signed_media_retry.py` (13 tests),
including that the rendered error names the real media id and contains neither `Expires=`
nor `Signature=`.

**Why not verified live:** reaching S1 requires a **billed** Veo generation, and
`gflow credits user` returns HTTP 401 on this account — the documented
[#795](https://github.com/ffroliva/gflow-cli/issues/795) condition where `aisandbox-pa`
read endpoints do not answer for a `flow.google.com`-served account. **The balance is
unreadable, so no generation was spent without the account owner's go-ahead.** That is a
named external blocker, not an omission.

### ❌ S3 — labs arm, not reachable from here

`_download_video` uses the labs `media.getMediaUrlRedirect` route. Per AGENTS.md's
host-discipline section, `labs.google/fx/tools/flow` answered **HTTP 308** to
flow.google.com on 6/6 visits across all three local profiles holding a live Flow session
([2026-09-14 survey](superpowers/spikes/2026-09-14-two-domain-protocol-survey.md)). No
account here can take that branch, so it cannot be live-verified in this cycle.

Covered offline: the new landing-host check has its own test asserting that bytes from a
non-Google host are refused and **nothing is written to disk**.

## The retry itself — measured, not assumed

The retry semantics could not be proven by waiting for an organic reset (see blocker), so
they were measured directly against a real TCP RST from a local socket server,
**A/B-controlled** — [spike](superpowers/spikes/2026-09-22-playwright-max-retries-econnreset.md),
`scripts/dev/spike_playwright_max_retries.py`:

| arm | config | outcome | elapsed | server connections |
|---|---|---|---|---|
| A | fail once, `max_retries=2` | **SUCCESS** | 1.03 s | **2** |
| B *(control)* | fail once, `max_retries=0` | **FAILURE** — `read ECONNRESET` | 0.75 s | 1 |
| C | fail always, `max_retries=2`, timeout **5 s** | FAILURE, `Failed after 3 attempt(s)` | **1.57 s** | **3** |

Arm B is the control: the identical fault with the retry disabled kills the call, so arm
A's pass is the retry and not luck. Arm C proves the backoff is charged to the **same**
timeout budget — three attempts inside 1.57 s rather than ~15 s — which is what keeps the
worst case on our 180 s download at ~180 s instead of nine minutes on a lock shared with
every other generation.

## Blocker recorded — the reporter's fault was NOT reproduced here

The 20× field probe was **20/20 clean**. It ran on the **recovery** path, on this network,
against the reporter's **2-in-6 on the generation** path. Different path, different
network. So the reset looks environment-specific — consistent with the VPN / proxy /
AV-inspection class the new remediation names — rather than a universal Flow behaviour.

**Consequence for this release:** the retry's *correctness* rests on the controlled socket
measurement above, and its *necessity* rests on the reporter's traceback plus the GCS
`x-goog-stored-content-length: 3789079` in their incident bundle. Neither rests on a local
repro, and this document does not claim one.

Also outstanding: whether the reporter observed `ECONNABORTED` or listed it as a likely
sibling. Node reports `socket hang up` with `code: 'ECONNRESET'`, so the native retry
covers two of the three conditions named in the issue. **If `ECONNABORTED` was observed,
a thin wrapper around the existing `post_with_retry` is still owed** — asked on #895,
unanswered at tag time.

## Not verified, with reasons

| Item | Why |
|---|---|
| S1 call site against a real generation | Veo balance unreadable (401, #795); credits not spent without the owner's go-ahead |
| S3 labs download | No local account can reach the labs arm (HTTP 308, 6/6) |
| An organic `ECONNRESET` on this network | 0 in 20 attempts; not forceable |
| `ECONNABORTED` coverage | Awaiting the reporter's answer on #895 |
