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
the diagnosis).
