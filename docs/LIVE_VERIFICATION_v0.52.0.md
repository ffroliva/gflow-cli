# Live Verification Ledger: v0.52.0

**Release Date:** 2026-08-05  
**Version:** v0.52.0  
**Environment:** Windows 11, Python 3.12.11, Profile: `ffroliva` (Chrome UI Automation)  
**Target:** Live Google Flow REST API & Playwright Chromium UI Automation

---

## What Was Live-Verified

1. **Intra-batch reference entity support & DAG sorting (#317):**
   - Verified `BatchPromptItem` parsing of `ref` and `reference_entity` fields.
   - Verified Kahn's algorithm topological sorting for batch prompts and cycle detection.

2. **Character entity provenance recording & video CLI options (#402):**
   - Verified `--reference-entity` and `--reference-entity-name` CLI options on `gflow video` (`t2v`, `i2v`, `r2v`).
   - Ran live E2E tests (`tests/e2e/test_entity_provenance_e2e.py`) against Google Flow (`ffroliva` profile).
   - Asserted `operations.metadata_json` records `entity_ids` and `entity_names` across successful and failed generation runs.

3. **Video duration selector cascade (#451):**
   - Verified duration control selector cascade matches modern Flow editor UI elements (`button`, `[role='button']`, `[role='option']`, `[role='menuitem']`, `[role='tab']`).
   - Asserted fail-closed `UiSelectorDriftError` on probe miss (#288 invariant).

---

## 5-Layer Verification Summary

| Layer | Evidence | Result |
|---|---|---|
| **Layer 1: File count** | DB operation rows created in SQLite `gflow.db` (`operations` and `assets` tables) | 🟢 PASSED |
| **Layer 2: Magic bytes / field value** | `metadata_json` contains `{"entity_ids": [...], "entity_names": [...]}` | 🟢 PASSED |
| **Layer 3: Dimensions/shape** | Generated assets match requested aspect ratio / model shape | 🟢 PASSED |
| **Layer 4: Structlog invariants** | Structlog events `duration_set`, `entity_resolved`, `generation_completed` fired clean | 🟢 PASSED |
| **Layer 5: User-confirmable artifact** | Live E2E tests (`test_t2i_entity_attach_records_provenance`, `test_i2i_entity_attach_records_provenance`, `test_rejected_entity_attach_still_records_provenance`) 3/3 passed | 🟢 PASSED |

---

## Verification Verdict

🟢 **v0.52.0 LIVE VERIFICATION PASSED**
