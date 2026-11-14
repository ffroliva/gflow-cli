# #1158 + #1202, release N: announce, break nothing

Issues: #1158 (output flags), #1202 (stable exit-code core). This plan covers release N
only. The N+1 switch is planned in [N1_PLAN.md](N1_PLAN.md) and is not built here.

## Decision: the file flag is `--out-file`, not `--out`

The #1158 decision comment says "add `--out PATH`". That cannot be done as written:
`--out` already means a **directory** on six commands (`image t2i`, `image i2i`,
`image batch`, `image upscale`, `data download`, and `video upscale` as `-o/--out`).
Adding `--out FILE` to the other seven would make one spelling mean a file on
`video t2v` and a directory on `image t2i`, and adding it to `image t2i`/`i2i` is a
name clash.

The major CLIs keep the file flag and the directory flag apart:

| CLI | File | Directory |
|---|---|---|
| curl | `-o, --output <file>` | `--output-dir <dir>` |
| gh release download | `-O, --output file` | `-D, --dir directory` |

`--output` is reserved for the format in N+1, and `--out` / `--out-dir` already name
directories here, so the file destination becomes **`--out-file PATH`**: it pairs with
the existing `--out-dir`, changes the meaning of no existing flag, and needs no guessing
from the value. MCP follows: `out_file`.

## Predict (condensed, 5 personas)

| Persona | Concern | Verdict |
|---|---|---|
| Wire/transport | No Flow request changes; the queued payload key stays `output_file` | GO |
| Back-compat | `-o/--output PATH` keeps working in N; only stderr gains one line. Scripts parsing stdout `--json` are untouched | GO |
| MCP parity | Two tools take `output` (`gflow_generate_image`, `gflow_generate_video`), not five; add `out_file`, deprecate `output` in its `description=` (agents never read the docstring Args block) | CAUTION: the parity gate is option-level, so CLI and MCP land in one PR |
| Exit codes | Five copies of the `EXIT_CODE_MAP` walk (cli.py, `_cli_helpers.py`, `json_output.py`, `image_batch.py`, `worker/daemon.py`); an opt-in that misses one gives two codes for one failure | CAUTION: collapse to one function first |
| YAGNI | No format detection, no `yaml`, no `gflow wait`; code 4 is reserved and emitted by nothing in N | GO |

Verdict: **CAUTION, proceed**.

## Scenarios (D13: each has its MCP twin)

| # | Scenario | CLI expectation | MCP twin |
|---|---|---|---|
| S1 | New flag | `--out-file a.png` writes there, no warning | `out_file="a.png"` writes there, no deprecation note |
| S2 | Old flag | `-o a.png` / `--output a.png` writes there, ONE stderr warning naming `--out-file` | `output="a.png"` works; its schema `description` and the tool `description=` say it is deprecated (what MCP clients read; no runtime field, per the owner's ask) |
| S3 | Both | `--out-file a.png -o b.png` exits 2, nothing generated | both params set: refused with a bad-param problem, nothing queued |
| S4 | `--json` | warning on stderr, stdout stays one valid JSON document | n/a (tools always return structured data) |
| S5 | Multi-image | `-n 4 --out-file a.png` keeps today's `a_0.png`..`a_3.png` | same through the daemon |
| S6 | `video upscale -o DIR` | works, one warning: `-o` will select the format; use `--out DIR` | `gflow_upscale_video(out_dir)` unchanged (no short form in MCP) |
| S7 | Help | `--out-file` documented; `-o/--output` help says deprecated | tool `description=` says `output` is deprecated |
| S8 | A new command later adds a path-typed `-o/--output` | the Click-tree test fails | parity gate fails until `out_file` is mapped |
| S9 | Exit codes, default mode | identical to today; `--json` error gains `exit_code_legacy` (= `exit_code`) | MCP error payloads gain the same field |
| S10 | `GFLOW_CLI_EXIT_CODES=2` | 4→40, 5→41, 6→42, 7→43, 8→3, 9→44; `exit_code` equals the real process status | MCP reports the same mapped code |
| S11 | `GFLOW_CLI_EXIT_CODES=bogus` | treated as legacy, never crashes | same |
| S12 | Cloud storage (`GFLOW_CLI_STORAGE_URI=s3://…`, #1008) | `video t2v/i2v --out-file` is refused before submit with exit 11 exactly like `-o/--output`, and the message names `--out-file`; nothing billed. Image and other commands keep today's behaviour | MCP video `out_file` refused before queueing, naming `out_file`; `output` still refused, naming `output` |

## Tasks

1. [ ] CLI: `--out-file` on the nine commands, deprecated `-o/--output`, both → exit 2, Click-tree test (agent A).
2. [ ] CLI: `video upscale` `-o` short form deprecated; `--out DIR` stays.
3. [ ] MCP: `out_file` on the two generate tools, `output` deprecated in `description=`, both refused, parity gate mapped (agent B).
4. [ ] e2e: `tests/e2e/test_out_file_e2e.py`, `e2e_image` ($0): CLI `--out-file`, CLI `-o` (warning + file), MCP `out_file`.
5. [ ] Docs, skills + plugin byte copies, plugin commands, examples, llms.txt, README, CHANGELOG "Upcoming breaking change".
6. [ ] #1202: one exit-code function, `GFLOW_CLI_EXIT_CODES=2` opt-in, `exit_code_legacy`, table test, `docs/ERRORS.md` old→new table (separate PR).
7. [x] S12 cloud storage: the CLI refusal names `--out-file` (the deprecation line already maps `-o` to it); MCP names the parameter passed, tests in `tests/cli/test_cloud_storage_local_output.py`, `docs/EXTERNAL_STORAGE.md`.
8. [ ] `/gflow:check`, reviews, Sonar 0, e2e on denon82, release.
