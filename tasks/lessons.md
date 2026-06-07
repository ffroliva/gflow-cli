# Lessons learned

> CLAUDE.md mandate: append patterns discovered through user corrections / hard-won review findings, so future sessions don't re-discover them. Read at session start when relevant.

Lessons are dated and reference the originating commit / session where applicable. Most are converted into stable rules in `~/.claude/projects/.../memory/project_conventions.md`; this file is the running notebook before consolidation.

---

## 2026-05-11 — Phase 4 hardening (v0.4.0a1)

### L1 — pyright `reportPrivateUsage` does not honor PEP 484 `as Y` re-export

**Context:** T4b/T5. Tried `from X import _foo as _foo` at consumer sites to silence pyright on underscore-prefixed names imported across modules. Pyright's `reportPrivateUsage` rule fires regardless of the `as` form (unlike mypy's behavior).

**Rule:** When a top-level shared module exports underscore-prefixed names (kept underscored for naming-history reasons — e.g. `_resolve_profile`), list them in `__all__` on the source module. Consumer-side `as Y` aliasing does not help. Cite this lesson in a comment near the `__all__` so a future contributor doesn't try to "clean it up".

**Source:** Commits `692b1fe`, `6391ce4`. Originating reviewer note: code-reviewer on T4b.

---

### L2 — `structlog.processors.format_exc_info` does NOT honor `show_locals=False`

**Context:** T5. The simpler processor's default behavior happens to omit frame locals today, but a future maintainer who swaps in `RichTracebackFormatter(show_locals=True)` would silently leak frame locals (auth tokens, signed URLs) into the structured log.

**Rule:** When configuring structlog with a privacy-sensitive contract (`show_locals=False` MANDATORY per Phase 4 spec C6), use the explicit form:

```python
exc_renderer = structlog.processors.ExceptionRenderer(
    structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
)
```

The verbose form makes the privacy guarantee visible at the call site. Pair with a test that puts a sentinel string in a local frame variable and asserts it does NOT appear in serialized output.

**Source:** Commits `b72c07f`, `1470a94`. Originating reviewer: T5 council H5 (python-reviewer).

---

### L3 — `monkeypatch` finalizer covers teardown, but `yield` is still worth writing

**Context:** T6. `_forbid_live_playwright` autouse fixture used `monkeypatch.setattr(...)` without `yield` — functionally safe because pytest's monkeypatch finalizer handles teardown automatically. Code reviewer flagged it as inconsistent with the rest of the fixture corpus.

**Rule:** Even when not strictly required, end every autouse fixture with `yield` and annotate as `-> Generator[None, None, None]`. Three benefits: (a) consistency with the other generator fixtures, (b) signal to future contributors that this fixture has a lifecycle, (c) ready place to add post-yield invariants (e.g. `assert _explode.call_count == 0`).

**Source:** Commit `abac707`. Originating reviewer: T6 council H1.

---

### L4 — `Path("/dev/null")` is a Windows tripwire

**Context:** T6 step bindings. `Path("/dev/null")` on Windows resolves to a relative path under the current drive — doesn't exist, doesn't behave like a sink.

**Rule:** Use `Path(os.devnull)` — portable: `NUL` on Windows, `/dev/null` on POSIX. The codebase targets Windows + macOS + Linux per CLAUDE.md; assume nothing about platform-specific paths.

**Source:** Commit `abac707`. Originating reviewer: T6 council M1.

---

### L5 — Redact body BEFORE truncating, never after

**Context:** T3. `WireFormatError.discovery.body_prefix_redacted` field. The order matters: tokens can be longer than 200 chars, so truncate-first might cut a token in half (leaving an un-redactable fragment) while redact-first reliably scrubs full token patterns before the slice.

**Rule:** At any raise site that captures a body prefix for diagnostic logging:

```python
body_prefix_redacted = _redact_for_log(body_text[:200])  # ⚠ wrong order — see L5
body_prefix_redacted = _redact_for_log(body_text)[:200]   # ✓ correct — redact full body first
```

**Source:** Commit `e1d3811`. Originating reviewer: T3 security council audit.

---

### L6 — `isinstance(True, int)` is True

**Context:** T1. `FlowApiError.__init__` uses `isinstance(args[0], int)` to auto-detect legacy positional calls. `bool` is a subclass of `int`, so `FlowApiError(True, ...)` silently took the legacy path with `status=True`.

**Rule:** Whenever an `isinstance(x, int)` guard is being used to detect "this is a status code or a numeric ID", add `and not isinstance(x, bool)`:

```python
if isinstance(args[0], int) and not isinstance(args[0], bool):
    ...  # legacy path
```

Same applies to `isinstance(x, float)`, `isinstance(x, str)` — Python's bool/int and bytes/str hierarchies have surprising membership.

**Source:** Commit `4f33262`. Originating reviewer: T1 council H1 (python-reviewer).

---

### L7 — `EXIT_CODE_MAP` ordering invariant must be most-specific-first

**Context:** T1. `EXIT_CODE_MAP` uses an `isinstance` walk; if you add a parent class before its subclass entry, `isinstance` matches the parent first and the subclass-specific code never fires.

**Rule:** Maintain `EXIT_CODE_MAP` as ordered-most-specific-first (leaf subclasses before their parents). Document this in a comment, AND lock with a test:

```python
def test_exit_code_map_ordering_invariant():
    seen: list[type] = []
    for cls in EXIT_CODE_MAP:
        for prior in seen:
            assert not issubclass(prior, cls), (
                f"{prior.__name__} is a subclass of {cls.__name__} but appears AFTER — "
                f"swap their order in EXIT_CODE_MAP."
            )
        seen.append(cls)
```

The test catches the regression at refactor time.

**Source:** Commit `94770ab`. Spec § 2 C4.

---

### L8 — Per-attempt token mint inside the retry loop is non-negotiable

**Context:** T3. First implementation (commit `a31e6b4`) routed all 5 wrapped methods through `_post_json` and minted the reCAPTCHA token ONCE per call, OUTSIDE the retry loop. Security council escalated this: if Flow's tokens are single-use (per the comments in `recaptcha.py`), retries 2 and 3 receive a spent token and inevitably fail with 401/403, defeating the retry layer entirely.

**Rule:** For any auth-bearing request that has a single-use-per-attempt token (reCAPTCHA, CSRF, nonces), the token mint MUST happen INSIDE the retry-loop body, on the worker's checked-out resource (Page), on every attempt. Never cache across retries. Verify with a test that asserts `mint.call_count == attempts` after a forced 3-attempt sequence.

**Source:** Commit `e1d3811`. Originating reviewer: T3 security council (HIGH escalation from MEDIUM after empirical token-replay analysis).

---

### L9 — `asyncio.Queue` without `maxsize` is unbounded; document or set the cap

**Context:** T2. The per-worker Page pool used `asyncio.Queue()` with no `maxsize`. A double-`put_nowait` would silently grow the pool past N, letting two coroutines hold the "same" Page concurrently.

**Rule:** When constructing `asyncio.Queue` for resource-pool semantics, ALWAYS pass `maxsize=N` matching the resource count. `put_nowait` then raises `QueueFull` loudly on a double-checkin — bug surfaces immediately. Also use generic typing: `asyncio.Queue[Page](maxsize=n)` for pyright strict.

**Source:** Commit `3677cb8`. Originating reviewer: T2 council H1+H2.

---

### L10 — Always read pre-existing source before reproducing semantics in a refactor

**Context:** T4b. The plan's boilerplate test for `_make_provider_dir` assumed the helper would `mkdir` the directory on call. Reality: the helper EXITS 2 if the dir doesn't exist (creation is `gflow auth login`'s responsibility). Test would have asserted wrong semantics if the executor hadn't checked.

**Rule:** For any "pure refactor" task that relocates code, the FIRST step is reading the pre-relocation source to lock the exact semantics — not relying on the plan's prose summary. Add a "Step N.0 Preflight" to every refactor plan that mandates this. Plan templates should NEVER ship boilerplate that assumes semantics; require explicit pin-down.

**Source:** Commit `692b1fe`. Originating insight: T4b executor's Step 4b.0 preflight (added by Coordinator after T3 spec deviation taught us this lesson the hard way).

---

### L11 — Per-task scope discipline prevents executor stop-the-line

**Context:** T6. Executor caught 3 plan/reality mismatches (`auth show` non-existent, `_run_batch` sequential, retry mock seam wrong) by STOPPING and surfacing rather than improvising. This let the Coordinator fix each at the right level (spec edit / new commit / scenario rewrite) instead of accumulating drift.

**Rule:** Implementer agents should STOP and surface to Coordinator when:
- A plan reference points at something that doesn't exist in the codebase (e.g. a CLI command that was renamed).
- A test would assert behavior the production code doesn't deliver.
- A scope-clean fix requires touching files outside the task's stated scope.

Improvising around these issues silently corrupts the spec-implementation alignment. The Coordinator pays a small interruption cost for a much larger preservation of fidelity.

**Source:** Multiple T6 dispatches. Reinforces the orchestration plan § 7 escalation rules.

---

### L12 — Council deviations: distinguish quality-concern HIGH from correctness HIGH

**Context:** Multiple. Council reviewers (python-reviewer especially) flag stylistic / typing concerns as HIGH alongside actual correctness bugs. The Coordinator must triage: a `logger: Any` parameter and a `bool`-passes-`isinstance(int)` guard are both labeled HIGH but only one is a runtime bug.

**Rule:** When consolidating council findings, classify each HIGH as:
- **Correctness HIGH:** the code behaves wrong (e.g. token reuse, bool guard, unbounded queue). MUST fix before advancing.
- **Quality HIGH:** the code is correct but violates a standard (typing strictness, naming, doc).  Triage: fix if cheap, defer to a backlog if expensive.

Don't treat all HIGH equally — the orchestration's "0 HIGH outstanding" gate is for correctness HIGHs. Quality HIGHs go to a per-task deferred-items list captured in memory.

**Source:** T2/T3/T4a/T5 review cycles.

---

## Operational meta-lessons

### L13 — Surface mid-flow stops cleanly; don't hide them

When a dispatched agent stops mid-task (context, tool error, surfaced blocker), the Coordinator should:
1. Run `git status --short` + grep what files were touched.
2. Verify partial work compiles / passes its narrow tests.
3. Either continue manually if scope is small, OR dispatch a continuation agent with explicit "you are continuing from here, here's the current state" framing.

Don't pretend the agent finished. Don't silently re-run the same prompt — context-budget burn without progress.

### L14 — Memory ledgers earn their keep on long phases

The `project_phase4_progress.md` task ledger turned out essential mid-phase for re-orienting after context warnings and for the Coordinator's own status updates. Update it after every task's Stage F. Include commit SHAs, test counts, [SEC] PASS flags, and deferred MEDIUMs — the metadata that makes the next session resumable.

### L15 — CHANGELOG promotes Unreleased to a version label at release time, not at task time

T7 vs T8 separation: write the user-facing notes against `[Unreleased]` during the phase, then promote the heading to `[<version>] — <date>` only in the release commit (T8). Avoids the version-date mismatch that happens when content lands days before the tag.

---

---

## 2026-05-11 — Doc-council polish session (v0.4.0a2)

### L16 — `if ! cmd; then case $?` always sees 0 (shell footgun in shipped docs)

**Context:** USAGE.md L346–356 had a shell example for exit-code branching that used `if ! gflow ...; then case $? in 2) ...`. Codex caught it; verification with `bash -c 'false; if ! false; then echo $?; fi'` printed `0`. The `if` consumed the exit code; the `case` never matched the real failure code.

**Rule:** When documenting exit-code branching in shell, capture `$?` BEFORE the conditional consumes it:

```bash
cmd                          # WRONG: if ! cmd; then case $? in ...
rc=$?                        # ✓ capture first
if [ "$rc" -ne 0 ]; then
  case "$rc" in ...
```

PowerShell equivalent uses `$LASTEXITCODE` (per-call). This is a documentation-only bug — no test catches it — so it must be enforced by review discipline.

**Source:** Commit `507de9a`. Originating reviewer: Codex CLI 0.130.0 + Claude python-reviewer (both flagged) on the v0.4.0a2 council.

---

### L17 — Verify doc claims against code-of-record before applying any reviewer's "fix"

**Context:** Claude python-reviewer's CRITICAL list claimed the README Python snippet had 4 bugs. I checked the actual signatures with `grep` against `src/gflow_cli/api/client.py` and `src/gflow_cli/api/dto.py`. Three of the 4 were real bugs; one was off by a different mechanism than the reviewer described. Codex independently flagged `tasks/lessons.md` as missing — the file existed at 195 lines (false positive).

**Rule:** No reviewer (Claude, Codex, Gemini) earns a `✓verified` row in the council consensus matrix without a literal `grep`/`sed`/file-existence check against the actual source. The discipline is named "don't guess" — the consequence of skipping it is propagating a reviewer's invented "fix" into a release. Wire one `ctx_execute(language: shell)` block per audit that probes every CLI signature, dto shape, route name, and method that the reviewers reference.

**Source:** Commit `507de9a`. Multiple council audits.

---

### L18 — `.env.template` should COMMENT OUT its defaults, not hard-pin them

**Context:** `.env.template` had `GFLOW_CLI_PROFILE=default`, `GFLOW_CLI_PROVIDER=flow`, `GFLOW_CLI_TIMEOUT_SECONDS=600`, etc. as active assignments — meaning a user who copies to `.env` (the documented onboarding step) and previously exported `GFLOW_CLI_PROFILE=experiments` in their shell would have their export silently overridden by the template's hard-pin.

**Rule:** Every default value in `.env.template` ships commented out: `# GFLOW_CLI_PROFILE=default`. The template's job is documentation (here's what you can set), not configuration (here's what you must set). Only truly required values (none in this project) get uncommented defaults.

**Source:** Commit `507de9a`. Originating reviewer: Claude python-reviewer + Codex MEDIUM on v0.4.0a2 council.

---

### L19 — Never assert billing behaviour of a private API

**Context:** USER_GUIDE.md Journey 7.3 said "Flow doesn't re-bill for previously-completed media". That's a confident claim about Google Flow's internal billing logic — a system we have no contract with and cannot inspect. If we're wrong, our readers (Ultra/Pro subscribers running batch automation) lose real Veo credits.

**Rule:** When documenting a reverse-engineered surface, claims about cost, retry semantics, idempotency, or refund behaviour are AT MOST observations ("**may** re-issue paid generations"), never assertions ("**doesn't** re-bill"). The cost-of-being-wrong is the user's money — soften the wording even if the observation has held in every test run so far.

**Source:** Commit `507de9a`. Originating reviewer: Claude article-writing + Codex (both flagged HIGH) on v0.4.0a2 council.

---

### L20 — Private-API endpoint names in user-facing docs should be generalised

**Context:** USER_GUIDE.md Journey 2 named an endpoint `flowMedia:batchGenerateVeoVideo` — invented, no such route exists in `samples/captured/` or `api/routes.py`. The real route is `/v1/video:batchAsyncGenerateVideoText`. The drift happened because someone wrote the prose against a recollected guess instead of grepping `routes.py`.

**Rule:** When prose names a private-API route, either:
1. Pin to the EXACT route from `samples/captured/` + `api/routes.py` and link to those files as ground truth, OR
2. Generalise ("POSTs to Flow's video generation endpoint on `aisandbox-pa.googleapis.com`") and let `samples/README.md` be the canonical name reference.

**Why both options:** Option 1 is reader-friendly but ages poorly when Flow rotates a route. Option 2 is safer for user-facing docs; option 1 is appropriate in `ARCHITECTURE.md` + `samples/README.md` where a contributor needs the literal name.

**Source:** Commit `507de9a`. Originating reviewer: Claude code-reviewer + Codex (both flagged HIGH) on v0.4.0a2 council.

---

### L21 — Tag the release-quality commit; let post-release housekeeping land on top

**Context:** v0.4.0a1 was tagged locally at `fa2b76e`. The doc council found ~38 distinct issues — material enough to warrant cutting v0.4.0a2 instead of polishing v0.4.0a1 in place. The doc-polish commit landed at `507de9a`; that's where we put the new tag. AFTER the tag, the Stage G verifier wrote a verification report (`docs/superpowers/verifications/2026-05-11-phase-4-stage-g.md`) — that landed as a separate commit `d075620` on `main`, NOT inside the tag.

**Rule:** A release tag points at the **release-quality** commit. Post-release housekeeping (verifier reports, council scratch artifacts, follow-up TODO acknowledgements) lands AFTER the tag on the branch. CI/CD release workflows trigger on tag-push and build the tag SHA; they don't see HEAD-after-tag. This keeps the tagged artifact reproducible while not blocking forward motion on the branch.

**Corollary:** A local-only tag can be deleted and replaced (`git tag -d v0.4.0a1 && git tag -a v0.4.0a2 -m "..."`) before push without harming history — the original commit remains as an ancestor of HEAD, just unlabelled. Public sees only the kept tag.

**Source:** This session. Originating insight: Coordinator's decision tree after the council audit determined v0.4.0a1 had too many doc bugs to ship as-is.

---

_End. Convert stable rules from this file into `project_conventions.md` once they survive a second phase without revision._

---

## 2026-05-15 — Code Mode / agent workflow session

### L22 — Namespace all project slash commands under `/gflow:`

**Rule:** Project commands live in `.claude/commands/gflow/` and are invoked as `/gflow:<name>`. Prevents collision with Claude Code built-ins and user-global commands. Never drop project commands at the root `commands/` level — no namespace means silent collision risk when a built-in of the same name is introduced.

**Source:** This session.

---

### L23 — `/gflow:check` auto-fixes but does not commit

**Rule:** `ruff check --fix` and `ruff format` rewrite files in place. The command must stop there — leave the diff for the agent or human to review before staging. Auto-committing lint fixes removes the review gate. Report which files changed; let the committer decide.

**Source:** This session.

---

### L24 — INDEX.md is worth loading at session start; heavy docs are not

**Rule:** INDEX.md is a small routing table (~37 lines) — cheap enough to load at every session start. It enables lazy loading of everything else. PLAN.md, KNOWN_ISSUES.md, and CHANGELOG.md are loaded on demand via `/gflow:status`, `/gflow:known-issues`, and `/gflow:changelog`. (Note: `/gflow:plan <feature>` is the plan-creator command; `/gflow:status` is the plan-reader.) Loading all four upfront burns tokens on sessions where only one is needed.

**Source:** This session.

---

### L25 — Changelog entries during development; migration owned by `/gflow:release`

**Rule:** Add entries to `[Unreleased]` in the same commit as the user-visible change (during development). `/gflow:release` owns the migration: moving `[Unreleased]` to a versioned section. Never accumulate all entries at release time — the diff is meaningless and defeats `git blame` on the changelog.

**Source:** This session. Reinforces L15.

---

### L26 — Escape the Setup Loop (Loop Braking)

**Rule:** If a test fails or skips 3 consecutive times with "Environment/Setup" errors, the agent MUST immediately stop retry attempts via CLI flags or environment variables and pivot to Source Research mode (analyzing `conftest.py` and fixture resolution logic). Retrying the same command with trivial variations is a signal of an unproductive "loop" and wastes context.

**Source:** The v0.13.0 release session (fixing E2E profile resolution skips).

---

### L27 — Async Testing (CliRunner vs. Asyncio Nesting)

**Rule:** Click command functions that call `asyncio.run()` (which is standard for all `gflow` generation commands via `run_with_handlers`) must be tested using synchronous test functions and `CliRunner.invoke()`. Never use `@pytest.mark.asyncio` for these tests, as nesting `asyncio.run()` within an existing test-driven event loop causes `RuntimeError: coroutine was never awaited` crashes.

**Source:** The v0.13.0 release session (refactoring `test_i2v_flags_e2e.py`).

---

### L28 — E2E Isolation (The `unisolated_home` Fixture)

**Rule:** Use the `unisolated_home` fixture in E2E/Smoke tests that must resolve real Chromium profiles from the host system. This fixture explicitly uses `monkeypatch` to restore the real `GFLOW_CLI_HOME` to satisfy security boundary checks, while preserving the temporary `GFLOW_CLI_DB_PATH` isolation for safety. Do not use scattered, imperative `os.environ` or `monkeypatch` calls.

**Source:** The v0.13.0 release session.
