"""URL constants for every Flow REST route gflow-cli touches.

Two hosts are involved:
  * aisandbox-pa.googleapis.com — the actual Flow API surface
  * labs.google/fx/api/trpc — tRPC routes for project lifecycle
"""

from __future__ import annotations

import re

FLOW_API_BASE = "https://aisandbox-pa.googleapis.com/v1"
LABS_TRPC_BASE = "https://labs.google/fx/api/trpc"
LABS_BASE = "https://labs.google"

# Strict allowlist for Google project IDs interpolated into URL paths.
# Alphanumeric + hyphen, 1-128 chars. Closes URL-injection vectors:
#   - percent-encoded slashes (%2F normalized by GCP/nginx L7 LBs)
#   - Unicode lookalikes (U+FF0F, U+2215, U+29F8, ...)
#   - CRLF / NUL bytes (header injection)
#   - URL metacharacters '?' and '#'
#   - whitespace-only / empty / oversized strings
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,128}$")

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


# Image generation — projectId is in the URL path, not the body.
def batch_generate_images_url(project_id: str) -> str:
    """Build the batchGenerateImages URL for a given project.

    Validates project_id against a strict allowlist (alphanumeric + hyphen,
    1-128 chars) to prevent URL-path injection via percent-encoding,
    Unicode lookalikes, CRLF, NUL, query/fragment metacharacters, or path
    traversal. The project_id is interpolated directly into the URL path,
    so anything outside the allowlist is rejected.
    """
    if not _PROJECT_ID_RE.fullmatch(project_id):
        msg = f"Invalid project_id: {project_id!r}"
        raise ValueError(msg)
    return f"{FLOW_API_BASE}/projects/{project_id}/flowMedia:batchGenerateImages"


# Bootstrap URL — the Flow editor page. The persistent context navigates here
# once before making API calls so Google's cookies + reCAPTCHA JS are loaded.
EDITOR_BOOTSTRAP_URL = "https://labs.google/fx/tools/flow?hl=en"

# Scene / Add Clip (aisandbox-pa) ------------------------------------------
SCENE_WORKFLOWS_UPDATE = f"{FLOW_API_BASE}/flow/scene/sceneWorkflows:update"

# Reuse the project-id allowlist shape for scene/workflow ids (UUID-like, path-interpolated).
_SCENE_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,128}$")


def scenes_url(project_id: str) -> str:
    """POST target that composes a scene from ordered workflowIds."""
    if not _PROJECT_ID_RE.fullmatch(project_id):
        msg = f"Invalid project_id: {project_id!r}"
        raise ValueError(msg)
    return f"{FLOW_API_BASE}/flow/projects/{project_id}/scenes"


def scene_workflows_url(scene_id: str, project_id: str) -> str:
    """GET target for scene read-back (order + trims + media).

    Flow requires BOTH ``sceneId`` and ``projectId`` as query params; without
    them the endpoint returns ``{}`` (empty). Confirmed from labs.google18.har
    (entries 54/58). Both ids pass the strict allowlist regex (alphanumeric +
    hyphen only), so direct interpolation into the query string is injection-safe.
    """
    if not _SCENE_ID_RE.fullmatch(scene_id):
        msg = f"Invalid scene_id: {scene_id!r}"
        raise ValueError(msg)
    if not _PROJECT_ID_RE.fullmatch(project_id):
        msg = f"Invalid project_id: {project_id!r}"
        raise ValueError(msg)
    return (
        f"{FLOW_API_BASE}/flow/scene/{scene_id}/workflows"
        f"?sceneId={scene_id}&projectId={project_id}"
    )


def flow_workflow_url(workflow_id: str) -> str:
    """PATCH target to commit a workflow's primaryMediaId before placement."""
    if not _SCENE_ID_RE.fullmatch(workflow_id):
        msg = f"Invalid workflow_id: {workflow_id!r}"
        raise ValueError(msg)
    return f"{ARCHIVE_WORKFLOW_BASE}/{workflow_id}"
