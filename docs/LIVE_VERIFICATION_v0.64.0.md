# Live verification — v0.64.0

> Evidence for [#626](https://github.com/ffroliva/gflow-cli/issues/626): lifting the
> `omni-flash` i2v END-frame guard. The paid surface under test is
> `gflow video i2v START PROMPT --model omni-flash --end-frame END`, which was rejected
> pre-submit with exit 17 before this release.

## Environment

- Date: 2026-09-02
- gflow-cli version: 0.63.0 → 0.64.0 (branch `feature/omni-flash-end-frame-626`)
- Python: 3.13 local; CI matrix 3.11 / 3.12 / 3.13
- Chrome: headed, real-Chrome strategy (`--browser chrome` profile)
- OS: Windows 11
- Profiles: `ffroliva`, `denon82` (two distinct Google accounts)

---

## Part A — Credit-free wire evidence (route-abort)

`scripts/dev/capture_i2v_intercept_submit.py` binds Start + End through the production
helpers, installs `page.route(..., abort)` on the submit XHR, clicks submit, and inspects the
request the browser *tried* to send. The request never reaches Veo, so **zero credits**.

| # | Profile | Account | Captured route | startImage | endImage |
|---|---|---|---|---|---|
| A1 | `ffroliva` | A | `video:batchAsyncGenerateVideoStartAndEndImage` | `35104644` | `1160c1c5` |
| A2 | `denon82` | B | `video:batchAsyncGenerateVideoStartAndEndImage` | `3dbe6f2f` | `d8fa8761` |

Two distinct Google accounts produced the identical route with both images non-null, which
rules out a single-account artifact and a visibly staged rollout. This is the same probe and
methodology that re-enabled omni-flash START-frame i2v on 2026-08-03.

Artifacts: `tmp/i2v_626/evidence.json`, `tmp/i2v_626_denon82/evidence.json`.

---

## Part B — Paid live generations

### B1 — omni-flash + start + end frame, `--duration 4` — ✅ VERIFIED

```bash
gflow video i2v <start>.jpg "she rises from the chair and turns toward the window, \
    warm afternoon light" --model omni-flash --end-frame <end>.jpg \
    --duration 4 --aspect 9:16 --profile ffroliva
```

Five-layer verification ledger:

| Layer | Evidence |
|---|---|
| 1 · Exit code | `0` |
| 2 · Wire route | `POST .../video:batchAsyncGenerateVideoStartAndEndImage` → HTTP 200, `startImage=a49791a4`, `endImage=06373fa6`, `referenceCount=0` |
| 3 · Flow status | `MEDIA_GENERATION_STATUS_SUCCESSFUL` (`poll_terminal`), media `2dba1401-f2e0-48da-917c-5189b1aa42b2` |
| 4 · Artifact | 2 465 096 B mp4; `ffprobe`: h264 + aac, 720×1280 (9:16 portrait), **duration 4.01 s** — matches `--duration 4` |
| 5 · Semantic | **First frame of the output is the start image; last frame is the END image.** Extracted with `ffmpeg -sseof -0.1`; visually confirmed as two different subjects. This is the layer that proves the end frame was *used*, not merely accepted. |

Layer 5 is the one that matters for this change. A run that bound the end frame but ignored it
would still pass layers 1–4.

The new post-submit route backstop (`_assert_i2v_route`) ran on this generation and correctly
did **not** fire — the route matched the frames the request carried.

### B2 — omni-flash + start + end frame, `--duration 10` — ⚠️ SUBMIT VERIFIED, RENDER UNCONFIRMED

Same command with `--duration 10`. This is the largest-payload combination and had never been
exercised end-to-end on this model.

| Layer | Evidence |
|---|---|
| 1 · Duration selection | `duration_tab` matched `[role='tab']:text-is('10s')`; `duration_set seconds=10` |
| 2 · Wire route | `POST .../video:batchAsyncGenerateVideoStartAndEndImage` → HTTP 200, `startImage=76f57d19`, `endImage=eb7d1627` |
| 3 · Flow status | ❌ **not obtained** — `batchCheckAsyncVideoGenerationStatus` returned HTTP 401 mid-poll (`AuthExpiredError`) |
| 4–5 | not reached |

A single retry failed earlier still, at `project.create` with HTTP 401, before any submit — so
no second credit was spent. `gflow auth status` verified the Flow session as valid
immediately before and after both attempts.

**This is [#561](https://github.com/ffroliva/gflow-cli/issues/561)**, a pre-existing defect
(browser-context 401 while the on-disk Flow session is valid), not a regression from this
change. Verification was stopped rather than spending further credits chasing an environmental
fault.

**Honest status:** `--duration 10` + end frame is verified as far as *submit* — Flow accepted
the 10-second first+last request on the correct route with both images bound. Whether the
render completes is **unconfirmed**. Per the project's rule, that is LIKELY, not CONFIRMED.
It carries low residual risk: B1 proved the render path for this model/route, and duration
selection is an independent, already-verified control (`supports_duration`, #451/#288).

---

## Offline gates (same tree)

| Gate | Result |
|---|---|
| `check_repo_hygiene.py` | 858 files, no violations |
| `check_doc_links.py` | all links resolved across 29 files |
| `check_website_docs_pii.py` | no private identifiers, 25 files |
| `generate_website_docs.py --check` | mirror in sync (20 files), nav complete |
| `ruff check src tests` | all checks passed |
| `ruff format --check src tests` | 414 files already formatted |
| `pyright src` | 78 errors — **byte-identical count on clean `develop`**; local stub skew, zero new |
| `pytest` (api/cli/mcp/chain/errors) | 2065 passed, 3 skipped |

---

## Verdict

The feature is **live-verified** for the case the issue reported and for the case a user is
most likely to hit. The 10-second variant is submit-verified with its render unconfirmed for
an unrelated, already-tracked auth defect — recorded here rather than papered over.
