# Live verification — v0.100.0

Release ledger for v0.100.0. It covers:
- `image generate` / `video generate` (alias `gen`, #1193), `gflow --help --json` (#1203) and `gflow cancel` (#1198);
- `gflow update` keeping a Windows pip install working (#1298) and reporting one result (#1163);
- `character list` / `show` on flow.google.com and secret redaction (#987 slices 1-2);
- `video chain --out-file` and `--resume-from` (#1293, #1300);
- `video extend` on flow.google.com (#1184), and telemetry command naming (#1209).

No CLI flag or exit code changes. MCP `tasks/cancel` now stores `cancelled` / `abandoned` (see the CHANGELOG's Changed entry).

## Environment

| | |
|---|---|
| Profiles | `ffroliva` (release runs, the t2v, the e2e) |
| Host Flow served | `flow.google.com` |
| Entrypoint | the real `gflow` CLI (`uv run gflow` in the release worktree); the CLI e2e drive it as a subprocess or through Click's runner, the MCP e2e call the tool in-process, `e2e_update` drives the installed `gflow.exe` from real pip / uv venvs |
| OS | Windows 11 |
| Tree | `chore/release-v0.100.0`, cut from `f36959e5` (develop after #1306), plus the #1300 resume e2e (`67eb827c`). Every run here except `video generate` and the final `e2e_update` ran before the version bump and the `gflow_generate_video` description edit in the release commit; neither changes a code path those runs drive |
| Veo credits spent | 4 veo-lite clips on `ffroliva` (586 → 546): 2 for the `--resume-from` e2e, 1 for the release-tree t2v, 1 for the `video generate` e2e |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Plain text-to-video on the release tree (step 0) | `gflow video t2v "a ceramic cup on a wooden table, soft morning light" --profile ffroliva --model veo-lite --out-file <tmp>/release-t2v.mp4 --json` | ✅ exit 0, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, a 2,196,231-byte file whose box reads `ftyp`, written at the `--out-file` path | 1 clip |
| `image generate` resolves the mode from the inputs (#1193, #1301) | `tests/e2e/test_json_output_e2e.py::test_e2e_image_t2i_json_shape[generate]`, `::test_e2e_image_gen_with_a_local_ref_resolves_to_i2i` | ✅ passed: `generate` with no ref is a t2i, `generate` with a local ref is an i2i. The `gen` alias is covered offline (`tests/cli/test_gen_bridge.py`) | $0 (image) |
| `video generate` resolves to t2v with no frame or ref (#1193) | `tests/e2e/test_json_output_e2e.py::test_e2e_video_t2v_json_shape[generate]` | ✅ passed | 1 clip |
| `--json` shapes | `tests/e2e/test_json_output_e2e.py` (auth list, models catalog, models list/show, t2i) | ✅ 4 more passed | $0 |
| `gflow --help --json` (#1203) | the real binary: `gflow --help --json`; offline `tests/cli/test_help_json.py` | n/a: no Flow surface. ✅ exit 0, 73 commands, `cancel` and both `generate` verbs among them; offline suite passed | $0 |
| `gflow cancel` and MCP `tasks/cancel` (#1198) | offline: `tests/test_cli_cancel.py`, `tests/test_cli_cancel_subprocess.py` (the installed CLI as a subprocess), `tests/mcp/test_tasks_extension.py` | n/a: no Flow surface; it rewrites a local queue row and calls nothing at Flow. ✅ passed (1333 with `tests/mcp` and `tests/cli`) | $0 |
| Browser teardown on cancel (regression check) | `tests/e2e/test_cancellation_e2e.py::test_cancel_during_launch_releases_lease_and_closes_browser` | ✅ passed: the lease is released and the browser closed | $0 |
| `gflow update` on a Windows pip install (#1298) | `tests/e2e/test_update_installer_e2e.py` (new `e2e_update`: wheels built from the tree, real tmp venvs) | ✅ 4 passed on the release tree: pip venv upgrades and imports, pip refuses with nothing changed while `gflow mcp run` holds a launcher, `pip --user` upgrades, uv tool still works. pipx (case E) passed on #1304's final tree with pipx 1.18.16; it is not installed here, so it skipped | $0 |
| `character list` / `show` and MCP twins on flow.google.com (#987 slice 2, #1306) | `tests/e2e/test_character_mcp_parity_e2e.py` | ✅ 3 passed: MCP list matches the CLI client, show by id and by name, no signed URL in the payload. Control on #1306: develop's `character list` exited 7 on the same project | $0 |
| `video chain --resume-from` (#1300) | `tests/e2e/test_chain_e2e.py::test_chain_cli_resume_keeps_the_manifest_index_and_seeds_from_the_last_clip` (new this cycle) | ✅ passed: the resumed link kept index 1, landed as `clip_2.mp4`, and was catalogued as an i2v | 2 clips |
| `video chain --out-file` (#1293) | `tests/e2e/test_chain_e2e.py::test_chain_cli_out_file_writes_numbered_mp4s_and_seeds_link_2` on #1297's final tree | ✅ passed there (20 credits, recorded in #1297). Not re-run here: the resume e2e above drives the same `--out-file` numbering | — |
| `video extend` on flow.google.com (#1184) | `tests/e2e/test_video_extend_migrated_e2e.py`, the 3 `e2e_auth` tests, on the release-tree t2v clip | ✅ 3 passed: the submit contract at $0, a media id outside the project refused before any click, a foreign aspect aborted by the production guard | $0 |
| Image `--out-file` still writes (regression check) | `tests/e2e/test_out_file_cli_e2e.py` | ✅ 3 passed | $0 (image) |
| Telemetry records the right command (#1209) | `tests/e2e/test_telemetry_local_e2e.py` | ✅ 6 passed, including no network socket opened | $0 |
| Secret redaction (#987 slice 1, #1302) | offline: `tests/data/test_redaction.py`, `tests/api/transports/test_batchexecute.py` (timing tests on 160 KB inputs, the cut-window and Bearer-order cases) | n/a: no Flow surface; it only scrubs our own error text | $0 |
| Version | `gflow --version` | ✅ `gflow, version 0.100.0` | $0 |

### What these runs do not prove

- **pipx on the release tree.** pipx is not installed in the release environment. Case E passed on #1304's final tree, and `update_check.py` did not change after it.
- **Video `@Name` on flow.google.com.** It stays not supported (exit 29), on purpose: slice 2 keeps mention routing on the labs read (`migrated_read=False`). Slice 5 changes it, behind a paid e2e.
- **`gflow update` on POSIX with a held launcher.** POSIX unlinks open files, and the guard does nothing there. Case A runs on any OS; only Windows was run here.
- **Whether the one-time pip command repairs a venv that an old `gflow update` already broke.** The docs give that command for upgrading *before* running `gflow update`; repairing a broken venv was not run.
- **`gflow cancel` against a job a gflow worker already submitted, live.** Covered offline (exit 22, `abandoned`); forcing it live needs a billed submit.
- **macOS:** there is no macOS machine here.

### Found by this cycle

- **#1298.**
  - Every gflow process holds its own `gflow.exe` through `importlib.metadata`'s cached zip handle, and pip uninstalls before it installs. That combination stranded the venv.
  - The fix was proven end to end before it was written.
  - Reviews then found that on Python 3.12+ `Distribution.files` hides missing RECORD entries, and that the old unit tests renamed this checkout's own `.venv` launchers.
  - A cross-platform test bug surfaced on CI, where `winerror` exists only on Windows.
- **#987 slice 1.** The contributor's generic "120 or more token characters" rule erased Flow media ids and split Bearer tokens, so it was dropped. Two existing patterns turned out to be quadratic, and both are now bounded.
- **#987 slice 2.** Reviews found three defects: a reply for another project could attach the wrong project's characters, a signed-out profile timed out instead of failing, and a landing check that runs before the reply wait can false-positive on flow.google.com's `/about` render.
- **#1300.** It had only an offline test. This cycle added its live e2e.
- **The release-tree extend run.** It first failed because the test's source aspect defaulted to 16:9 while the release clip was portrait. That was a setup error: re-run with `GFLOW_CLI_E2E_EXTEND_ASPECT=9:16`, it passed, and nothing was billed either time.

## Gates

| Gate | Result |
|---|---|
| `/gflow:check` on the release tree | ✅ every script gate, ruff, format, pyright; pytest 7205 passed, 34 skipped; coverage 94% |
| CI on #1302, #1304, #1305, #1306 | ✅ all checks, including SonarCloud (0 new issues, the scan step concluded success) and sharpit |
| Reviews | `/code-review xhigh` + `ponytail-review xhigh` by fresh agents on #1302, #1304 and #1306, one fix round each; every CodeRabbit comment answered |
| `/gflow:doc-review` | Council verdict: RED (completeness) / YELLOW / YELLOW. The RED findings were checked against the code and fixed in the release commit: this ledger credited `gflow cancel` and `--help --json` to e2e tests that never call them (now relabelled, offline and real-binary evidence added, `video generate` run live), and the Windows pip one-time upgrade was not findable from README or KNOWN_ISSUES (both added). Also fixed: the skill listing `video generate` as a wrong subcommand, video `@Name` on flow.google.com (exit 29) undocumented in USAGE, CHARACTER and the MCP description, a false USAGE line on character references for images, exit 22 missing `ServerCancelUnavailableError`, `--help --json` and character list/show missing from AGENTS.md / llms.txt / README. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only) |
