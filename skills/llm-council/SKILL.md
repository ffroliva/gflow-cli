---
name: llm-council
description: Use when a pr-council-review (PR or branch mode) result needs independent corroboration from a different model family before trusting a GREEN verdict — high-stakes, security-sensitive, or architecturally significant reviews where same-model-family Claude subagents might share a blind spot. Also use when the user asks for "external", "second opinion", "cross-model", or names codex/antigravity (`agy`) or another external CLI coding agent alongside a review.
---

# `llm-council` — external-tools review layer

## Overview

Wraps `pr-council-review` (unchanged) and adds a layer of external CLI coding agents (`codex`, plus Antigravity — the `agy` harness) as additional independent reviewers, then folds their verdicts into the same synthesis. Internal Claude subagents are independent per-dimension but share one model family's blind spots — a phrasing choice, a Windows-vs-POSIX nuance, or a syntax error that reads fine to one Claude reviewer reads fine to all of them. A different model family catches a different error distribution. Confirmed live: on one review, external tools caught 6 real, distinct issues (a wording-accuracy bug, a wrong test count, a Windows-only test-triviality nuance, a test-isolation gap, a missing test, a malformed markdown fence) that 12 internal Claude-subagent dispatches (6 dimensions × 2 rounds) had all missed.

## When to Use

- Any `pr-council-review` run (PR# mode or branch mode) where the artifact is high-stakes enough to want a second, differently-biased opinion before calling it GREEN.
- Not needed for a quick spot-check or draft iteration — use `/review` (single-agent) for that; `pr-council-review` alone for a normal-stakes PR.

## Quick Reference — Tiers

| Tier | Internal (pr-council-review) | External tools |
|---|---|---|
| `small` (default) | ✅ full dimension council | none — identical to running `pr-council-review` directly |
| `medium` | ✅ | `codex` |
| `high` | ✅ | `codex` + Antigravity (`agy`) |

Tier controls **tool breadth**, not review rounds. Fix → re-verify → repeat until GREEN (or a round cap) happens at every tier — that's how council review works, not a tier knob.

## Tool Registry

Fixed, tested invocation recipes. Do not improvise a command for a listed tool — the "obvious" invocation is often a trap (see `codex` below).

### `codex`

- **NEVER** `codex review`. Its built-in prompt has gotten stuck in a self-inflicted loop reading skill files via a malformed PowerShell command, then retrying the identical broken command for 20+ minutes with zero progress. Confirmed reproducible on a clean retry.
- **Use:** `codex exec -s read-only -C <absolute-repo-dir> --skip-git-repo-check "<direct, fully self-contained prompt>"`.
- **Probe:** `codex --version` (near-instant; confirms binary health only, not auth/quota).
- **Timeout budget:** real calls run 10-20 min at default (`xhigh`) reasoning effort. Always background it — never block synchronously.
- **Orphan risk:** a killed/timed-out `codex exec` can leave `codex.exe` / `codex-code-mode-host.exe` / sandbox-helper processes running on Windows. After any kill, verify via `tasklist`/`ps` that the named PIDs are actually gone before retrying — a retry racing an orphan still writing the same output path silently corrupts the result.

### Antigravity (`agy`)

- **What it is:** Google's Antigravity harness, invoked via `agy`. It supplies the `high`-tier's second, different-model-family opinion.
- **Best-known recipe:** `agy --agent <gsd-agent-name> --new-project --add-dir <absolute-repo-dir> -p "<prompt>"` (run `agy agents` to list available agent names, e.g. `gsd-plan-checker` for reviewing a plan).
- **Probe:** `agy --version` first regardless — a fast binary-health check.
- **If it's missing or fails:** Antigravity is a newer harness and has failed non-interactively in testing (an interactive workspace prompt even with `--add-dir`; a bare unexplained termination error even with `--agent`/`--new-project`) — plausibly account-quota exhaustion, unconfirmed. Don't silently retry past its probe. When `agy` isn't installed or won't run non-interactively, **suggest installing it (or substituting another external CLI coding agent — e.g. a codex-only `medium` run)** and continue best-effort with whatever did return; never block the whole round on it.

## Dispatch Flow

1. Resolve tier → tool list.
2. **Probe every resolved tool in parallel, short timeout (~10-15s).** A tool that doesn't respond is excluded from this round — name it in the report, don't just drop it silently. The probe only catches binary-health failures (not installed, hung shell); a quota-exhausted tool can still pass the probe and fail on the real call — that's what the dispatch-layer disclosure step below is for.
3. Dispatch `pr-council-review` (unchanged) for the internal dimension council.
4. In parallel, dispatch each surviving external tool via its registry recipe, backgrounded.
5. Fold each returning verdict into the same synthesis table `pr-council-review` produces — same GREEN/YELLOW/RED vocabulary, tagged by source (e.g. `D3 (internal)` vs `codex (external)`). A tool that fails or times out after passing its probe is dropped from **this round** with an explicit note in the report. Never silently drop, never block the whole round on one flaky tool.
6. Any finding — internal or external — that warrants a fix gets applied, then re-verified against the specific dimension/tool that flagged it (not necessarily the whole pool again).
7. Report: `pr-council-review`'s existing shape, plus an "External tools" line noting which ran / were skipped / failed and why.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Running `codex review` because it sounds like the obvious subcommand for a review task | Use `codex exec -s read-only -C <dir> --skip-git-repo-check "<prompt>"` — see registry |
| Dispatching the real (slow) external call before probing | Probe first, short timeout — a dead tool costs 10-20 min discovered late vs. ~15s discovered early |
| Treating one failed external tool as a reason to abandon the whole external layer | Best-effort: drop that tool for this round, disclose it, keep going with whatever did return |
| Blocking synchronously on an external tool call | Always background it — internal dimensions and other external tools shouldn't wait |
| Retrying a timed-out tool without checking for orphaned processes first | `tasklist`/`ps` check + explicit kill before any retry against the same output path |
| Silently downgrading to codex-only when `agy` is unavailable | Disclose the drop and **suggest installing Antigravity or substituting another external CLI agent** — don't hide the reduced coverage |

## Cross-References

**REQUIRED SUB-SKILL:** the internal council dispatch is `pr-council-review` (`skills/pr-council-review/SKILL.md`) — this skill does not reimplement dimension detection, synthesis rules, or report shape, it wraps them.
