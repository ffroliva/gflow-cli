# Council memory

Durable review knowledge the `/gflow:` skills route to by name. One fact per
file; the filename **is** the address.

## How it is used

A council dimension fires and opens exactly the files its row in the
`skills/pr-council-review/SKILL.md` **Dimension → Slugs** table names:
`[[some-slug]]` resolves to `some-slug.md` in this directory. No searching, no
judgement call about what is relevant — which is the point. Nothing here is read
unless a dimension asks for it, so adding files does not make reviews noisier.

## Why it lives in the repo

Claude Code keys its own memory by working-directory path, so a fresh clone
starts with an empty namespace. The sandboxed autonomous triage runs on a
different machine from any maintainer's laptop, and on PR #650 that gap produced
a review asserting "no memory entry contradicts this PR" from a directory it
could not read — while the maintainer's local store recorded that exact PR as
rejected. Files here ship with the tree, so every agent that can read the repo
can read them, with no sync to drift.

## What belongs here

A **published subset** of the maintainer's working memory: facts a reviewer
needs, with private identifiers stripped. Session handoffs, environment notes,
and anything naming an account, profile, project UUID, or local path stay in the
private store — `scripts/ci/check_council_memory.py` rejects those patterns.

## Adding or changing a file

1. Write it as `<slug>.md` with `name:` in the frontmatter matching the filename.
2. **Cite it** from the dimension that needs it, in the Dimension → Slugs table.
3. Run `python scripts/ci/check_council_memory.py`.

The gate enforces the round trip both ways: a citation with no file fails, and a
file no dimension cites fails. The second half is deliberate — an uncited file is
unroutable, so it is dead weight that ages badly without anyone noticing.
