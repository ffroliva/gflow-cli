"""URL constants for every Flow REST route flow-cli touches.

Two hosts are involved:
  * aisandbox-pa.googleapis.com — the actual Flow API surface
  * labs.google/fx/api/trpc — tRPC routes for project lifecycle
"""

from __future__ import annotations

FLOW_API_BASE = "https://aisandbox-pa.googleapis.com/v1"
LABS_TRPC_BASE = "https://labs.google/fx/api/trpc"
LABS_BASE = "https://labs.google"

# Asset / media
UPLOAD_IMAGE = f"{FLOW_API_BASE}/flow/uploadImage"

# Video generation (reCAPTCHA-required for GENERATE_VIDEO)
GENERATE_VIDEO = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoText"
CHECK_VIDEO_STATUS = f"{FLOW_API_BASE}/video:batchCheckAsyncVideoGenerationStatus"

# Workflow management
ARCHIVE_WORKFLOW_BASE = f"{FLOW_API_BASE}/flowWorkflows"  # + /{workflow_id}

# Project lifecycle (tRPC, different host)
CREATE_PROJECT = f"{LABS_TRPC_BASE}/project.createProject"


# Media download — public-style URL that 302s to a signed Cloud Storage URL.
# Cookies still required.
def media_download_url(name: str) -> str:
    """Build the redirect URL for an asset name (UUID)."""
    return f"{LABS_BASE}/fx/api/trpc/media.getMediaUrlRedirect?name={name}"


# Bootstrap URL — the Flow editor page. The persistent context navigates here
# once before making API calls so Google's cookies + reCAPTCHA JS are loaded.
EDITOR_BOOTSTRAP_URL = "https://labs.google/fx/tools/flow?hl=en"
