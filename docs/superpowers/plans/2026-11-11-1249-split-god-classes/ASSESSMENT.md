# Assessment of #1249: CONFIRMED (confidence 9/10)

**Issue:** Architecture: split god classes (FlowApiClient 3.4k lines) and enforce a size limit.
**Class:** refactor / tech debt. Not a bug. No user-visible behaviour to reproduce.

## Verified against the code (2026-11-11, develop @ b7fc3b3f)

| Claim in the issue | Measured | Source |
|---|---|---|
| `FlowApiClient` 3,410 lines, 83 methods (36 public) | 3,410 lines (389-3798) | AST of `src/gflow_cli/api/client.py` |
| `client.py` 4,169 lines | 4,169 | `wc -l` |
| No size/complexity rule in ruff `select` | `select = ["E","F","W","I","B","UP","N","T20","S"]` | `pyproject.toml` |
| ruff C901 / PLR0915 / PLR0904 offenders | 38 / 16 / 2 (PLR0904 is preview-only in ruff 0.17.4) | `ruff check src --preview --select ...` |
| The 400-line rule is prose only | `docs/ARCHITECTURE.md` "When a module file grows past 400 lines..." | doc |

## The seam inside FlowApiClient (AST, self-call graph)

The class has **five** responsibilities, not two:

1. **Session lifecycle** (397-1408): `__init__`, `__aenter__/__aexit__`, launch kwargs and guard,
   cookie pre-read, persistent context, page pool, transport setup, close.
2. **Account bootstrap** (834-1148): account chooser, locale resolution.
3. **Session primitives** (1412-1906): recorder hooks, incidents, page checkout, access token,
   `_post_json/_patch_json/_get_json`, retry.
4. **Workspace**: projects, agent info, uploads, workflows, scenes, project listing, concat.
5. **Generation**: `generate_*`, image drivers, reCAPTCHA mint, poll, downloads, credits;
   plus **media edits** (upsample, extend) and **characters** (CRUD + character image).

Coupling the issue names is real: `generate_image`/`generate_images_batch`/`generate_video` call
`self.create_project` (workspace); character image generation calls `download_image`
(generation) and `get_character`.

## Surfaces

CLI and MCP both reach FlowApiClient through `services/`; neither changes. MCP parity is
untouched (no leaf, option or payload key moves).

## e2e gate

Any PR that moves FlowApiClient code touches `api/`, so G6 applies: `e2e_auth`, `e2e_image`
and `tests/e2e/test_credits_mcp_e2e.py` on denon82. The gate PR and the characterization PR
touch no Flow surface (scripts/CI/tests only) - stated explicitly in those PRs.

## Next

Phase 2: predict (transport-adjacent) -> PREDICT.md.
