# Live verification — v0.70.0 (pre-release evidence, 2026-09-06)

Every run below was executed against real Google Flow from the repo working tree at
`develop`, not from an installed build. All accounts available to this maintainer
(`denon82`, `ffroliva`, `compiledgrowth.official`) are on the **migrated
`flow.google.com` host**, which shapes what could and could not be exercised — see
*Not verified* at the end, which is part of this ledger rather than an omission.

Five layers per run: file count · magic bytes · dimensions/shape · structlog invariants ·
a user-confirmable artefact.

## Runs

| # | Profile (state) | Entrypoint | Command | Exit | Output |
|---|---|---|---|---|---|
| 1 | `denon82`, moved | CLI | `gflow video r2v --ref Stacky_canonical_face.jpg --ref Stacky_canonical_body.jpg --project 7fa97443… --model veo-lite --aspect 16:9` | **0** | `f7f7ce79-….mp4`, `ftypisom`, **1,125,515 B**, 8.000 s, 1280×720 |
| 2 | `denon82`, moved | CLI | `gflow video t2v … --model veo-quality --aspect 16:9` (beat 1, "The Ridge") | **0** | `s1_hero.mp4`, 8.000 s, 1280×720 |
| 3 | `denon82`, moved | CLI | `gflow video r2v --ref ridge_char_kael.png --model veo-lite` (beat 2) | **0** | `b2_wide.mp4`, 8.000 s, 1280×720 |
| 4 | `denon82`, moved | CLI | `gflow video t2v … --model veo-quality` (beat 3) | **0** | `s2_naia.mp4`, 8.000 s, 1280×720 |
| 5 | `denon82`, moved | CLI | `gflow video r2v --ref ridge_char_naia.png --model veo-lite` (beat 4) | **0** | `b4_reveal.mp4`, 8.000 s, 1280×720 |
| 6 | `denon82`, moved | `pytest -m e2e` | `tests/e2e/test_auth_verification_e2e.py` | **PASS** | 3 passed |
| 7 | `denon82`, moved | `pytest -m e2e` | `tests/e2e/test_migrated_host_e2e.py -k "image_on_a_moved_account or kill_switch or serves_this_account"` | **PASS** | 3 passed |
| 8 | `denon82`, moved | `pytest -m e2e` | `tests/e2e/test_transports_e2e.py -k health_check` | **PASS** | 2 passed |
| 9 | `denon82`, moved | `pytest -m e2e` | `tests/e2e/test_incident_quality_e2e.py` | **PASS** | 1 passed (was failing: `fidelity 0.667`) |

### Exit-code corrections, each verified on the real CLI

| Command | Before | After |
|---|---|---|
| `gflow video r2v … -o <existing directory>` | exit **1** "Unexpected error" after ~2 min, **one clip billed and orphaned** | exit **2** in **0.8 s**, `Invalid value for '-o' / '--output': File 'tmp/promo' is a directory`, no browser launched |
| `gflow character create` on a moved account | exit **1**, bare `RuntimeError: Character editor not ready…` after a 20 s wait | exit **36**, `Flow served the migrated flow.google.com frontend…`, immediate |
| `gflow image t2i` on a moved account | — | exit **36** (confirmed on `ffroliva` and `compiledgrowth.official`; the constraint, not a regression) |
| `gflow video r2v --model veo-lite-lp` | — | exit **11**, names the four tiers this account is actually offered, **$0** |
| `gflow video t2v --duration 8` | — | exit **11**, "renders no duration control … only Omni 1.1 Flash does", **$0** (#451/#288/#630 cohort) |

## The assembled artefact

`the-ridge.mp4` — **19.000 s**, **1280×576**, 24 fps, H.264 + AAC. Four beats trimmed and
joined with the ffmpeg **concat filter** (not the demuxer — `skills/video-production`
records the demuxer producing 4 % lip drift on mixed frame rates).

`skills/video-production/clip_qa.py` on the cut:

```
ok  the-ridge  onset=0.00s  face=1.93/0.99  frame=2.23  a/v=+0.000s
```

Per clip: `ka01` fluid, `wd02` fluid, `rv04` fluid. `na03` reported
`DRIFT sync=+0.500s r=0.7` — **verdict void**: `clip_qa --selftest` on that same clip
failed to recover a **known injected 0.2 s** shift (`saw -0.500s`), which disqualifies the
reading. The clip carries no speech, so the face-motion↔audio correlation has nothing to
lock onto. Frames reviewed by eye, per the skill's standing rule that no metric separates
good motion from bad.

**User-confirmable:** both characters are recognisably the same people in the wide
two-shot (beat 2) as in their own close-ups (beats 1 and 3), from **one face plate per
shot plus the character canon repeated verbatim**. Contact sheet:
`the-ridge-evidence.png`. Full production record with every prompt, model, plate and
metric: `the-ridge-review.html`.

## What this release's headline feature actually did

Reference-to-video on the migrated host (#683) was exercised three times (runs 1, 3, 5)
and bound its references every time. Run 1 is the cleanest single proof: two hand-drawn
reference images went in, and the clip came back carrying the same marker strokes, the
same face and the same paper texture — not a generic figure.

## Not verified, and why

- **#692's original failure could not be reproduced.** On this maintainer's migrated
  account, *unfixed* v0.69.0 already returned exit 36 in both A/B arms. The reporter's own
  re-run also returned exit 36, but on v0.69.0 — a build without #694 — so it demonstrates
  the failure is **intermittent**, not that the fix works. #694 plus #701's bounded re-poll
  close the window *by construction*: any mint failure on a page that reads as migrated
  now yields exit 36, and the read is no longer a single instant. The issue stays open
  until someone confirms on a build that contains it.
- **Flow CHARACTER entities were not exercised**, because `character create` cannot run on
  any account available here. The cast in "The Ridge" therefore uses the skill's sanctioned
  no-entity path.
- **`gflow image`, `scene`, `movie`, `extend`** were not exercised on a generation path:
  all exit 36 on the migrated host and no unmoved account exists here.
- **A Google sign-in interstitial's fidelity score** (whether it renders buttons with no
  ligatures and would now read as "rendered" — #699's open question) has no bundle
  evidence either way.
