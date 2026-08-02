# Live Verification Evidence — Issue #411: Predictable Output Flag (`--output` / `-o`)

- **Feature**: Predictable filename output flag (`--output` / `-o`) for `gflow image t2i`, `gflow image i2i`, `gflow video t2v`, `gflow video i2v`, and MCP server tools (`gflow_generate_image`, `gflow_generate_video`).
- **Branch**: `feature/411-predictable-output-flag`
- **Date**: 2026-08-02
- **Isolated Infrastructure**: Docker MinIO container (`minio/minio:RELEASE.2024-06-13T22-53-53Z`) + isolated DB workspace (`GFLOW_CLI_HOME=tmp/isolated_home`).

---

## 5-Layer Evidence Ledger

| Layer | Evidence & Contract Verified | Result |
|---|---|---|
| **1. File Count** | Single-asset `--output custom.png` creates exactly 1 file at target path. Multi-asset (`--count 2 -o custom.png`) creates `custom_1.png` and `custom_2.png`. | ✅ **PASSED** |
| **2. Magic Bytes / Payload** | Local PNG files verify `\x89PNG\r\n\x1a\n` header. MP4 videos verify ISO-BMFF `ftyp` container header. MinIO S3 objects match raw byte payloads. | ✅ **PASSED** |
| **3. Dimensions / Target Paths** | Target path parents are auto-created when nested (e.g. `./deep/nested/output.png`). Output paths match explicitly requested stems. | ✅ **PASSED** |
| **4. Structlog & Isolation Invariants** | Task logs reflect explicit `output_file` resolution. Zero production database (`gflow.db`) pollution (all DB writes isolated to ephemeral workspace). | ✅ **PASSED** |
| **5. User-Confirmable Artifacts** | Full test execution across CLI, MCP, and Docker MinIO storage integration suites: | ✅ **121 / 121 PASSED** |

---

## Test Execution Summary

1. **CLI Predictable Output Suite** ([`tests/cli/test_predictable_output.py`](../../tests/cli/test_predictable_output.py))
   - `test_t2i_explicit_output_single_file`: PASSED
   - `test_t2i_explicit_output_multi_count`: PASSED
   - `test_t2i_explicit_output_nested_dir`: PASSED
   - `test_i2i_explicit_output_file`: PASSED
   - `test_video_t2v_explicit_output_file`: PASSED
   - `test_video_i2v_explicit_output_file`: PASSED

2. **MCP Parity & Server Suite** ([`tests/mcp/`](../../tests/mcp/))
   - `tests/mcp/test_cli_parity.py`: 4/4 PASSED
   - `tests/mcp/test_server.py` & wired tools: 103/103 PASSED

3. **Docker MinIO Container Integration Suite** ([`tests/integration/test_storage_s3.py`](../../tests/integration/test_storage_s3.py))
   - `test_predictable_output_s3_single_file`: PASSED (written to `s3://gflow-test/gflow/renders/custom_shot.png` in Docker MinIO container, read-back verified)
   - `test_predictable_output_s3_multi_file`: PASSED (written to `s3://gflow-test/gflow/batch/output_1.png` and `output_2.png`)
   - 6 base S3 integration tests: PASSED

---

## Verification Conclusion

All 5 verification layers pass cleanly. Output filenames and cloud storage keys behave predictably across single-asset, multi-asset, nested directory, and MinIO S3 storage environments without polluting the live database.
