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
| `gflow character create` on a moved account | exit **1**, bare `RuntimeError: Character editor not ready…` after a 20 s wait | **exit 0 — the character is created.** See below; the exit-36 behaviour this row originally recorded was itself the defect |
| `gflow image t2i` on a moved account | — | exit **36** (confirmed on `ffroliva` and `compiledgrowth.official`; the constraint, not a regression) |
| `gflow video r2v --model veo-lite-lp` | — | exit **11**, names the four tiers this account is actually offered, **$0** |
| `gflow video t2v --duration 8` | — | exit **11**, "renders no duration control … only Omni 1.1 Flash does", **$0** (#451/#288/#630 cohort) |

## `gflow character create` on the migrated host — verified, and the premise corrected

This release originally recorded "exit 36, immediate" as the *improvement* for
`character create` on a moved account, and recorded CHARACTER entities as unexercisable.
Both were wrong, from the same root: a 20 s readiness timeout was read as proof the
feature does not exist on `flow.google.com`, and #701 then added a `raise_if_migrated`
guard that aborted **before** probing the DOM — which made the claim unfalsifiable,
because no run could look.

Measured instead (`scripts/dev/spike_migrated_character_*.py`): the editor is fully
present there, on the **same** labs tRPC + aisandbox backend. Only the view layer
differs — labs is React + Slate, flow.google.com is Angular + ProseMirror — so seven
selectors missed. `[data-slate-editor]` matched 0; `.ProseMirror[contenteditable]`
matched 1, unoccluded.

**Verified live on `ci-probe` (migrated), read back independently with
`gflow character show`:**

| | |
|---|---|
| Name | `Kael Ridge Full` — patched, not "Untitled Character" |
| Personality | `seco, direto — fala pouco; criado no interior` (UTF-8 round-tripped) |
| Voice | `Algenib` |
| Portrait | workflow `5698df21…`, media `ca5732c9…`, 633 976 B |
| Body | workflow `de846b9c…`, media `247eec3b…`, 449 397 B |
| Distinct images | md5 `60975a89…` vs `60a27f39…` |

Exit 0, both slots on disk. The portrait generation answers on `batchexecute` rpcid
`ogiZ0b`, not the labs `flowMedia:batchGenerateImages` the listener waited 180 s for —
so the client was reporting failure over work that had already succeeded.

**`--model` determinism**, four consecutive runs alternating tiers, correct every time.
The picker previously proceeded on whatever tier the editor showed, so `--model nano2`
silently generated on Nano Banana Pro. It now verifies by re-reading the chip and raises
rather than generating on a tier the user did not ask for; refusing is free because the
picker runs before submit.

**Orphan rollback**, A/B controlled on the live account: **1 → 2** entities on a failed
create without the fix, **2 → 2** with it.

**NOT verified:** the labs.google path for any of the seven anchors changed.
**Blocker:** every account available on this machine has been migrated by Google, so no
labs.google Flow session exists to drive; the labs branches are covered by unit tests only.
Tracked on [#639](https://github.com/ffroliva/gflow-cli/issues/639) alongside the rest of the
two-frontend matrix. Named as a blocker, not claimed. ([#703](https://github.com/ffroliva/gflow-cli/pull/703))

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
  **New evidence, 2026-09-06:** `flow.google.com` **can** mint a reCAPTCHA Enterprise
  token. On `/project/<id>` the enterprise script and site key
  (`6LdsFiUsAAAA…`) are present and a mint returns a 2404-char token; on the root grid
  there is no script at all. The mint runs on the pooled bootstrap page, which after the
  handoff IS the root grid — which is why the reporter's bundle showed `route: "/"`.
  So "any mint failure on a migrated page yields exit 36" is a refusal of the host, not a
  fix of the cause. The re-runnable probe and its write-up land with the spike-skill PR
  and are deliberately not linked from here while they sit on another branch: a ledger
  must not carry a reference its own branch cannot resolve, and `check_doc_links.py`
  does not cover this file, so nothing would have caught it.
- **`gflow image`, `scene`, `movie`, `extend`** were not exercised on a generation path.
  **Blocker:** all four exit 36 on the migrated host, and no unmoved account exists here —
  every profile on this machine has been moved, so the labs path cannot be driven at all.
  `gflow image t2i` was re-measured for this release and still exits 36
  ([#639](https://github.com/ffroliva/gflow-cli/issues/639) tracks the migrated-frontend
  feature matrix). The reason is now known and recorded: the migrated project composer has
  no image-generation mode — its add menu is a media library (Scenes / Images / Videos /
  Upload media) — so this is a porting gap, not a regression.
- **A Google sign-in interstitial's fidelity score** (whether it renders buttons with no
  ligatures and would now read as "rendered" — #699's open question) has no bundle
  evidence either way.
