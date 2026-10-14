# Spike — why a Flow visit does not renew the credits session (2026-10-14)

**Question.** `gflow credits list --refresh` renewed 8 stale profiles by loading
`FLOW_URL`, every renewal verified, and aisandbox-pa still answered 401 for all 8.
`gflow-api credits --refresh` recovered the same kind of profile. What differs?

**Script.** [`scripts/dev/spike_labs_nextauth_refresh.py`](../../../scripts/dev/spike_labs_nextauth_refresh.py)
— logs request paths, statuses and the labs session body's `error` key; never a token.

## Observations

1. `labs.google/fx/tools/flow` 308s to `flow.google.com` on all 8 profiles
   (`auth_renew_landed host=migrated`). labs' NextAuth never runs on that visit.
2. A stale profile's `GET /fx/api/auth/session` answers **200 with a token** plus
   `error: ACCESS_TOKEN_REFRESH_NEEDED` and an `expires` in the past. That expired
   token is what aisandbox-pa rejects.
3. `quick` mode (load `labs.google/fx/`, ask the session at `domcontentloaded`):
   still `ACCESS_TOKEN_REFRESH_NEEDED`, same `expires`.
4. `settle` mode, same profile (load, wait for `networkidle` + 2.5 s): the page's own JS
   runs a silent re-sign-in —
   `GET session` → `POST /fx/api/auth/signin/google` → `302 accounts.google.com/o/oauth2/v2/auth`
   → `302 /fx/api/auth/callback/google` → `GET session` — ~2.5 s after load. The session
   then has no `error` and `expires` moves ~24 h ahead.
5. After renewal with polling (`auth/renew.py::_await_labs_refresh`): 7 of 10 profiles
   read a balance (510 → 939 credits). The 3 that still fail all land on Flow's public
   page (`landing=public`) and stay `ACCESS_TOKEN_REFRESH_NEEDED` for the full 20 s;
   `gflow-api credits --refresh` fails on one of them too (control). That is the open
   `/about` entry in [KNOWN_ISSUES](../../../KNOWN_ISSUES.md), and it needs `gflow auth login`.

## Conclusion

Renewal for the credits path must load `labs.google/fx/` and wait for the labs app's
silent re-sign-in to land, polling the session until `ACCESS_TOKEN_REFRESH_NEEDED`
clears (bounded). A fixed sleep works by luck; the poll is the measured contract.
