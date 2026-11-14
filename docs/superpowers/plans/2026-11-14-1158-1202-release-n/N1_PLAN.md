# #1158 + #1202, release N+1: the switch (planned, NOT built)

Ships no earlier than one full release cycle after release N (decision on #1158).

## #1158: `-o/--output` selects the format

- `-o/--output json|table|id` on every command that prints structured data. `--json`
  stays as an alias for `-o json`.
- No `yaml` (it would add PyYAML; gh ships without it). Adding a value later is not breaking.
- `id` follows kubectl `-o name`: one id per line for lists, the created id for creates,
  no header, data on stdout, errors on stderr.
- The `json_output.py` shape stays the contract and changes additively only. No version flag.
- A non-format value (`-o clip.mp4`) exits 2 with `--output now selects the format; use
  --out-file clip.mp4`. It never guesses and never writes a file.
- `video upscale -o DIR` exits 2 with `use --out DIR`.
- MCP: `output` removed from `gflow_generate_image` / `gflow_generate_video`; `out_file`
  only. MCP has no format concept (tools always return structured data).
- CHANGELOG **Breaking** entry with the one-line migration.

Tasks (when the time comes): a shared `output_format_option()` that replaces the 29
`--json` call sites one command group per PR, the Click-tree test inverted (every
`-o/--output` is a format choice), a parity-gate exemption for `-o` with its reason, the
MCP `output` removal, docs.

## #1202: new exit codes become the default

- N+1: `GFLOW_CLI_EXIT_CODES=2` is the default; `=1` restores legacy with one stderr
  deprecation line per run.
- N+2: legacy removed, `exit_code_legacy` dropped from `--json` (announced in N+1).
- Code 4 ("accepted, still running") is emitted only once a `--no-wait` / `gflow wait`
  path exists (#1194); until then it stays reserved.
