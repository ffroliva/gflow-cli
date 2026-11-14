# Spike: concurrent clones of one signed-in Chrome profile (2026-11-12)

**Verdict: WORKS at 1 clone.** A copy of a signed-in profile directory, caches left out, drives
flow.google.com at the same time as the original, and neither signs the other out. So live e2e
can shard across one clone per xdist worker. Fan-out above 2 browsers per account is **not
measured yet**.

## Question

e2e runs one test at a time because one Chrome profile cannot be opened by two processes (the
profile lock, exit 11). The lock is keyed by the profile **directory**
(`profile_lease.py::_canonicalize`), so a copy gets its own lock. Open question: do N copies of
one Google session survive running together, or does Google rotate the session cookies and sign
the copies out?

## Method

`scripts/dev/spike_profile_clone_concurrency.py denon82 1`:

1. Copy `profile_denon82` to `profile_denon82-spike1`, leaving out caches and Chrome's lock files
   (`SKIP` in the script).
2. Run `gflow credits user --profile <p> --json` with `GFLOW_CLI_WIRE_READS=0`, so it opens Flow in
   the browser, on the original and the clone **at the same time**.
3. Run it again on each, one after the other, to check both are still signed in.
4. Delete the clone.

No credits spent. Output records exit codes and durations only.

## Results (denon82, host served: flow.google.com)

| Step | Original | Clone |
|---|---|---|
| Clone copy | — | 4.4 s, **265 MB** (source 1,574 MB) |
| Concurrent credits read (18 s wall) | exit 0 | exit 0 |
| Sequential recheck | exit 0 (10 s) | exit 0 (10 s) |

## Other profiles on this machine (why denon82)

- `ci-probe`: signed out (Flow redirects to `/about`, the Google re-verification state).
- `default`: refused by the engine-downgrade guard (last written by Chrome 153; gflow would open
  it with bundled Chromium 149). The guard is right: opening it could wipe the session.

## Not measured (next steps before relying on wide fan-out)

- More than 2 concurrent browsers on one account (4, 8, 12): Google throttling, reCAPTCHA scoring.
- The per-account daily image quota and the credit balance are shared by every clone; tests that
  spend either must be grouped (`xdist_group` + `--dist loadgroup`) or budgeted.
- Long sessions: whether a clone stays valid over a full 60-minute e2e run.
- A clone of a clone, or reusing clones across runs (they were deleted here).
