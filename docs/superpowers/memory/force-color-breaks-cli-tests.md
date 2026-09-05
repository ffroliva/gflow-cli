---
name: force-color-breaks-cli-tests
description: FORCE_COLOR in the agent shell makes ~26 CLI tests fail on ANSI codes; run pytest with `env -u FORCE_COLOR` before believing a red suite
---

`FORCE_COLOR=3` is set in the Claude Code shell on this host. Rich then emits
ANSI escapes even under `CliRunner`, and every CLI test asserting on plain
substrings fails:

```
assert 'Project ID: proj-100' in '\x1b[1mProject ID:\x1b[0m ...'
```

Observed 2026-07-27: **26 failures** across `tests/test_cli_project.py` and
siblings, in a tree that was otherwise green. `env -u FORCE_COLOR uv run python
-m pytest -q` on the identical commit: **2794 passed**.

**Why:** it looks exactly like a real regression — the failures cluster in the
files touching whatever you just changed, so the instinct is to blame the diff.
I nearly attributed them to a back-merge that had not touched any of those files.

**How to apply:** run the suite as `env -u FORCE_COLOR uv run python -m pytest -q`.
If a run comes back red with ANSI escapes visible in the assertion diff, re-run
with the var stripped BEFORE investigating the code. CI is unaffected (no
FORCE_COLOR there), so a green CI plus a red local run on the same commit is the
signature of this, not of a flake.

Cheap discriminator: `git diff --name-only <base> HEAD` — if the failing test
files are not in that list, suspect the environment first.
