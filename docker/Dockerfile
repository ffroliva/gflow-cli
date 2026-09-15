# Spike image v2: REAL Google Chrome, not Playwright's bundled Chromium.
#
# gflow's own gate (browser_manager.is_playwright_chrome_channel_available) accepts
# exactly one Linux path for channel="chrome": /opt/google/chrome/chrome. That is where
# the google-chrome-stable .deb installs, so this image satisfies gflow's detector and
# not merely Playwright's.
# 3.14, not 3.11. 3.11 is only the project's requires-python FLOOR — a reason for the
# minimum SUPPORTED version, not for a runtime image.
#
# CI's matrix is ["3.11","3.12","3.13"], so 3.14 is not covered by the suite. That matters
# less here than it looks: a container pins its interpreter, so this is deterministic
# rather than an ambient version that might surprise someone. The gap is closed by
# verifying the image's actual runtime contract below rather than by staying on an older
# interpreter — see docker/README.md § "Verified on this image".
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    GFLOW_CLI_UPDATE_CHECK=false \
    GFLOW_CLI_HOME=/data/gflow

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb xauth ca-certificates curl gnupg \
 && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

# The version lives in an ARG so a bump is a one-line change (or `--build-arg`), and
# `tests/test_dockerfile_version_pin.py` fails when this default drifts from
# pyproject.toml — otherwise the image silently keeps installing an old release while
# docker/README.md goes on claiming the new one was verified.
#
# Declared HERE rather than beside FROM, and the placement is load-bearing. Measured
# 2026-09-15 with an A/B on a throwaway image, using a unique marker so no stale cache
# entry could match:
#
#   ARG after  the expensive layer -> that layer CACHED on a version bump
#   ARG before the expensive layer -> that layer REBUILT
#
# Chrome above is most of this image's ~1.6 GB, so the wrong placement would turn every
# gflow bump into a full Chrome reinstall. Keep this ARG below the apt layer.
#
# Deliberately NOT pinned: google-chrome-stable. Chrome's apt repo keeps only the current
# build, so a pinned version stops resolving within weeks. The trade-off is that this
# layer caches indefinitely and a rebuild is what picks up a newer Chrome.
ARG GFLOW_VERSION=0.75.0
RUN pip install --no-cache-dir "gflow-cli==${GFLOW_VERSION}"

VOLUME ["/data"]
WORKDIR /data
CMD ["gflow", "--help"]
