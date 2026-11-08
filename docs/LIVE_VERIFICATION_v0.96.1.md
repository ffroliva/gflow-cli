# Live verification — v0.96.1

Release ledger for v0.96.1: Omni 1.2 Flash start+end frames, video submits refused before they
are billed, and the 10s Omni 1.2 end-frame cap (#1178, PR #1181).

## Environment

| | |
|---|---|
| Profile | `ffroliva` (real-browser Chrome strategy) for the release runs; `denon82` for the spike |
| Host Flow served | `flow.google.com` |
| Entrypoint | the real `gflow` CLI (`uv run gflow` in the release worktree); the e2e drives the CLI through `CliRunner` and the MCP tool in-process |
| OS | Windows 11 |
| Tree | `chore/release-v0.96.1` (cut from `fbabf435`, the PR #1181 merge head) |
| Veo credits spent | 1 Omni 1.2 8s 360p clip + 1 veo-lite clip on the release tree (`ffroliva` 660 → 642: 18 credits); 2 Omni 1.2 8s clips billed by the spike's miss on `denon82` |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Plain text-to-video on the release tree (step 0) | `gflow video t2v "a ceramic cup on a wooden table, soft morning light" --profile ffroliva --model veo-lite` | ✅ exit 0 in 115s, 2,526,417 bytes, `ftypisom`, 7.93s, 720x1280 h264 + aac, 24 fps | 1 lite clip |
| Omni 1.2 start+end runs (#1178) | `gflow video i2v start.png "…" --end-frame end.png --model omni-1.2-flash --duration 8 --resolution 360p --aspect 9:16` | ✅ exit 0 in 119s, 1,665,326 bytes, `ftypmp42`, 8.04s, 360x640 h264 + aac, 24 fps; first frame mean RGB (201,54,33) = the red start input, last frame (41,78,196) = the blue end input | 1 Omni 1.2 clip |
| Route guard on both doors (#1178) | `tests/e2e/test_omni12_start_end_guard_e2e.py` on the release tree | ✅ 4 passed (CLI and MCP × right frames, swapped end id): the guard passes Omni 1.2's body-only `nprQif` with its `*_i2v_fl_*` key and both ids; a swapped end id is aborted unsent ("billed nothing"), and the safety route sees no submit | $0 |
| Same e2e on the PR head | `550bf16a`, `ffroliva` | ✅ 4 passed; credits 660 before and after | $0 |
| 10s end-frame cap (#1178) | DTO, CLI (exit 2) and MCP (400 before queueing) | Offline only: `tests/api/test_omni_1_2.py`, `tests/cli/test_cli_video.py`, `tests/mcp/test_server.py` | $0 |
| Version | `gflow --version` | ✅ `gflow, version 0.96.1` | $0 |

### What these runs do not prove

- **The over-10s end-frame behaviour.** No account here has a Google AI plan (`denon82`'s
  slider stops at 10s), so the cap rests on the reporter's measurement
  at 12s and 15s on Google AI Ultra. Blocker: no account in that tier. #1178 stays open for it.
- **A batched `rpcids=X,nprQif` submit.** Never observed; the guard, like the observer before
  it, reads the first rpc id. Not measured, not handled.

### Found by this cycle

- The spike's first route predicate matched the URL `rpcids` only, missed Omni 1.2's body-only
  `nprQif`, and two Omni 1.2 clips were billed on `denon82`. That is the bug class the route
  guard closes; the spike now routes every `batchexecute` (spike doc, "The miss").
- `test_video_extend_migrated_e2e.py::test_e2e_extend_submit_contract_at_zero_cost` fails on
  unchanged develop too (no `rqZuUc`, no model key in `fZytfe`): filed as #1180, unrelated.

## Gates

| Gate | Result |
|---|---|
| `check_repo_hygiene.py`, `check_website_docs_pii.py`, `generate_website_docs.py --check`, `check_council_memory.py`, `generate_plugin_skills.py --check` | ✅ clean |
| `check_doc_links.py` | ✅ after this ledger (the only misses were links to it) |
| ruff, ruff format, pyright strict | ✅ clean, 615 files formatted, 0 errors |
| `pytest --cov` (`-n 8`) | ✅ 6056 passed, 12 skipped, 93% |
| `check_release_artifacts.py` | ✅ release artifacts OK |
| `/gflow:doc-review` | _Council verdict: YELLOW across 3 auditors (completeness YELLOW, cross-reference GREEN, drift GREEN); no false claim. Fixed in the release-prep commit: the USAGE exit-7 row, the i2v and r2v exit-7 lines and MCP.md's retryable section now say a refused video submit is aborted unsent (hint: *billed nothing*) and how to tell it from one already sent; USAGE's Omni 1.2 section says start+end works; the i2v `--model` help and the gflow-cli skill carry the 10s end-frame cap. Deferred: no Omni model is named in `llms.txt`/README (pre-existing). Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._ |

## Post-tag evidence

| Check | Result |
|---|---|
| Signed tag | `v0.96.1` on `8a6b04ee`, SSH-signed (`git cat-file -p v0.96.1` carries one `BEGIN SSH SIGNATURE`) |
| `Release` workflow | run 42450733650: success (`build-and-publish`, `mcp-registry / publish`) |
| GitHub Release | `v0.96.1`, published 2026-11-08T05:26:20Z, not a prerelease |
| PyPI | `https://pypi.org/pypi/gflow-cli/0.96.1/json` lists `gflow_cli-0.96.1-py3-none-any.whl` and `gflow_cli-0.96.1.tar.gz` |
| Release PR | #1182 merged into `main` (merge commit `5bc95e09`), every check green; back-merge `41928a05` into `develop` |
| Reporter | thanked on #1178 with a request to test 8s and 10s start+end and report the over-10s behaviour on Ultra |
