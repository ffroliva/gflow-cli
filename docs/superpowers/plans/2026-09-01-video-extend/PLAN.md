# Video Extend Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature video-extend` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Let a caller produce a video clip longer than Flow's 8-second ceiling by
chaining Veo *extend* generations — `gflow video extend <media-id> "<prompt>"` as the
primitive, and `--extend N` on the generate commands as the attribute.

**Named consumer:** the Compiled Growth business-parable pipeline
(`compile-growth-monorepo`). Its story clips are timed to narration audio as the
master; when a beat's narration outruns the 8s clip, its own skill documents the
fallbacks as (1) slow the clip down — with a hard "below 0.70 = obvious slow-mo"
floor — or (2) **drop the scene to a Ken-Burns still**. Both are quality losses
happening today. Extend removes the ceiling: 8s + one extend covers a beat up to
~22s at that floor. Its current lever, `--duration 10`, is worse — `VEO_3_1_*`
models expose no duration control at all, and that skill's own pitfall table
records that passing `--duration` makes gflow refuse pre-generation.

**Architecture:** Direct-wire, not UI automation. A pure resolver + frozen request
DTO live beside the scene value objects in `api/scene.py`; `client.extend_video()`
mirrors `client.upsample_image()` (mint → `_post_json` → typed errors). Extend is
scene-anchored, so it rides the existing scene layer rather than a new module.
`ui_automation_video.py` is **not** touched. The one genuinely new component is an
outbound status poller — none exists in `src/` today.

**Predict verdict:** council returned **STOP — 4/10**, gated on two blockers. Both
have since been resolved by measurement, converting this to **GO**:

| Blocker | Resolution |
|---|---|
| A1 — "we have never composed a video-generation body; the route may be reCAPTCHA-walled" | **Measured 200.** `spike_extend_ourstack_verify.py` replayed the captured body through `client._post_json` with our own `TokenMinter` token. One-variable A/B. 10 credits, model key echoed back. |
| DA #2 — "blocked on #142 project-pinning" | **Dissolved.** #142's blocker is that the *UI-automation* transport cannot pin a project. Direct-wire extend passes `clientContext.projectId` explicitly and was verified doing so. #142 retains standalone value but is not a prerequisite. |
| DA #3 — "zero user demand" | **Answered first-party** by the parable pipeline above. |

Seam quality — the actual value proposition — was then verified end-to-end
(2026-09-01): frames either side of a true extend seam are continuous, audio shows
no dropout. Evidence:
[2026-08-31-veo-extend-route-recon.md](../../spikes/2026-08-31-veo-extend-route-recon.md).

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| **HIGH** | Concurrent segment submission collapses the natural 110s pacing floor and reproduces the #241 WAF burst | Serial is a **structural** property — no concurrency param, no `asyncio.gather`. Enforced by test. |
| **HIGH** | Auto-retry on 403 → the "retries into a wall" shape `ACCOUNT_SAFETY.md` disowns | Abort on first 403, preserve partials, surface cooldown guidance. Never re-mint-and-retry. |
| **MED** | All 403s map to `WafRejectionError` (`client.py:2958`); a tier/model 403 would send users into a pointless 30–60 min cooldown | Distinct `ExtendUnavailableError` (exit 35) mirroring `UpscaleUnavailableError`'s precedent |
| **MED** | `sessionId` is unredacted — `redact_metadata` covers `token`/`recaptchatoken` only | Add `sessionid` to the key set before any extend body is logged |
| **MED** | Stale reCAPTCHA token if minted before the prior segment settles (~2 min TTL vs 110s gen) | Mint as the **last** operation before each POST |
| **MED** | Poll interval inherited at 2s → ~825 requests at a WAF-scored host over 15 segments instead of ~75 | Sleep ~90s, then poll every 10s. Never below 5s. |
| **MED** | `chain.py:283` defaults `jitter=0.0`; inheriting it gives machine-perfect 110s cadence | Default extend's jitter to the `GFLOW_CLI_JITTER_RANGE` env value, never 0 |
| **MED** | A single command can spend 150 credits over ~28 min | Pre-flight balance check + credit estimate + confirm gate, all before any client is built |
| **LOW** | Frame-index semantics only half-known (24 frames = 1.0s at 24fps; head-vs-tail origin unproven) | Hardcode the observed `1..24`. Do **not** expose as a flag — MCP schema symmetry would freeze it into two public surfaces |
| **LOW** | Four long-video surfaces confuse users (`scene`, `chain`, `movie`, extend) | A decision line in `USAGE.md`; extend is an operation, "long" is a number |

---

## File structure

### New files
```
src/gflow_cli/api/video_extend.py
  ExtendVideoRequest / FrameRange frozen value objects, to_wire(), and the pure
  resolve_extend_model(listing, service_tier) -> str selector.
tests/api/test_video_extend.py
  Unit tests for the resolver + body builder (no network).
tests/api/test_video_status_poll.py
  Unit tests for the new outbound poller.
```

### Modified files
```
src/gflow_cli/api/routes.py
  EXTEND_VIDEO constant (added). Dead-constant warning already landed.
src/gflow_cli/api/client.py
  + extend_video(), + poll_video_status(), + _initial_data session cache.
src/gflow_cli/errors.py
  + ExtendUnavailableError (exit 35).
src/gflow_cli/data/redaction.py
  + "sessionid" to redact_metadata's key set.
src/gflow_cli/cli_video.py
  + `gflow video extend`; + --extend N on t2v/i2v; jitter default fix.
src/gflow_cli/_cli_helpers.py
  KeyboardInterrupt handler prints credits spent + resume id (fixes chain + movie too).
docs/USAGE.md, docs/CONFIGURATION.md, CHANGELOG.md
```

---

## Task 1 — Test scaffold: model resolver + request body (red)

**What:** Red unit tests for the two pure pieces, from the captured wire data.

**Files:** `tests/api/test_video_extend.py`

**Steps:**
- [ ] Fixture from the real `projectInitialData` model list (8 extend/extension entries, sanitised)
- [ ] No production code in this commit

**Tests created (red):**
- [ ] `resolve_extend_model` picks `veo_3_1_extension_lite` at `SERVICE_TIER_INTERMEDIATE`
- [ ] Skips entries whose `creditMapping[tier].cost == "UNAVAILABLE"` (the `_ultra` trap)
- [ ] Raises when no entry is orderable on the tier — never falls back to a hardcoded key
- [ ] Prefers lowest cost among orderable candidates
- [ ] Filters on `requirements` containing `VIDEO_REQUIREMENT_EXTENSION`
- [ ] `to_wire()` reproduces the captured body byte-for-byte (minus token/uuids)
- [ ] Rejects aspect `1:1` — no SQUARE key exists in either family

---

## Task 2 — `api/video_extend.py`: resolver + DTO (green)

**What:** Make Task 1 green. Pure module, no I/O.

**Files:** `src/gflow_cli/api/video_extend.py`

**Steps:**
- [ ] `FrameRange` frozen dataclass; default `(1, 24)` with a comment recording 24fps × 1.0s
- [ ] `ExtendVideoRequest` frozen dataclass + `to_wire(session_id, token)`
- [ ] `resolve_extend_model(listing, *, service_tier, aspect)` — pure, returns `str`
- [ ] **Do not** widen `VideoModel`; extend keys are runtime strings on a separate DTO
- [ ] `useV2ModelConfig` / `PAYGATE_TIER_ONE` as module-private wire constants, not config

---

## Task 3 — Outbound status poller (the real build cost)

**What:** `client.poll_video_status()` — nothing in `src/` polls outbound today; the
production video poller passively scans Flow's own captured traffic and would time
out at 600s on a direct-wire submit.

**Files:** `src/gflow_cli/api/client.py`, `tests/api/test_video_status_poll.py`

**Steps:**
- [ ] Shape it after `_poll_concat_until_done` (`client.py:1817`) — each poll its own `_post_json`
- [ ] **Never hold a checked-out Page across a poll sleep** — self-deadlocks at `concurrency=1`
- [ ] Initial sleep ~90s, then 10s interval; floor of 5s enforced in code
- [ ] Reuse the existing `parse_video_status` parser (`api/video.py:609`)
- [ ] Raise `TransportTimeoutError` on deadline; per-segment timeout 900s not 600s

**Tests (red first):**
- [ ] Returns on first terminal SUCCESSFUL status
- [ ] Raises on terminal FAILED
- [ ] Page is checked back in before each sleep
- [ ] Interval below 5s is rejected

---

## Task 4 — `client.extend_video()` + error taxonomy

**What:** The transport method. Mirrors `upsample_image` including its 403 disambiguation.

**Files:** `src/gflow_cli/api/client.py`, `src/gflow_cli/errors.py`, `src/gflow_cli/data/redaction.py`

**Steps:**
- [ ] Validate ids **before** minting (fail fast, don't waste a token)
- [ ] Mint as the **last** step before POST (TTL hazard)
- [ ] `ExtendUnavailableError` (exit 35) for a tier/model 403; never auto-retried
- [ ] Add `sessionid` to `redact_metadata`'s key set
- [ ] Cache `projectInitialData` per session (`self._initial_data`) — it cannot change mid-run
- [ ] `structlog`: `extend_segment_started` / `_completed`, and `extend_model_resolved` (key, tier, unit_cost, candidate_count)

---

## Task 5 — `gflow video extend` (the primitive)

**What:** One extend of one media. No chaining yet.

**Files:** `src/gflow_cli/cli_video.py`

**Steps:**
- [ ] `gflow video extend MEDIA_ID PROMPT [--project] [--aspect 9:16|16:9] [-o] [--json]`
- [ ] Creates the scene first when none exists (`client.create_scene`) — Flow's UI does this too
- [ ] Credit estimate + balance check + confirm gate before any client is built; `--yes` / `--dry-run`
- [ ] Reject `1:1` at the Click boundary
- [ ] Sibling of `t2v`/`i2v`/`chain` — **not** under `gflow scene`, which is documented credit-free

---

## Task 6 — Chaining: `--extend N` + resume

**What:** Fold length into the generate commands, as an attribute.

**Files:** `src/gflow_cli/cli_video.py`, `src/gflow_cli/chain.py` (recorder reuse)

**Steps:**
- [ ] `--extend N` on `t2v` / `i2v`; `click.IntRange(1, 30)`
- [ ] Multiple prompts accepted, last one reused when fewer than N
- [ ] Tail-only seeding: segment N+1 seeds from segment N's media
- [ ] **Serial by construction** — no concurrency parameter exists to pass
- [ ] Jitter defaults to `GFLOW_CLI_JITTER_RANGE`, never 0; also fix `chain`'s `0.0` default
- [ ] Record at **submit** time, not download time (a billed segment must survive an interrupt)
- [ ] Abort on 403 → `ChainPartialError` (21) with partials + resume id preserved
- [ ] `BrowserSessionClosedError` (15) also degrades to 21 so paid segments aren't presumed lost
- [ ] Print the resume id at **start**, not only on failure
- [ ] Per-segment progress line (plain `console.print`, never a `rich.progress` live region — Windows console)

---

## Task 7 — Auto-concat on `-o`

**What:** One command → one continuous mp4.

**Files:** `src/gflow_cli/cli_video.py`

**Steps:**
- [ ] When `-o` is given, call the existing credit-free `client.concatenate_scene` after the last segment
- [ ] Verified working: 3 clips → 23.02s / 1280×720 / 24fps h264+aac
- [ ] Fixed `--seed` and `--json` provenance so a shown run is a reproducible run
- [ ] Concat failure must not discard already-paid segments — report the scene id

---

## Task 8 — Ctrl+C handler (root-cause fix, benefits chain + movie too)

**What:** `_cli_helpers.py:393` currently exits 130 with no message and no hook.

**Files:** `src/gflow_cli/_cli_helpers.py`

**Steps:**
- [ ] On interrupt, print credits spent and the `--resume-from` id
- [ ] Fix once in `run_with_handlers` — `chain` and `movie run` have the identical gap today

---

## Task 9 — Docs + CHANGELOG

**Files:** `docs/USAGE.md`, `docs/CONFIGURATION.md`, `CHANGELOG.md`, `KNOWN_ISSUES.md`

**Steps:**
- [ ] **Decision line** disambiguating the four surfaces: clips you already have → `scene` (free); more continuous footage from one clip → `video extend`; N distinct shots with continuity → `video chain`; scripted multi-scene → `movie`
- [ ] Document that extend output is a Scene, and `-o` stitches it
- [ ] Note the 8s input cap and that overshoot is safe (tail-truncation is the consumer's own rule)
- [ ] No new env vars — `GFLOW_CLI_JITTER_RANGE` already exists and applies

---

## Explicitly out of scope

- `--quality lite|fast|max`. The 10-credit vs 100-credit gap is **unmeasured**; ship
  cheapest-available (Flow's own behaviour) and add the flag when someone can say
  what the expensive model delivers.
- Exposing `startFrameIndex`/`endFrameIndex`. Unit known (24fps × 1.0s), origin not.
- A seconds-based `--duration`. Collides with Flow's model-conditional duration row,
  and the consumer's `ceil((need − 8) / 8)` is one line.
- Issue #142 project-pinning — related, valuable, not a prerequisite.
- Promotion assets. Those live in `gflow-cli-remotion`; the library is the library.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` updated
- [ ] Docs updated (`USAGE.md` decision line is mandatory, not optional)
- [ ] **Live verification:** one `--extend 2` run end-to-end, ffprobe the output,
      and eyeball the seam. Offline tests cannot prove Flow still behaves as captured.
- [ ] No `# TODO` in diff without a tracked issue link
