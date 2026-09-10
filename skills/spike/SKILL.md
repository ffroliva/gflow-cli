---
name: spike
version: "1.0"
description: >
  Gather EVIDENCE from the live Flow surface — DOM, network, HAR — before claiming a
  feature is broken, missing, or impossible. The first layer of investigation for any
  "X does not work on Y" question, and the only thing that can settle one.
---

# `spike` — measure the blackbox before you describe it

gflow-cli drives a product it does not own. Every statement about what Flow does is
either **measured** or **guessed**, and a guess written into code or docs becomes a
fact nobody re-checks.

**Load this skill before you write, say, or encode any of these:**

- "X is not supported / not available / not rendered on this host"
- "this surface is labs-only" · "the migrated host cannot do X"
- "the selector is gone" · "Flow removed it"
- a `raise_if_migrated`-style guard, a capability table, or a KNOWN_ISSUES entry
  asserting an absence
- an issue reply telling a user a feature cannot work

## The rule this exists to enforce

```
A SELECTOR THAT DOES NOT MATCH IS EVIDENCE ABOUT THE SELECTOR.
IT IS NEVER EVIDENCE ABOUT THE FEATURE.
```

A wait that times out tells you your anchor missed. It tells you nothing about whether
the thing exists. To claim absence you need a **positive observation of absence** — a
DOM inventory that lists what IS there, a network log that shows what was and was not
called. "It timed out" is not that.

> **Written from a two-line failure that cost a day.** On 2026-09-06 `gflow character
> create` was believed impossible on `flow.google.com`. The entire chain came from one
> 20 s readiness timeout: selector missed → gate timed out → "no prompt textbox" →
> "labs-only surface" → "renders no prompt textbox for it, **ever**", shipped in a code
> comment, the CHANGELOG, a release ledger and a test class NAME. #701 then added a
> guard that aborted *before* probing the DOM — which made the claim **unfalsifiable**,
> because no run could ever look. Reality: the editor was fully present, on the same
> backend; labs renders React + Slate, the migrated host renders Angular + ProseMirror.
> Seven selectors changed and it worked. Thirty minutes of DOM reading would have
> prevented all of it.

**Never put a guard in front of a probe.** A fail-fast that runs before the evidence is
collected deletes the evidence that would correct it. If you must fail fast, fail
*after* looking, and say what you looked at.

## The ladder — cheapest rung that answers the question

1. **Read an existing capture.** `scripts/dev/_spike_out/` and
   `docs/superpowers/spikes/` may already hold the answer. Free.
2. **In-process probe** — `scripts/dev/spike_*.py`. Playwright driving gflow's own
   transport, so you see exactly what gflow sees. Use when you can already reach the
   surface, or want to observe the production path's own traffic. `$0` unless you
   submit.
3. **HAR + DOM harness** — [`scripts/dev/har-spike/`](../../scripts/dev/har-spike/README.md).
   CDP-attached **real Chrome**; a **human** drives the failing action by hand and you
   get the complete HAR. Use when the driver cannot get far enough to observe anything,
   or when an in-process capture is ambiguous. This is the tiebreaker.
4. **Only then** form a conclusion.

Start at 1. Escalate only when the rung below cannot answer it.

## What a spike must capture

Write a new `scripts/dev/spike_<question>.py` when none fits. It should record:

- **Structure, not labels.** Ligature text, ARIA roles, custom-element tag names,
  `href`s. Never anchor on display text — see the locale-invariance rule in AGENTS.md.
  Custom elements (`<flow-slot-chip-button>`) are the best anchors available: they are
  component boundaries, not layout accidents.
- **The carrier.** labs renders ligatures in `<i class="google-symbols">`, the migrated
  host in `<mat-icon>`. Same ligature, different tag — a mismatch here looks exactly
  like a missing feature.
- **Both sides of a transition.** Snapshot before AND after the click. A signal present
  in both proves nothing; one that appears only after is a real settle signal.
- **The network.** Which hosts, which routes, which `batchexecute` rpcids. This is how
  "the backend is shared, only the frontend was rebuilt" gets established instead of
  assumed.
- **Occlusion.** An element can exist, be visible, and still not be clickable. Record
  what `elementFromPoint` returns over it.
- **A control.** If you are testing a fix, run the same probe with the fix stashed. A
  result with no control is a coincidence with formatting.

## Cost discipline

Navigation, DOM reads, `flow.createEntity`, `batchDeleteAssets` and a reCAPTCHA mint are
all **free**. Image generation costs daily **quota**, zero credits. Video costs
**credits**. Say which in the spike's docstring, and delete anything the spike created.

## Profile etiquette — never kill a browser on a profile you do not hold

**`ProfileLockedError` is the lease working, not a stale lock.** It means another
process owns that profile right now, and the error carries the holder's evidence. Read
it. Then wait, or spike on a different profile. Both are cheap; neither can corrupt
anything.

**Never kill Chrome processes to clear the way.** Two Chrome instances on one
`user_data_dir` is the corruption the lease exists to prevent, and a process list cannot
tell you which browser belongs to whom — so "these look like my orphans" is a guess made
against a fact the lease already gave you.

> **Written from the incident it prevents.** On 2026-09-07 two sessions worked this repo
> at once. One ran the e2e suite on `denon82` and held its lease. The other hit
> `ProfileLockedError`, read the resulting Chrome processes as orphans of its own spike,
> and killed eighteen of them in two batches; nine belonged to the running suite. It then
> diagnosed the cause as "spike scripts do not take the lease" — but its own spike went
> through `FlowApiClient`, which acquires at `api/client.py` before Chrome starts, so it
> *had* held the lease. **The tool was correct and was overruled by a process list.**
> A real defect did surface underneath — three scripts launched Chrome outside any lease,
> fixed with a guard test in #717 — but it was not what caused the incident, and fixing it
> would not have prevented it. This rule would have.

If you write a spike that launches Chrome itself rather than through `FlowApiClient`,
wrap it: `async with ProfileLease(profile_dir), async_playwright() as pw:`. Chrome must
never start on a profile this process does not own.

## Pre-register the reading before you run

Write down what each possible outcome will mean **before** the spike executes — in the
script's own docstring, where it is timestamped by the commit. Then the result cannot be
reinterpreted to suit whatever change the spike was gating.

| Outcome | Reading |
|---|---|
| N/N | stable |
| mixed | it flaps |
| 0/N | **does not reproduce; settles nothing** |

That last row is the one worth pre-writing, because it is the one you will be tempted to
spin. **A condition that has stopped reproducing has not been shown to be transient.** It
is equally consistent with some state having changed underneath it, and a spike that
cannot distinguish those has produced one honest result: *unmeasured*.

Unmeasured is a real finding. Report it as the answer, not as a failed run — and say what
would settle it, so the next person who sees the condition live knows what to capture.

> **Worked example:** [`2026-09-10-about-redirect-stability.md`](../../docs/superpowers/spikes/2026-09-10-about-redirect-stability.md)
> — asked whether Flow's `/about` redirect is transient, got 0/5, and shipped
> "unmeasured" rather than letting a disappearance argue for a retry flag.

## Output

- Evidence → `scripts/dev/_spike_out/` (**gitignored**; captures carry Bearer tokens,
  cookies and prompts, and `*.har` is gitignored repo-wide). Never paste a raw capture
  into an issue — `scripts/dev/har-spike/extract_har_summary.py` produces the redacted
  summary that is safe to share.
- Findings → `docs/superpowers/spikes/<date>-<slug>.md`. The finding is durable; the
  bytes that produced it are not.
- The spike script itself → committed. A question worth asking once gets asked again.

## When you are done

State the verdict as what was **observed**, with the file and line of the evidence —
not as what you concluded. Then say plainly what you did NOT measure. An unmeasured
gap named is a lead; an unmeasured gap implied is the next day lost.

Feeds: [`issue-assessment`](../issue-assessment/SKILL.md) (triage needs evidence, not a
hypothesis), [`predict`](../predict/SKILL.md) (persona claims about a live surface must
cite a capture), [`live-verify`](../live-verify/SKILL.md) (proves the fix; this proves
the diagnosis), and — for a bug — [`scenario`](../scenario/SKILL.md), where what you
observed becomes the `Given`/`When`/`Then` of a test.

## A spike is step 0, never the deliverable

A spike answers a question. It does not close an issue, and its script is not the
regression test — nothing re-runs it, so nothing notices when Flow changes again.

**The observation you just made is the body of a scenario.** What you drove is the
`Given`, what you triggered is the `When`, what the DOM or the wire actually returned
is the `Then`. Carry it into [`scenario`](../scenario/SKILL.md) and, if it can only
happen in a browser, into `tests/e2e/test_<slug>_bdd.py` — the route is
[`issue-resolve`](../issue-resolve/SKILL.md) § The Bug Lane.

A spike whose finding never became a test has bought you one answer, once, at full
price.
