---
name: content-policy-text-scan-false-positives-on-page-chrome
description: Scanning the whole page body for policy phrases killed every benign generation (live, 2026-06-14). Detection is scoped to alert/dialog/live regions, and a false miss is always preferred to a false positive.
---

Content-policy detection **never scans `document.body`**. It is scoped to explicit
announcement regions:

```python
# src/gflow_cli/api/transports/drivers/agentic.py:210
_POLICY_REGION_SELECTOR = '[role="alert"], [aria-live="assertive"], [role="dialog"]'
```

**Why:** the body-wide version shipped and was rolled back. A `"Content policy"`
footer/menu link that Flow renders on **every** page load matched the scan, so a
benign prompt raised a spurious `ContentPolicyError` (exit 5) within ~6 s —
observed live 2026-06-14. Every agentic generation died. Written up in
`docs/AGENT_UI_E2E.md` § "Three races the first live run exposed", and pinned by
`test_await_images_raises_content_policy_on_explicit_text` +
`test_await_images_ignores_body_chrome_policy_text`.

The trade is deliberate and directional: a chat-message-only refusal is **missed**,
which degrades to a timeout. `agentic.py:780` states it outright — *"we prefer a
false miss (timeout) over a false positive"* — because a miss costs one run and a
false positive breaks every run. Any future detector inherits that ordering.

**A pre-submit baseline does not rescue a body-wide scan.** It only neutralises
text that is **already mounted**; a toast, a lazily-opened menu, or a tile the CDK
virtual scroller recycles into view after the click all read as new. And the media
grid *is* virtualized — 13 rendered `flow-video-tile` against 183 records
(`docs/superpowers/spikes/2026-09-17-stranded-clip-recovery.md`) — so a card that
was merely off-viewport at baseline time counts as this run's refusal when it
later mounts. That turns a queued, rendering generation into a terminal
`ContentPolicyError`.

Two phrases are specifically **not** refusal evidence:

- **`"not been charged"`** is what Flow shows for a **deliberately aborted submit**
  (`docs/LIVE_VERIFICATION_v0.78.0.md` — the `route.abort()` credit-free
  verification technique, see [[credit-free-route-abort-verification]]). Matching
  it reports the project's own zero-cost verification runs as policy refusals.
- **`"unusual activity"`** is an account-state message, not a prompt verdict, and a
  credit shortfall produces refusal-shaped surfaces of its own — see
  [[flow-credits-videos-only]] and the #721/#719 pair where a drained wallet
  replaced the submit control entirely.

**How to apply:** a new refusal/blocked detector anchors on **structure** and reads
text only from the node it already matched — detection structural, detail textual.
`CREDITS_WARNING` (`src/gflow_cli/api/transports/migrated_composer.py:152`) is the
house precedent for this exact problem shape: one structural locator, a ~10-line
guard, and a spike behind it. English phrase lists in
`src/gflow_cli/api/transports/` additionally violate the locale-invariance rule —
see [[flow-locale-leak-icon-ligatures]] and [[ligature-carrier-differs-by-host]].

Reintroduced and caught by council review on **PR #873** (2026-09-18), which
re-derived the body-wide scan from scratch. The lesson existed the whole time, in
`docs/AGENT_UI_E2E.md` — a file no review dimension cites. That is why it is here:
a lesson only a human reader can find will be re-learned by the next contributor.
See [[migrated-refusal-is-a-dom-card-not-a-wire-record]] for the real problem it
was trying to solve.
