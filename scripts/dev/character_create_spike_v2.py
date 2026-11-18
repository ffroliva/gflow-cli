#!/usr/bin/env python3
r"""Validation spike V2 — Flow Character DIRECT-REST create -> persist loop (READ-ONLY).

Corrected successor to ``scripts/dev/character_create_spike.py``. V1 disproved the
``generate_image`` reuse assumption (2026-06-02): ``generate_image`` force-clicks
"+ New project" and passive-captures Flow's own JS request, so it lands in a FRESH
project and cannot inject ``entityContext`` — the subsequent ``flowWorkflows`` PATCH
404'd on the character's project. See ``docs/CHARACTER.md`` § 6.2 and § 11
("Transport decision = Option A").

This V2 validates **Option A**: assemble the ``batchGenerateImages`` body OURSELVES
and POST it (Bearer REST, ``_post_json``) into an EXISTING project, carrying
``mediaGenerationContext.entityContext``. The whole point is to prove the server
stamps ``workflows[0].parentEntityId == <our entityId>`` and keeps
``workflows[0].projectId == <the existing project>`` (NOT a new one), and that the
entity's ``imageReferences[]`` are AUTO-LINKED by ``entityContext`` (we do NOT set
them in the save PATCH). It reuses ONLY gflow's reCAPTCHA-mint + Bearer machinery,
never its new-project navigation.

It reuses V1's CORRECT helpers verbatim: ``_create_entity`` (tRPC ``flow.createEntity``),
the inline ``flowWorkflows`` PATCH idiom, and ``_read_back`` (tRPC ``projectInitialData``).

Throwaway diagnostic under ``scripts/dev/`` — NOT shipped, NOT imported by the
package, NO data-layer writes.

What it exercises (one full create -> persist -> read-back loop):

  0. POST  https://labs.google/fx/api/trpc/flow.createEntity   {"json":{"projectId":<pid>}}
        (tRPC, session-cookie auth) -> new entityId.  FREE.
  1. Mint a single-use reCAPTCHA token via client._mint_recaptcha_token("imageGeneration").
  2. Build the batchGenerateImages body OURSELVES (NOT generate_image):
        clientContext{ recaptchaContext{token, RECAPTCHA_APPLICATION_TYPE_WEB},
                       projectId:<EXISTING>, tool:"PINHOLE", sessionId:";<ms>" }
        mediaGenerationContext{ batchId:<uuid4>,
                                entityContext{ entityId, characterSlot{imageReferenceIndex:0} } }
        useNewMedia:true
        requests:[{ clientContext:<same>, imageModelName:"NARWHAL",
                    imageAspectRatio:"IMAGE_ASPECT_RATIO_LANDSCAPE",
                    structuredPrompt{parts:[{text}]}, seed:826730, imageInputs:[] }]
        POST -> routes.batch_generate_images_url(<EXISTING pid>) via client._post_json
        (content_type defaults to aisandbox text/plain;charset=UTF-8).  <-- COSTS ~1 CREDIT
  3. Parse + ASSERT (the whole point):
        workflows[0].parentEntityId == entityId   AND
        workflows[0].projectId     == the EXISTING project (NOT a new one).
  4. PATCH https://aisandbox-pa.googleapis.com/v1/flowWorkflows/{workflowId}
        metadata.primaryMediaId = mediaId.  (inline PATCH; same shape as commit_workflow.)  FREE.
  5. PATCH https://aisandbox-pa.googleapis.com/v1/flow/entities
        entityInfo.displayName + characterInfo.personalityNotes.
        Deliberately does NOT set imageReferences — we TEST that entityContext auto-linked it.  FREE.
  R. GET   https://labs.google/fx/api/trpc/flow.projectInitialData (tRPC) -> assert the entity
        has entityType==CHARACTER and characterInfo.imageReferences[].workflowId contains our
        workflowId (proving entityContext auto-linked it).  FREE.

Credit safety:
  - Generates EXACTLY ONE image (single request item) -> ~1 credit. No regen retries, no loops.
    createEntity / both PATCHes / read-back are FREE (no reCAPTCHA, no credit).
  - Idempotent-safe to re-run: each run mints a FRESH throwaway entity ("Spike Test V2").
  - No data-layer / DB writes. No signed fifeUrls are persisted or printed.

Run command (from the worktree root, using the worktree venv):

    # PowerShell
    $env:GFLOW_SPIKE_PROJECT_ID = "<an existing Flow project UUID on denon82>"
    .venv\Scripts\python.exe scripts\dev\character_create_spike_v2.py --profile denon82

    # bash
    GFLOW_SPIKE_PROJECT_ID=<project-uuid> \
      .venv/Scripts/python.exe scripts/dev/character_create_spike_v2.py --profile denon82

Required environment / args (same as V1):
  - profile: ``denon82`` (Chrome-strategy profile; --profile flag or GFLOW_CLI_PROFILE).
    Real-browser auth is mandatory for the credited image step ([[real-browser-auth-mandatory]]).
  - GFLOW_SPIKE_PROJECT_ID (or --project): an EXISTING Flow project UUID on that account.
  - --headless: optional; default headed (reCAPTCHA scores headed sessions higher).

Cost: ~1 credit (the single face image). Everything else is free REST.
This spike validates the entityContext DIRECT-REST path (Option A).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import structlog

# Resolve the worktree's ``src`` onto sys.path so the spike runs against THIS
# worktree's gflow_cli, mirroring how scripts/dev/*.py compute ROOT.
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gflow_cli import auth as auth_mod  # noqa: E402
from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402

logger = structlog.get_logger("character_create_spike_v2")

# tRPC routes (session-cookie auth) — same host/base as routes.CREATE_PROJECT.
CREATE_ENTITY_URL = f"{routes.LABS_TRPC_BASE}/flow.createEntity"
PROJECT_INITIAL_DATA_URL = f"{routes.LABS_TRPC_BASE}/flow.projectInitialData"

# aisandbox Bearer REST — entities collection PATCH (save the character).
FLOW_ENTITIES_URL = f"{routes.FLOW_API_BASE}/flow/entities"

# Wire constants — mirrored from gflow_cli.api.image (_CLIENT_TOOL / _RECAPTCHA_APP_TYPE).
CLIENT_TOOL = "PINHOLE"
RECAPTCHA_APP_TYPE = "RECAPTCHA_APPLICATION_TYPE_WEB"
# image.py::generate_image defaults recaptcha_action="imageGeneration"; mirror it so the
# self-assembled POST mints the same action the native image path uses.
RECAPTCHA_ACTION = "imageGeneration"

IMAGE_MODEL = "NARWHAL"
IMAGE_ASPECT = "IMAGE_ASPECT_RATIO_LANDSCAPE"
SEED = 826730  # fixed int (Date/random are fine in a normal script; this is NOT a wf sandbox)
IMAGE_REFERENCE_INDEX = 0  # 0 = face slot (1 = body, etc. — see docs/CHARACTER.md § 6.2)

DISPLAY_NAME = "Spike Test V2"
PERSONALITY = "spike v2"
FACE_PROMPT = (
    "studio portrait headshot of a fictional adult person, neutral expression, "
    "soft even lighting, plain grey background, photorealistic"
)


def _step(n: str, msg: str) -> None:
    print(f"[spike-v2] {n}  {msg}", flush=True)


def _unwrap_trpc(data: Any) -> dict[str, Any]:
    """Return the ``result.data.json`` payload of a single (non-batched) tRPC reply.

    Reused verbatim from V1. tRPC single-call replies are
    ``{"result":{"data":{"json":{...}}}}``. We defensively accept a top-level
    list (batched form) by taking element 0.
    """
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        msg = f"unexpected tRPC reply shape: {type(data).__name__}"
        raise ValueError(msg)
    result = data.get("result", {})
    inner = result.get("data", {}) if isinstance(result, dict) else {}
    payload = inner.get("json", inner) if isinstance(inner, dict) else {}
    if not isinstance(payload, dict):
        msg = "tRPC reply missing result.data.json object"
        raise ValueError(msg)
    return payload


async def _create_entity(client: FlowApiClient, project_id: str) -> str:
    """Step 0 — mint a fresh CHARACTER entity. Returns the new entityId.

    Reused verbatim from V1 (the createEntity helper was CORRECT). tRPC POST,
    session-cookie auth (content_type application/json like project.createProject).
    FREE — no reCAPTCHA, no credit.
    """
    body = {"json": {"projectId": project_id}}
    data = await client._post_json(  # noqa: SLF001 — spike reuses the generic helper
        CREATE_ENTITY_URL,
        body,
        content_type="application/json",
        route_name="createEntity",
    )
    payload = _unwrap_trpc(data)
    entity_id = payload.get("entityId")
    if not entity_id:
        msg = f"createEntity returned no entityId; keys={sorted(payload)}"
        raise ValueError(msg)
    entity_type = (payload.get("entityInfo") or {}).get("entityType")
    _step("0 OK", f"minted entityId={entity_id} entityType={entity_type}")
    return str(entity_id)


def _client_context(*, project_id: str, recaptcha_token: str, session_id: str) -> dict[str, Any]:
    """Mirror gflow_cli.api.image._client_context (used at root AND per-request)."""
    return {
        "recaptchaContext": {
            "token": recaptcha_token,
            "applicationType": RECAPTCHA_APP_TYPE,
        },
        "projectId": project_id,
        "tool": CLIENT_TOOL,
        "sessionId": session_id,
    }


def _build_character_batch_body(
    *, project_id: str, entity_id: str, recaptcha_token: str, session_id: str, batch_id: str
) -> dict[str, Any]:
    """Build the batchGenerateImages body OURSELVES — the Option A core.

    Mirrors gflow_cli.api.image._build_batch_generate_images_body but ADDS
    ``mediaGenerationContext.entityContext`` (the field generate_image cannot
    inject) and pins ``projectId`` to the EXISTING project. imageInputs is empty
    (fresh face slot). See docs/CHARACTER.md § 6.2.
    """
    cc = _client_context(
        project_id=project_id, recaptcha_token=recaptcha_token, session_id=session_id
    )
    request: dict[str, Any] = {
        # clientContext is duplicated inside the request — same shape, same token.
        "clientContext": _client_context(
            project_id=project_id, recaptcha_token=recaptcha_token, session_id=session_id
        ),
        "imageModelName": IMAGE_MODEL,
        "imageAspectRatio": IMAGE_ASPECT,
        "structuredPrompt": {"parts": [{"text": FACE_PROMPT}]},
        "seed": SEED,
        "imageInputs": [],  # empty for a fresh slot (face = first/base gen)
    }
    return {
        "clientContext": cc,
        "mediaGenerationContext": {
            "batchId": batch_id,
            "entityContext": {
                "entityId": entity_id,
                "characterSlot": {"imageReferenceIndex": IMAGE_REFERENCE_INDEX},
            },
        },
        "useNewMedia": True,
        "requests": [request],
    }


async def _generate_face_with_entity_context(
    client: FlowApiClient, *, project_id: str, entity_id: str
) -> tuple[str, str, str]:
    """Steps 1-3 — mint token, POST self-built body, parse + ASSERT. COSTS 1 CREDIT.

    Returns (workflow_id, media_id, returned_project_id). Asserts the server
    stamped parentEntityId == entity_id AND projectId == the existing project.
    """
    # Step 1 — mint a single-use reCAPTCHA token (project-agnostic mint on the pooled Page).
    token = await client._mint_recaptcha_token(RECAPTCHA_ACTION)  # noqa: SLF001
    _step("1 OK", f"minted reCAPTCHA token (action={RECAPTCHA_ACTION}, len={len(token)})")

    # Step 2 — build + POST our own body with entityContext into the EXISTING project.
    batch_id = str(uuid.uuid4())
    session_id = f";{int(time.time() * 1000)}"  # same format gflow transports use
    body = _build_character_batch_body(
        project_id=project_id,
        entity_id=entity_id,
        recaptcha_token=token,
        session_id=session_id,
        batch_id=batch_id,
    )
    url = routes.batch_generate_images_url(project_id)
    data = await client._post_json(  # noqa: SLF001 — content_type defaults to aisandbox text/plain
        url,
        body,
        route_name="batchGenerateImages",
    )

    # Step 3 — parse media[0] + workflows[0], then ASSERT the binding.
    media = data.get("media") if isinstance(data, dict) else None
    workflows = data.get("workflows") if isinstance(data, dict) else None
    if not isinstance(media, list) or not media:
        msg = f"batchGenerateImages returned no media[]; keys={sorted(data) if isinstance(data, dict) else type(data).__name__}"
        raise ValueError(msg)
    if not isinstance(workflows, list) or not workflows:
        msg = f"batchGenerateImages returned no workflows[]; keys={sorted(data) if isinstance(data, dict) else type(data).__name__}"
        raise ValueError(msg)

    media0 = media[0] if isinstance(media[0], dict) else {}
    wf0 = workflows[0] if isinstance(workflows[0], dict) else {}
    media_id = media0.get("name")
    workflow_id = wf0.get("name")
    returned_parent = wf0.get("parentEntityId")
    returned_project = wf0.get("projectId")
    if not media_id or not workflow_id:
        msg = f"missing media[0].name or workflows[0].name; media0={sorted(media0)} wf0={sorted(wf0)}"
        raise ValueError(msg)

    _step(
        "2 OK",
        f"POST batchGenerateImages -> workflowId={workflow_id} mediaId={media_id} (1 credit spent)",
    )

    # ---- THE WHOLE POINT: entityContext binding assertions ----
    parent_ok = returned_parent == entity_id
    project_ok = returned_project == project_id
    print("[spike-v2] --- entityContext binding assertions ---", flush=True)
    print(
        f"[spike-v2]   workflows[0].parentEntityId = {returned_parent}  "
        f"(expect {entity_id}) -> {parent_ok}",
        flush=True,
    )
    print(
        f"[spike-v2]   workflows[0].projectId      = {returned_project}  "
        f"(expect EXISTING {project_id}, NOT a new one) -> {project_ok}",
        flush=True,
    )
    if parent_ok and project_ok:
        _step("3 PASS", "entityContext bound the generation to the character in the existing project")
    else:
        _step("3 FAIL", "entityContext binding NOT confirmed (see assertions above)")

    return str(workflow_id), str(media_id), str(returned_project)


async def _set_primary_image(
    client: FlowApiClient, *, project_id: str, workflow_id: str, media_id: str
) -> None:
    """Step 4 — PATCH the workflow's primaryMediaId. FREE Bearer REST.

    Inline PATCH (same wire shape as client.commit_workflow), reused from V1 so
    the spike documents the exact Character payload.
    """
    body = {
        "workflow": {
            "name": workflow_id,
            "projectId": project_id,
            "metadata": {"primaryMediaId": media_id},
        },
        "updateMask": "metadata.primaryMediaId",
    }
    await client._patch_json(  # noqa: SLF001
        routes.flow_workflow_url(workflow_id),
        body,
        route_name="setPrimaryImage",
    )
    _step("4 OK", "PATCH flowWorkflows -> primaryMediaId set")


async def _save_character(
    client: FlowApiClient, *, project_id: str, entity_id: str
) -> None:
    """Step 5 — PATCH flow/entities to persist the character. FREE Bearer REST.

    Reuses V1's payload shape but DELIBERATELY omits imageReferences — we test
    that entityContext (step 2) already auto-linked the reference. Only the
    displayName + personalityNotes are set here.
    """
    body = {
        "entity": {
            "projectId": project_id,
            "entityId": entity_id,
            "entityInfo": {
                "displayName": DISPLAY_NAME,
                "characterInfo": {
                    "personalityNotes": PERSONALITY,
                },
            },
        },
        "updateMask": (
            "entityInfo.displayName,"
            "entityInfo.characterInfo.personalityNotes"
        ),
    }
    await client._patch_json(  # noqa: SLF001
        FLOW_ENTITIES_URL,
        body,
        route_name="saveCharacter",
    )
    _step("5 OK", f"PATCH flow/entities -> saved '{DISPLAY_NAME}' (imageReferences NOT set — testing auto-link)")


async def _read_back(
    client: FlowApiClient, *, project_id: str, entity_id: str, workflow_id: str
) -> bool:
    """Step R — GET projectInitialData and assert our character persisted + auto-linked.

    Reused verbatim from V1. tRPC GET; the input rides as a urlencoded
    ``?input={"json":{...}}`` query param (standard tRPC GET convention).
    Session-cookie auth. FREE.
    """
    trpc_input = json.dumps({"json": {"projectId": project_id}}, separators=(",", ":"))
    url = f"{PROJECT_INITIAL_DATA_URL}?input={quote(trpc_input, safe='')}"
    data = await client._get_json(url, route_name="projectInitialData")  # noqa: SLF001
    payload = _unwrap_trpc(data)
    contents = payload.get("projectContents") or {}
    entities = contents.get("entities") or []
    if not isinstance(entities, list):
        _step("R FAIL", "projectContents.entities is not a list")
        return False

    match: dict[str, Any] | None = None
    for ent in entities:
        if isinstance(ent, dict) and str(ent.get("entityId")) == entity_id:
            match = ent
            break
    if match is None:
        _step("R FAIL", f"entityId={entity_id} not found among {len(entities)} entities")
        return False

    info = match.get("entityInfo") or {}
    entity_type = info.get("entityType")
    char_info = info.get("characterInfo") or {}
    refs = char_info.get("imageReferences") or []
    ref_workflow_ids = [r.get("workflowId") for r in refs if isinstance(r, dict)]

    type_ok = entity_type == "CHARACTER"
    ref_ok = workflow_id in ref_workflow_ids

    print("[spike-v2] --- read-back assertions ---", flush=True)
    print(f"[spike-v2]   entityId         = {entity_id}", flush=True)
    print(f"[spike-v2]   entityType       = {entity_type}  (expect CHARACTER) -> {type_ok}", flush=True)
    print(f"[spike-v2]   displayName      = {info.get('displayName')}", flush=True)
    print(
        f"[spike-v2]   imageRef workflowIds = {ref_workflow_ids}  "
        f"(expect AUTO-LINKED contains {workflow_id}) -> {ref_ok}",
        flush=True,
    )
    return bool(type_ok and ref_ok)


async def _run(*, profile_dir: Path, headless: bool, project_id: str) -> int:
    t0 = time.monotonic()
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        entity_id = await _create_entity(client, project_id)
        workflow_id, media_id, returned_project = await _generate_face_with_entity_context(
            client, project_id=project_id, entity_id=entity_id
        )
        binding_ok = returned_project == project_id
        await _set_primary_image(
            client, project_id=project_id, workflow_id=workflow_id, media_id=media_id
        )
        await _save_character(client, project_id=project_id, entity_id=entity_id)
        read_ok = await _read_back(
            client, project_id=project_id, entity_id=entity_id, workflow_id=workflow_id
        )
    elapsed = time.monotonic() - t0
    ok = binding_ok and read_ok
    if ok:
        print(
            f"[spike-v2] PASS  entityContext direct-REST path verified "
            f"(binding + auto-link read-back) in {elapsed:.1f}s",
            flush=True,
        )
        return 0
    print(
        f"[spike-v2] FAIL  Option A NOT confirmed "
        f"(binding_ok={binding_ok} read_back_ok={read_ok}) ({elapsed:.1f}s)",
        flush=True,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Flow Character DIRECT-REST (entityContext) create->persist validation spike (V2)."
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"),
        help="Profile name (Chrome-strategy). Default: denon82 / GFLOW_CLI_PROFILE.",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("GFLOW_SPIKE_PROJECT_ID"),
        help="Existing Flow project UUID. Default: $GFLOW_SPIKE_PROJECT_ID.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser headless (default: headed; reCAPTCHA scores headed sessions higher).",
    )
    args = parser.parse_args(argv)

    project_id = args.project
    if not project_id:
        print(
            "[spike-v2] ERROR: no project id. Set GFLOW_SPIKE_PROJECT_ID or pass --project <uuid>.",
            file=sys.stderr,
        )
        return 2

    profile_dir = auth_mod.profile_dir(args.profile)
    if not profile_dir.exists():
        print(
            f"[spike-v2] ERROR: no session for profile '{args.profile}'. "
            "Run `gflow auth login --profile denon82` first.",
            file=sys.stderr,
        )
        return 2

    _step("--", f"profile={args.profile} project={project_id} headless={args.headless}")
    print("[spike-v2] NOTE: this run spends ~1 credit on the single face image.", flush=True)
    try:
        return asyncio.run(
            _run(profile_dir=profile_dir, headless=args.headless, project_id=project_id)
        )
    except KeyboardInterrupt:
        print("[spike-v2] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
