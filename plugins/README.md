# `plugins/` — generated plugin payloads

**Do not hand-edit anything under `plugins/gflow/skills/`.** Those files are generated from the
canonical `skills/<name>/SKILL.md` tree by:

```bash
python scripts/ci/generate_plugin_skills.py            # regenerate
python scripts/ci/generate_plugin_skills.py --check    # CI gate: fail on any drift
```

CI runs the `--check` form, so a hand-edit here turns the build red rather than quietly forking
the protocol. This mirrors what `generate_website_docs.py --check` already does for
`website/docs/`.

## Why a copy exists at all

AGENTS.md says vendor directories hold thin wrappers and never protocol content. A Claude Code
plugin cannot follow a pointer — the marketplace installs a *directory*, so the skills it ships
must physically live under the plugin root. The rule's purpose is to stop two copies drifting,
and the generator plus the `--check` gate is how that purpose is met here.

## Why only two skills

`skills/` holds eighteen, and most of them drive *this repo's* development lifecycle — `release`,
`check`, `pr-council-review`, `sonar`, `doc-review`, `issue-resolve`. Shipping those to a user who
just wants to drive Google Flow is noise at best and misdirection at worst: an agent that reads
`release/SKILL.md` will happily try to cut a release of someone else's project.

So the plugin ships exactly `gflow-cli` and `video-production`. The generator's `SHIPPED` tuple is
the single place that decision lives, and `--check` fails on any directory under the plugin that
it did not put there — including an empty one left behind by a rename.

## What the generator changes

Only links that climb out of the repo. `](../../KNOWN_ISSUES.md)` resolves inside the repo and
points at nothing inside an installed plugin, so it becomes an absolute GitHub URL. Links within a
skill (`](composition.md)`) and between the two shipped skills (`](../gflow-cli/SKILL.md)` ) still
resolve under the plugin's own layout and are left exactly as they are.

## The other two channels

`.codex-plugin/plugin.json` and `.agents/plugins/marketplace.json` point at this same curated
payload. They previously pointed at `./skills/` and `./` respectively, which shipped all eighteen
skills to Codex and ChatGPT desktop users. `tests/test_plugin_manifests.py` pins all three.
