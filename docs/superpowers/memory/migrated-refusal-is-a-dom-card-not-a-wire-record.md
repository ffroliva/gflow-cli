---
name: migrated-refusal-is-a-dom-card-not-a-wire-record
description: On flow.google.com a policy refusal renders as a media-grid card; the batchexecute record is a bare status-4 or never parses, so gflow reports a retryable timeout for something that will never succeed.
---

On `flow.google.com` a content/abuse refusal is delivered to the **page**, not to
the wire. The grid renders a card —

> Failed · We noticed some unusual activity… · You have not been charged for this
> generation

— while the batchexecute side gives gflow one of three useless shapes: silence
(the submit reply never parses), a bare `status 4` record with no reason field, or
a parsed-but-empty reply.

So the driver names it wrong, in a way that matters:

| What Flow means | What gflow says today | Consequence |
|---|---|---|
| refused, never retry | `TransportTimeoutError` (exit 9, **retryable**) | the CLI, MCP and worker envelopes all invite a retry of a refusal |
| refused, rewrite the prompt | `migrated host reported status 4` | true and unactionable |
| refused | `WireFormatError` (exit 7) | the remediation string tells the user to file a frontend bug |

`ContentPolicyError` (exit 5, remediation *"rewrite and retry"*) exists and is
wired through `--json`, MCP and the worker — but its raise sites are all on the
**old REST path** (`errors.py`, docstring: "two known raise sites"). On the
migrated host a policy refusal is therefore **unreportable**, and `retryable` is
`true` when it should be `false` (`ContentPolicyError` is absent from
`RETRYABLE_ERRORS`).

**Why it is worth knowing even before it is fixed:** a refusal presenting as a
transport fault is what sends a session spiking selectors and filing frontend bugs
for Google simply saying no. Recognise the shape first — see
[[feedback-intermediate-signal-is-not-terminal]]: a null or unparsed submit reply
is an intermediate signal, and the outcome lives in the project, not in the
initiating call.

**How to apply:** reading the card is the right instinct; how it is read decides
whether the fix is an improvement or a regression. Anchor on the failed tile's
**structure** and read `inner_text()` of the node you already matched — never
scan the page body for English phrases, and never treat `"not been charged"` as
refusal evidence (it is the credit-free abort signature). The full set of traps,
with the live incident behind each, is
[[content-policy-text-scan-false-positives-on-page-chrome]].

Before implementing, spike the failed-card DOM: as of 2026-09-18 nothing in
`docs/superpowers/spikes/` describes it, so any structural anchor is currently a
guess — see [[migrated-host-driver-wire-lessons]] for what *is* measured on this
host. Diagnosed by an external contributor in PR #873; the diagnosis is sound and
the PR was blocked on its detection mechanism, not on its premise.
