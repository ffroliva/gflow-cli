# Handover — Issue #15 (`gflow video i2v` 401) — 2026-05-17

## Status: investigation complete, fix NOT started

Root cause found — and it is **not** what issue #15 or the v2 spec assumed.
Full evidence: [`specs/2026-05-17-issue-15-root-cause-findings.md`](specs/2026-05-17-issue-15-root-cause-findings.md).

## Root cause (one line)

`gflow auth login` does not persist the `labs.google` NextAuth session cookie
(`__Secure-next-auth.session-token`) into the profile. The CLI ends up signed
out of the Flow *app* (it has Google SSO cookies but not the app session), so
`create_project` — the first `i2v` step — returns `401 UNAUTHORIZED` and i2v
never reaches `uploadImage`. The "missing Bearer header" hypothesis is disproven.

## Superseded — do NOT implement as written

- `specs/2026-05-17-i2v-uploadimage-401-bearer-auth-design.md` (v2 spec)
- `plans/2026-05-17-issue-15-i2v-bearer-auth.md` (7-task plan)

Both targeted the wrong layer (an aisandbox-pa `Bearer` header). Kept for
history; the findings doc explains why they are wrong.

## Branch `fix/issue-15-i2v-bearer-auth` (off `develop`, pushed)

Committed:
- v2 spec + 7-task plan (superseded — kept for history).
- Root-cause findings doc.
- `feat(api): env-flagged outgoing request-header logging` — `client.py` gained
  `_redact_headers_for_log` + `GFLOW_CLI_LOG_REQUEST_HEADERS=1` request-header
  logging. **Keep it** — genuinely useful diagnostic tooling.
- A merge of `develop` into the branch.

Exploratory probes (local only, `tmp/issue-15-phase1/`, untracked — `tmp/` is
gitignored): `probe_createproject.py`, `har_trace.py`, `probe_cookiedb.py`,
`trace.har` (25 MB HAR), and `*.log` run logs.

## Next step — fresh brainstorm → spec → plan for the AUTH-LAYER fix

The fix belongs in the auth layer: capture and persist the full Flow **app
session** (Playwright `storage_state`, or the live cookie jar *including*
session cookies) at the moment sign-in completes — *before* the Passive-Capture
browser closes — and restore it into `FlowApiClient`'s context. Also strengthen
`auth_login` verification to assert a real `labs.google` app session, not just
`SAPISID` presence.

One open sub-question (findings §5): exactly why the session-token is not
flushed (in-memory session cookie vs. NextAuth callback not completing). One
small probe answers it; do that first.

Skills to use: `superpowers:brainstorming` → `superpowers:writing-plans`; raise
the `architect` and a security review for the session-credential handling.

## Workflow

`develop` is the integration branch; branch from `develop`, PR to `develop`,
`develop` → `main` only for releases. `main` is GitHub-branch-protected
(PR + 5 CI checks, no direct pushes). PRs to `develop` only after the code is
fully functional and E2E tested.
