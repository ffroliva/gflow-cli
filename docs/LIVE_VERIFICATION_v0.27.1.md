# Live Verification — v0.27.1

Release date: 2026-07-07. Headline: **v0.27.0 release follow-up fixes and documentation sync** (Issue #239, PR #261).

Verification run: 2026-07-07, local validation gates, Windows, `.venv` build of the release tree. **Deliberately credit-free**: this patch release contains only default parameter wiring, Rich console escaping, MCP guide documentation synchronization, and user documentation. No live Flow REST api/browser automation code paths were changed, so no live generation credits were spent.

## Scope

| Change | Surface | Verdict |
|---|---|---|
| Wire package version `__version__` as default `version` in `build_handoff()` | `composition.py` | ✅ Unit tests pass (`test_build_handoff_shape_and_schema`) |
| Escape brackets `\[` in plan output mode and refs | `cli_movie.py` | ✅ Unit tests pass (`test_format_scene_line_escapes_brackets_for_rich`) |
| Add `gflow_list_tools` to MCP agent guide | `mcp/resources.py` | ✅ MCP guide resource updated |
| Set `__version__` on FastMCP server on startup | `mcp/server.py` | ✅ MCP tests pass |
| Add HTML anchors for style configuration errors | `docs/MOVIE.md` | ✅ Document checked via `check_doc_links.py` |
| Add `gflow movie` subcommands documentation | `docs/USAGE.md` | ✅ USAGE.md updated and link-checked |

## Evidence ledger (Verification Gates)

All local quality gates executed in order as a merge-readiness baseline:
1. **Hygiene check:** `check_repo_hygiene.py` ran with 0 violations across 505 tracked files.
2. **Internal links check:** `check_doc_links.py` successfully resolved all internal markdown links across 24 files (including `docs/MOVIE.md` and `docs/USAGE.md`).
3. **Lint check:** `ruff check src tests` passed with 0 errors.
4. **Formatting check:** `ruff format --check src tests` verified all files are formatted.
5. **Type check:** `pyright src` completed with 0 errors and 0 warnings.
6. **Tests run:** 2056 tests passed, 16 skipped, 55 deselected.
