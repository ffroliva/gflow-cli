# Live verification — v0.93.0

> Evidence for this release, gathered on 2026-11-05. Every row below was a run someone watched,
> not an inference.

## Environment

| | |
|---|---|
| Profile | `ffroliva` (real-browser Chrome strategy) for generation; `denon82` and the disposable `probe-1026` for the revoked-session arms |
| Host Flow served | `flow.google.com` |
| Entrypoint | the real `gflow` CLI, in-process through Click for the e2e rows, and `gflow_generate_video` for the MCP rows |
| OS | Windows 11 |
| Tree | `develop@b7c0f8ce`, the release tree, unless a row says otherwise. The lost-reply rows ran on `d4130ebd`; `git diff d4130ebd..b7c0f8ce -- src/gflow_cli/api` is empty |
| Veo credits spent | 82 by the `nano21` row, 704 to 622, mostly finding #1153, plus one extension after it. The rows below are 4 lite clips and 1 extension |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| A finished video is delivered (#1153) | `gflow video t2v`, nothing injected, release tree | ✅ exit 0, 1,780,583 bytes, `ftyp`, equal to the record | 1 lite clip |
| The same, before the fix | `gflow video t2v` on `develop@80326579` | ✅ reproduced: status 3, 3,938,084 bytes, exit 6, nothing on disk | 1 lite clip |
| `video extend` reads through the same seam (#1153) | `gflow video extend`, release tree | ✅ exit 0, 5,339,962 bytes, `ftyp`; `as29s` carried `flow-content.google` | 1 extension |
| A lost t2v reply is recovered (#1141) | `tests/e2e/test_migrated_submit_body_loss_bdd.py`, CLI row | ✅ 1 passed: one submit reply, clip on disk | 1 lite clip |
| The same | MCP row, `gflow_generate_video` | ✅ 1 passed | 1 lite clip |
| A mention miss is refused (#1143) | `tests/e2e/test_mention_chip_order_bdd.py`, release tree | ✅ passed, no submit (asserted) | $0 |
| Login opens on screen | `tests/e2e/test_login_window_position_e2e.py`, release tree | ✅ passed; the control without the flag opens at x=-1268 | $0 |
| The workspace check when My Account cannot answer (#1144) | `tests/e2e/test_workspace_oracle_e2e.py`, release tree | ✅ passed, including a forced handoff through the real launch | $0 |
| A session ended on the server is refused (#1144) | [spike](superpowers/spikes/2026-11-05-revoked-session-workspace.md), on #1155's tree | ✅ `/about`, refused; a live copy authenticated | $0 |
| `nano21` (#1145) | `gflow image t2i --model nano21`, release tree | ✅ exit 0, a JPEG on disk | 1 image |
| `oauthlib` 4.0.1 | `pip-audit` in CI | ✅ passes | $0 |

## The five layers

1. **File count** — one `.mp4` for the plain run, one `.jpg` for `nano21`.
2. **Magic bytes** — the clip carries `ftyp` at offset 4, and `read_clip` refuses anything else.
3. **Shape** — the clip's size equals the size Flow reports: `_verify` compares them and refuses a
   mismatch.
4. **Structlog invariants** — `migrated.result` with `url_host: null`, then `migrated.recover_navigate`
   and `migrated.download`. On the lost-reply rows, `migrated.video_submit_reply_lost` once.
5. **User-confirmable artifact** — both files open in a normal player or viewer.

## Observed, not caused by this release

- Each t2v run spends the 20 s grace before reading the clip's route. No status reply named the
  clip's own URL in those runs, and on `extend` `as29s` did
  ([spike](superpowers/spikes/2026-11-05-clip-url-is-a-thumbnail.md)).
- `video extend` logged `extend_record_failed` (`DataIntegrityError`) from `record_started_extend`,
  with the clip written. It also printed a `TargetClosedError` from teardown after exit.
- `browser_teardown.context_close_error` appears after generation runs, as in v0.92.1.
- One `gflow auth status` on `denon82` right after copying the profile did not verify; the next, a
  minute later, did on both arms. Its cause is unknown.

## Not run

- **Other accounts, and a host other than flow.google.com.** No account here is served anything
  else (named external blocker). #1153 may behave differently elsewhere.
- **The reporter's natural reply loss (#1141).** These rows force it; how often it happens on t2v is
  unknown.
- **A session ended by a password change.** Only `accounts.google.com/Logout` was measured, one run
  per arm. Signing out from Your devices did not end the session in the one try.
- **i2v and r2v on the release tree.** They share `download`; the lost-reply r2v rows were not
  repeated.
- The early non-submitting Enter (#1143) and one `nano21` "Unexpected error" (#1145) did not recur
  and stay unexplained.

## Left out of this release

#1142 (reload past the agent-only composer) stays with its contributor. It does not reach its own
case on the CLI or MCP path, and no account here is served that composer.

## Pre-tag gates

| Gate | Result |
|---|---|
| `/gflow:check` | ✅ all repo gates, ruff, format, pyright 0 errors, pytest 5695 passed, 11 skipped, coverage 93% |
| Live verification | ✅ the rows above |
| `/gflow:doc-review` | ✅ mechanical sections pass after the council's fixes |

### Doc-review council

Completeness RED, cross-reference YELLOW, drift RED. Tier 1, all fixed in the release commit:
three docs still called a lost t2v reply exit 9, and the `gflow_auth_status` description said a
browser boots only on a cookie failure. Tier 2, all fixed: the one-clip limit in the changelog,
the `.gflow_account` and "no browser" claims, the v0.92.1 index entry, this ledger's cost and
extend rows. Left as issues: the billed `fetch_mp4` wire error's hint, and `extend_record_failed`.

## Post-tag evidence

Filled in after the tag.
