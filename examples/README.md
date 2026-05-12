# gflow-cli — examples

Runnable example scripts demonstrating how to drive Google Flow's image
generation surface from Python on a **Google AI Pro or Ultra**
subscription.

> gflow-cli does not bypass billing, plan tiers, or usage limits. It
> consumes them on the authenticated profile's behalf — the same way
> the Flow UI does when you click *Create* in the editor.

## Prerequisites

1. Active **Google AI Pro or Ultra** subscription on a Google account.
2. `gflow-cli` installed (`pip install gflow-cli` or `uv pip install
   gflow-cli`).
3. A Playwright Chromium user-data-dir signed in to Flow:

   ```bash
   gflow auth login --profile <your-profile-name>
   ```

   Follow the browser prompt to sign in. The profile dir persists; you
   only need to do this once per Google account.

4. Set `GFLOW_EXAMPLE_PROFILE=<your-profile-name>` in your shell, or pass
   `--profile` on each invocation.

## Example index

| Script | What it does |
|---|---|
| [`single_image_t2i.py`](single_image_t2i.py) | Generate ONE image from a prompt and save the PNG. |
| [`batch_from_config.py`](batch_from_config.py) | Run a sequence of prompts from a JSON config file (wraps `gflow run --config ...`). |
| [`sample_config.json`](sample_config.json) | Template batch config — three prompts at different aspect ratios. Copy and edit. |

## Quick start

```bash
# Single image:
GFLOW_EXAMPLE_PROFILE=<your-profile> python examples/single_image_t2i.py \
    --prompt "a quiet mountain lake at dawn, cinematic photography"

# Batch from JSON:
GFLOW_EXAMPLE_PROFILE=<your-profile> python examples/batch_from_config.py
```

Output PNGs land under `./gflow-output/<UTC-timestamp>/` by default.

## Notes

- The first run opens a visible Chromium window (`headless=False` is
  required — Flow's reCAPTCHA checks rely on a real rendering
  pipeline). Subsequent runs reuse the same profile dir and silently
  skip the sign-in step.
- Each prompt costs the same as one *Create* click in the Flow UI on
  your subscription — gflow-cli is a thin automation layer, not a
  free-tier wrapper.
