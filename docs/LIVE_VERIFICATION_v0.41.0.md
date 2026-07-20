# Live verification — v0.41.0 (2026-07-20)

Release scope: **production-readiness hardening** ([#357]) — queue safety
(versioned payloads, atomic claims, checkpointed execution phases),
cross-process profile lease (`ProfileLockedError` exit 11),
cancellation-safe browser teardown, driver honesty (typed
`SupportsSendPrompt` injection, frozen `TransportSetup`), mention-index
fail-closed (`MentionIndexUnavailableError` exit 29), external-CDP lifecycle
removal, and nonfunctional `gflow video batch` removal.

## Live matrix (ffroliva profile, 2026-07-20)

Exercised against live Flow on `develop@a0c7a3f` (post-merge of #357):

1. **Stale-session fail-fast**: authenticated session expired →
   `AuthExpiredError` (exit 3) raised immediately, **no credits spent**.
   Re-auth succeeded.
2. **Free image generation**: `batchGenerateImages` returned HTTP 200, valid
   768×1376 JPEG produced. Provenance recorded, zero leftover gflow-owned
   Chrome processes, healthy cookie DB.
3. **Paid veo-lite T2V**: `batchAsyncGenerateVideoText` returned HTTP 200,
   `MEDIA_GENERATION_STATUS_SUCCESSFUL`, valid `ftyp` MP4 output. Clean
   lease acquire/release/reacquire cycle observed.

## 5-layer evidence ledger

| Layer | Evidence |
|---|---|
| Stale-session | `AuthExpiredError` exit 3, no spend, re-auth succeeds |
| Free image gen | `batchGenerateImages` 200, valid JPEG, provenance row |
| Paid T2V | `batchAsyncGenerateVideoText` 200, `SUCCESSFUL`, valid MP4 |
| Profile lease | Clean acquire/release/reacquire across runs, no `ProfileLockedError` |
| Browser lifecycle | Zero leftover gflow-owned Chrome processes after each run |

## Not verified live this cycle

- Daemon/MCP live queue-claim and credit-free `remote_started`
  re-entry reconciliation are offline-proven only.
- The four D4 live cancellation paths are unit-proven.
- omni-flash NULL-operation-id path not exercised (used veo-lite
  deliberately).
- The POSIX `fcntl.flock` lease branch has not executed anywhere yet — only
  the Windows leg ran locally. A `profile-lease-matrix` CI job covers
  Windows/macOS/Linux.

## Pre-tag gates

- Offline: repo hygiene, doc-links, `ruff check`, `ruff format --check`,
  `pyright src` (0 errors) all clean.
- Test suite: **2513 passed, 4 skipped**, 72 deselected.

## Post-tag evidence

- Tag: `v0.41.0`, signed (SSH), pushed to origin.
- Release PR: [#358](https://github.com/ffroliva/gflow-cli/pull/358).

[#357]: https://github.com/ffroliva/gflow-cli/pull/357
