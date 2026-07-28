---
name: doc-review
version: "1.0"
description: >
  Use before cutting any gflow-cli release or after a major documentation change — systematic council-driven audit that combines a mechanical 7-section checklist with a 3-agent parallel review (completeness / cross-reference / drift). Produces a consensus verdict and a concrete fix list.
---

# `/gflow:doc-review` — Documentation Review Gate

Run this gate before cutting a release. Two phases:

1. **Mechanical checks** (sections 1–7 below) — fast, scripted, deterministic. Run inline.
2. **LLM council audit** (section 8) — 3 parallel review subagents, then consensus synthesis. The mechanical pass catches stale strings and dead links; the council catches drift between docs, fictional claims, and gaps a human reader would notice.

Every item reports **PASS / UPDATED / WARN / FAIL**. Any **FAIL** or any **RED** council verdict blocks the release.

> **Provenance:** the council protocol below was validated on the v0.8.1 docs refresh release (2026-05-23). The audit found 11 fixable issues that the mechanical checklist alone missed (e.g., the `experimental/` subpackage fiction in 3 files, the `gflow video i2v` non-working hero snippet in README). Keep the council step — it pays for itself.

---

## 0 · Pre-flight

- Confirm you are on the release branch (`release/vX.Y.Z` or `hotfix/...`), not on `develop` or `main`.
- If a release spec exists at `docs/superpowers/specs/YYYY-MM-DD-*-design.md`, identify it now — the council auditors will use it as the ground truth for "what was supposed to ship."
- If there is no spec, that is also fine — the council will work from the docs alone, but flag the absence as a process risk.

---

## 1 · Version references

Check these files for stale version strings (old numbers, outdated status labels):

| File | What to verify |
|---|---|
| `README.md` | Status badge, "Project status" section, install example |
| `AGENTS.md` | Module list, exit-code range, dev-environment commands |
| `llms.txt` | Header summary (Python version, stable surface claims) |
| `CLAUDE.md` | "Active phase" pointer if present |
| `PLAN.md` | Phase status (`IN PROGRESS` / `DONE`) and version annotations |
| `KNOWN_ISSUES.md` | `Open` entries resolved by this release → move to `Resolved` with evidence |
| `CHANGELOG.md` | `[Unreleased]` empty; link footer matches new version |
| `pyproject.toml` + `src/gflow_cli/__init__.py` | Both set to the new version |
| `docs/PROJECT_STATUS.md` | "Current release" subsection + milestone-history row for this version |

Quick grep:

```bash
grep -rn "v[0-9]\+\.[0-9]" README.md AGENTS.md llms.txt CLAUDE.md PLAN.md KNOWN_ISSUES.md CHANGELOG.md pyproject.toml docs/PROJECT_STATUS.md
```

Historical references in `CHANGELOG.md`, `docs/LIVE_VERIFICATION_v*.md`, and milestone-history rows in `docs/PROJECT_STATUS.md` are allowed.

---

## 2 · `docs/INDEX.md` completeness

Every `.md` in `docs/` needs an entry; every entry must point to a real file. AGENTS.md and llms.txt also need rows since they are agent-facing entry points.

```bash
# Files in docs/ missing from INDEX.md
for f in docs/*.md; do
  grep -q "$(basename "$f")" docs/INDEX.md || echo "MISSING from INDEX: $f"
done

# Entries in INDEX.md pointing to deleted files
grep -o 'docs/[^)]*\.md' docs/INDEX.md | while read path; do
  [ -f "$path" ] || echo "DEAD LINK in INDEX: $path"
done

# AGENTS.md, llms.txt rows present?
grep -E "AGENTS\.md|llms\.txt" docs/INDEX.md || echo "MISSING: agent-facing root files row"
```

---

## 3 · Per-release evidence file

After each release a `docs/LIVE_VERIFICATION_vX.Y.Z.md` must exist.

- Latest version has one (create stub if live run already documented elsewhere).
- `docs/INDEX.md` "latest release" cue points to it.
- Previous versions' files are preserved (historical record — never delete).
- File contains both a **Pre-tag gates** section (filled before signing) and a **Post-tag evidence** section (filled after publish).

---

## 4 · Doc-link validation

Run the link checker:

```powershell
$env:PYTHONUTF8=1
uv run python scripts/ci/check_doc_links.py
```

Expected: `All links resolved across N files.` (exit 0). Broken links must be fixed in source docs before continuing — do not edit the script to ignore them.

If `scripts/ci/check_doc_links.py` is absent, create it from the v0.8.1 plan (it is a 30-line stdlib-only Python script).

---

## 4b · Published website (`website/`) — part of the documentation work, not a follow-up

`website/` is the mkdocs-material site published to GitHub Pages, and
`website/docs/` is an **anonymized copy** of canonical `docs/`. Treat updating
it as part of the same change that touched the docs: a documentation uplift is
not done until the published surface reflects it. The mirror has drifted
silently for months before (PR #362 shipped a PII leak; content lagged
canonical for whole releases), which is why all three gates below exist.

**1. PII gate (the CI check — a leak fails the build):**

```powershell
$env:PYTHONUTF8=1
uv run python scripts/ci/check_website_docs_pii.py
```

Expected: `No private identifiers found across N published website/docs files.`

**2. Content-drift + nav check — the mirror is generated from canonical by
`scripts/ci/generate_website_docs.py` (the anonymization map is data in that
script). Verify it is in sync (this is the same check CI runs):**

```powershell
$env:PYTHONUTF8=1
uv run python scripts/ci/generate_website_docs.py --check
```

Expected: `website/docs mirror in sync (N files), nav complete.`

- Any `DRIFT:` line is a **FAIL** — a canonical doc changed and the mirror was
  not regenerated. Fix it:

  ```bash
  uv run python scripts/ci/generate_website_docs.py   # regenerate, then stage website/docs/
  ```

- Any `NAV-ORPHAN:` line is a **FAIL** — the page is published but no `nav:`
  entry in `website/mkdocs.yml` points at it, so it exists on the site only for
  whoever guesses the URL. Mirroring a doc and wiring it into the nav are two
  separate acts; add the entry under the right nav section and re-run.

**3. New canonical doc → decide explicitly whether it is published.** Not every
doc belongs on the site (release evidence, recon specs, and plans generally do
not). If it should be published: create the file under `website/docs/`, add its
`nav:` entry in `website/mkdocs.yml`, and the generator maintains the content
thereafter. If it should not, say so in the review so the omission is a
decision rather than an oversight.

`CHANGELOG.md` and the bespoke site pages (`index.md`, `agents.md`,
`installation.md`, `onboarding*.md`) are intentionally NOT generated — the
generator's `WEBSITE_ONLY` set excludes them.

---

## 4c · Code↔docs parity (git-driven — the highest-value lens)

The mechanical checks and the council's drift auditor read *from the docs
outward* ("does this doc claim match the code?"). They do NOT reliably catch
the inverse — **shipped work that no doc mentions at all** — because no string
sweep notices an *absent* doc. This lens (adapted from the
`auditing-documentation` skill's dimension 3) reads *from the code outward*.

```bash
# Everything that shipped since the last release tag (or origin/develop on a feature branch).
git log --oneline "$(git describe --tags --abbrev=0)"..HEAD
git diff --stat "$(git describe --tags --abbrev=0)"..HEAD
```

For each shipped surface — new CLI flags/env vars, new modules under
`src/gflow_cli/`, new operational behavior — name the doc that covers it, and
assign a coverage grade: **D0** (nothing) · **D1** (docstrings only) · **D2**
(one line in INDEX/README) · **D3** (dedicated how-to-operate doc) · **D4** (D3
+ troubleshooting + linked from an entrypoint). Anything operator-facing must
be **D3+**; a new env var or command at **D0/D1** is a release-blocking finding.
List every surface below D3 with its grade and the doc it needs.

---

## 5 · Skill files (`skills/` + `.claude/commands/gflow/`)

Canonical skill CONTENT lives in `skills/<name>/SKILL.md`; `.claude/commands/gflow/`
holds thin wrappers. Scan BOTH for stale phase/version references:

Scan all skills for stale phase/version references:

```bash
grep -rn "v[0-9]\+\.[0-9]\|Phase [A-Z0-9]" skills/ .claude/commands/gflow/
```

Also confirm the process skills stay in step with infra changes — e.g. when a
new CI gate or published surface lands (`scripts/ci/*`, the `website/docs/`
mirror), the skills that should run it (`check`, `doc-review`, `release`) name it.

Update in the release prep commit if any references are wrong. The `release.md` skill's step list and the `plan.md` skill's phase pointers age fastest.

---

## 6 · CHANGELOG link footer

```bash
tail -10 CHANGELOG.md
```

Must read: `[Unreleased]: …compare/vNEW_VERSION…HEAD` and each prior `[X.Y.Z]: …compare/vPREV…vCURR`.

---

## 7 · Memory files

Check `C:\Users\ffrol\.claude\projects\C--development-github-gflow-cli\memory\`:

| File | What to verify |
|---|---|
| `MEMORY.md` | All listed files exist; no dangling entries |
| `phase-b-followups.md` | Items shipped in this release marked done |
| `video-generation-spec.md` | PR/status accurate |
| `image-generation-401-next.md` | Resolution status and evidence pointer still valid |
| `release-signing.md` | Procedure section matches what was actually done |
| `release-back-merge-gap-recovery.md` | Still relevant; gap from any prior release recorded |

```bash
cd "C:\Users\ffrol\.claude\projects\C--development-github-gflow-cli\memory"
grep -o '\[.*\]([^)]*\.md)' MEMORY.md | grep -o '([^)]*)' | tr -d '()' | while read f; do
  [ -f "$f" ] || echo "DEAD LINK: $f"
done
```

---

## 8 · LLM council audit (NEW — required for every release ≥ v0.8.1)

The mechanical checks above catch stale strings, dead links, and missing files. They do **not** catch:

- A doc claiming a subpackage exists when it does not (no string sweep would notice).
- Two docs covering the same topic with subtly different facts.
- A working-looking hero snippet that points to a stub command (`gflow video i2v` returned "not yet available" but appeared as a production loop above the fold in v0.8.1).
- A user-facing onboarding gap that a fresh reader would hit immediately.

The council does. Dispatch three review subagents **in parallel** (they are read-only and independent). Each writes its verdict to `tmp/council/`.

### Setup

```bash
mkdir -p tmp/council
```

### Auditor 1 — Completeness

Role: as a fresh user / fresh AI agent, **what do you need that isn't there?**

Audit:
- `README.md`, `AGENTS.md`, `llms.txt`, `CLAUDE.md`, `docs/INDEX.md`, `docs/PROJECT_STATUS.md`
- `docs/ARCHITECTURE.md` (especially any new sections)
- `docs/LIVE_VERIFICATION_v*.md` (latest)
- `CHANGELOG.md` (just the new entry)
- `pyproject.toml`
- The release spec at `docs/superpowers/specs/` (if present)
- The implementation plan at `docs/superpowers/plans/` (if present)

Output to `tmp/council/01-completeness.md` with verdict GREEN / YELLOW / RED and four sections:
1. Critical gaps (block release)
2. Important gaps (fix before next release)
3. Nice-to-haves (track for next version)
4. What's done well (preserve)

### Auditor 2 — Cross-reference & alignment

Role: do the docs **agree with each other**? Where two files cover the same topic, do they say the same thing?

Check:
- Install instructions, auth flow, headed-browser story, version number, Python requirement — all match exactly?
- Internal `[X](path)` links — files exist AND claims at the link target equivalent to the linking-doc's claim?
- Routing chain (README → AGENTS.md → CLAUDE.md → docs/INDEX.md) — consistent? Any contradictions?
- CHANGELOG vs PROJECT_STATUS — milestone history matches changelog entries?
- Spec vs implementation — does the shipped state match the design spec?

Output to `tmp/council/02-crossref.md` with verdict + four sections (inconsistencies / drift / broken anchors / well-aligned).

### Auditor 3 — Drift & staleness

Role: does the documentation match the actual code and project state? **Where are claims unverifiable, contradicted by reality, or referencing things that no longer exist?**

Check empirically (every claim needs `ls` / `grep` / `cat` evidence cited inline):
- Version claims — `pyproject.toml`, `__init__.py`, every "v0.8.1 — alpha" statement agree?
- Module / file claims — every `src/gflow_cli/{...}` module list verified against the actual source tree?
- Subpackage claims (e.g., `experimental/`) — do those directories exist?
- CLI command claims in hero snippets — is the subcommand really implemented? Does it actually do what the example shows, or is it a stub?
- Exit-code claims — verify the range against `errors.py::EXIT_CODE_MAP`.
- Skill files, GIF assets, slash-command files — exist at the paths claimed?

Output to `tmp/council/03-drift.md` with verdict + three sections (fictional claims / stale references / verified-real).

### Consensus

After all three reports return:

1. Read all three. List every distinct finding.
2. Tier the findings:
   - **Tier 1 (release-blocking)** — any RED verdict, any "block release" finding, any fictional claim that ships to PyPI.
   - **Tier 2 (important polish)** — drift between related docs, minor mismatches, missing-but-recoverable references.
   - **Tier 3 (track for later)** — nice-to-haves, prose improvements, deferred features.
3. Apply Tier 1 fixes immediately (block the release until done).
4. Apply Tier 2 fixes if the release schedule permits, otherwise open issues.
5. Tier 3 → backlog (memory `phase-X-followups.md` or a new GitHub issue).

Synthesize a single fix-plan comment under the relevant `LIVE_VERIFICATION_vX.Y.Z.md` `Pre-tag gates` entry for `/gflow:doc-review`:

> _Council verdict: <GREEN|YELLOW|RED> across all 3 auditors. <N> findings; <M> Tier 1 fixed in commit <SHA>, <K> Tier 2 fixed in commit <SHA2>, <J> Tier 3 deferred to backlog. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

If any auditor returns RED, **stop and escalate to the user**. Do not apply fixes blindly to a RED finding — it usually means the doc is wrong about the code, the code is wrong about the doc, or both.

---

## Output format

After each section write one line:

```
[0] Pre-flight        — PASS (release spec at docs/superpowers/specs/YYYY-MM-DD-*.md)
[1] Version refs      — PASS
[2] INDEX completeness — UPDATED (added AGENTS.md row)
[3] Evidence file     — PASS
[4] Doc-link check    — PASS (9 files, 0 broken)
[5] Skill files       — UPDATED (refreshed /gflow:plan phase pointer)
[6] CHANGELOG footer  — PASS
[7] Memory files      — WARN: phase-b-followups.md has shipped items not yet ticked
[8] Council audit     — YELLOW (11 findings, all fixed in commit abc1234)
```

Then list every **UPDATED** change, every **WARN** / **FAIL** finding, and the council fix list for the user to review.

---

## Integration with `/gflow:release`

This skill is invoked at **step 9** of `/gflow:release` (between "review commands for staleness" and "commit the release prep"). The mechanical pass (sections 1–7) is the same as before; the council audit (section 8) is the new requirement and runs after the mechanical pass succeeds.

All discovered fixes are folded into the release prep commit unless genuinely unrelated to the release.

---

## Post-release: memory consolidation

After the release ships and the back-merge completes, the **release spec** and **implementation plan** under `docs/superpowers/{specs,plans}/` are project-management artifacts, not user-facing docs. The durable knowledge they contain (patterns learned, governance rules, architecture decisions) should be extracted into project memory at `~/.claude/projects/<...>/memory/`, then the spec/plan files **deleted from the repo** in a final cleanup commit.

This keeps the public-facing `docs/` tree focused on what users and contributors need, while the planning artifacts live on as memory entries usable by future Claude Code sessions. See the `release-back-merge-gap-recovery` memory and the v0.8.1 release commit history for an example.
