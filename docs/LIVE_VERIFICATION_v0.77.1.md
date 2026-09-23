# Live verification — v0.77.1

**Date:** 2026-09-17 · **Host:** Windows 11 Pro 10.0.26200 · **Flow host served:** `flow.google.com`
**Cost:** $0 — no Veo credits spent. No generation was submitted in any run below.

Step 4b of [`/gflow:release`](../skills/release/SKILL.md). Every user-facing change in this
release is exercised against the real thing, or its blocker is named. Nothing is listed as
"unverified" that could have been verified — see AGENTS.md § The Iron Law.

This is a patch release of four fixes. Two of them (#846, #848) touch **no Flow surface** —
they are packaging and local install management — so for those "live" means a real clean
install and a real broken venv, not a browser. That is stated per item rather than left to
inference.

---

## 1. Clean Windows install (#846) — VERIFIED, and this is the release's headline

The only bug here that made the published package unusable. Verified as a **controlled A/B on
a clean Python 3.13 venv** carrying only the runtime closure — no dev extras, `colorama`
confirmed absent before the first arm.

### Arm A — `develop` @ 0.77.0, before the fix

```
$ python -c "import importlib.util as u; print(u.find_spec('colorama') is not None)"
False

$ GFLOW_CLI_LOG_FORMAT=text gflow doctor
  File "...\structlog\dev.py", line 88, in _init_terminal
    raise SystemError(
SystemError: ConsoleRenderer with `colors=True` on Windows requires the colorama package installed.
```

### Arm A′ — the same command, stderr piped (the control that explains the blast radius)

```
$ GFLOW_CLI_LOG_FORMAT=auto gflow doctor
[PASS] env.browsers_missing: ok
[PASS] auth.files_present: ok
Overall: issues
```

`AUTO` resolves to the JSON renderer when stderr is not a TTY, so it never builds a
`ConsoleRenderer`. This is why every CI job and every scripted invocation stayed green while
interactive users could not run a single command — and it is the reason the regression test
asserts the **declaration** rather than the behaviour.

### Arm B — this release, 0.77.1, fresh venv, same command

```
$ uv pip install .
 + colorama==0.4.6
 + gflow-cli==0.77.1

$ GFLOW_CLI_LOG_FORMAT=text gflow doctor
[PASS] auth.files_present: ok
Overall: issues
[exited with code 33]
```

Exit 33 is `gflow doctor` findings-present — a successful diagnosis, not an error class
(AGENTS.md § Code style). The point is that the process reached its own report at all.

### MCP twin — a separate surface, run separately

```
$ echo -n "" | GFLOW_CLI_LOG_FORMAT=text gflow mcp run
2026-09-17T05:50:37Z [info] mcp.server.starting cli_version=0.77.1 name=gflow-cli transport=stdio
[exited with code 0]
```

Both doors route through the same Click group callback, so the packaging fix covers both —
but per AGENTS.md § The second law, the twin was **run**, not inferred.

### The 5-layer ledger

| layer | evidence |
|---|---|
| install shape | fresh `uv venv --python 3.13`, runtime closure only, `colorama` absent in arm A and `colorama==0.4.6` resolved in arm B |
| process outcome | arm A `SystemError` from `structlog/dev.py:88`; arm B exit 33, the documented findings-present code |
| structlog invariants | arm B emits its normal TEXT-rendered events; arm A′ emits JSON, proving the AUTO split |
| surface parity | CLI (`gflow doctor`) and MCP stdio (`gflow mcp run` → `mcp.server.starting`) both reached |
| user-confirmable artifact | the reporter's own workaround (`uv pip install colorama`) is no longer needed — the dependency resolves unaided |

**Provenance:** reported from outside by a user on a clean Windows 11 install with the root
cause already localized. Their diagnosis was correct in every particular; see
[#846](https://github.com/ffroliva/gflow-cli/issues/846).

---

## 2. Sign-in window closes on a migrated account (#849) — VERIFIED at merge, PARTIAL here

**What ran on this release head, live, zero credits:**

```
$ GFLOW_CLI_E2E_PROFILE=ffroliva pytest -m e2e_auth
tests/e2e/test_auth_verification_e2e.py  4 passed
```

That file is the e2e coverage of `auth/verification.py` — the module #849's fix changed, and
the only Flow-surface module changed anywhere in this release. It executes
`verify_flow_profile` and `has_migrated_app_session` against a real profile and a real
`myaccount.google.com` response, not a fixture.

**What did NOT run here, and why — a named external blocker.** The end-to-end behaviour #849
describes (the browser window closing by itself) requires a *fresh interactive Google
sign-in*: typing a password and clearing 2FA at Google's own consent screen. That is a human
action this session cannot perform, and it is not a credential problem that could be worked
around — it is the interaction itself. Per AGENTS.md § The Iron Law this is a permitted
exception **because it is named**, not because it was inconvenient.

It is not unverified work, though: the arm-by-arm live verification was done before merge and
is recorded on [PR #851](https://github.com/ffroliva/gflow-cli/pull/851), which cites `4
passed` on the live zero-credit `e2e_auth` suite against a real profile. This release re-ran
that same suite on the release head and got the same result.

---

## 3. `gflow update` detects a half-replaced install (#848) — VERIFIED

No Flow surface; this is local package management. The load-bearing new logic is
`update_check._import_failure`, which asks a **fresh, isolated interpreter** (`-I`) whether
the installed package still imports — because `_version_in_venv` reads `*.dist-info` and never
touches the modules the next command will import.

Exercised on the release head against two real venvs, one deliberately broken by deleting an
installed module:

```
intact venv (0.77.0)     rc=0  -> importable
half-replaced venv       rc=1  -> ModuleNotFoundError: No module named 'gflow_cli.observability'
_import_failure() here         -> None
```

All three arms matter. The third is the safety contract in the docstring: a probe that cannot
run must return `None`, so *an environment we failed to ask about is never reported as a
broken one*.

Also confirmed on a real PyPI install (`pip install gflow-cli==0.77.0`), which is the shape
`gflow update` actually manages:

```
$ gflow update --check
{"installer": "pip", "event": "update.installer_detected", "cli_version": "0.77.0"}
gflow-cli 0.77.0 is up to date.
```

And that a source checkout is still refused rather than half-managed — exit 11,
`ConfigurationError`, with the reinstall hint.

---

## 4. Migrated-host address scan is linear (#852) — VERIFIED

No Flow surface of its own; it is the scan inside `auth/verification.py`'s migrated-host
probe. Measured on the release head, against the base64-shaped input that produced the
original blow-up (the character class covers the whole URL-safe base64 alphabet, which a
Google page is full of):

| input | v0.77.0 (measured on #852) | v0.77.1 (measured here) |
|---|---|---|
| 5 000 chars | 0.12 s | 0.014 ms |
| 10 000 chars | 0.48 s | 0.004 ms |
| 20 000 chars | 2.00 s | 0.005 ms |
| 40 000 chars | **12.07 s** | **0.020 ms** |
| 440 000 chars | — (not attempted) | 0.113 ms |

Output is unchanged for every address shape, including the Workspace and custom domains #791
turned on:

```
find_emails("contact ffroliva@gmail.com and flavio.oliva@riskcontrollimited.co.uk here")
-> ['ffroliva@gmail.com', 'flavio.oliva@riskcontrollimited.co.uk']
```

The live half is covered by item 2: the same `e2e_auth` run executes `find_emails` over an
actual `myaccount.google.com` response.

---

## What else ran, and what failed

The full `-m e2e_auth` sweep against the live `ffroliva` profile was **39 passed, 7 failed, 12
skipped**. The 7 failures are recorded here rather than omitted, with the reason they do not
gate this release:

```
test_aisandbox_auth_live.py::test_rest_upload_image_authenticates_after_sapisidhash
test_credits_e2e.py::test_credits_http_fast_path_live
test_credits_mcp_e2e.py::test_mcp_get_credits_returns_a_real_balance_for_one_profile
test_credits_mcp_e2e.py::test_mcp_get_credits_all_profiles_preserves_partial_results
test_incident_quality_e2e.py::test_incident_bundle_diagnostic_quality
test_transports_e2e.py::test_e2e_agent_mode_recovered_before_mode_switch
test_transports_e2e.py::test_e2e_agent_chat_panel_recovered_before_mode_switch
```

All seven raise `AuthExpiredError` from `_common.py:321` — Flow serving an
`accounts.google.com` sign-in route instead of the gallery, i.e. account/session state on this
workstation (the lost `ya29` Bearer and the `/about`-state profile already on record), not
code.

**That claim was controlled, not assumed.** `git diff --name-only v0.77.0..HEAD -- src/`
returns exactly four files — `auth/internal_chromium.py`, `auth/real_chrome.py`,
`auth/verification.py`, `update_check.py`. **None** of the seven failing tests' modules changed
in this release, and the one changed Flow-surface module (`auth/verification.py`) is covered by
the 4/4 that passed. The #846 fix itself changes no runtime source at all: its diff is
`pyproject.toml`, `uv.lock`, `CHANGELOG.md` and one test.

These failures are a real gap in what this workstation can verify, and they are not new with
this release. They are tracked with the profile-state issues already open.

---

## Offline gates (for completeness — these are not live verification)

- Impeccable Routine: hygiene, doc links, website-docs PII, website-docs mirror, council
  memory, `ruff check`, `ruff format --check`, `pyright src` (0 errors) — all green.
- Offline suite: **4477 passed, 24 skipped**.
- PR [#857](https://github.com/ffroliva/gflow-cli/pull/857): 16/16 CI checks green, including
  SonarCloud and the `Dependency resolution drift (no lockfile)` job that installs from the
  declared ranges rather than the lockfile — the job that would have caught #846 had colorama
  ever been declared wrongly.
