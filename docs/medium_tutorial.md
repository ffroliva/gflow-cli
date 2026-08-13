# Control Google Veo and Imagen from Your Terminal with gflow-cli

Google Flow provides access to Google's video model, Veo, and its image model, Imagen. Google exposes these models through a web interface. gflow-cli brings them to your terminal.

gflow-cli is an unofficial Python command-line interface. It uses a reverse-engineered private REST API to drive image and video generations. You can script, batch, and pipe your creative workflows.

## Prerequisites

Before you begin, ensure you have:
* Python 3.11 or newer.
* The `uv` package manager.
* A Google account with access to Google Flow.
* A desktop environment to complete the one-time authentication.

## Step 1: Install gflow-cli and Chromium

Install the CLI tool using `uv`:

```bash
uv tool install gflow-cli
```

Download the Chromium browser binary for Playwright:

```bash
uv tool run --from gflow-cli playwright install chromium
```

This download requires 150 MB of disk space.

## Step 2: One-Time Authentication

Authenticate your Google account. Run the following command:

```bash
gflow auth login --browser chrome
```

This command opens a Chrome window. Google blocks standard Playwright browsers. You must specify the `--browser chrome` flag to bypass this check. Log in to your Google account. Solve any CAPTCHA challenges.

After you see the Google Flow dashboard, return to the terminal. The CLI saves your cookies in a user-local directory (for example, `%LOCALAPPDATA%\gflow-cli\` on Windows or `~/Library/Application Support/gflow-cli/` on macOS).

Verify your session status:

```bash
gflow auth status
```

The output confirms your cookie status.

## Step 3: Generate a Text-to-Image Still

Generate images using the `image t2i` command. Define your prompt and select an aspect ratio:

```bash
gflow image t2i "a serene mountain lake at dawn" --model nano-pro --aspect 16:9
```

Supported aspect ratios are `9:16`, `16:9`, `1:1`, `4:3`, and `3:4`. 

Model choices:
* `nano2`: Fast generation with balanced quality. This is the default.
* `nano-pro`: Higher quality.
* `image4`: Photorealistic outputs.

The command saves the files to a date-partitioned folder inside your output directory. You can specify a custom output directory:

```bash
gflow image t2i "neon cyberpunk alley" --aspect 16:9 --out ./my-images
```

## Step 4: Generate a Text-to-Video Clip

Generate a video using Google Veo with the `video t2v` command:

```bash
gflow video t2v "a steam locomotive moving through the snow at dusk" --aspect 16:9 --seed 4242
```

The `--seed` flag provides reproducibility. The command returns a standard `.mp4` file in your output directory.
