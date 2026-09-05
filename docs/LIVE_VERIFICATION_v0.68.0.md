# Live verification — v0.68.0 (pre-release evidence, 2026-09-05)

**Feature:** `gflow update` — self-update through the installer that put gflow-cli here
(`src/gflow_cli/update_check.py::run_update`, `src/gflow_cli/cli_update.py`) — and the #479
once-a-day notice rendered as a stderr banner (`src/gflow_cli/cli.py::_print_update_notice`).
PR [#668](https://github.com/ffroliva/gflow-cli/pull/668). The implementation plan and
scenario table were folded into project memory at release time
(`docs/superpowers/memory/windows-running-launcher-blocks-uv-upgrade.md`), per the doc-review
skill; this file keeps the measurements.

This feature touches no Flow transport, so "live" here means **against the real package
managers and the real PyPI index, from the installed `gflow.exe` itself** — the surface a
user actually runs — not against mocks. Nothing in this verification spent credits or
touched a Flow account.

## Method

A wheel was built from the merged branch with the version lowered to **0.60.1**, installed
through each manager into a scratch tool directory (`UV_TOOL_DIR` / `PIPX_HOME`, via
`--find-links` — no `direct_url.json`, so it counts as an index install), the install-time
pin removed from the receipt / metadata, and `gflow update` run **from that install's own
`gflow.exe`**, upgrading it to the real PyPI **0.67.0**. Windows 11 Pro, uv 0.8.16,
pipx 1.8 (`pipx[uv]`).

## Runs

| # | Manager | Command | Exit | Manager exit | Venv after (`gflow --version` through the old launcher) |
|---|---|---|---|---|---|
| 1 | `uv tool` | `gflow update --check --json` | **0** | — | unchanged (0.60.1); JSON `update_available: true`, `command: ["uv","tool","upgrade","gflow-cli"]`, `upgraded: false` |
| 2 | `uv tool` | `gflow update` | **0** | **1** — `Updated gflow-cli v0.60.1 -> v0.67.0` then `failed to copy … gflow.exe … used by another process (os error 32)` | **0.67.0** |
| 3 | `pipx` | `gflow update` | **0** | 0 — `upgraded package gflow-cli from 0.60.1 to 0.67.0` | **0.67.0** |

Run 2's text output, verbatim shape:

```
Upgraded gflow-cli 0.60.1 -> 0.67.0 via uv.
Restart any running `gflow serve` / MCP server to pick it up.
`uv tool upgrade gflow-cli` exited 1 AFTER installing 0.67.0 — see its output above. On Windows
this is the running `gflow.exe` launcher, which cannot be replaced while in use; it keeps
working, and the next update from another shell refreshes it.
```

## Five-layer ledger

| Layer | Evidence |
|---|---|
| 1 · Artifact count | One upgraded venv per manager (2/2); one `--check` report that changed nothing (`uv-receipt.toml` and `Lib/site-packages` untouched, verified by re-running `--version`) |
| 2 · Magic / identity | `gflow_cli-0.67.0.dist-info` present in both venvs after the run; `gflow_cli-0.60.1.dist-info` gone |
| 3 · Shape | `--check --json` document carries exactly `{status, installed, latest, update_available, installer, command, upgraded, notes}`; the notes tuple holds the one launcher note on run 2 and nothing on run 3 |
| 4 · Structlog invariants | `update.installer_detected installer=uv` / `=pipx`, `update.command_finished returncode=1 after=0.67.0` (run 2) and `returncode=0 after=0.67.0` (run 3); the piped-stderr form of the #479 notice printed as **one line** before the command ran (`A newer gflow-cli is available: 0.67.0 (installed 0.60.1). Run \`gflow update\` to upgrade. …`) |
| 5 · User-confirmable | `gflow --version` → `gflow, version 0.67.0` from the very launcher that was running during the upgrade |

## What the runs changed in the code

1. **A running uv trampoline can be neither overwritten nor renamed.** `os.replace` and
   `mv` on a running `gflow.exe` both fail (error 32 / "Device or resource busy", measured
   against `gflow serve`). The first design — rename the launcher aside, restore on failure
   — moved the idle `flow.exe` and never the one that mattered; it was deleted.
2. **The manager's exit code is not the outcome.** uv had already installed the wheel when
   it exited 1. `run_update` re-reads `importlib.metadata.version("gflow-cli")` from a fresh
   interpreter after the run and reports that (run 2: exit 0 with a note; a zero exit with an
   unmoved version is exit 11).
3. `uv tool upgrade` honours an install-time pin silently, and uv's wheel cache is keyed by
   filename — both bit the test harness before they could bite a user, and both are recorded
   in the published memory slug.

## Banner

- Piped stderr (subprocess capture): one plain line — layer 4 above, measured on runs 2/3.
- Terminal: the bordered panel was rendered through the same `Panel` call with a forced
  terminal console and read back (title `Update available`, version line, `gflow update`
  line, release-notes URL, silence hint); the `sys.stderr.isatty()` gate is unit-tested on
  both branches (`tests/test_cli_update.py`). A real interactive-terminal screenshot was
  **not** taken this cycle.

## #670 — migrated submit-enable race (PR #672, @ChandraLiuswanto), verified on the flagged maintainer account

Folded into v0.68.0 from develop after the release branch was cut. The fix changes a real
generation path (`migrated_composer.submit_and_observe` now polls `is_enabled()` for up to
5 s after `insert_text`), so it was run for real on this machine at PR head `e3c3220`
(0.67.0 + the PR diff, built in a detached worktree), one veo-lite clip:

| # | Command | Profile / cohort | Result |
|---|---|---|---|
| 1 | `gflow video t2v "a paper crane unfolding on a wooden desk, soft morning light" --model veo-lite --aspect 16:9 --project c5550ed7-… --json` | ffroliva (flagged, en-GB), route `migrated` under `auto` | **exit 0, 60 s wall**, `MEDIA_GENERATION_STATUS_SUCCESSFUL` |

Five-layer ledger: one new mp4 (`44f890db-….mp4`); `ftyp` at offset 4; **1,578,650 B** =
the size the `jwpduf` / `as29s` records reported, ffprobe `h264 1280x720 8.000 s`; structlog
`migrated.dispatch → navigate → editor_ready → settings_applied → prompt_typed (17:51:15.810)
→ submit_clicked (17:51:16.165) → submit_observed (YhhmEf) → status ×7 → status 3 (bytes) →
as29s → download`, no `error_raised`; the media id and workflow id are in the run's
`result.json`. `prompt_typed → submit_clicked` = **355 ms**: the new poll ran through at least
one disabled read. On stock 0.67.0 this account submitted at ~200 ms and never hit the race,
which is why the v0.67.0 ledger was green while the reporter's account failed every run. A
first attempt with `--duration 8` on veo-lite aborted pre-submit with exit 11 at $0 — this
cohort renders no duration row for veo-lite (the v0.67.0 ledger's #650 finding, unrelated).

NOT verified: the reporter's account (only they can run it; their patched-wheel run in the
PR body covers it), an unmigrated account (none remains on this machine), and the MCP queued
path (no payload key changed; it reaches the identical `submit_and_observe`).

## Pre-tag gates (filled before signing)

| Gate | Result |
|---|---|
| Impeccable Routine on `develop` at 9525cb5 (hygiene, doc links, website PII, website mirror, council memory, ruff, format, pyright) | all green |
| Offline pytest (`not live and not e2e and not smoke`) on 9525cb5 | 3849 passed, 24 skipped, 1 failed in 6:54 — `tests/features/test_incident_diagnostics_steps.py::test_remote_errors_do_not_expose_local_incident_paths`, a file untouched since July that passes in isolation (1.25 s); a second session was running pytest in the same checkout during the sweep. The release PR's CI run is the authority for the full suite |
| Branch council on PR #668 (9 dimensions) | D1/D4/D5/D9 YELLOW → all must-fix applied in a7b95ff; D3/D8/D14/D15 GREEN; D2 did not return |
| PR #668 CI (15 checks incl. SonarCloud) | green on b8b59a3 |
| Manager runs (this file) | uv: exit 0 + note; pipx: exit 0 |
| `/gflow:doc-review` mechanical pass (sections 1–7) | PASS — version refs, INDEX cue, evidence file, doc links, website PII + mirror, skill files, CHANGELOG footer, memory index |
| `/gflow:doc-review` council (3 auditors) | Completeness YELLOW, Cross-reference YELLOW, Drift GREEN. 0 Tier 1. Tier 2 fixed in aedeaaa (llms.txt names `gflow update`; ARCHITECTURE inventory; USER_GUIDE upgrade hop; CI / cache-refresh wording; CONTRIBUTING plan-consolidation note) and in the follow-up doc commit (five-command gate lists in DEVELOPMENT.md and RELEASE.md aligned to the nine; `uv.lock` in RELEASE.md's bump list; NOT-verified lists aligned; CLAUDE.md INDEX size). Tier 3 deferred: `gflow doctor` absent from llms.txt before this pass (now added), verdict-name abbreviations in CONTRIBUTING. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only) |

## Post-tag evidence

| Item | Evidence |
|---|---|
| Tag | `v0.68.0` on `16ed7e2` (release branch head after folding #672), SSH-signed, `git tag -v` → "Good git signature" |
| Release workflow | https://github.com/ffroliva/gflow-cli/actions/runs/33984171793 — completed, success |
| GitHub Release | https://github.com/ffroliva/gflow-cli/releases/tag/v0.68.0 — published 2026-09-05T18:29:12Z, not a prerelease, 5 assets |
| PyPI | `pip index versions gflow-cli` → `gflow-cli (0.68.0)` |
| Release PR | #674 `chore/release-v0.68.0 → main`, 16 checks passing at the tagged head (SonarCloud included) |

Note for the next cycle: the tag was first created on 14f5742, then deleted locally and
re-signed on 16ed7e2 after PR #672 landed on develop during the doc-review pass. Nothing
had been pushed in between, so no published tag ever moved.

## NOT verified this cycle (recorded, not omitted)

- **Plain-venv `pip` on Windows** — no venv on this machine runs `gflow.exe` from a pip
  install; pip removes `Scripts/gflow.exe` and is likely to hit the same lock. The
  `uv venv`-without-`pip` refusal is unit-tested only.
- **macOS / Linux for any manager** — POSIX allows replacing a running binary, so the
  manager should exit 0 there; same code path, same venv-is-truth check. LIKELY, not
  CONFIRMED.
- **A real interactive-terminal screenshot of the panel** — rendered through a forced
  terminal console and read back (see "Banner" above), not photographed on a live tty.
- **A real newer release on PyPI** for the banner from a normal install — 0.68.0 is the
  first release after the change, so the first genuine banner a user sees will be for
  0.68.1 or later. The #479 mechanism itself was live-verified at v0.56.0.
