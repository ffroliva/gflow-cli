# Scenario: close SQLite connection when `DataStore.open` fails (#1495)

The cause is proven in local Python and SQLite code, so the reproduction is behavioral tests against real SQLite files rather than Gherkin: `DataStore.open` in `src/gflow_cli/data/store.py` opens a `sqlite3.Connection` and runs `PRAGMA` statements and `apply_migrations()` without closing `conn` on failure, and `DataStore.open_readonly` guards only `sqlite3.Error` around its `PRAGMA` probe while leaving `cls(conn=conn, path=path)` outside the guard. All writable catalog and queue callers across CLI, MCP, worker, and UI open the database through `DataStore.open`, and diagnostics (`doctor`, `inspect_schema`) open through `DataStore.open_readonly`, so fixing both methods in `src/gflow_cli/data/store.py` covers every caller.

## Coverage map

| Dimension | Active | Reason |
|---|---|---|
| D5 Concurrency | yes | Leaked connections hold file/WAL locks across sequential opens in the same process |
| D6 Data layer | yes | `DataStore.open` and `DataStore.open_readonly` connection lifecycle and transaction rollback |
| D7 Errors | yes | `DataMigrationError`, `DataStoreError`, `OSError`, and `BaseException` propagation and `__cause__` integrity |
| D8 Cross-platform paths | yes | Windows pins open SQLite handles (`WinError 32` on rename/unlink); all platforms emit `ResourceWarning` on unclosed GC |
| D13 MCP parity | yes | Long-lived MCP and worker processes keep running after a failed open and re-open the path |
| D1, D2, D3, D4, D9, D10, D11, D12 | no | No auth/account, WAF, browser selectors, batch manifest, network transport, headless display, new CLI/input validation, or new logging events are touched |

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D6, D8 | Failing `PRAGMA` on a corrupt or non-SQLite file (`sqlite3.DatabaseError` wrapped as `DataStoreError`, `route="data.open"`) | Critical | `conn` is closed before raising; renaming, unlinking, and re-opening the path succeed immediately even while the exception is live | Unit (real SQLite file) |
| 2 | D6, D8, D13 | `apply_migrations` raises `DataMigrationError` (newer schema version or checksum drift on an existing WAL database) | Critical | `conn` is closed before raising; `DataMigrationError` propagates unwrapped; renaming, unlinking, and re-opening succeed in the same process | Unit (real SQLite file) |
| 3 | D6, D7 | Migration SQL statement fails inside `DataStore.transaction(immediate=True)` (`DataStoreError`, `route="data.transaction"`) | Critical | Partial DDL rolls back, `conn` is closed before raising, and `DataStoreError` propagates with no leaked handle | Unit (real SQLite file) |
| 4 | D7, D8 | Post-connect `OSError` during migration loading (`DataStoreError`, `route="data.open"`) | High | `conn` is closed before raising and `DataStoreError.__cause__` preserves the `OSError` | Unit (real SQLite file) |
| 5 | D5, D6, D7 | Post-connect `BaseException` (`KeyboardInterrupt`) in `DataStore.open` and `DataStore.open_readonly` | High | `conn` is closed before raising and `BaseException` propagates unwrapped | Unit (real SQLite file) |
| 6 | D6, D7 | Pre-connect failure (`_secure_store_files` raising `OSError` or `sqlite3.connect` failing on a directory or blank `Path()`) | High | Raises `DataStoreError` without `UnboundLocalError` on `conn` | Unit (existing tests in `tests/data/`) |

## Must-cover before merge (Critical + High)

Rows 1 to 5 are covered by new behavioral tests in `tests/data/test_store.py` asserting both immediate Windows file-rename availability while the raised exception is live and zero `ResourceWarning`s across all platforms. Row 6 is covered by existing tests in `tests/data/test_store.py` (`test_open_on_a_blank_path_is_a_data_store_error`, `test_open_restricts_the_db_but_not_its_neighbours`) and `tests/data/test_store_migrations.py` (`test_open_wraps_oserror_into_datastoreerror`).

## Deferred (Medium + Low)

None.
