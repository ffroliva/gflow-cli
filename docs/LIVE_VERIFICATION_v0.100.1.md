# Live verification — v0.100.1

Release ledger for v0.100.1. It covers:
- Locale-invariant count selector for settings pane on `flow.google.com` under non-English account locales (e.g. German `hl=de`, #1312, #1315).
- Package metadata updates: OS classification keywords and marketplace URLs in `pyproject.toml`, LobeHub manifest `lhm.plugin.json`.

No CLI flag, exit code, or MCP schema changes.

## Environment

| | |
|---|---|
| Profiles | `ffroliva` |
| Host Flow served | `flow.google.com` |
| Entrypoint | `MigratedComposer` in `gflow_cli.api.transports` |
| OS | Windows 11 |
| Tree | `chore/release-v0.100.1`, cut from `develop` (`9a8a1e94`) |
| Veo credits spent | 0 credits (settings pane inspection & toggle selection) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| German count radio binding ($0 E2E) (#1312) | `tests/e2e/test_migrated_locale_e2e.py::test_e2e_count_binds_in_a_german_pane` | ✅ passed in 15.17s: navigated to `flow.google.com` under `hl=de`, exercised setting count=2 (`2-mal`), verified checked, persisted and verified count=1 (`1-mal`), zero drift, zero errors | $0 |
| Locale-invariant count selector spike probe (#1312) | Live `flow.google.com` settings pane under German locale (`hl=de`) via `scripts/dev/spike_1312_count_locale.py` | ✅ passed: settings pane opened with German count radios (`1-mal`, `2-mal`, `3-mal`, `4-mal`), `_select(axis='count', text='x1')` matched `1-mal` without drift, selected it with `checked=true`, and closed the pane cleanly (exit 0) | $0 |
| Offline count matcher & contract suite | `tests/api/transports/test_migrated_composer.py`, `tests/api/transports/test_migrated_composer_contract.py` | ✅ 221 passed across all locales (`1-mal`, `1 vez`, `1 fois`, `1x`, `bare 1`), 1 skipped | $0 |
| Version | `gflow --version` | ✅ `gflow, version 0.100.1` | $0 |

### What these runs do not prove

- Account locales beyond German (`hl=de`). Tested offline via unit test parameter matrix for Portuguese (`1 vez`), French (`1 fois`), etc.
