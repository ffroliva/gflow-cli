# Onboarding

From install to your first AI-generated image in about 5 minutes — or hand the whole thing to your AI assistant.

!!! warning "Before you begin"
    gflow-cli is **unofficial, alpha, and reverse-engineered**. It drives a headed browser on *your own* Google Flow session — treat it as your own account risk. It works with any Google account that has Flow access with Flow access, and every generation bills your account. **Not affiliated with Google.**

## Prerequisites

- Python 3.11+ (`uv` installs a managed Python — no global install needed)
- A Google account with Flow access (a paid AI Pro/Ultra plan only affects credit allowances)
- Google Chrome installed
- A display for the one-time browser login

---

## Step 1 — Install

Install the CLI:

```bash
uv tool install gflow-cli
```

Install Chromium (one-time, required by the browser transport):

```bash
uv tool run --from gflow-cli playwright install chromium
```

Verify the installation:

```bash
gflow --version
```

```
gflow, version 0.41.0
```

---

## Step 2 — Authenticate

The `--browser chrome` flag is mandatory — Google rejects Playwright's bundled Chromium.

```bash
gflow auth login --browser chrome
```

A Chrome window opens at the Flow sign-in page. Sign in with your Google account (2FA included), keep going until the Flow editor loads, then **close the browser** — gflow verifies automatically.

```
Session saved.
Profile dir: ~/.local/share/gflow-cli/profile_default
Renamed profile from default to your-name (derived from Google account).
```

On first run, the profile is created as `default` then auto-renamed to your email's local-part (e.g. `your.name@gmail.com` → `your-name`). Single-account users never have to think about profiles. To use a custom name, pass `--profile myname`.

---

## Step 3 — Generate

### Your first image

Generate a text-to-image with the `--tool creative-director` flag, which expands your prompt with Google's 5-component formula before any credits are spent.

```bash
gflow image t2i \
  "a lighthouse in a storm, moody, cinematic" \
  --tool creative-director
```

```
Flow project created: project/abc123
Generating images…
✓ out/images/2026-07-20/8f3a1b2c_1.png
```

Files are saved to the output directory automatically — by default `./out/images/`. Route outputs to S3, MinIO, or GCS with [external storage](EXTERNAL_STORAGE.md).

### Turn it into a video

Turn that still into an 8-second Veo clip. This step costs 1 Veo credit.

```bash
gflow video i2v \
  out/images/2026-07-20/8f3a1b2c_1.png \
  "slow cinematic push-in" \
  --aspect 9:16
```

```
Generating video… (~60-90s)
✓ out/videos/2026-07-20/9e4d7f2a.mp4
```

Video outputs land in `./out/videos/`.

---

## Step 4 — Success!

Your first generation is ready. Image generations are free — only Veo video costs credits.

**Try next:**

- `gflow video t2v "..."` — text-to-video
- `gflow character create` — reusable face + body reference
- `gflow image batch` — manifest-driven batch generation
- `gflow mcp run` — let your AI assistant drive it

---

## Troubleshooting

If something goes wrong on this path, here are the most common errors and exactly how to fix them.

### AuthBrowserRejectedError (exit 14)

Google rejected Playwright's bundled Chromium as an insecure browser.

**Fix:** Install Google Chrome from [chrome.com](https://chrome.com), then restart your terminal and run:

```bash
gflow auth login --browser chrome
```

### No profiles found (exit 2)

You ran a generation command before authenticating.

**Fix:** Run the one-time login first:

```bash
gflow auth login --browser chrome
```

### Authentication expired (exit 3)

Your browser session is no longer valid.

**Fix:** Re-authenticate:

```bash
gflow auth login --browser chrome
```

### Profile locked (exit 11)

Another process holds this profile directory.

**Fix:** Close any running `gflow serve` daemon or stray Chrome windows, then retry.

---

## Let your assistant drive it

Everything above, an agent can do for you. Start the MCP server and point Claude, Cursor, or Copilot at it.

```bash
gflow mcp run
```

```
serving over stdio:
  • gflow_generate_image
  • gflow_generate_video
ready — your assistant can drive Flow.
```

Just ask in plain language — the agent picks the model, aspect, and prompt tooling. See [Agent-driven](agents.md) for the full setup.

---

## Where to next

- [**Authentication →**](AUTHENTICATION.md) — profiles, reCAPTCHA, and the headed-browser transport in depth.
- [**User Guide →**](USER_GUIDE.md) — 15 task-oriented walkthroughs.
- [**Agent-driven (MCP) →**](agents.md) — wire gflow-cli into your agent.
- [**Known issues →**](KNOWN_ISSUES.md) — WAF/403 pacing, credit traps, and current limitations.
