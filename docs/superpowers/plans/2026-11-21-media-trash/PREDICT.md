# Predict: Media Trash and Restore (`#1363`)

## Verdict: GO
**Confidence:** 8.2/10 (Persona scores: Architect 8, Security 9, Performance 9, CLI/MCP UX 7, Devil's Advocate 8). No STOP conditions.

## Summary

The live spike on `flow.google.com` confirmed that moving an item to Trash and restoring it are governed by a single `batchexecute` RPC (`pGCYOe`). The call requires zero credits, carries no reCAPTCHA token slots, and operates directly on `flow_media_id` and `flow_project_id`. Driving this via the wire is mandatory: Flow's UI renders tiles inside an Angular CDK virtual scroller that unmounts off-screen items, making UI-only click automation unviable. Shipping both `gflow media trash` and `gflow media restore` (with strict interactive confirmation unless `--yes`, unambiguous catalog ID resolution, and matching MCP twin tools) provides a robust, restorable, and safe user experience.

---

## Persona findings

### Architect — GO (8/10)
- **Layering & Seam:** The implementation fits the clean architecture boundaries:
  - Transport layer (`api/flow_wire.py` / `FlowApiClient`): sends `pGCYOe` mutations directly.
  - Service layer (`services/media_trash.py`): handles catalog lookup, ambiguity checks, and state transitions.
  - Presentation layer (`cli_media.py` / `mcp/tools.py`): exposes CLI commands and MCP twins.
- **Wire-First vs UI:** The spike demonstrated that UI click automation is structurally compromised because `img[data-media-id]` carries the workflow ID (not media ID) and `cdk-virtual-scroll-viewport` fails to mount off-screen tiles under synthetic scroll. Wire transport via `pGCYOe` bypasses the DOM entirely, requiring only `<media_id>` and `<project_id>`.
- **Fallback Seam:** Follows the proven `flow_wire.py` pattern: attempt direct wire mutation with saved cookies and `at` when valid; fall back to browser page context evaluation when session cookies are stale.

### Security / reCAPTCHA — GO (9/10)
- **Token & WAF Risk:** `pGCYOe` was verified to have `has_token_slot: false`. It requires no reCAPTCHA Enterprise reload or mint, preventing WAF heat accumulation.
- **Destructive Safety:** The operation is soft deletion (restorable Trash), not permanent destruction.
- **Boundary Checks:** All profile path lookups and database queries enforce `GFLOW_CLI_HOME` isolation.
- **Error Redaction:** Problem Details schemas ensure no session tokens, cookie fragments, or raw API payloads leak into logs.

### Performance / Playwright — GO (9/10)
- **Latency Advantage:** Direct wire `pGCYOe` executes in ~500–800 ms, compared to ~50 s for a full Playwright page lifecycle, project navigation, left-nav filter application, tile hover, menu open, and click.
- **Resource Footprint:** Zero headless Chromium instances are required when direct wire cookies are fresh.
- **Scroller Immunity:** Avoids all viewport scrolling and DOM polling overhead.

### CLI and MCP UX / Cross-platform — CAUTION (7/10)
- **Confirmation Contract:** Moving an item to Trash is destructive in the immediate project view. The CLI must require an interactive confirmation prompt (`Move media <id> to Trash? [y/N]`) unless `--yes` / `-y` is passed. Non-interactive sessions without `--yes` must fail fast with exit 2.
- **MCP Twin Parity:** Both `media_trash` and `media_restore` must be exposed as MCP tools. The MCP `media_trash` tool must require a boolean confirmation flag (`confirm: bool = False`) to prevent autonomous agents from accidentally deleting media without deliberate intent.
- **Ambiguity Guard:** Passing an ID that matches multiple catalog rows across different profiles must fail with `DataStoreError` (exit 16), demanding `--profile`.
- **Output Schema:** Under `--json`, output must include `media_id`, `project_id`, `status` (`"trashed"` or `"restored"`), and clear CLI remediation hints for restoration.

### Devil's Advocate — GO (8/10)
- **Why Both Trash and Restore?** Without `restore`, users who make an accidental deletion via CLI are stranded and forced to open a browser. Furthermore, automated E2E tests would leave residual junk in the test account's Trash. Both operations use the identical RPC (`pGCYOe`) with minor payload variations (`[null, null, 1]` vs `""` and mask `["collection_id"]`). Implementing both is minimal code and maximizes safety.
- **Task 1 Dependency:** The exact 17-character field mask string was redacted as `<s:17>` during the spike capture. Capturing this literal string using `--show-mask` is the clear Task 1 prerequisite before implementing wire constants.

---

## High-confidence risks (flagged by 2+ personas)

1. **Off-Screen Tile Unreachability in UI Automation:** Flagged by Architect, Performance, and Devil's Advocate. Attempting to click DOM tiles fails for any asset not in the top viewport row due to Angular CDK virtual scrolling. **Mitigation:** Enforce wire-first implementation via `pGCYOe`.
2. **Accidental Media Destruction:** Flagged by Security and CLI/MCP UX. Trashing removes assets from project workflows. **Mitigation:** Require `--yes` on CLI (or interactive confirmation) and an explicit `confirm: true` parameter on the MCP twin.
3. **Identifier Confusion (`media_id` vs `workflow_id` vs local catalog ID):** Flagged by Architect and UX. **Mitigation:** Resolve input IDs through `DataRepository`, extracting the concrete `flow_media_id` and `flow_project_id` before invoking the wire RPC.

---

## Conflicts resolved

- **UI Automation Fallback vs Wire-Only:** While gflow typically maintains UI automation as a fallback, UI clicks for media deletion are demonstrably broken for virtualized items. Wire execution (either via direct HTTP or via browser-evaluated `batchexecute` POST) is the only reliable transport.

---

## Required mitigations before EXECUTE

1. **Capture Field Mask Literal:** Run Task 1 with `--show-mask` to record the exact 17-character protobuf field mask string.
2. **Strict Confirmation Flags:** Ensure CLI requires `--yes` and MCP requires `confirm=True`.
3. **Dual Surface Symmetry:** Ship matching `media_trash` and `media_restore` commands and MCP tools simultaneously.
