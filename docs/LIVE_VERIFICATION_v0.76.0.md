# Live verification — v0.76.0

**Date:** 2026-09-16 · **Cost:** $0 — reads only. No generation, no entity created, no
Veo credits spent. · **Tag:** `v0.76.0`

This release contains **no generation-path change**, so the usual 5-layer media ledger
(file count → magic bytes → dimensions → structlog → gallery) has nothing to weigh. Each
item below instead names the evidence that is appropriate to it, and the items that could
**not** be verified this cycle are recorded with their reason rather than omitted.

---

## 1 · `FlowAccessUnavailableError` — exit 39 (#833)

**Verified live, by this session, on two real accounts.**

An account with no Flow access previously got `UiSelectorDriftError` (exit 23) — gflow
blaming its own selectors and inviting a bug report — when the truth was that Google had
never granted the account Flow.

The positive arm (the guard fires on an unentitled account) was verified by the session
that shipped #833. The arm verified **here** is the negative one, which is the arm that
decides whether the guard is safe to ship: a guard with only a positive arm is
indistinguishable from a guard that is permanently on.

```
profile              unavailable_screen   aisandbox_root   settled url
ffroliva             0                    1                https://flow.google.com/about
denon82              0                    1                https://flow.google.com/about
```

- `unavailable_screen = 0` on both — the guard does **not** fire on accounts that have
  Flow access.
- `aisandbox_root = 1` on both — the app **booted**. Without this the zero would be
  unfalsifiable, indistinguishable from a page that never rendered.
- Both accounts settled at **`/about`**, which matters: that is the state that broke this
  release's *withdrawn* #791 oracle (§ 5). The exit-39 signal reads a component that
  exists only on the unavailable screen, so `/about` cannot mimic it — confirmed here
  independently of the #833 session's own run on the same account.

**Wiring confirmed against the shipped tree**, not assumed:

```
__version__ = 0.76.0
FlowAccessUnavailableError -> 39     (EXIT_CODE_MAP)
python -m gflow_cli, version 0.76.0
```

## 2 · mcpservers.org listing + badge (#834)

**Verified live, with a negative control.** The listing 403s to `curl`, so a naive check
would have called the link dead:

| request | result |
|---|---|
| `mcpservers.org/badge.svg` | **200**, `image/svg+xml` |
| `/servers/ffroliva/gflow-cli` (browser UA) | **200**, 115 643 B, contains `gflow-cli` and `Google Flow` |
| `/servers/ffroliva/<bogus>` (browser UA) | **404** — the control, so the 200 is not a catch-all |

The 403 is user-agent bot-blocking.

## 3 · MCP → worker payload-key gate (#628)

**Not a Flow surface.** It is an AST gate over the repository's own source: it extracts the
keys `mcp/tools.py` writes into the queue payload and the keys anything under `worker/`
reads back, and fails on any written key nothing consumes. It found the `project_name`
defect on its first run. Verified by the offline suite; there is nothing live to exercise.

---

## Not verified this cycle, and why

**These are named blockers, not omissions.** Each says what would settle it.

### `docker` containerised sign-in (#830)

**Not verified.** The fix is a WSLg X11 socket path (`/mnt/wslg/.X11-unix`) plus a version
`ARG`, and exercising it needs a Docker host with WSL integration enabled for the distro —
not available to this session. The version pin *is* gated offline by
`tests/test_dockerfile_version_pin.py`, including the ARG's position, since declaring it
above the Chrome layer would invalidate ~1.6 GB of apt cache per bump. **What would settle
it:** one `docker compose run login` from inside WSL on a Windows host.

### Official MCP Registry publish (#829)

**Cannot be verified before this release exists.** Both triggers resolve the workflow from
the **default branch**, and the job runs on `release: published` — so it goes live when this
tag reaches `main`, and v0.76.0 is the first release that can exercise it. `workflow_dispatch`
covers the re-run case. `validate` runs first on both paths, so a bad `server.json` stops
before anything is sent. **What would settle it:** the `MCP Registry` job on this release's
own run.

### Migrated login (#791) — **withdrawn, not shipped**

A fix was built, council-reviewed across eight dimensions, and **withdrawn before merge**.
It read `a[href*="SignOutOptions"]` from the rendered DOM and claimed the signal was
server-attested. It is not: Flow's `/about` hop is decided **client-side with zero requests
to Flow**, and *"the backend grants access while the frontend declines to open it"*
([#756](https://github.com/ffroliva/gflow-cli/issues/756),
[spike](superpowers/spikes/2026-09-11-about-redirect-is-decided-client-side.md)). The probe
would have reported authenticated users as logged out whenever their account was in that
state — #791's own failure, reintroduced by its remedy.

Both accounts measured above are in the `/about` state, which is how it was caught: the e2e
went red on a live account whose state moved mid-session, after eight green dimensions.
Full retraction in the § Q6 section of the spike carried on PR
[#835](https://github.com/ffroliva/gflow-cli/pull/835) — that document is not in this
release because the branch was never merged. **#791 remains open**; PR #835 stays a draft because its HTTP findings and plumbing
are reusable.

### `gflow credits` on migrated accounts (#795)

Unchanged and still open. A rescued login would not have fixed it — the two share a root
condition (labs mints no `access_token`) but only login had a way around it.

---

## Offline gates at the tagged tree

```
ruff check / ruff format --check        PASS
repo hygiene                            PASS  (1130 files)
doc links                               PASS  (189 files)
website/docs mirror --check             PASS  (21 files, nav complete)
website docs PII                        PASS  (26 published files)
council memory                          PASS  (49 files, all cited and resolving)
tests/auth + tests/api + tests/mcp      2090 passed, 3 skipped
pytest -m e2e test_auth_verification    4 passed
```
