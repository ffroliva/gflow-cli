# Live verification — v0.99.0

Release ledger for v0.99.0: `--out-file PATH` with `-o/--output PATH` deprecated (#1158), the
MCP `out_file` parameter, the opt-in stable exit-code core `GFLOW_CLI_EXIT_CODES=2` (#1202), the
`video chain` notice (#1293), and the `UiAutomationTransport` / `MigratedComposer` splits
(#1249 wave 2). Nothing in this release changes a default.

## Environment

| | |
|---|---|
| Profiles | `denon82` (release runs, the t2v, the e2e) |
| Host Flow served | `flow.google.com` |
| Entrypoint | the real `gflow` CLI (`uv run gflow` in the release worktree); the CLI e2e drive it as a subprocess, the MCP e2e calls the tool in-process |
| OS | Windows 11 |
| Tree | `chore/release-v0.99.0`, cut from `dae97e62` (develop after #1292) |
| Veo credits spent | 1 veo-lite clip on `denon82` (the release-tree t2v) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Plain text-to-video on the release tree (step 0), written with the new flag | `gflow video t2v "a ceramic cup on a wooden table, soft morning light" --profile denon82 --model veo-lite --out-file <tmp>/plain.mp4 --json` | ✅ exit 0 in 79 s; file at exactly the `--out-file` path; 2,401,750 bytes; magic `ftyp`; H.264 720×1280 (portrait), 7.93 s; no deprecation line on stderr | 1 veo-lite clip |
| `--out-file PATH` (#1158) | `tests/e2e/test_out_file_cli_e2e.py::test_e2e_out_file_new_flag` (`image t2i --out-file a.png`) | ✅ passed; the image is written (as `a.jpg`: gflow names the file after its bytes), no deprecation line | $0 |
| `-o/--output PATH` still works and warns once (#1158) | `…::test_e2e_out_file_deprecated_flag` (`image t2i -o b.png`) | ✅ passed; image written, the deprecation line appears exactly once on stderr | $0 |
| Cloud storage refuses a video file path before billing, new spelling (#1008, #1158) | `…::test_e2e_out_file_is_refused_under_cloud_storage` (`GFLOW_CLI_STORAGE_URI=s3://…`, `video t2v --out-file`) | ✅ passed; exit 11, "`--out-file` needs the clip … Nothing was submitted", no file | $0 |
| MCP `out_file` (#1158) | `tests/e2e/test_out_file_mcp_e2e.py` (`gflow_generate_image(out_file=…)`, `wait=True`, temp output dir) | ✅ passed; the reported file is in the output dir with the requested stem | $0 |
| Exit codes default unchanged, `exit_code_legacy` present (#1202) | `gflow image t2i … --profile denon82 --json` with an invalid browser proxy, default vs `GFLOW_CLI_EXIT_CODES=2`; `gflow image t2i` on an empty profile in both modes | ✅ exit 11 in both modes with `exit_code: 11, exit_code_legacy: 11` (`ConfigurationError`, `charged: no`); exit 31 in both modes for the empty profile — codes 10–39 do not move | $0 |
| Version | `gflow --version` | ✅ `gflow, version 0.99.0` | $0 |

### What these runs do not prove

- **The remapped codes (4–9 → 40–44, 8 → 3) from a live failure.** A rate limit, a network
  fault or a transport timeout cannot be forced on demand here: the browser proxy route is
  refused by config validation before any network call, and there is no timeout setting to
  shrink. Covered by `tests/test_exit_codes.py`: every class in `EXIT_CODE_MAP` pinned in both
  modes, the real `image t2i` command exiting 40 with `exit_code: 40, exit_code_legacy: 4`
  under mode 2 and 4/4 by default, a batch keeping out-of-credits (37) above a network blip,
  and the opt-in read from `.env`.
- **The deprecated spelling on video commands live.** The CLI e2e drives `image t2i` for both
  spellings; the release t2v used `--out-file`. The shared decorator is the same object on all
  nine commands, and the Click-tree guard plus the per-command tests cover the others offline.
- **Click 8.1 / 8.2 at runtime.** The lock pins Click 8.5.1. The alias merge was measured on
  click 8.1.8, 8.2.1 and 8.5.1 with a probe, and the deprecation tests pass under
  `uv run --with click==8.1.8`.
- **`video extend --out-file` under cloud storage.** It is not refused before submit, as before
  this release; whether it fails after billing was not measured (no bucket here). Unchanged.
- **macOS:** no macOS machine here.

### Found by this cycle

- The first alias implementation wrote the primary option's slot during parsing. On Click 8.1
  and 8.2 (allowed by `click>=8.1.0`) the unused `--out-file` then overwrote it with `None`:
  `-o clip.mp4` would have generated, billed and dropped the path. Measured, fixed in the
  callback, before merge.
- `tests/mcp/test_cli_parity.py` listed `output_file` as CLI-only, so the option-level gate never
  proved the file path reached MCP. It now maps all three spellings.
- `video chain` has never written to `-o PATH` (#1293). It now says so on stderr.
- Reviews on #1202 found that the opt-in did nothing from `.env`, that mode 2 changed which
  failure a batch reported (a network blip outranked out-of-credits), and that three tests still
  imported a removed helper. All fixed with tests before merge.
- sharpit failed a test that ran four CLI invocations in one call phase (its N+1 counter sums
  per test); split into one invocation per case.

## Gates

| Gate | Result |
|---|---|
| `/gflow:check` on the merged develop tree | ✅ hygiene, size, doc links, PII, website mirror, plugin skills, ruff, format, pyright 0; 6889 passed, 16 skipped |
| CI on #1291 and #1292 | ✅ all checks, including SonarCloud (0 new issues) and sharpit |
| Reviews | `/code-review xhigh` + `ponytail-review xhigh` by fresh agents on both PRs, one fix round each; every CodeRabbit comment answered |
| `/gflow:doc-review` | ✅ mechanical sections pass (versions, INDEX, evidence file, doc links, PII, mirror, plugin copies, CHANGELOG footer). Council verdict: YELLOW across all 3 auditors, no RED and no fictional claim. The findings: the cloud-storage refusal stated more broadly than the code (now names `video t2v`/`i2v`/`r2v`; `extend` and `scene create` are not refused up front), `video chain` listed as taking `--out-file` with no caveat, no exit-code pointer in USAGE / AGENTS.md / MCP.md, a stale `.env.template` comment, `40–63` read as used rather than reserved, and the MCP parameter order. All fixed in the release commit. Deferred: a README mention of `--out-file` and the exit-code opt-in, and the "three releases" vs "a later release" wording. Reports in `tmp/council/` (local only). |
