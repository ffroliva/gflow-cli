# `/gflow:live-verify` — live-verification enforcement design

> **Status:** DRAFT — pre-implementation · **Date:** 2026-07-19
> **Origin:** two failures in the same session shipping v0.40.0 — (1) an hour spent chasing a bug that
> only existed on a stale local branch (testing against real `develop` would have caught this
> immediately), (2) a 3-agent doc-review council caught 5 places where docs directly contradicted a
> just-shipped feature, which no code-level check would ever find. Both are instances of one root
> cause: this project verifies a blackbox it doesn't own, and nothing in the workflow enforces that
> fact per-feature — only at release time (`skills/release/SKILL.md` step 4b).
> **Scope:** gflow-cli only. Generalizing to other repos (e.g. compile-growth-monorepo) is a
> deliberate future follow-up, not baked in here.
> **Council-reviewed 2026-07-19:** a 3-agent audit (HLD coherence / LLD cross-reference /
> evidence-drift) reviewed this spec plus its implementation plan before execution. Findings folded
> in below; reports at `tmp/council-skillstack/0{1,2,3}-*.md` (local-only, not committed). Most
> important finding: the council independently reconstructed the real commit graph of today's
> incident and confirmed the Pre-flight check in 3.2 *would* have caught it — the failure was that
> the check didn't exist and wasn't run, not a design gap.

## 1. Problem

gflow-cli drives Google Flow — a system it does not own — via reverse-engineered HAR/DOM/browser-log
behavior. Offline checks (`ruff`, `pyright`, unit/BDD tests) verify that *gflow-cli's own code* does
what it's supposed to; they cannot verify that Flow still behaves the way it was captured, because
Flow is an external blackbox that changes without notice (see issue [#174], an A/B UI rollout that
silently broke entity staging on one account and not another).

Today's session hit two distinct, related failures:

1. **Stale-state waste.** Work was built and live-verified against a local checkout
   (`gflow-cli-pr-344`) that turned out to predate current `develop` — missing a guard
   (`has_reference_images`), a consolidation (`resolve_and_apply`), and a test fix that `develop`
   already had. An hour was spent diagnosing and fixing a bug that didn't exist in the real
   codebase. A cheap state check at the start would have caught this immediately.
2. **Release-only live verification.** The only place this project currently enforces "prove it
   against real Flow" is `skills/release/SKILL.md` step 4b — a gate that fires once, at release
   time, for the whole accumulated batch of unreleased work. Nothing enforces it per-feature, so a
   feature can sit "offline-green" for weeks before its live behavior is ever checked.

## 2. Goal

Enforce two things, both scoped to gflow-cli, both evidence-based (no claim without a fresh
verification artifact, per `superpowers:verification-before-completion`):

1. **Before investing effort** on a feature/fix, confirm the checkout actually reflects current
   `develop` — catch the stale-branch trap before it wastes time, not after.
2. **Before claiming a feature done**, require it to be exercised against real Flow (not just
   offline tests) with recorded evidence — generalizing release step 4b to fire per-feature, not
   just per-release.

Non-goals: this does not replace `/gflow:check` (offline gates before commit) or `/gflow:doc-review`
(release-time doc council) — it fills the gap between them: a state check before work starts, and a
live-verify gate before work is called finished, independent of whether a release is imminent. It
also does not modify `skills/release/SKILL.md` step 4b itself (see §5, deferred).

## 3. Design

### 3.1 One skill, two entry points

`skills/live-verify/SKILL.md` + `.claude/commands/gflow/live-verify.md` → `/gflow:live-verify`,
matching the existing `/gflow:check` / `/gflow:release` / `/gflow:doc-review` convention. (Named
`live-verify`, not `e2e-gate` — this repo already uses "e2e" for a distinct concept, the
`@pytest.mark.e2e` test marker; "live-verify" matches the skill's own vocabulary and avoids the
collision.) Two clearly delineated sections within the one file (mirroring how `release.md` already
handles multiple phases in one skill), each referenced from a different moment in the workflow
rather than invoked together:

- **Part 1 — Pre-flight**, referenced at the *start* of feature/fix work.
- **Part 2 — Live-verify**, referenced *before claiming done* (after `/code-review` and
  `/ponytail:ponytail-review`, before commit/PR).

**Trigger mechanism:** this project's skills are proactively invoked, not manually remembered — per
`using-superpowers`, "if you think there is even a 1% chance a skill might apply, you MUST invoke
it." `skills/live-verify/SKILL.md`'s frontmatter `description` must therefore name both moments
explicitly (e.g. "Use when starting work on a gflow-cli feature/fix — Part 1; use before claiming
gflow-cli work done, especially anything touching a generation path — Part 2") so the standard
proactive-invocation mechanism fires it at both points, the same way `brainstorming` already
auto-triggers on "let's build X." This mechanism is specific to agents that honor the
`using-superpowers` convention (this project's primary Claude Code workflow) — it is not a
substitute for discoverability by other agents this repo explicitly supports (Cursor/Codex/Gemini
CLI/Aider), which is why the `AGENTS.md` skills-reference table row (3.5) is required, not optional
reinforcement.

### 3.2 Part 1 — Pre-flight

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD             # what branch am I actually on?
git rev-list --count HEAD..origin/develop   # am I behind?
git log --oneline -5                        # do recent commits match what I expect?
```

`git rev-list --count HEAD..origin/develop` is an asymmetric DAG set-difference — it counts commits
`develop` has that the current `HEAD` lacks, regardless of whether `HEAD` has its *own* private
unmerged history. This is precisely what catches a diverged stale branch, not just a
behind-by-fast-forward one: the council confirmed against today's real incident that this exact
command returns nonzero (2) against the stale `pr-344` checkout, because a guard commit
`develop` had (`c1f19f6`) predated the stale branch's last commit — it would have fired mid-session
had it existed and been run.

- If the checkout is behind `origin/develop` (nonzero count above): **stop and surface it** before
  investing further effort. Don't silently proceed, don't silently switch — name the divergence and
  ask.
- Working in a separate sibling checkout is a normal pattern in this project's workflow and is not
  itself a red flag — a real feature branch is *supposed* to differ from `develop`. The actual
  signal is "differs from `develop` in a way that suggests staleness" (e.g. missing a
  function/guard `develop` already has) rather than "differs by adding new work on top of it." When
  in doubt, diff the specific file(s) about to be touched against `origin/develop`'s version before
  assuming they match.

### 3.3 Part 2 — Live-verify

"Live" means concretely: **drive the real generation commands** (`t2i`, `i2i`, `i2v`, and siblings
like `t2v`/`r2v` where applicable) against a real authenticated Flow account, covering **multiple
variations** of the change — not one happy-path call. A change is default-in-scope for this gate if
it touches a generation code path; skipping requires a named reason, not silence.

1. **Define the live matrix** before running anything: which command(s) does the change touch, and
   which variations actually exercise it (e.g. for a mention-resolution fix: a character mention, a
   media mention, an ambiguous-name case, an unresolvable name)?
2. **Cost tiers gate confirmation, not existence — and the tier follows the operation, not the
   command family:**
   - Bare entity CRUD with no image generation (`create_entity`, `list_characters`,
     `patch_entity` at the API level; `t2i`/`i2i` themselves) are **credit-free** — run as needed to
     cover the matrix without a separate confirm each time, but still mindful of WAF/volume
     discipline (cumulative daily volume, not burst-rate — don't fire dozens of live calls
     back-to-back without surfacing it).
   - **Anything that generates real media is costed**, even on an otherwise-free command family —
     e.g. `gflow character create --face-prompt` generates real face/body images and costs Imagen
     credits despite being "character CRUD" in name; `i2v` and other video-generation paths are
     always costed. Costed operations always require explicit operator go-ahead before running,
     batched (name what will run and why) rather than one-off asks per call.
3. **Run each variation, capture evidence per run** using the release skill's actual 5-layer ledger
   shape (file count + magic bytes + dimensions/shape + structlog invariants + a user-confirmable
   artifact — `skills/release/SKILL.md` §4b), adapted per-field to what the change under test
   actually produces, written to a lightweight per-feature evidence note at
   `tmp/live-verify/<feature-slug>.md` (gitignored, not the full `docs/LIVE_VERIFICATION_vX.Y.Z.md`
   release ceremony) — the per-feature note gets folded into the real `LIVE_VERIFICATION` doc when
   the feature actually ships in a release.
4. **On pass** — all matrix variations green, proceed to commit.
5. **On fail** — Part 2 hands off to the failure-routing logic (3.4).

### 3.4 Failure-routing (reproducibility re-test)

1. **Check for a known match first** (costed failures only, to avoid an unnecessary re-spend) —
   grep `KNOWN_ISSUES.md` / open GitHub issues for a matching error signature. If matched, skip the
   costed re-test; treat as known external flake immediately.
2. **If no match, re-test once before concluding anything.** Free for `t2i`/`i2i` (just re-run).
   For a costed failure, re-testing needs the same operator confirm as any costed run.
3. **Compare outcomes:**
   - **Same failure, same code, no known-issue match** → real bug. Route back to execution (fix it;
     use `superpowers:systematic-debugging` for root-causing if the cause isn't obvious). Re-run the
     gate after the fix.
   - **Different outcome, same code, no changes in between** (or a known-issue match) → external
     flake (e.g. issue [#174]). Do not loop trying to "fix" it. Record it in the evidence note —
     what failed, that it's not reproducible against unchanged code, and a link to the matching
     issue if one exists. Treat the gate as passed-with-a-caveat for *this* run, not blocked.
   - **The failure reveals the plan's premise was wrong** (not a coding bug, not a flake — e.g. a
     wrong assumption about Flow's API behavior that a spike would have caught) → route back to
     planning/design, not execution. Don't keep patching code against a wrong premise. (This nearly
     happened today before the `resolve_and_apply` discovery reframed the whole investigation.)
4. Every branch's outcome is recorded in the evidence note — passes, fails, and flakes are all
   evidence, not just the final green state.

### 3.5 Standing awareness and discoverability

**New bullet in `AGENTS.md`'s existing "Working discipline — verify before you act" section**, next
to "If a claim can't be verified in the current environment, it's LIKELY — not CONFIRMED":

> **This project reverse-engineers a blackbox.** gflow-cli doesn't own Google Flow — it drives real
> Flow through inspected HAR/DOM/browser-log behavior. Offline checks (types, lint, unit/BDD tests)
> verify *our* code does what we think it does; they cannot verify Flow still behaves the way we
> captured it. Every feature that touches a generation path is **live-verified**, not just
> offline-tested, before it's called done — see `/gflow:live-verify`.

**New row in `AGENTS.md`'s "Skills reference (cross-tool)" table** — this table is explicitly stated
as resolvable by any agent (not just Claude Code's proactive-invocation mechanism), so it is the
real cross-agent discovery path, not optional reinforcement.

**New row in `docs/INDEX.md`** pointing to this spec and its implementation plan — required by
`doc-review`'s own mechanical check (§2, "every `.md` in `docs/` needs an entry"), which will FAIL
next release if skipped.

**One-line pointer appended to `skills/check/SKILL.md`'s final paragraph**, so anyone running
`/gflow:check` before commit is reminded that offline-green isn't done-done for generation-path
changes.

**Note on the doc-links gate:** `scripts/ci/check_doc_links.py` scans a hardcoded allowlist of
top-level `docs/*.md` files and does **not** cover `skills/*.md` — it cannot verify links inside the
new skill file. The relative link from `skills/live-verify/SKILL.md` to this spec must be verified
manually (path resolution, not the doc-links gate) when the skill file is created.

### 3.6 Driver

Main context or `superpowers:subagent-driven-development` — never a stateless one-shot subagent.
Diagnosing a live failure needs memory of what's already been tried (today's spike → fix → re-test
loop would break across fresh, context-less subagent calls).

## 4. Testing

This is a prose skill file, not code — "testing" it means dry-running it on the next real feature
that touches a generation path, and confirming: (a) the pre-flight check actually fires and would
catch today's stale-branch case if re-run against it (independently confirmed by the review council,
see header); (b) the live-verify matrix produces a real evidence note; (c) the `AGENTS.md` pointer
and skills-table row are where a fresh agent would actually look. No synthetic self-test — the first
real usage is the test, per this project's own "verify third-party runtime behavior empirically"
principle applied to itself.

## 5. Explicitly deferred

- Generalizing this skill (or its pattern) to other repos, e.g. compile-growth-monorepo's own live
  surfaces (n8n pipeline, Supabase, MinIO).
- **Updating `skills/release/SKILL.md` step 4b to delegate to or aggregate per-feature
  `tmp/live-verify/*.md` evidence notes**, rather than the two gates operating independently. The
  council flagged this as a real coherence gap (release step 4b duplicates rather than reuses this
  skill's logic), but modifying the release protocol itself is a separate, higher-risk change
  deserving its own review, not bundled into this skill's first ship.
- Fixing `doc-review/SKILL.md`'s pre-existing, unrelated drift bug (§ header says "step 9", the
  real integration point in `release.md` is step 10) — found incidentally by the council, tracked
  separately, not this spec's concern.
- A further, broader council audit beyond this one — this spec's own council pass already covers
  HLD/LLD/evidence-drift for the skill set as it stands after this ships.

[#174]: https://github.com/ffroliva/gflow-cli/issues/174
