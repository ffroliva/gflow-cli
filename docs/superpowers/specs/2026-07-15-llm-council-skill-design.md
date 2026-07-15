# `llm-council` — external-tools review layer — design

**Prompted by:** a live session (2026-07-15) reviewing the issue #316 design spec and implementation plan. `pr-council-review`'s internal Claude-subagent council ran twice (6 dimensions, 2 rounds each) and found real, legitimate issues both times. Manually bolting on external CLI tools (`codex`, `agy`) afterward, ad hoc, surfaced **6 additional real, distinct issues that all 12 internal-agent dispatches (6 dimensions × 2 rounds) completely missed** — wording accuracy (claiming a "hash bypass" on a path that was never hashed), a wrong test-count claim, a Windows-specific test-triviality nuance, a test-isolation gap, a missing env-var-parsing test, and a malformed nested-markdown-fence bug. This is strong, concrete evidence that an independent model family catches a different error distribution than same-family reviewers, however many of them — worth codifying as a reusable skill rather than repeating the ad hoc Bash-orchestration every time.

## Problem

`pr-council-review` (PR mode + branch mode, `skills/pr-council-review/SKILL.md`) dispatches N parallel Claude subagents across fixed review dimensions and synthesizes a verdict. Every reviewer in that council is the same model family running the same harness — genuinely independent per-dimension, but not independent of shared blind spots (a phrasing choice that reads as accurate to one Claude reviewer reads as accurate to all of them; a Windows/POSIX nuance nobody's dimension prompt asked about goes unchecked by all of them equally). Tonight's session added external tools (`codex`, `agy`) manually — via raw `Bash` calls, rediscovering invocation gotchas live (`codex review`'s built-in prompt spiraled into a stuck self-inflicted PowerShell-quoting loop; `codex exec -s read-only` worked; `agy` failed twice, differently, likely account-quota-related) — and it worked, but every gotcha will be rediscovered from scratch next time without a place to record it.

## Decisions (from brainstorming)

- **Standalone skill, composes with `pr-council-review`, doesn't extend it.** `llm-council` invokes `pr-council-review` for the internal dimension council unchanged, then adds an external-tools layer on top and folds both into one synthesis. `pr-council-review` stays focused on diff-review dimensions and doesn't need to know external tools exist. This also means `llm-council` inherits `pr-council-review`'s existing input contract (PR# mode, branch mode) for free — no new "what am I reviewing" surface to design.
- **Tier axis = external tool count, not round count.** `small` (default) = internal council only, byte-identical to running `pr-council-review` directly today. `medium` = internal + `codex`. `high` = internal + `codex` + `gemini`. Round behavior (fix → re-verify → repeat until GREEN or a cap) is orthogonal to tier — it's how council review works at any tier, not a tier knob, matching what actually happened tonight (two full fix-and-reverify rounds on the same tier).
- **`agy` is registered but excluded from all default tiers**, opt-in via an explicit flag, until it's proven reliable non-interactively. Two failures tonight, two different error shapes (an interactive "no active workspace" prompt even with `--add-dir`; a bare "Agent execution terminated due to error" even with `--agent gsd-plan-checker --new-project`) — plausibly account-quota exhaustion per the user, not a structural incompatibility, but unconfirmed either way. Structurally excluding it from the default pool avoids repeatedly eating its ~5min timeout for a tool that hasn't yet produced one usable review.
- **Lightweight pre-flight probe before the real dispatch.** A real external review takes 10-20 minutes; discovering a dead tool only after burning that whole window (what happened with the first `codex review` attempt and both `agy` attempts) is expensive. Each tool gets a cheap, short-timeout probe (`--version`, or a trivial prompt) before it's included in a round. Only tools that respond join; skipped tools are named in the report, not silently dropped.
- **Best-effort synthesis, explicit disclosure.** A tool that passes the probe but then fails or times out during the real call is dropped from that round with a one-line note in the report — consensus is computed from whatever actually returned, same "no silent caps" principle the underlying `Workflow` tooling already uses for findings. Never block the whole review on one flaky external tool.

## Tool registry

Each entry is a fixed, tested invocation recipe — the exact thing tonight's session had to rediscover live. New tools get added here as their recipe is proven, not guessed at dispatch time.

### `codex`

- **Never** `codex review` — its built-in prompt got stuck tonight in a self-inflicted loop: it tried to concatenate two skill files via a malformed PowerShell command, then retried the exact same broken command for 20+ minutes without making progress. Confirmed the same command failed identically on a clean retry.
- **Use:** `codex exec -s read-only -C <absolute-repo-dir> --skip-git-repo-check "<direct, fully self-contained prompt>"`. `-s read-only` avoids write-sandbox friction for a pure review task; `-C` pins the working directory explicitly rather than relying on shell `cd`; `--skip-git-repo-check` avoids a git-repo-detection dependency that isn't needed for a read-only review.
- **Probe:** `codex --version` (near-instant, confirms the binary is installed and responds) — does not confirm auth/quota validity, only binary health. A quota exhaustion would still only surface on the real call; the probe's job is catching "not installed" / "hung shell," not every failure mode.
- **Timeout:** real calls ran 10-20 min tonight (`xhigh` reasoning effort, default in this environment) — budget accordingly; run in background, never block on it synchronously.
- **Known gotcha:** a killed/timed-out `codex exec` can leave orphaned `codex.exe` / `codex-code-mode-host.exe` / sandbox-helper processes on Windows that a plain process-tree kill doesn't clean up — `taskkill //F //PID <pid>` each one individually before retrying, or the retry's own file redirect can race with the orphaned process still writing to the same path.

### `gemini`

- Installed (`gemini` CLI present, confirmed via `which`), **never actually invoked** in tonight's session — no proven recipe yet. First real use in this skill IS the recipe-discovery pass; record whatever invocation/probe pattern works (or doesn't) back into this registry entry once tried. Provisional until validated.

### `agy` (opt-in only, not in default pool)

- **Best-known recipe:** `agy --agent <gsd-agent-name> --new-project --add-dir <absolute-repo-dir> -p "<prompt>"` (the GSD agent registry — `agy agents` lists `gsd-plan-checker`, `gsd-code-reviewer`-adjacent names, etc. — pick the closest match to the artifact under review, e.g. `gsd-plan-checker` for a plan, when one fits).
- **Failures tonight, both non-reproducible in a useful way:** (1) `agy -p` with no `--agent`/`--new-project` responded with an interactive "no active workspace set, would you like to..." prompt instead of doing the review, even after `--add-dir` was added on retry. (2) `agy --agent gsd-plan-checker --new-project --add-dir ... -p ...` returned a bare `Error: Agent execution terminated due to error.` with zero diagnostic detail.
- **Working hypothesis (unconfirmed):** account/quota limit, per the user. If so, the pre-flight probe (`agy --version` or equivalent) won't catch it — quota exhaustion is an auth/billing-layer failure, not a binary-health failure, same caveat as `codex`'s probe. Do not re-litigate this open question inside the skill; just keep `agy` opt-in and best-effort until someone confirms root cause.

## Availability probe

Before dispatching any tool's real review call, run its registry-defined probe with a short timeout (~10-15s). A tool that doesn't respond in time is excluded from this round and named in the report ("codex: excluded, probe timed out" / "agy: not in default pool, opt-in with --include-agy"). This is a binary-health check only — it cannot catch every failure mode (e.g. quota exhaustion that only manifests on a real call), so a probe pass is necessary but not sufficient; the best-effort/disclose policy at the real-dispatch layer is still the backstop.

## Dispatch flow

1. Resolve tier → tool list (`small` = none, `medium` = `[codex]`, `high` = `[codex, gemini]`, plus `agy` if the opt-in flag is set).
2. Probe each resolved tool in parallel, short timeout. Drop non-responders, note them.
3. Dispatch `pr-council-review` (PR# or branch mode, unchanged) for the internal dimension council.
4. In parallel with step 3, dispatch each surviving external tool via its registry recipe, backgrounded (never block synchronously — real calls run 10-20 min).
5. As each external call returns (or times out / errors), fold its verdict into the same synthesis table `pr-council-review` already produces, tagged by source (`D3 (internal)` vs `codex (external)`), same GREEN/YELLOW/RED vocabulary. A tool that fails after the probe is dropped from THIS round with an explicit note — not silently.
6. If any external finding warrants a fix (same must-fix bar as an internal finding), apply it, then re-run — same round loop as tonight, orthogonal to tier: re-verify against the specific dimensions/tools that flagged something, not necessarily the full pool again.
7. Report: same shape as `pr-council-review`'s existing report, with an added "External tools" section listing which ran, which were skipped/failed and why, and their findings folded into the main Must-fix/Nice-to-have/Confirmed-good lists like any other dimension.

## Process hygiene

Backgrounded tool calls that get killed on timeout can leave orphaned processes (confirmed with `codex.exe` tonight, twice). After any kill, verify via `tasklist`/`ps` that the named PIDs are actually gone before retrying the same tool — a retry that races an orphaned process still writing to the same output file silently corrupts the result.

## Non-goals

- No new redaction/security layer around external tool output — the same trust level as internal subagent output applies (these are local CLI tools the user already has installed and authenticated).
- No attempt to normalize different tools' native output formats beyond folding their prose verdict into the shared GREEN/YELLOW/RED + Must-fix/Nice-to-have shape — a tool's own citations/style stay as-is in its section.
- No change to `pr-council-review` itself — this skill is purely additive, composing at the dispatch/synthesis layer.
- Does not resolve `agy`'s root-cause failure — flagged as an open question, worked around via opt-in exclusion, not fixed.

## Testing

This is a skill (a Markdown behavior spec + orchestration script), not application code — no pytest coverage. Verification is: run `llm-council` at each tier against a real branch/PR and confirm (a) `small` behaves identically to invoking `pr-council-review` directly, (b) `medium`/`high` actually dispatch and fold in `codex`/`gemini` results, (c) a deliberately-broken tool invocation (e.g. temporarily renaming the `codex` binary) is caught by the probe and reported as skipped rather than hanging the whole round.
