---
name: live-verify
version: "1.0"
description: >
  Two-part gate for gflow-cli feature/fix work. Part 1 (Pre-flight): use when
  starting work on a gflow-cli feature or fix — confirms the checkout
  reflects current develop before investing effort. Part 2 (Live-verify):
  use before claiming gflow-cli work done, especially anything touching a
  generation code path (t2i/i2i/i2v/t2v/r2v) — requires live evidence
  against real Flow, not just offline tests.
---

# `/gflow:live-verify` — Live-verification enforcement

gflow-cli reverse-engineers a blackbox: Google Flow. Offline checks (`ruff`, `pyright`,
unit/BDD tests) verify gflow-cli's own code does what it's supposed to; they cannot verify
Flow still behaves the way it was captured, because Flow is external and changes without
notice (see [#174](https://github.com/ffroliva/gflow-cli/issues/174)). This gate enforces two
things, both evidence-based (no claim without a fresh verification artifact — see the
`superpowers:verification-before-completion` skill):

1. **Part 1 — Pre-flight**, at the *start* of feature/fix work: confirm the checkout reflects
   current `develop` before investing effort.
2. **Part 2 — Live-verify**, *before claiming done* (after `/code-review` and
   `/ponytail:ponytail-review`, before commit/PR): exercise the change against real Flow.

Full design rationale:
[`docs/superpowers/specs/2026-07-19-live-verify-design.md`](../../docs/superpowers/specs/2026-07-19-live-verify-design.md).

---

## Part 1 — Pre-flight

Run before writing any code for a new feature/fix:

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD
git rev-list --count HEAD..origin/develop
git log --oneline -5
```

`git rev-list --count HEAD..origin/develop` is an asymmetric DAG set-difference — it counts
commits `develop` has that the current `HEAD` lacks, regardless of `HEAD`'s own private
unmerged history. This is what catches a genuinely diverged stale branch, not just a
behind-by-fast-forward one.

- **If the count is nonzero:** stop. `git pull` (on `develop`) or rebase/merge (on a feature
  branch) before continuing.
- **If the branch's last real commit looks old relative to recent `develop` activity** (a
  smell for stale WIP — e.g. missing a function/guard that `develop` already has): stop and
  surface it. Don't silently proceed, don't silently switch — name the divergence and ask the
  user how to proceed.
- **A separate sibling checkout is not itself a red flag** — this project's workflow
  routinely uses sibling checkouts for isolated feature branches, and a real feature branch
  is *supposed* to differ from `develop`. The actual signal is "differs from `develop` in a
  way that suggests staleness" (missing something `develop` has), not "differs by adding new
  work on top of it." When in doubt, diff the specific file(s) about to be touched against
  `origin/develop`'s version before assuming they match:

```bash
git diff origin/develop -- <path/to/file>
```

## Part 2 — Live-verify

"Live" means: **drive the real generation commands** (`t2i`, `i2i`, `i2v`, and siblings like
`t2v`/`r2v` where applicable) against a real authenticated Flow account, covering **multiple
variations** of the change — not one happy-path call. A change touching a generation code
path is default-in-scope; skipping this gate requires a named reason, not silence.

**1. Define the live matrix.** Before running anything, name explicitly:
   - Which command(s) does the change touch?
   - Which variations actually exercise it? (E.g. for a mention-resolution fix: a character
     mention, a media mention, an ambiguous-name case, an unresolvable name.)
   - **Which surface(s)?** A capability that ships as both a CLI command and an MCP tool has
     **two live paths, not one** — and the MCP queued path runs different code
     (`mcp/tools.py` → queue payload → `worker/codec.py` → request → daemon owns download and
     recording). A CLI run does not exercise it. Decide per change whether the MCP path needs
     its own live run, and if you skip it, say so with a reason. The cheap version is usually
     enough: one queued MCP call on a credit-free operation proves the payload keys round-trip,
     which is the hop that silently no-ops (#495). Offline parity tests cannot see this —
     they never build a real payload and decode it.

**2. Check the cost tier for each variation — the tier follows the operation, not the
   command family:**
   - Bare entity CRUD with no image generation (`create_entity`, `list_characters`,
     `patch_entity` at the API level; `t2i`/`i2i` themselves) are **credit-free** — run as
     needed to cover the matrix without a separate confirm each time. Still mind WAF/volume
     discipline: don't fire dozens of live calls back-to-back without surfacing it to the
     user first.
   - **Anything that generates real media is costed**, even on an otherwise-free command
     family — e.g. `gflow character create --face-prompt` generates real face/body images
     and costs Imagen credits despite being "character CRUD" in name; `i2v` and other
     video-generation paths are always costed. Always get explicit operator go-ahead before
     running a costed variation. Batch the ask: name what will run and why, once, not one
     confirm per call.

**3. Run each variation, capture evidence per run.** Use the release skill's actual 5-layer
   ledger shape (`skills/release/SKILL.md` §4b), adapted per-field to what the change under
   test produces:

   | Layer | Evidence |
   |---|---|
   | File count | New file(s)/DB row(s) produced by the run |
   | Magic bytes / field value | The specific artifact or field the change is supposed to affect (e.g. a real image's magic bytes, or de-tagged prompt text in the catalog) |
   | Dimensions/shape | For media output: actual dimensions match the requested aspect ratio/model |
   | Structlog invariants | The expected log event fired (e.g. `mention_resolved`, not `mention_unresolved`) |
   | User-confirmable artifact | Real output a human could open and check (image/video file, `size > 1024` bytes) |

   Write this to a lightweight per-feature evidence note at `tmp/live-verify/<feature-slug>.md`
   (gitignored — this is not the full `docs/LIVE_VERIFICATION_vX.Y.Z.md` release ceremony).
   Fold it into the real `LIVE_VERIFICATION` doc when the feature ships in a release.

**4. On pass:** all matrix variations green — proceed to commit.

**5. On fail:** go to Failure-routing below.

## Failure-routing (reproducibility re-test)

**1. Check for a known match first** (costed failures only, to avoid an unnecessary
   re-spend): grep `KNOWN_ISSUES.md` and open GitHub issues for a matching error signature.

```bash
gh issue list --repo ffroliva/gflow-cli --search "<error text>" --state all
```

**2. If no match, re-test once.** Free for `t2i`/`i2i` — just re-run. For a costed failure,
   re-testing needs the same operator confirm as any costed run.

**3. Compare outcomes and route:**

   | Outcome | Route |
   |---|---|
   | Same failure, same code, no known-issue match | **Real bug.** Back to execution — fix it (use `superpowers:systematic-debugging` if the cause isn't obvious). Re-run this gate after the fix. |
   | Different outcome, same code, no changes in between — OR a known-issue match | **External flake.** Do not loop trying to "fix" it. Record it in the evidence note (what failed, that it's not reproducible against unchanged code, link to the matching issue if any). Gate passes-with-caveat for this run. |
   | The failure reveals the *plan's premise* was wrong (not a bug, not a flake) | **Back to planning/design**, not execution. Don't keep patching code against a wrong premise. |

**4. Record every outcome** in the evidence note — passes, fails, and flakes are all
   evidence, not just the final green state.

## Driver

Main context or `superpowers:subagent-driven-development` — never a stateless one-shot
subagent. Diagnosing a live failure needs memory of what's already been tried; a fresh,
context-less subagent call breaks a spike-then-fix-then-retest loop.

## Pipeline Continuation (Next Step Handoff)

Upon completing Live Verification:
1. **Verification 🟢 PASSED:** Proactively announce: **"Live verification passed. Next step: Phase 9 PR Creation & Issue Resolution (`/gflow:issue-resolve <N>`)."**
2. **Verification 🔴 FAILED:** Return to **Phase 6 Task Execution** to resolve the identified transport failure.

## Notes

- This gate does not replace `/gflow:check` (offline gates before commit) or
  `/gflow:doc-review` (release-time doc council) — it fills the gap between them.
- Testing this skill itself means dry-running it on the next real feature that touches a
  generation path — there is no synthetic self-test for a live-Flow gate.
