# Council memory

Durable review knowledge the `/gflow:` skills route to by name. One fact per
file; the filename **is** the address.

Routing, and why this lives in the repo rather than a maintainer's machine, are
explained once in `skills/pr-council-review/SKILL.md` (the note above the
Dimension → Slugs table). This file covers only what you need to add or change
one.

## Two stores, and which one wins

These files are a **published subset** of the maintainer's private Claude Code
memory, not a mirror of it. The published copy is **canonical for review**: a
fact that lives in both is edited here first. The private store may then be
resynced from it, never the reverse.

Only review-relevant facts are published. Session handoffs, environment notes,
and anything the gate classes as private stay upstream.

## What the gate does and does not require

- **Skill citations are binding.** A `[[…]]` in `skills/**/SKILL.md` with no
  matching file here fails CI, and a file here that no skill cites fails too.
- **Links *between* memory files are not.** Memory convention is to link
  liberally, so an unresolved link marks something worth writing later. Those
  are reported as `INFO` and never fail.
- **Private identifiers are refused** — session ids, addresses, real names,
  device names, local checkout paths, tokens, IPs. Note that names already
  published across the canonical `docs/` tree (the `ffroliva` GitHub handle, the
  `denon82` profile in worked examples) are *not* scrubbed here; re-redacting
  them would imply a privacy property the repo does not have. The website mirror
  holds a stricter bar and has its own guard.

## Adding or changing a file

1. Write it as `<slug>.md`, with frontmatter `name:` exactly matching the filename.
2. **Cite it** from the dimension that needs it, in the Dimension → Slugs table.
3. Run `python scripts/ci/check_council_memory.py`.

Step 2 is the one people skip. It is enforced because an uncited file is
unroutable — no dimension will ever open it — so it is dead weight that ages
badly with nobody watching.
