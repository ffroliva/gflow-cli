# Live Verification — v0.22.0

Release theme: **Tools framework ("Creative Director")** — TOML-defined prompt tools, the
`gflow tools` group, the uniform `--tool/-t` option across all generation commands, "My Tools"
user-authored tools, MCP parity, and an expander wall-clock budget.

This file follows the project's release-evidence convention (a `LIVE_VERIFICATION_vX.Y.Z.md` per
release). It has two parts: **Pre-tag gates** (filled before signing) and **Post-tag evidence**
(filled after the live owner-run).

---

## Pre-tag gates

### Automated coverage (CI-green on the release tree)

The entire feature set shipped via PRs #210/#211/#213 (framework + broaden + docs) and the
v0.22.0 follow-ups #214/#215/#216, **each merged to `develop` with full CI green** on Python
3.11 / 3.12 / 3.13 and **SonarCloud quality gate at zero**:

| Surface | Coverage |
|---|---|
| `gflow tools list/show/run` | `tests/cli` + `tests/tools` |
| `--tool/-t` on `image t2i/i2i/batch`, `video t2v/i2v/r2v/chain` | `tests/cli`, `tests/features` (BDD), `tests/tools/test_runtime.py` |
| Tool schema / loader / registry | `tests/tools/test_spec.py`, `test_loader.py`, `test_registry.py` |
| Banned-keyword stripping | `tests/tools/test_banned.py` |
| Gemini expander (never-fatal, retries, **time budget**) | `tests/tools/test_expander.py` (incl. budget/timeout cases) |
| Provenance (`expanded_prompt`, `metadata_json.tool`, redaction) | `tests/data` recorder tests |
| MCP `gflow_list_tools` + `tools` array param + §61 parity | `tests/mcp/test_server.py` |
| `expand_prompt` deprecation marker | `tests/mcp/test_server.py::test_expand_prompt_is_marked_deprecated` |
| "My Tools" user-dir loader | `tests/tools/test_registry.py::TestMyToolsLoader` |

Local gates on the release tree: `pyright src` 0 errors, `ruff check` / `ruff format --check`
clean, `scripts/ci/check_repo_hygiene.py` clean, `scripts/ci/check_doc_links.py` all resolved.

### Doc-review council

Council verdict: **RED / YELLOW / GREEN** across the 3 auditors (completeness / cross-reference /
drift). 1 RED finding (release-blocking) and the Tier-2 polish were **fixed in the release-prep
commit**; drift auditor was GREEN. Findings + resolutions:

- **Tier 1 (was blocking) — FIXED:** the canonical reference docs still documented the removed
  `-e/--expand` flag and omitted the entire tools surface. Updated `docs/USAGE.md` (replaced the
  `-e/--expand` option + section with `-t/--tool`; added a `gflow tools` command section) and
  `docs/CONFIGURATION.md` (`GFLOW_CLI_GEMINI_API_KEY` / `_MODEL` now describe the `creative-director`
  tool, not `--expand`; added the `<GFLOW_CLI_HOME>/tools/` My-Tools dir + redaction note).
- **Tier 2 — FIXED:** the expander wall-clock budget was undocumented — added to
  `CHANGELOG [0.22.0]`, `PROMPT_EXPANSION.md §6`, and `TOOLS.md §3`; added a README feature line;
  fixed the stale "dormant" `load_user_tools` docstring in `tools/loader.py`.
- **Tier 3 (deferred, cosmetic):** banned-keyword list ordering differs (set-equal, 13 terms);
  the CLI-vs-MCP `tools list` field asymmetry (`requires_env`) could note it's deliberate.

Post-fix gates re-run green: `check_doc_links` (23 files), `ruff`/`ruff format`, `pyright src` 0,
`tests/tools`+`tests/mcp` 85 passed, repo-hygiene clean.

### Live feature verification — ⏳ PENDING (owner-run)

The credit/key-gated live e2e is **deferred to a post-tag owner run** and is **not** met at
tag time. Reason: a true end-to-end check of `--tool creative-director` requires a real
`GFLOW_CLI_GEMINI_API_KEY`, an authenticated Flow profile, and (for the generation half) Veo
credits — none of which are available to the automated release agent. The tool path is
**never-fatal by design** (a missing key / API fault degrades to the original prompt), so this
does not risk a broken default install, but the live rewrite-and-record assertion below must
still be exercised by the owner.

Owner checklist (run after publish, then fill **Post-tag evidence**):

1. `gflow tools list` shows `creative-director`; `gflow tools show creative-director` lists styles.
2. `GFLOW_CLI_GEMINI_API_KEY=… gflow tools run creative-director "a cat on a couch" --style cinema --json`
   → `was_expanded: true`, a vivid multi-sentence prompt, **no banned keywords** in output.
3. A real generation with a tool, e.g.
   `gflow image t2i "a fox in the snow" --tool creative-director:style=cinema` (image gen is
   credit-free) → assert the catalog row records the **original** prompt in `prompt`, the
   rewrite in `expanded_prompt`, and `metadata_json.tool = {name, version, model, params,
   config_hash}` (`gflow data show <id> --json`).
4. Redaction: repeat (3) with `GFLOW_CLI_HISTORY_PROMPTS=redacted` → `expanded_prompt` withheld;
   `metadata_json.tool` reduced to `{name, version, params_hash, config_hash}`.
5. "My Tools": drop a TOML in `<GFLOW_CLI_HOME>/tools/mytool.toml` → it appears in
   `gflow tools list`; a same-named file overriding a builtin logs `tool_user_override`.

---

## Post-tag evidence

### Tool / expander / My-Tools — ✅ live-verified 2026-06-28 (real Gemini API)

Run with the owner's `GFLOW_CLI_GEMINI_API_KEY` (from `.env.local`), CLI v0.22.0:

| # | Check | Command | Result |
|---|---|---|---|
| 1 | Tool registered | `gflow tools list` | `creative-director` listed (Title/Category/Description correct) ✅ |
| 2 | Real expansion | `gflow tools run creative-director "a cat on a couch" --style cinema --json` | `was_expanded: true`; 16→1189 chars; vivid 5-component prose naming a real camera (Sony A7R IV / Cooke S7/i) + prestige anchor (Kinfolk); structlog `prompt_expanded` (model `gemini-2.5-flash`) ✅ |
| 3 | Banned-keyword guarantee | `gflow tools run creative-director "a luxury watch" --style product` | output scanned against all 13 banned terms → **none present** ✅ |
| 4 | Never-fatal (bad key) | `GFLOW_CLI_GEMINI_API_KEY=invalid-key-xyz gflow tools run creative-director "a dog" --json` | `was_expanded: false`, original returned, no error/exit ✅ |
| 5 | Video category style | `gflow tools run creative-director "a city at night" --style cinematic` | rewrites with cinematic vocabulary (video-gated style resolves) ✅ |
| 6 | "My Tools" loader | wrote `<HOME>/tools/haiku-bot.toml`; `GFLOW_CLI_HOME=<tmp> gflow tools list` | user tool `haiku-bot` listed **alongside** the builtin `creative-director` ✅ |

These exercise the whole prompt-tool path (registry → `apply_tool` → `build_instruction` → live
`PromptExpander.expand` → `strip_banned_keywords`), the never-fatal contract, category gating, and
the user-dir loader — end to end against the real Gemini endpoint.

### Generation recording (`expanded_prompt` + `metadata_json.tool` + redaction) — ✅ live-verified 2026-06-28

Real credit-free `image t2i` generations on the authenticated `denon82` profile (agentic UI
cohort, model NARWHAL), CLI v0.22.0:

**Store mode** — `gflow image t2i "a fox in the snow" --tool creative-director:style=cinema --profile denon82`:
- File: a real **729 KB JPEG** (magic bytes `ff d8 ff`) written to the `--out` dir ✅
- `prompt` = `"a fox in the snow"` (the **original**) ✅
- `expanded_prompt` = the 971-char rewrite (`"A sleek, mature red fox, …"`) ✅
- `operations.metadata_json.tool` = `{name: "creative-director", version: "1", model: "gemini-2.5-flash", params: {style: "cinema"}, config_hash: "e8399085…"}` ✅

**Redacted mode** — same command with `GFLOW_CLI_HISTORY_PROMPTS=redacted` (`"a red barn at dusk"`):
- `prompt` = `None`, `prompt_redacted` = `1`, `expanded_prompt` = `None` (both withheld) ✅
- `metadata_json.tool` = `{name, version, params_hash, config_hash}` only — no `model`, no raw
  `params` (replaced by `params_hash`) ✅
- `config_hash` identical to store mode (same resolved `ToolConfig`) — tamper-evidence confirmed ✅

**All 5 ledger layers cleared** (file + magic bytes + real generation + DB rows in both prompt
modes + structlog `prompt_expanded`/`ui_driver.bound`). v0.22.0 live verification is **complete**.
