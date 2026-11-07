# Live verification — v0.95.0

Release ledger for v0.95.0: weekly usage reports after a one-time notice, EU/EEA consent (#1164,
PR #1166), and the byte-exact `gflow mcp setup` backup (PR #1170).

## Environment

| | |
|---|---|
| Profile | `ffroliva` (real-browser Chrome strategy) |
| Host Flow served | `flow.google.com` |
| Entrypoint | the real `gflow` CLI (`uv run gflow` in the release worktree); the e2e rows drive it through a pty, piped subprocesses and an MCP stdio client |
| OS | Windows 11 |
| Tree | `chore/release-v0.95.0` (cut from `5947d5f4`, the PR #1166 merge head) |
| Telemetry endpoint | `https://gflow-telemetry.ffroliva.workers.dev/v1/report` (Cloudflare Worker + D1 `gflow-telemetry`, EU) |
| Veo credits spent | 1 lite clip |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Plain text-to-video on the release tree (step 0) | `gflow video t2v "a ceramic cup on a wooden table, soft morning light" --profile ffroliva --model veo-lite` | ✅ exit 0 in 1m51s, 2,299,200 bytes, `ftypisom`, 7.93s, 720x1280 h264 + aac, 24 fps | 1 lite clip |
| Weekly upload to the live endpoint (#1164) | `tests/e2e/test_telemetry_upload_e2e.py` on the release tree | ✅ 8 passed: notice once in a real tty and nothing sent that run; no notice when piped; a real POST lands in D1 from a command that exits at once; EU region uploads only after `gflow telemetry on`; `telemetry off` and `DO_NOT_TRACK=1` stop the next upload; a 410 from a local Worker stops the client for good | $0 |
| Local counters still behave (v0.94.0 surface) | `tests/e2e/test_telemetry_local_e2e.py` on the release tree | ✅ 6 passed: commands add rows; `DO_NOT_TRACK` and `GFLOW_CLI_TELEMETRY=0` add nothing; crash fingerprint without message or path; MCP stdio client recorded with its tool and client name; no network socket from the local path | $0 |
| Production database left clean | `wrangler d1 execute gflow-telemetry --remote` row counts before and after every live run | ✅ `totals` 0 / `seen` 0 before and after (the tests subtract exactly their own contribution) | $0 |
| `gflow mcp setup` backup is byte-exact (PR #1170) | `gflow mcp setup --target cursor` against a seeded `~/.cursor/mcp.json` (CRLF, non-ASCII, no trailing newline) in a throwaway home | ✅ exit 0; `mcp.json.gflow-backup` sha256 `9937ce9497be2ee5…` equals the original, `cmp` identical; merged config keeps `other` and adds `gflow` | $0 |
| Version | `gflow --version` | ✅ `gflow, version 0.95.0` | $0 |

The same live upload suite also passed 8/8 on the PR #1166 head `fcc8a263` before merge, watched by
the maintainer's session, with the same D1 0/0 check and no leftover local Worker process.

## The five layers

1. **File count** — one `.mp4` (`a69e0c3b-47a2-4751-81e9-c8d923832aa0.mp4`) for the plain run.
2. **Magic bytes** — `000000206674797069736f6d` -> `ftypisom` at offset 4.
3. **Shape** — 720x1280 portrait, 24 fps, 7.93s, 2,299,200 bytes, h264 + aac.
4. **Structlog invariants** — `browser_teardown.context_close_error` and
   `browser_teardown.force_close_returned` on teardown, as in v0.93.0 and v0.94.0; every log line carries
   `cli_version: 0.95.0`.
5. **User-confirmable artifact** — the clip opens in any video player.

## Observed, not caused by this release

- `browser_teardown.context_close_error` (`TargetClosedError`) after the generation, unchanged since v0.92.1.
- A `SyntaxWarning` from the third-party `wmi` package on first import under Python 3.13.

## Not run

- **Other accounts, and a host other than flow.google.com.** No account here is served anything else
  (named external blocker).
- **A real EU machine.** The EU path is driven by setting the timezone (`TZ=Europe/Paris`) in the e2e;
  the region decision reads only the timezone, so this exercises the same code a machine in the EU runs.
- **`i2v`, `r2v`, `extend`.** Not touched by this release; omitted to conserve Veo credits.

## Pre-tag gates

| Gate | Result |
|---|---|
| `check_repo_hygiene.py` | ✅ 1442 tracked files, 0 violations |
| `check_release_artifacts.py` | ✅ release artifacts OK |
| `check_doc_links.py` | ✅ all links resolved across 255 files |
| ruff, ruff format, pyright strict | ✅ clean, 607 files formatted, 0 errors |
| `pytest --cov` (`-n 8`) | ✅ 5933 passed, 12 skipped |
| `generate_website_docs.py --check` | ✅ mirror in sync (22 files), PII gate clean |
| Live verification | ✅ the rows above |
| `/gflow:doc-review` | _Council verdict: RED across 3 auditors (completeness RED, cross-reference YELLOW, drift RED). 11 distinct findings, all docs lagging the code: `docs/USAGE.md` called the report "what a future version would send" and documented only `status`/`show`; `docs/INDEX.md`, `llms.txt` and the `gflow telemetry` group help still described local-only telemetry; the v0.94.0 block in PROJECT_STATUS read as a current "no network" claim; `TELEMETRY.md` omitted the EU consent-week cutoff; the website mirror rendered the data controller as "your.name Oliva" (now names the public handle); AGENTS.md module list lacked `telemetry_upload.py`. The merge that brought PR #1166 up to date had also overwritten the released `[0.94.0]` CHANGELOG section; it is restored byte-identical to `main`. All fixed in the release-prep commit. Kept as is: `TELEMETRY.md` "Pending legal review (#1164)" — true. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._ |

## Post-tag evidence

| Check | Result |
|---|---|
| Signed tag | to be filled after the tag |
| `Release` workflow | to be filled after publish |
| GitHub Release | to be filled after publish |
| PyPI | to be filled after publish |
