---
name: background-e2e-pytest-pattern
description: Reliable pattern for running long credit-spending pytest e2e in background on Windows where harness run_in_background is broken
---

The harness `Bash(run_in_background=true)` is broken on this Windows setup (see [[windows-dev-quirks]]). The working pattern for backgrounding a pytest e2e that may take 5-10 minutes (Flow T2V/I2V latency) is:

1. **`ctx_execute` (shell)** to spawn the process and detach it with `nohup ... &`. The shell exits immediately; pytest survives.
2. **Write log + PID files** under `.planning/e2e-logs/` (gitignored as of 2026-05-26):
   - `nohup .venv/Scripts/python.exe -m pytest -m e2e <test_file> -v --tb=short -s > "$LOG" 2>&1 &`
   - `echo $! > "$PIDF"`
3. **`Monitor`** with a poll-loop that exits on PID death — emits progress events as the log grows and a terminal `--- TEST PROCESS EXITED ---` event on completion. Filter the tail for `PASSED|FAILED|ERROR|Traceback|SUCCESSFUL|MEDIA_GENERATION|onboarding|...` so silence really means "nothing happened yet."

The full working snippet that ran PR #70's de-DE T2V (2026-05-25, ~71 s end-to-end) lives at `.planning/e2e-logs/pr70_t2v_de-DE_*.log`.

**Why:** the Anthropic prompt cache is 5 min. Tail-`grep -m1`-with-tail-never-exiting traps and naïve `sleep` loops both waste cache and miss outcomes. The PID-poll pattern gives one notification at completion plus N progress events.

**How to apply:**

- Use `.planning/e2e-logs/<pr#>_<test>_<locale>_<ts>.log` as the log path so the artefact has provenance.
- Pre-flight: confirm the chosen profile has a non-empty `Cookies` file (`stat -c %s` ≥ 30 KB is a fine sanity check — see [[real-browser-auth-mandatory]]).
- Surface 5-layer ledger verification ([[verification-ledger-5-layer]]) after the test exits: file presence, magic bytes, ffprobe dims, structlog success event, ask user for gallery confirmation.
- Per [[e2e-tests-parameterize]] — `GFLOW_CLI_LOCALE=<bcp47>`, `GFLOW_CLI_E2E_PROFILE=<name>`, `GFLOW_CLI_E2E_VIDEO_ASPECT=<aspect>` should be passed explicitly even if defaults exist.

The Monitor's poll loop:

```sh
prev=""
while true; do
  ... emit any new matching lines from $LOG ...
  if ! ps -p $PID > /dev/null 2>&1; then
    echo "--- TEST PROCESS EXITED (pid $PID gone) ---"
    tail -8 "$LOG"
    exit 0
  fi
  sleep 6
done
```

`sleep 6` keeps under the 5-min cache window even for the longest e2e (~10 min). For shorter known-fast tests (~70 s like de-DE T2V) the loop will emit the SUCCESS event and exit before any cache miss.

Related: [[windows-dev-quirks]], [[verification-ledger-5-layer]], [[e2e-tests-parameterize]], [[real-browser-auth-mandatory]], [[e2e-cost-stratification-pattern]] (which marker tier to run — pick `e2e_auth` for 0-credit smoke, `e2e_image`/`e2e_video` for the credit-spending tiers this pattern is designed for).
