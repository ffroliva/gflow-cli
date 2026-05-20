# Issue #15 — Root-Cause Findings (supersedes the v2 spec hypothesis)

> **Status:** Investigation complete · **Date:** 2026-05-17
> The v2 design spec's hypothesis ("aisandbox-pa REST routes need a `Bearer`
> header") is **INVALIDATED**. The real bug is upstream, in the auth layer.
> No production fix is planned yet — see §5.

## 1. Summary

`gflow video i2v` fails at its FIRST API call — `POST .../trpc/project.createProject`
on `labs.google` — with `401 UNAUTHORIZED`. It never reaches `uploadImage`
(the route issue #15 and the v2 spec targeted).

**Root cause:** `gflow auth login` does not produce a usable `labs.google`
*application* session in the profile. The profile holds Google SSO cookies but
not the NextAuth session cookie that the Flow app's tRPC API authenticates on.
The CLI is, in effect, signed out of the Flow app.

## 2. Evidence

Live reproduction on profile `denon82`, session verified fresh 22 s prior:

1. **Phase-1 gate** — `gflow video i2v` 401s at `createProject`, not `uploadImage`.
2. **`createProject` 401 body** — clean tRPC error:
   `{"error":{"json":{"message":"UNAUTHORIZED","code":-32001,...}}}`.
3. **HAR network trace** (`tmp/issue-15-phase1/trace.har`, 72 entries):
   - The editor URL loads the **logged-out Flow landing page** — hero gallery
     images, marketing video — not the authenticated editor.
   - **No `/api/auth/session` request** — the app session is decided
     server-side; the server saw no valid session.
   - `createProject` sent cookies `_ga, __Secure-next-auth.callback-url,
     __Host-next-auth.csrf-token, _ga_X2GNH8R5NS` — **no session token**.
   - **Nothing on the wire ever `set-cookie`'d a `*session-token`.**
4. **Profile cookie DB** (`profile_denon82/Default/Network/Cookies`, 85 cookies):
   - `labs.google` cookies present: `__Host-next-auth.csrf-token` and
     `__Secure-next-auth.callback-url` — both `is_persistent=0` (session
     cookies) — plus 2 `_ga` analytics cookies.
   - **`__Secure-next-auth.session-token` is absent from the DB entirely**
     (`SELECT ... WHERE name LIKE '%session-token%'` → 0 rows, all hosts).
   - Google SSO cookies (`SAPISID`, `__Secure-1PSID`, …) ARE present.
5. Captured working `samples/captured/05_createProject.json` uses cookies only
   (no `Authorization`) and returns 200 — so the failure is a missing
   *session cookie*, not a missing *header*. The v2 spec's `Bearer` fix would
   not address it.

## 3. Mechanism

`labs.google` runs its own NextAuth session layer on top of Google SSO. Its
tRPC API requires `__Secure-next-auth.session-token`. That cookie is
session-scoped (its NextAuth siblings are `is_persistent=0`) and is **not
persisted into the profile by `gflow auth login`** — even though the user
signs in "to the Flow editor." Re-loading the editor does not re-establish it
(no silent SSO → app-session upgrade on a plain page load).

The `auth_login_success_verified` check only confirms the persistent `SAPISID`
Google cookie exists — it never checks the `labs.google` app session, so it
reports success while the profile is functionally signed out of Flow.

## 4. Impact

- Blocks `gflow video i2v` (and any REST flow that starts with
  `create_project` — likely `t2v` and others).
- Issue #15's v2 spec and 7-task plan are **superseded** — do not implement
  them as written.

## 5. Open question for the fix (one probe still outstanding)

Exactly *why* the session-token is not persisted — candidates:
(a) it is an in-memory session cookie not flushed before the Passive-Capture
    browser closes; (b) the Passive-Capture sign-in never completes the
    NextAuth callback that mints it. Finding 2.4 rules a decryption problem
    OUT (the name is simply not in the DB).

The fix likely belongs in the **auth layer** — capture the full session
(e.g. Playwright `storage_state`, or the live cookie jar including session
cookies) at the moment sign-in completes, *before* the browser closes, and
restore it into the client context. The `auth_login` verification should also
assert a real `labs.google` app session, not just `SAPISID` presence. This
needs its own brainstorm → spec → plan cycle.

## 6. Artifacts

- `tmp/issue-15-phase1/` — probes (`probe_createproject.py`, `har_trace.py`,
  `probe_cookiedb.py`), HAR trace, run logs. Exploratory; `tmp/` is untracked.
- This document supersedes
  `docs/superpowers/specs/2026-05-17-i2v-uploadimage-401-bearer-auth-design.md`
  and its plan `docs/superpowers/plans/2026-05-17-issue-15-i2v-bearer-auth.md`.
