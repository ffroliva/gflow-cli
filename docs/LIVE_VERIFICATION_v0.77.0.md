# Live verification — v0.77.0

**Date:** 2026-09-16 · **Host:** Windows 11 · **Flow host served:** `flow.google.com`
**Cost:** $0 — no Veo credits spent. No generation was submitted in any run below.

Step 4b of [`/gflow:release`](../skills/release/SKILL.md). Every user-facing change in this
release is exercised against live Flow, or its blocker is named. Nothing is listed as
"unverified" that could have been verified — see AGENTS.md § The Iron Law.

---

## 1. Migrated-host login (#791) — VERIFIED, and this is the release's headline

The one change users will feel. Verified as a **controlled A/B on a real Google Workspace
account**, with the profile's cookies untouched between arms, so the only variable is the code.

**Account:** `flavio.oliva@riskcontrollimited.co.uk` — a Google Workspace custom domain, not
`@gmail.com`. That matters; see § 2.

### Arm A — released 0.76.0 (no fallback)

```
gflow auth login --profile flavio.oliva --browser chrome
{"event": "auth_login_browser_closed_by_user", "cli_version": "0.76.0"}
{"event": "auth_chrome_marker_rolled_back", "outcome": "google_session_only"}
{"event": "auth_flow_session_unverified", "outcome": "google_session_only"}
Authentication credential missing: Signed in to your Google account, but the Flow app
sign-in wasn't completed.
[exited with code 8]
```

### Arm B — this release, same profile, same cookies, nothing re-entered

```
{"event": "auth_migrated_host_fallback_authenticated",
 "detail": "labs NextAuth absent; flow.google.com OSID session + SSO cookies present"}
{"event": "auth_flow_session_verified",
 "user_email": "flavio.oliva@riskcontrollimited.co.uk", "probe": "on_disk"}
[OK] Flow session verified (flavio.oliva@riskcontrollimited.co.uk).
[exited with code 0]
```

### The 5-layer ledger

| layer | evidence |
|---|---|
| **artifact** | `profile_flavio.oliva/.gflow_account` written: `flavio.oliva@riskcontrollimited.co.uk` |
| **shape** | `FlowSessionStatus.outcome == authenticated`, `user_email` populated |
| **invariants** | `auth_migrated_host_fallback_authenticated` then `auth_flow_session_verified`; **no** `auth_chrome_marker_rolled_back` |
| **state** | `channel_for_profile(profile)` → `chrome` (was `None` after arm A) |
| **user-confirmable** | `gflow auth list` shows the account instead of `unknown`; exit 0 instead of 8 |

### Re-run after merge, from `develop`

```
verify_flow_profile(profile_flavio.oliva) -> outcome=authenticated
                                             email=flavio.oliva@riskcontrollimited.co.uk   3.1s
```

Verified on the merged mainline, not only on the PR branch.

### Refusal path — fail-closed, measured

The oracle must not say yes too easily. Swept all 10 local profiles: **5 reproduce #791**, and
exactly one carries both cookies the gate requires.

| profile | cookies | SAPISID | flow OSID | result |
|---|---:|---|---|---|
| default | 51 | ✅ | ❌ | refused |
| ffroliva.bak-224933 | 44 | ✅ | ❌ | refused |
| gflowclireview | 53 | ✅ | ✅ | **authenticated 3/3** |
| pr389fresh2 | 44 | ✅ | ❌ | refused |
| promo-denon82 | 53 | ✅ | ❌ | refused |

4 refusals, 1 upgrade, 0 false positives.

---

## 2. Workspace-domain coverage — VERIFIED

An earlier revision gated authentication on `[\w.+-]+@[\w-]*\.?gmail\.com`. Measured against
that pattern:

| address | old pattern |
|---|---|
| `denon82@gmail.com` | MATCH |
| `dev@axelate.io` | **NO MATCH** |
| `user@mycompany.com` | **NO MATCH** |
| `user@googlemail.com` | **NO MATCH** |

So #791 would have stayed open for every Google Workspace account while appearing fixed, and
`docs/AUTHENTICATION.md:474` documents Workspace SSO as supported. The shipped code decides on
the landing URL, never the address. § 1's account **is** a Workspace domain, so § 1 is this
section's verification.

**Label extraction**, measured on a live `myaccount` response (1.28 MB): 9 email matches, all 9
the account's own address, 0 competing candidates — so first-match was right by luck. The
shipped code takes the most frequent, and § 1 confirms it picks correctly on a second,
differently-shaped Workspace page.

---

## 3. Profile-downgrade prevention (#796) — VERIFIED

`channel_for_profile` measured directly, same profile, across the A/B above:

| | after arm A (0.76.0) | after arm B (this release) |
|---|---|---|
| `channel_for_profile` | **`None`** | **`chrome`** |
| marker rolled back | yes | no |

#796 was filed as *"not yet reproduced end-to-end — the chain is read from code."* It is
reproduced, and this release prevents it at the root rather than reporting it better.

---

## 4. MCP Registry publish (#841) — NOT VERIFIABLE BEFORE THIS TAG

The repaired wiring has never run: `release.yml` now **calls** `mcp-registry.yml` instead of
relying on `on: release: published`, which never fired once because GitHub starts no workflow
runs from `GITHUB_TOKEN`-created events.

**This release is its first exercise.** Unlike the previous design, it cannot be rehearsed with
a throwaway Release — only a real tag push runs it. Recorded as a named blocker rather than
claimed. **Check after the tag:**

```bash
gh run list --workflow=release.yml --limit 1        # the mcp-registry job should appear
mcp-publisher  # registry should read 0.77.0 active, not 0.76.0
```

If the job does not appear, #841 is not fixed and the issue must be reopened. Five regression
tests pin the wiring offline in the meantime, each verified to fail against a broken variant.

---

## 5. Version-site gate (#839) — VERIFIED BY THIS CUT

First release cut under the gate added this cycle. All seven sites bumped and **every gate
passed on the first attempt**:

```
tests/test_release_version_sites.py + test_plugin_manifests.py
  + test_dockerfile_version_pin.py          27 passed
check_repo_hygiene.py                       1134 tracked files, no violations
```

The v0.76.0 cut met two gate failures in sequence for exactly this reason. This one met none.

---

## 6. Migrated-host generation e2e — BLOCKED, named

`tests/e2e/test_migrated_host_e2e.py -m e2e_image` **cannot run on this machine.** Flow bounces
every project URL to `/about` before any driver code executes:

```
FlowAppError (exit 31): Flow redirected to its public landing page
(https://flow.google.com/about) instead of .../project/<id>
```

A/B-controlled so this is not mistaken for a regression:

| tree | profile / project | result |
|---|---|---|
| PR #781 | ffroliva / cf2ec9ac | 2 failed, `/about` |
| PR #781 | denon82 / 2373d074 | 2 failed, `/about` |
| **develop (control)** | denon82 / 2373d074 | **2 failed, identical** |

Identical failure with and without the change, so it is the account state, not the code. The
backend grants access — `gflow project list` returns those projects — while the frontend
declines to open them (#756).

**This release changes no generation path**, so nothing here is untested by it: the blocker
bounds what *could* have been verified, not what this release altered.

---

## 7. Not exercised, and why

- **`gflow credits` on migrated accounts** — still labs-gated (#795), unchanged here.
- **Video generation** — no Veo path changed in this release, and the credit balance is
  unreadable on a migrated account (#795), so any run would be a blind spend.
- **The login auto-close** — still hangs on a migrated account. The detector polls the same
  labs endpoint that never answers, so it spins to the login timeout while the banner promises
  it will close for you. Observed in arm A above. **Not fixed in this release**; recorded on
  #791.
