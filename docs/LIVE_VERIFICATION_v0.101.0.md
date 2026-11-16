# Live verification — v0.101.0

Release ledger for v0.101.0. It covers:
- Breaking output flag migration: `-o/--output [json|table|id]` format selector across all commands (#1158).
- Strict `--out-file PATH` enforcement across all file-writing commands, with exit code 2 and actionable remediation when file paths are passed to `-o/--output` (#1158).
- MCP parity: removal of deprecated `output` parameter from `gflow_generate_image` and `gflow_generate_video` (`out_file` only).
- Exposed format choices in `gflow --help --json` via `OutputFormatType(click.Choice[str])`.

## Environment

| | |
|---|---|
| Profiles | `ffroliva` |
| Host Flow served | `flow.google.com` |
| Entrypoint | CLI & MCP |
| OS | Windows 11 |
| Tree | `chore/release-v0.101.0`, cut from `develop` (`7eb0efa3`) |
| Veo credits spent | 0 credits (format and CLI surface verification) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| `--out-file` flag enforcement & `-o` format selector (#1158) | `tests/e2e/test_out_file_cli_e2e.py` | ✅ passed: verified `image t2i --out-file <path>` writes asset, while `-o <path>` fails immediately with exit 2 and loud remediation | $0 |
| Universal format selector & remediation suite (#1158) | `tests/cli/test_output_format.py`, `tests/cli/test_out_file_deprecation.py` | ✅ 44 unit tests passed: `-o json\|table\|id` verified across commands, case-insensitivity, conflict guards, bare IDs on stdout with stderr error routing | $0 |
| Output hardening BDD feature (#1158) | `tests/features/output_hardening.feature` | ✅ 5 scenarios passed: `--out-file` destination and `-o` format options verified | $0 |
| MCP Parity Gate (#1158) | `tests/mcp/test_cli_parity.py`, `tests/mcp/test_server.py` | ✅ passed: verified complete removal of deprecated `output` parameter from generate tools; CLI and MCP options fully symmetric | $0 |
| Version verification | `gflow --version` | ✅ `gflow, version 0.101.0` | $0 |

### What these runs do not prove

- Live generation under Flow credit billing on this specific release tag (video generation unchanged from v0.100.0/v0.100.1).
