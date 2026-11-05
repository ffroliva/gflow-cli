# Spike: does the workspace check accept a revoked session? (#1144)

**Date:** 2026-11-05 · **Profiles:** `probe-1026` (disposable, signed out afterwards), `denon82` (a copy only)
· **Scripts:** [`spike_revoked_session.py`](../../../scripts/dev/spike_revoked_session.py), [`spike_workspace_oracle.py`](../../../scripts/dev/spike_workspace_oracle.py)
· **Cost:** $0

## Question

`/gflow:predict` and the council made this the merge condition for #1144: with a session revoked on
the server and its cookies still on disk, does the visible New-project button still authenticate?

## Observed

| Case | My Account (control) | Workspace (forced) | Landed |
|---|---|---|---|
| `probe-1026`, signed in | authenticated | authenticated | workspace |
| Same, after "sign out" under Your devices | authenticated | authenticated | workspace |
| Copy of `probe-1026`, original then logged out | None | None | `/about` |
| Copy of `denon82`, live, no logout | None | authenticated | workspace |
| `denon82`, the original | authenticated | authenticated | workspace |

- Signing out from Your devices did not end this session: My Account still accepted it, so that run
  was void. The likely reasons are the wrong entry or a delay, and neither was established.
- Logging the original out through `accounts.google.com/Logout` removed its `SID`, and the original's
  `gflow auth status` then reported it signed out of Flow.
- The copy kept the cookies frozen from before that logout, and the workspace refused it.

## Reading

The fallback refuses a session that was ended on the server. The live copy is the positive control:
the same copying leaves the workspace check able to authenticate, so the refusal comes from the
logout and not from the copy.

My Account rejected the live copy, so a `None` in the control arm of a copy does not prove a
revocation by itself. That does not affect the fallback, which reads the workspace only when the
My Account request cannot complete. A redirect off My Account stays a refusal.

## Not measured

- More than one account per arm.
- A session ended by a password change or by "sign out of all devices".
- How long a session ended that way stays accepted, if at all.
- One `gflow auth status` on the original `denon82` right after the copy did not verify, and the next
  one a minute later did, on both arms. Its cause is unknown.
