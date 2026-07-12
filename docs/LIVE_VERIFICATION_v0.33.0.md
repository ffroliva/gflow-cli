# Live verification — v0.33.0 (2026-07-12)

Both user-facing changes exercised against live Flow on the merged `develop`
tip (`669884c`), profile `denon82`, before tagging. Evidence follows the
5-layer ledger (file count · magic bytes · dimensions/shape · structlog
invariants · user-confirmable artifact).

## 1. #287 follow-up (PR #296) — picker reaches assets deep in a crowded project

**Command (the original repro, verbatim surface):**

```
gflow video i2v 2f72aa2a-5081-4df8-a3b1-64b6a859274c "slow dolly-in, soft light shifting across the scene" \
  --project f6caf027-ad68-49e3-aac9-ee32f1582bf3 --profile denon82 --out-dir <scratch>/330-e2e-i2v
```

- **Exit code:** `0` (v0.32.1 failed this exact command with `TransportTimeoutError`, exit 9).
- **File count:** 1 mp4 produced.
- **Magic bytes:** `ftyp` at offset 4 — real ISO-BMFF video.
- **Size:** 3.0 MB.
- **Structlog invariants:** `picker_project_name_resolved` → `picker_project_already_active`
  (alignment machinery confirms the picker was on the target project; no switch needed)
  → `frame_ref_attached` → `video_saved`. Zero upload events — the in-project asset
  was referenced, not re-uploaded.
- **Artifact:** `497d687d-86cc-4bcc-aeab-878a1d1c4fae.mp4` (scratch dir; user-inspectable).

## 2. #241 (PR #297) — anti-bot jitter: configurable, small default, t2i path paced

**Round 1 (pre-merge, flag path):** `gflow image t2i "<p1>" "<p2>" --jitter 4-6` →
`batch_jitter_sleep {"seconds": 4.98, "index": 1}` — exactly one pause, between the
two submissions (never before the first), within the requested 4–6 s. Prompt 1
saved a real jpg.

**Round 2 (post-merge, default path):** same 2-prompt t2i with NO `--jitter` flag →
`batch_jitter_sleep {"seconds": 1.47, "index": 1}` — within the new 0.5–1.5 s
default, fired once. This is the release-defining behavior: the previously
unpaced t2i multi-prompt path now paces, at the minimal default.

**Environment finding (pre-existing, NOT a regression):** both round-2 generations
failed post-submission with the #281 `MediaAttributionError` (exit 26), and
`prefer_classic.cohort_natively_agentic` fired — the `denon82` cohort now mounts
Flow's agentic UI unconditionally and `GFLOW_CLI_PREFER_CLASSIC=1` cannot exit it.
The identical failure mode exists on released v0.32.1 (same cohort); the jitter
feature under verification is upstream of generation and verified by the sleep
event + the round-1 saved jpg. Tracked in KNOWN_ISSUES (agentic attribution) —
candidate for the next issue wave.

## Not verified this cycle

- `GFLOW_CLI_JITTER_RANGE` via a real `.env` file on a live run (covered by unit
  tests through pydantic-settings; env-var form is equivalent at the Settings layer).
- `gflow run --config` image-batch pacing (same `run_sequential_batch` code path
  as t2i, verified live above; `gflow run` adds only config parsing on top).
