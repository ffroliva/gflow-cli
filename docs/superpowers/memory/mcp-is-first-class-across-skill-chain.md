---
name: mcp-is-first-class-across-skill-chain
description: "User requires MCP↔CLI parity to be a duty of EVERY pipeline phase, not a post-hoc check — full blast-radius visibility"
---

The user requires **MCP to be a first-class citizen in every workflow skill**, not a
checklist item at the end. Their words: "the mcp and cli must be in sync", "the skill must
perform this parity check so that we dont get loose ends and left behind that let us blind.
we need to have full visibility of the blast radious", and "we [have] the predict and
scenario etc all of them have their responsability and should touch the mcp domain as well".

**Why:** gflow ships every capability twice (CLI command + MCP tool), and
`tests/mcp/test_cli_parity.py` is **command-level only** — it fires when a new *leaf* lacks a
mapping, and stays green while an option goes unmirrored, a queued-payload key goes unread, or
a tool docstring asserts a restriction the CLI no longer has. In #626 the CLI stopped
rejecting `omni-flash --end-frame` while `mcp/tools.py` and `docs/MCP.md` kept telling agents
it was rejected, through green lint, pyright, 2065 tests, AND the parity gate. Prose in
AGENTS.md saying "keep them in sync" had been there the whole time and did not fire — see
[[wire-the-rule-dont-document-it]].

**How to apply:**
- Wiring shipped in PR #627: each phase owns a slice — `issue-assessment` (name affected
  surfaces) → `predict` (persona 4 scopes MCP blast radius) → `scenario` (**D13**) → `plan`
  (**task 6, not optional when task 5 exists**) → `pr-council-review` (**D15**) → `check`
  (**step 1b**) → `live-verify` (MCP queued path is different code) → `doc-review` (a FALSE
  MCP claim is release-blocking).
- The six mirror axes live **once**, in `skills/check/SKILL.md` step 1b. Cite them; never copy
  the table into another skill — a duplicated checklist drifts, which is the failure being
  fixed.
- The highest-value and least obvious axis: a request is built in **three** places — CLI,
  MCP-direct, and MCP-queued (`mcp/tools.py` payload → `worker/codec.py` decode). The third
  matches keys **by string**, so a mismatch type-checks, lints, passes tests, and silently does
  nothing (precedent: #495). Issue #628 tracks automating that one check.
- Related: [[cli-param-changes-need-mcp-parity]] (the older, narrower note — this supersedes
  its scope).
