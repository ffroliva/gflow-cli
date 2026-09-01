# Live verification — v0.63.0

Every claim below was **observed**. Claims that could not be observed are stated
as such in §5, not omitted.

## Environment

| | |
|---|---|
| Date | 2026-09-01 |
| Profile | `ffroliva` (locale `en`) |
| Code under test | `feat/video-extend` @ the branch that became `5bd1476` on `develop` |
| Surface | `gflow video extend` — the one new user-facing command in this release |
| Source clip | `b9458021-fc2d-4d95-ab53-cf844c6f1079` (landscape) |
| Veo credits spent | **20** (2 segments × 10). Model resolution, the capability listing read, scene creation and the final concat were all free. |

## 1. The command chains extends and bills what it said it would

```
gflow video extend b9458021-fc2d-4d95-ab53-cf844c6f1079 "..." "..." -n 2 --aspect 16:9 -o live_extend.mp4
```

The pre-flight plan, printed before anything was submitted:

```
model       : veo_3_1_extension_lite (20 credits total, balance 875)
```

and the resolver's own record of how it got there:

```json
{"model_key": "veo_3_1_extension_lite", "service_tier": "SERVICE_TIER_INTERMEDIATE",
 "unit_cost": 10, "candidate_count": 99, "event": "extend_model_resolved",
 "cli_command": "video extend"}
```

`candidate_count: 99` is the load-bearing number: the key was **resolved from the
live capability listing**, not hardcoded. The account's own tier
(`SERVICE_TIER_INTERMEDIATE`) selected an orderable key, which is what prevents
the tier-403 rather than classifying it afterwards.

Final line: `Extended — 2/2 segment(s), 20 credits`. Plan cost and actual spend
agree.

## 2. Chaining is tail-only — the property the whole feature rests on

This is the claim that separates `extend` from `chain`, and it is the one a mock
cannot establish. Both submissions, verbatim:

```json
{"segment": 1, "of": 2, "media_id": "0c9364f3-9c2e-4369-8e4a-28eace534ce3",
 "source_media_id": "b9458021-fc2d-4d95-ab53-cf844c6f1079",
 "model_key": "veo_3_1_extension_lite"}

{"segment": 2, "of": 2, "media_id": "648f9291-bae5-4a03-bbe1-82a113e30881",
 "source_media_id": "0c9364f3-9c2e-4369-8e4a-28eace534ce3",
 "model_key": "veo_3_1_extension_lite"}
```

Segment 2's `source_media_id` **is segment 1's `media_id`**. The chain advances
from the tail it just produced, not from the original clip. Had the orchestrator
re-seeded from the source, both lines would name `b9458021…` and the output
would have been two divergent 8-second continuations of the same moment rather
than one continuous 23-second shot.

## 3. Ledger

| Layer | Observed |
|---|---|
| File count | 1 rendered mp4 from 3 scene clips (original + 2 extensions) |
| Magic bytes | h264 + aac in an mp4 container, read back by `ffprobe` |
| Dimensions / shape | **23.02s · 1280×720 · 24fps** — landscape preserved end to end |
| Structlog invariants | `extend_model_resolved` (×2, once per segment) → `extend_segment_started` segment 1 → segment 2 with the chained `source_media_id` → completion at `2/2`, `credits_spent=20` |
| User-confirmable artifact | `live_extend.mp4` — a single continuous clip that plays past Flow's 8-second ceiling, which is the entire user-facing promise of the release |

## 4. The verification earned its keep — it found a defect the suite could not

The offline suite passes on this feature and is structurally incapable of seeing
this: **an extend segment carries 7.000000 seconds of content, not the 8 Flow
advertises and bills.**

Per-second mean volume across the 3-clip render:

```
 7s -28.8   8s -26.4                <- original -> extension seam: continuous
14s -29.1  15s -75.1  16s -33.9     <- extension -> extension seam: 1s dropout
```

Finer slices place the silence at 15.00–15.99s exactly, over a **frozen video
frame**. Reproduced independently on a second render made days earlier from
different prompts (14s −22.4, **15s −70.1**, 16s −23.9), so it is systematic.

The cause is a metadata disagreement, not a rendering bug: the capability listing
advertises `videoLengthSeconds: 8` and `getSceneWorkflows` reports
`total_duration=8.0` for a clip whose streams `ffprobe` measures at 7.000000s.
`ConcatInput` passes those values through verbatim, so Flow's server-side concat
stretches a 7s clip into an 8s slot by holding the last frame and muting. An
N-segment chain has N−1 of these; the final segment escapes only because the
render ends before its padding (23.02s for 3 clips, not 24s).

**Filed, not guessed at.** It is in [KNOWN_ISSUES.md](../KNOWN_ISSUES.md) with the
three questions that must be answered before any clamp is written — whether 7.0s
holds across models, aspects and tiers; whether Flow's own UI pads identically;
and whether any API field reports true duration. `USAGE.md`, the CLI `--help` and
`CHANGELOG.md` all say ~7s rather than 8s as a result. Three of the four deferred
code-review findings block on this same question, deliberately.

## 5. Recorded as NOT verified

- **Portrait (`--aspect 9:16`).** The live run was 16:9. `veo_3_1_extension_lite`
  advertises `LANDSCAPE,PORTRAIT` at the same 10 credits, and the resolver reads
  the aspect from the listing, but no portrait extend has been submitted.
- **`--aspect` against a mismatched source.** Not validated by the code, and hit
  during this very run (landscape source, 9:16 default). Deferred deliberately —
  the fix needs the source clip's real aspect, which needs either a media probe
  or a listing field not yet confirmed to exist. Guessing there is precisely how
  the 7s defect reached users upstream.
- **Chains longer than 2 segments.** `-n` accepts up to 30; only `-n 2` was run.
  Pacing, the per-segment interrupt context and the credit pre-flight are all
  exercised at n=2, but a long chain's cumulative per-profile load is not.
- **`--resume-from` against a live partial scene.** The resume arithmetic
  (append at `last position + 1`, seed from the scene's real tail) is covered
  offline against a fake scene; no interrupted live run has been resumed.
- **The insufficient-credits refusal, live.** Verified offline only — the account
  had enough balance, so the pre-flight never fired against Flow.
- **Ctrl+C mid-run, live.** The per-segment interrupt context is unit-tested; no
  live run was interrupted.
- **`OperationKind.EXTEND` rows.** Asset rows land; the operation row is not
  written yet, so `gflow data` shows the media but not the operation. Deferred
  with reasons.
