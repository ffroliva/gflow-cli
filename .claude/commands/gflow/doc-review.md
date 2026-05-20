---
description: Use before cutting any gflow-cli release or after a major feature lands — systematic audit of version refs, INDEX completeness, per-release evidence files, CHANGELOG footer, skill files, and memory records for staleness.
---

# `/gflow:doc-review` — Documentation Review Gate

Run this gate before cutting a release. Every item reports **PASS / UPDATED / WARN / FAIL**. Any **FAIL** blocks the release.

---

## 1. Version references

Check these files for stale version strings (old numbers, outdated status labels):

| File | What to verify |
|---|---|
| `README.md` | Status badge, "What's new" section, install example, milestone table |
| `CLAUDE.md` | `Active phase` — version, PyPI status, active backlog pointer |
| `PLAN.md` | Phase status (`IN PROGRESS` / `DONE`) and version annotations |
| `KNOWN_ISSUES.md` | `Open` entries resolved by this release → move to `Resolved` with evidence |
| `CHANGELOG.md` | `[Unreleased]` empty; link footer matches new version |
| `pyproject.toml` + `src/gflow_cli/__init__.py` | Both set to the new version |

Quick grep to spot stragglers:

```bash
grep -rn "v[0-9]\+\.[0-9]" README.md CLAUDE.md PLAN.md KNOWN_ISSUES.md CHANGELOG.md pyproject.toml
```

---

## 2. `docs/INDEX.md` completeness

Every `.md` in `docs/` needs an entry; every entry must point to a real file.

```bash
# files in docs/ missing from INDEX.md
for f in docs/*.md; do
  grep -q "$(basename "$f")" docs/INDEX.md || echo "MISSING from INDEX: $f"
done

# entries in INDEX.md pointing to deleted files
grep -o 'docs/[^)]*\.md' docs/INDEX.md | while read path; do
  [ -f "$path" ] || echo "DEAD LINK in INDEX: $path"
done
```

---

## 3. Per-release evidence file

After each release a `docs/LIVE_VERIFICATION_vX.Y.Z.md` must exist.

- Latest version has one (create stub if live run already documented elsewhere)
- `docs/INDEX.md` has an entry for it
- Previous versions' files are preserved (historical record — never delete)

---

## 4. `.claude/commands/gflow/` skill files

Scan all skills for stale phase/version references:

```bash
grep -rn "v[0-9]\+\.[0-9]\|Phase [A-Z0-9]" .claude/commands/gflow/
```

Update in the release prep commit if any references are wrong.

---

## 5. CHANGELOG link footer

```bash
# Verify [Unreleased] compares from the new version, not an older one
tail -10 CHANGELOG.md
```

Must read: `[Unreleased]: …compare/vNEW_VERSION…HEAD`

---

## 6. Memory files

Check `C:\Users\ffrol\.claude\projects\C--development-github-gflow-cli\memory\`:

| File | What to verify |
|---|---|
| `MEMORY.md` | All listed files exist; no dangling entries |
| `phase-b-followups.md` | Items shipped in this release marked done |
| `video-generation-spec.md` | PR/status accurate |
| `image-generation-401-next.md` | Resolution status and evidence pointer still valid |
| `release-signing.md` | Procedure section matches what was actually done |

```bash
# Check MEMORY.md index integrity
cd "C:\Users\ffrol\.claude\projects\C--development-github-gflow-cli\memory"
grep -o '\[.*\]([^)]*\.md)' MEMORY.md | grep -o '([^)]*)' | tr -d '()' | while read f; do
  [ -f "$f" ] || echo "DEAD LINK: $f"
done
```

---

## 7. Stale file cleanup (optional)

Flag (do not delete without user confirmation):

- Temporary debugging artifacts in `docs/` or repo root that were never meant to be permanent
- Draft specs whose work has fully shipped (move to `docs/archive/` or add "SHIPPED" header)
- `LIVE_VERIFICATION` files older than two releases that could be archived

Always ask the user before removing or archiving.

---

## Output format

After each section write one line:

```
[1] Version refs      — PASS
[2] INDEX completeness — UPDATED (added LIVE_VERIFICATION_v0.7.0.md entry)
[3] Evidence file     — PASS
[4] Skill files       — UPDATED (removed stale "Phase A in progress" in plan.md)
[5] CHANGELOG footer  — PASS
[6] Memory files      — WARN: release-signing.md procedure section may need update — check with user
[7] Stale files       — PASS
```

Then list every **UPDATED** change and every **WARN** / **FAIL** finding for the user to review.

---

## Integration with `/gflow:release`

This skill is invoked at **step 9** of `/gflow:release` (between "review commands for staleness" and "commit the release prep"). All discovered fixes are folded into the release prep commit unless genuinely unrelated to the release.
