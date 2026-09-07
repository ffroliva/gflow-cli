# Acceptance-gate fixtures

Known-answer clip strips for testing whether an acceptance step actually catches a defect,
rather than whether it can recite the rule.

Each fixture is a **matched pair**: two 1 fps contact sheets of the *same beat*, shot with
the same reference plate, the same model and the same prompt shape, differing only in the
defect. Everything a judge might latch onto — cast, wardrobe, location, grade, framing,
duration — is held constant, so a correct verdict cannot come from a general impression of
quality.

**Both arms are required.** A benchmark with only the bad arm is won by rejecting
everything.

## `temporal-01` — object scale drift + materialisation

| arm | file | correct verdict |
|---|---|---|
| bad | `temporal-01-reject_1fps.jpg` | **REJECT** |
| good | `temporal-01-accept_1fps.jpg` | **ACCEPT** |

Cell k is second k, read row-major.

**The defect, for scoring** (do not paste this at the judge): in the reject arm the
background monolith starts as a small distant stub, a **detached fragment appears near
frame-top at ~2–3 s**, and the object then grows to several times its original size and
looms behind the actors by the end. The camera does not move. In the accept arm the
monolith holds one size and position throughout.

**Scoring.** Two things, not one:

1. the verdict (REJECT / ACCEPT) — a judge that rejects both scores zero;
2. **the citation** — the reject arm must name the second, or the cell, where the defect is
   visible. A correct verdict with no citation is an unfalsifiable guess, and this repo has
   a standing rule against those.

**Provenance.** Both arms are real Veo output from a production run on 2026-09-07, not
synthetic. The defect was caused by a prompt asking for *"the first sunlight strikes the top
of the monolith and travels slowly down its face"* alongside *"very slow rise"* on the
camera — a lighting instruction phrased as downward travel, plus a rising camera. The
accept arm is the re-shoot with that ambiguity removed.

**Why this pair is worth keeping.** The bad arm passed the letterbox check, both
stream-length checks, the audio check and every motion gate in `clip_qa.py` — and was
accepted twice by an agent: once off a single frame at ~3.3 s, by which point the object
was already large and stable, and again off a four-frame filmstrip on a review page where
the artifact is plainly visible. It was caught by the account owner watching the clip. So
this is a case where every mechanical gate and two rounds of agent inspection said pass.

The verdict line for the reject arm, from the run that shipped this fixture:

```
fluid rb04 onset=0.00s face=1.9/0.63 frame=1.88 sync=+0.000s r=-0.11 a/v=+0.000s
```

`fluid`, and a frame-motion of **1.88** — the second highest of the five beats in that
production. The growing object did not merely evade the gate, it **improved the score**,
which is the documented reason a metric can never own this decision.

## Not runnable by the current harness — a real blocker, not a caveat

`scripts/dev/skillopt/harness.py` is **text-only**: it drives OpenAI-compatible chat
endpoints and sends `question` as a string. All 19 entries in `../tasks.json` are text Q&A.

There is deliberately **no** `tasks.json` entry for these fixtures yet. Adding one would
put a task in the scored set that silently cannot see its own input, and a benchmark that
scores something other than what it claims to measure is worse than no benchmark.

Making it runnable needs multimodal message parts in the harness (image content blocks, a
per-task image list, and a way to skip cleanly on endpoints that refuse images). Until
then these fixtures are usable **by hand**: open a strip, apply the seven temporal checks in
[`../SKILL.md`](../SKILL.md) § "The temporal pass", and see whether the defect is named with
its second.

## Adding a fixture

One pair covers **one** artifact class. The taxonomy in
[`../failure-modes.md`](../failure-modes.md) § "The clip is wrong across TIME" lists six
more with no fixture at all: identity swap, wardrobe change, background morph, changing
person/limb count, and axis break.

Prefer a **real failure with its own re-shoot** over an injected one. A synthetic artifact
tests whether a judge can spot something painted on; a real one tests whether it can spot
what this engine actually does wrong.
