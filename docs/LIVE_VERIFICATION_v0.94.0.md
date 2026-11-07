# Live verification — v0.94.0

> Evidence for this release, gathered on 2026-11-07. Every row below was a run someone watched,
> not an inference.

## Environment

| | |
|---|---|
| Profile | `ffroliva` (real-browser Chrome strategy) |
| Host Flow served | `flow.google.com` |
| Entrypoint | the real `gflow` CLI, in-process through Click for the e2e rows, and Click CLI / MCP stdio client |
| OS | Windows 11 |
| Tree | `chore/release-v0.94.0` (cut from `cca0c871`, PR #1165 merge head) |
| Veo credits spent | 1 lite clip |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Plain text-to-video generation on release tree (step 0) | `gflow video t2v "a ceramic cup on a wooden table" --profile ffroliva --model veo-lite` | ✅ exit 0, 1,853,301 bytes, `ftyp` (`ftypisom`), 7.93s, 720x1280 h264+aac | 1 lite clip |
| Duration selector guard on veo-lite | `gflow video t2v ... --duration 4` | ✅ exit 11 (`ConfigurationError`), pre-submit refusal: Flow host renders no duration control offering 4s for this cohort | $0 |
| Headless session renewal | `gflow auth renew --profile ffroliva` | ✅ exit 0, renewed and verified as `ffroliva@gmail.com` with `flow.google.com OSID session + SSO cookies present` | $0 |
| Local usage telemetry e2e | `tests/e2e/test_telemetry_local_e2e.py` on PR #1165 merge head 8f66f292 and release tree | ✅ 6 passed in 30.13s (commands add rows; DO_NOT_TRACK and GFLOW_CLI_TELEMETRY=0 add nothing; crash fingerprint without message/path; real MCP stdio client recorded as `mcp`; no network socket opened) | $0 |
| Version and local telemetry inspection | `gflow --version`, `gflow telemetry show`, `gflow data stats --json` with temp home | ✅ printed `gflow, version 0.94.0`, schema 1 telemetry report with version 0.94.0, command stats JSON | $0 |

## The five layers

1. **File count** — one `.mp4` (`tmp/01b730c6-0be6-4b72-988c-bab5b6169760.mp4`) for the plain run.
2. **Magic bytes** — the clip carries `ftyp` at offset 4 (`000000206674797069736f6d` -> `ftypisom`).
3. **Shape** — 720x1280 portrait 9:16, 24 fps, duration 7.93s, 1,853,301 bytes.
4. **Structlog invariants** — `browser_teardown.context_close_error` and `browser_teardown.force_close_returned` logged upon teardown; `auth_migrated_host_fallback_authenticated` during session renew; clean JSON payloads on telemetry.
5. **User-confirmable artifact** — `tmp/01b730c6-0be6-4b72-988c-bab5b6169760.mp4` opens in any standard video player.

## Observed, not caused by this release

- Flow's settings pane for `veo-lite` on the maintainer cohort renders no duration control, so `--duration 4` exits 11 pre-submit with a clean `ConfigurationError` and guidance to omit `--duration`.
- `browser_teardown.context_close_error` appears after generation runs, as observed in v0.93.0 and v0.92.1.

## Not run

- **Other accounts, and a host other than flow.google.com.** No account here is served anything else (named external blocker).
- **Additional video generation paths (`i2v`, `r2v`, `extend`) on this cycle.** Omitted to conserve the account's Veo credit budget (spends credits), relying on the required plain t2v step 0 generation.

## Pre-tag gates

| Gate | Result |
|---|---|
| `check_repo_hygiene.py` | ✅ 0 violations |
| `check_release_artifacts.py` | ✅ all artifacts present |
| `check_doc_links.py` | ✅ all internal doc links resolve |
| `generate_website_docs.py --check` | ✅ docs mirror in sync |
| Version pin tests | ✅ `test_plugin_manifests.py` and `test_dockerfile_version_pin.py` 19 passed |
| Live verification | ✅ the rows above |
| `/gflow:doc-review` | _Council verdict: RED across 3 auditors (completeness RED, cross-reference YELLOW, drift YELLOW). 8 distinct findings, all mechanical; all fixed in commit this commit. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._ |

## Post-tag evidence

| Check | Result |
|---|---|
| Signed tag | `v0.94.0`, SSH-signed (`git cat-file -p v0.94.0` carries one `BEGIN SSH SIGNATURE`) |
| `Release` workflow | run (to be filled after publish) |
| GitHub Release | `v0.94.0`, published (to be filled after publish), not a prerelease |
| PyPI | `pip index versions gflow-cli --no-cache-dir` lists 0.94.0 as the latest |
