# gflow-cli Configuration Reference

## Environment variables

| Env var | Default | Description |
|---|---|---|
| `GFLOW_CLI_HOME` | Platform data dir | Root directory for profiles and config. |
| `GFLOW_CLI_OUTPUT_DIR` | `~/Downloads/gflow-cli` | Where generated images and videos are saved. |
| `CHROME_BINARY` | (autodetect) | Override Chrome binary path. Falls back to platform-standard locations. |
| `GFLOW_CLI_CONCURRENCY` | `4` | Maximum parallel API requests per batch. |
| `GFLOW_CLI_GEMINI_API_KEY` | _(unset)_ | Public Gemini key enabling prompt expansion (`-e`/`--expand`). Unset = expansion is a graceful no-op. [Get one](https://aistudio.google.com/apikey). |
| `GFLOW_LIVE` | _(unset)_ | Set to `1` to enable live-API test markers (`@pytest.mark.live`). |

---

## Browser

There is no `GFLOW_CLI_BROWSER` mode switch — the production path is
`UiAutomationTransport`, a Playwright persistent context using Playwright's
own internal CDP port (never externally exposed). A separate packaged
CDP attach/spawn lifecycle (`auto`/`fresh`/`cdp:<port>` modes, a port-probe
range, and a `.gflow-cdp.lock` file) previously existed in
`browser_manager.py` but had no CLI wiring and no production consumer; it
was removed 2026-07-19 (see `.superpowers/sdd/cdp-decision.md`). Only Chrome
binary discovery remains, used to pick a channel for the real transport.

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
