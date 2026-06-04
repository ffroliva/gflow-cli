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
# User-facing Flow UI base — note the `/fx` segment (matches real editor URLs,
# e.g. https://labs.google/fx/pt/tools/flow/project/<pid>). Verified via Phase-2 spike T-A.
LABS_FX_BASE = "https://labs.google/fx"

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

# Character entities (tRPC + aisandbox Bearer REST) ------------------------
CREATE_ENTITY_URL = f"{LABS_TRPC_BASE}/flow.createEntity"
PROJECT_INITIAL_DATA_URL = f"{LABS_TRPC_BASE}/flow.projectInitialData"
FLOW_ENTITIES_URL = f"{FLOW_API_BASE}/flow/entities"

# Scene / Add Clip (aisandbox-pa) ------------------------------------------
SCENE_WORKFLOWS_UPDATE = f"{FLOW_API_BASE}/flow/scene/sceneWorkflows:update"

# Server-side concatenation (the "Baixar"/download action): stitches a scene's
# clips into ONE extended MP4 server-side — credit-free, no ffmpeg. Top-level
# `/v1:` methods (NOT under /flow/…), same colon-method shape as GENERATE_VIDEO.
# The combined MP4 is returned inline as base64 in the status `encodedVideo`.
RUN_VIDEO_FX_CONCATENATION = f"{FLOW_API_BASE}:runVideoFxConcatenation"
RUN_VIDEO_FX_CHECK_CONCATENATION_STATUS = f"{FLOW_API_BASE}:runVideoFxCheckConcatenationStatus"

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
        f"{FLOW_API_BASE}/flow/scene/{scene_id}/workflows?sceneId={scene_id}&projectId={project_id}"
    )


def flow_workflow_url(workflow_id: str) -> str:
    """PATCH target to commit a workflow's primaryMediaId before placement."""
    if not _SCENE_ID_RE.fullmatch(workflow_id):
        msg = f"Invalid workflow_id: {workflow_id!r}"
        raise ValueError(msg)
    return f"{ARCHIVE_WORKFLOW_BASE}/{workflow_id}"


def character_editor_url(locale: str, project_id: str, entity_id: str) -> str:
    """Build the user-facing Flow character editor URL for a given entity.

    Returns the browser-navigable page URL (not an API endpoint) at which the
    Flow UI renders the character editor for *entity_id* inside *project_id*,
    localised to *locale*.

    *locale* may be a full BCP-47 tag (e.g. ``"en-US"``, ``"pt-BR"``). Flow's UI
    only routes under the *short* primary-subtag segment — the full tag 404s
    (issue #153) — so the tag is reduced to its lower-cased primary subtag
    (``"en-US" -> "en"``, ``"pt-BR" -> "pt"``); already-short inputs pass
    through unchanged.

    Pattern: ``https://labs.google/fx/{seg}/tools/flow/project/{project_id}/character/{entity_id}``
    """
    segment = locale.strip().split("-", 1)[0].lower() or "en"
    return f"{LABS_FX_BASE}/{segment}/tools/flow/project/{project_id}/character/{entity_id}"
