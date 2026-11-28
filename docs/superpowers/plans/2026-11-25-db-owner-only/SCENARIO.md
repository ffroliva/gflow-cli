# Scenario: owner-only `gflow.db` (#1437)

The cause is proven, so the reproduction is ordinary tests rather than Gherkin:
`DataStore.open` in `src/gflow_cli/data/store.py` called `mkdir` and `sqlite3.connect`
without setting permissions. Every writable open goes through that method, so the
fix is there. Read-only opens create nothing and change nothing.

## Coverage map

| Dimension | Active | Reason |
|---|---|---|
| D5 Concurrency | yes | several processes and connections open one file |
| D6 Data layer | yes | the changed code |
| D7 Errors | yes | `open` must still raise only its typed errors |
| D8 Cross-platform paths | yes | POSIX modes and Windows ACLs differ |
| D11 Input validation | yes | `GFLOW_CLI_DB_PATH` accepts any path |
| D12 Observability | yes | two new warning events |
| D13 MCP parity | yes | the worker and MCP server are long-running and open the same file |
| D1, D2, D3, D4, D9, D10 | no | no account, browser, Flow or batch code is touched |

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D6 | A second open in one process closes a descriptor on the database and drops its POSIX locks | Critical | An existing database is never opened and closed outside SQLite | Unit (POSIX) |
| 2 | D11 | The parent of `GFLOW_CLI_DB_PATH` is shared (`0755`, `2770`, `1777`) | Critical | Its mode and the ACLs of neighbouring files do not change | Unit (both) |
| 3 | D11 | `GFLOW_CLI_DB_PATH` names a directory, or a sidecar name is a symlink to another file | Critical | Nothing that is not the database is changed | Unit (POSIX) |
| 4 | D8 | The database is a symlink, dangling or live | High | The target and its sidecars are `0600` | Unit (POSIX) |
| 5 | D6 | An older database and sidecars are `0644` or `0640` | High | All are `0600` after an open | Unit (POSIX) |
| 6 | D6 | The file is readable between creation and tightening | High | It is `0600` when SQLite connects, and created so | Unit (POSIX) |
| 7 | D7 | `GFLOW_CLI_DB_PATH` is blank | High | `DataStoreError` (exit 16), no raw `ValueError` | Unit (both) |
| 8 | D7 | Permissions cannot be changed (another owner, FAT volume) | High | A warning is logged and the open succeeds | Unit (both) |
| 9 | D13 | gflow recreates a deleted database in a long-running process on Windows | High | The new file gets its own ACL | Unit (Windows) |
| 10 | D8 | The first run on Windows is elevated | High | No directory ACL is written, so the normal user keeps access | Unit (Windows); an elevated run is not tested |
| 11 | D5 | Two processes open a missing database at once | Medium | The second sees an existing file and continues | Review probe |
| 14 | D5 | Two threads create the database at once | High | The second waits until the first has closed the new file | Unit (both) |
| 12 | D12 | A non-owner opens a shared database repeatedly | Low | One warning per file it cannot tighten, on each open | Not tested |
| 13 | D6 | Windows `-wal` and `-shm` files in a readable directory (fresh open, reopen, crash leftovers, symlinked DB) | High | Restricted to the current user on `DataStore.open` and re-hardened across SQLite sidecar recreations | Unit (Windows) |

## Must-cover before merge (Critical + High)

Rows 1 to 9, 13 and 14 are covered by `tests/data/test_store.py`. So is row 10, except for a
real elevated run.

## Deferred (Medium + Low)

Rows 11 (checked by a probe, no test) and 12; explicit grants that survive on an existing
Windows database or sidecar file; and the brief window between SQLite creating `-wal`/`-shm`
and `DataStore.open` stripping inherited directory ACEs.
