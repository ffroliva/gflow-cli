# gflow-cli Configuration Reference

## Environment variables

| Env var | Default | Description |
|---|---|---|
| `GFLOW_CLI_HOME` | Platform data dir | Root directory for profiles and config. |
| `GFLOW_CLI_OUTPUT_DIR` | `~/Downloads/gflow-cli` | Where generated images and videos are saved. |
| `GFLOW_CLI_BROWSER` | `auto` | Browser mode. See **Browser mode** section below. |
| `CHROME_BINARY` | (autodetect) | Override Chrome binary path. Falls back to platform-standard locations. |
| `GFLOW_CLI_CONCURRENCY` | `4` | Maximum parallel API requests per batch. |
| `GFLOW_LIVE` | _(unset)_ | Set to `1` to enable live-API test markers (`@pytest.mark.live`). |

---

## Browser mode (D.2.3+)

| Env var | Default | Description |
|---|---|---|
| `GFLOW_CLI_BROWSER` | `auto` | `auto` attaches to running Chrome via CDP or spawns detached; `fresh` launches new Playwright Chromium per call (legacy); `cdp:<port>` attaches to explicit CDP endpoint. |
| `CHROME_BINARY` | (autodetect) | Override Chrome binary path. Falls back to platform-standard locations. |

### When to use which

- **`auto`** (recommended): real Chrome fingerprint = highest reCAPTCHA risk score; persistent
  across CLI invocations. First call spawns Chrome detached; subsequent calls attach via CDP.
- **`fresh`**: legacy single-shot Playwright Chromium. Use when you need a clean profile per call.
- **`cdp:<port>`**: connect to an already-running Chrome you spawned yourself
  (e.g. with `--remote-debugging-port=9222`).

### Chrome binary autodetection order

1. `CHROME_BINARY` env var (checked first — always wins)
2. `shutil.which("chrome")` / `shutil.which("google-chrome")` / `shutil.which("chromium")`
3. Platform-standard paths:
   - **Windows**: `C:\Program Files\Google\Chrome\Application\chrome.exe`,
     `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe`,
     `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`
   - **macOS**: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
   - **Linux**: `/usr/bin/google-chrome`, `/usr/bin/chromium`, `/usr/bin/chromium-browser`

If Chrome is not found, gflow-cli raises a `ConfigurationError` with an install hint.

### CDP port range

By default gflow-cli uses CDP port `9222`. If that port is occupied by a non-gflow Chrome,
it probes `9222–9229` until a free port is found. The chosen port is persisted in
`<profile_dir>/.gflow-cdp.lock` so subsequent calls reuse the same port.

If all 8 ports are occupied, a `ConfigurationError` is raised.

### Lockfile

`<profile_dir>/.gflow-cdp.lock` — JSON file containing `{pid, port, profile_name}`.
Written atomically (tmp + `os.link`, `O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0o600`)
when Chrome is first spawned. Stale locks (PID no longer alive) are cleaned up
automatically on the next CLI invocation.

### Security note — localhost CDP trust model

The Chrome DevTools Protocol endpoint (`http://localhost:<port>/json/version`)
is **not authenticated**. Any process on the same machine that can reach
`localhost:9222` can drive the browser. gflow-cli treats a port owner that
matches our lockfile as trusted; an unmanaged Chrome on the same port is
attached with a `attached_to_unmanaged_chrome=true` warning logged.

**Do not run gflow-cli on a shared multi-user machine** where untrusted users
have shell access. Use a dedicated user account or a VM per worker.
