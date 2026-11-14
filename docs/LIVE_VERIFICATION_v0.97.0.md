# Live verification — v0.97.0

Release ledger for v0.97.0: credit balances read without a browser, wire health in `doctor` and
MCP `gflow_auth_status`, the Chrome close hang on Windows, the httpx URL log leak and the
`video extend` model label (#1229, #1140, #1180).

## Environment

| | |
|---|---|
| Profiles | `denon82` (release runs, the paid t2v); `ffroliva` (the extend e2e, see below) |
| Host Flow served | `flow.google.com`, build `boq_labs-ai-sandbox-frontend_20261110.00_p0` (reported by `gflow doctor`) |
| Entrypoint | the real `gflow` CLI (`uv run gflow` in the release worktree); the e2e drive the CLI through `CliRunner` and the MCP tools in-process |
| OS | Windows 11 |
| Tree | `chore/release-v0.97.0`, cut from `ef6c4582`, with develop merged up to `45224a46` (#1243, comments and tests only) |
| Veo credits spent | 1 veo-lite clip on `denon82` (50 → 40: 10 credits) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Plain text-to-video on the release tree (step 0) | `gflow video t2v "a ceramic cup on a wooden table, soft morning light" --profile denon82 --model veo-lite` | ✅ exit 0 in 89 s, 2,278,087 bytes, `ftypisom`, 7.93 s, 720x1280 h264 + aac, 24 fps | 1 lite clip |
| Credits without a browser (#1229) | `gflow credits user --profile denon82 --json`, 5 runs, no Chrome open | ✅ exit 0 ×5: 2.68 / 2.44 / 2.74 / 2.72 / 2.92 s (p50 2.72 s); 0 Chrome processes on the profile afterwards; balance 50 | $0 |
| The browser read still works (kill switch) | same command with `GFLOW_CLI_WIRE_READS=0` | ✅ exit 0 in 10.73 s through Chrome; 0 Chrome processes left on the profile afterwards (the #1140 close fix: v0.96.x paid ~30 s more here) | $0 |
| A second account | `gflow credits user --profile ffroliva --json` | ✅ balance 660 over the direct read | $0 |
| Proxy skip | `HTTPS_PROXY=http://127.0.0.1:9 gflow credits user --profile denon82 --json` | ✅ exit 0 in 10.5 s; `credits.wire_fallback reason=proxy_configured`, then `credits.read source=page`; balance 40 | $0 |
| Chrome open on the profile | a separate Chrome held `denon82` (about:blank) while `gflow credits user --profile denon82 --json` ran | ✅ exit 11 in 10.6 s, `profile-locked` ("Profile locked"); no balance invented | $0 |
| No httpx request URL in logs | a host process at `logging.INFO` (what the MCP server did), then the real direct read on `denon82` | ✅ 0 `HTTP Request` lines, 0 `f.sid=`. Positive control, httpx logger forced back to INFO: 2 `HTTP Request` lines, 1 carrying `f.sid` | $0 |
| Wire health (#1229) | `gflow doctor --json` (default profile) | ✅ `auth.wire_read` pass: `status=accepted build=boq_labs-ai-sandbox-frontend_20261110.00_p0` | $0 |
| e2e, CLI and MCP twins | `pytest -m "e2e and e2e_auth"`: `test_credits_wire_bdd` (3), `test_credits_mcp_e2e` (4), `test_auth_status_mcp_e2e` (1), `test_wire_health_e2e` (1), `test_credits_on_flow_bdd` (1) | ✅ 10 passed (second run, full log kept). The first run had 1 failure in `test_credits_wire_bdd.py` that cannot be named: the output filter dropped the test name and no log was kept. That file then passed 3/3 alone, and the whole set 10/10 | $0 |
| `video extend` model label (#1180) | `tests/e2e/test_video_extend_migrated_e2e.py` (`e2e_auth`, `fZytfe` aborted) on `ffroliva`'s Omni 1.2 clip | ✅ 3 passed: submit contract, a foreign media refused before any click, a foreign aspect aborted by the production guard | $0 |
| Version | `gflow --version` | ✅ `gflow, version 0.97.0` | $0 |

### What these runs do not prove

- **`--refresh` lifting the signed-out mark.** `tests/e2e/test_credits_refresh_e2e.py` needs a profile
  whose saved session has expired (`GFLOW_CLI_E2E_STALE_PROFILES`); none on this machine has. Blocker:
  no stale profile. Covered offline, including an ablation that fails with the old guard (#1243).
- **Long-horizon cookie staleness** with no browser visits: S2 #1222.
- **macOS:** the direct read is off by default there and was not run (no macOS machine here).
- **Linux,** where the direct read is on by default: no Linux machine here holds a Flow-signed-in
  profile. The Linux cookie store and its lock behaviour are unmeasured (KNOWN_ISSUES says so).
- **HTTP 429/503 back-pressure:** Flow has not returned one in about 62 spike calls plus this cycle's
  reads, and provoking one would mean sending traffic the app does not send. Covered offline
  (`tests/services/test_credits_wire_fallback.py`, SCENARIO row 29).

### Found by this cycle

- On `denon82`, Flow renders the extend menu item **disabled** for both clips tried (the new veo-lite
  clip and an Omni Flash clip from 2026-11-07). The driver waits 30 s and reports a misleading
  `Zzl0ze` timeout. The untouched v0.96.1 tag fails identically, and `ffroliva` passes 3/3 on this
  tree, so it is not a regression. The cause is unproven (that account had 40 credits left). Filed
  as #1244.
- A fresh `/code-review` of the CodeRabbit fix on #1238 showed that its stated reason was wrong (a labs 410
  is already `renewed`). Comments, docstring and tests were corrected in #1243. The pre-existing
  findings it raised are filed as #1242.

## Gates

| Gate | Result |
|---|---|
| `check_repo_hygiene.py`, `check_doc_links.py`, `check_website_docs_pii.py`, `generate_website_docs.py --check`, `check_council_memory.py`, `generate_plugin_skills.py --check` | ✅ clean |
| ruff, ruff format, pyright strict | ✅ clean, 627 files formatted, 0 errors |
| `pytest --cov` (`-n 8 --dist worksteal`) | ✅ 6292 passed, 12 skipped, 93%. The 1 failure, `test_built_distributions_contain_sql_migrations`, is the known load flake: it passed alone (2 passed) |
| `check_release_artifacts.py` | ✅ release artifacts OK |
| `/gflow:doc-review` | _Council verdict: YELLOW across all 3 auditors (completeness YELLOW, cross-reference YELLOW, drift YELLOW); no fictional claim, nothing blocking. Mechanical pass clean (versions, CHANGELOG footer, skills, 144 memory links). Fixed in the release-prep commit: the proxy rule beside every macOS-default mention; the three `store_unreadable` causes; the MCP probe's no-send cases; `gflow doctor` no longer said to lift the signed-out mark (it is per-process); ACCOUNT_SAFETY and CONFIGURATION now point to the account-risk entry; the doctor proxy hint names every proxy source; the `gflow_get_credits` description says when the direct read is off and that 429/503 means wait; KNOWN_ISSUES "below" → "above"; the milestone row lists #1180 and the httpx fix. This ledger gained the proxy, locked-profile and log-leak runs, and the Linux and 429/503 blockers. Deferred (Tier 3): two older `store_unreadable` hints (`services/doctor.py`, `mcp/tools.py`) still give two causes, not three; `wire.retry_after` is not in the documented field shape. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._ |
