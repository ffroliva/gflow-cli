#!/usr/bin/env python3
r"""Validation spike — Flow Character create -> persist loop (READ-ONLY repo spike).

Validates the reverse-engineered Flow **Character** wire protocol end-to-end on a
LIVE Flow account by reusing gflow's existing FlowApiClient primitives. This is a
throwaway diagnostic script under ``scripts/dev/`` (NOT shipped, NOT imported by
the package, NO data-layer writes). It exists to de-risk the production
implementation of ``gflow character create`` (issue #145) before any feature code
is written — see ``docs/CHARACTER.md`` / ``docs/CHARACTER_RECON.md`` for the full
protocol and ``docs/CHARACTER.md`` § 11 ("Validation gate") for why this spike runs.

What it exercises (one full create -> persist -> read-back loop):

  0. POST  https://labs.google/fx/api/trpc/flow.createEntity   {"json":{"projectId":<pid>}}
        (tRPC, session-cookie auth) -> parse new entityId from result.data.json.entityId
  1. Generate ONE face image via the EXISTING image-generation path
        (client.generate_image -> flowMedia:batchGenerateImages, tool="PINHOLE",
         imageModelName="NARWHAL", structuredPrompt.parts[].text, NO imageInputs).
        reCAPTCHA + Bearer are handled by gflow's transport. Returns a GeneratedImage
        carrying both .workflow_id and .media_name (the mediaId).  <-- COSTS ~1 CREDIT
  2. PATCH https://aisandbox-pa.googleapis.com/v1/flowWorkflows/{workflowId}
        {"workflow":{...,"metadata":{"primaryMediaId":mediaId}},"updateMask":"metadata.primaryMediaId"}
        (Bearer REST) -> pick the generated image as the workflow's primary media.
  3. PATCH https://aisandbox-pa.googleapis.com/v1/flow/entities
        {"entity":{...,"entityInfo":{"displayName":"Spike Test","characterInfo":{
         "personalityNotes":"spike","imageReferences":[{"workflowId":workflowId}]}}},
         "updateMask":"entityInfo.displayName,...personalityNotes,...imageReferences"}
        (Bearer REST) -> save the character (the "Concluir" action).
  R. GET   https://labs.google/fx/api/trpc/flow.projectInitialData (tRPC) ->
        assert projectContents.entities[] contains our entityId with
        entityType=="CHARACTER" and characterInfo.imageReferences[].workflowId
        includes our workflowId. Prints PASS / FAIL.

Credit safety:
  - Generates EXACTLY ONE image (count=1) -> 1 credit. No retries that re-generate,
    no loops. createEntity / both PATCHes / read-back are FREE (no reCAPTCHA, no credit).
  - Idempotent-safe to re-run: each run mints a FRESH throwaway entity ("Spike Test").
  - No data-layer / DB writes. No signed fifeUrls are persisted or printed.

Run command (from the worktree root, using the worktree venv):

    # PowerShell
    $env:GFLOW_SPIKE_PROJECT_ID = "<an existing Flow project UUID on denon82>"
    .venv\Scripts\python.exe scripts\dev\character_create_spike.py --profile denon82

    # bash
    GFLOW_SPIKE_PROJECT_ID=<project-uuid> \
      .venv/Scripts/python.exe scripts/dev/character_create_spike.py --profile denon82

Required environment / args:
  - profile: ``denon82`` (Chrome-strategy profile; --profile flag or GFLOW_CLI_PROFILE).
    Real-browser auth is mandatory for the credited image step ([[real-browser-auth-mandatory]]).
  - GFLOW_SPIKE_PROJECT_ID (or --project): an EXISTING Flow project UUID on that
    account to mint the character into.

Cost: ~1 credit (the single face image). Everything else is free REST.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
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
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model  # noqa: E402

logger = structlog.get_logger("character_create_spike")

# tRPC routes (session-cookie auth) — same host/base as routes.CREATE_PROJECT.
CREATE_ENTITY_URL = f"{routes.LABS_TRPC_BASE}/flow.createEntity"
PROJECT_INITIAL_DATA_URL = f"{routes.LABS_TRPC_BASE}/flow.projectInitialData"

# aisandbox Bearer REST — entities collection PATCH (save the character).
FLOW_ENTITIES_URL = f"{routes.FLOW_API_BASE}/flow/entities"

DISPLAY_NAME = "Spike Test"
PERSONALITY = "spike"
FACE_PROMPT = (
    "studio portrait headshot of a fictional adult person, neutral expression, "
    "soft even lighting, plain grey background, photorealistic"
)


def _step(n: str, msg: str) -> None:
    print(f"[spike] {n}  {msg}", flush=True)


def _unwrap_trpc(data: Any) -> dict[str, Any]:
    """Return the ``result.data.json`` payload of a single (non-batched) tRPC reply.

    Mirrors how ProjectInfo.from_create_response reads project.createProject. tRPC
    single-call replies are ``{"result":{"data":{"json":{...}}}}``. We defensively
    accept a top-level list (batched form) by taking element 0.
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

    tRPC POST, session-cookie auth (content_type application/json like
    project.createProject). FREE — no reCAPTCHA, no credit.
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


async def _generate_face(client: FlowApiClient, project_id: str) -> tuple[str, str]:
    """Step 1 — generate ONE face image via the existing image path. COSTS 1 CREDIT.

    Reuses client.generate_image -> flowMedia:batchGenerateImages with tool=PINHOLE
    (baked into _client_context as _CLIENT_TOOL) and imageModelName=NARWHAL. No
    imageInputs (face = first/base gen). Returns (workflow_id, media_id).
    """
    req = GenerateImageRequest(
        prompt=FACE_PROMPT,
        aspect=Aspect.LANDSCAPE,
        model=Model.NARWHAL,
        count=1,
    )
    image = await client.generate_image(project_id=project_id, req=req)
    workflow_id = image.workflow_id
    media_id = image.media_name  # the mediaId / primaryMediaId candidate
    _step("1 OK", f"generated face workflowId={workflow_id} mediaId={media_id} (1 credit spent)")
    return workflow_id, media_id


async def _set_primary_image(
    client: FlowApiClient, *, project_id: str, workflow_id: str, media_id: str
) -> None:
    """Step 2 — PATCH the workflow's primaryMediaId. FREE Bearer REST.

    Identical wire shape to client.commit_workflow, but inlined here so the spike
    documents the exact Character payload. (Production code should reuse
    client.commit_workflow.)
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
    _step("2 OK", "PATCH flowWorkflows -> primaryMediaId set")


async def _save_character(
    client: FlowApiClient, *, project_id: str, entity_id: str, workflow_id: str
) -> None:
    """Step 3 — PATCH flow/entities to persist the character. FREE Bearer REST."""
    body = {
        "entity": {
            "projectId": project_id,
            "entityId": entity_id,
            "entityInfo": {
                "displayName": DISPLAY_NAME,
                "characterInfo": {
                    "personalityNotes": PERSONALITY,
                    "imageReferences": [{"workflowId": workflow_id}],
                },
            },
        },
        "updateMask": (
            "entityInfo.displayName,"
            "entityInfo.characterInfo.personalityNotes,"
            "entityInfo.characterInfo.imageReferences"
        ),
    }
    await client._patch_json(  # noqa: SLF001
        FLOW_ENTITIES_URL,
        body,
        route_name="saveCharacter",
    )
    _step("3 OK", f"PATCH flow/entities -> saved '{DISPLAY_NAME}'")


async def _read_back(
    client: FlowApiClient, *, project_id: str, entity_id: str, workflow_id: str
) -> bool:
    """Step R — GET projectInitialData and assert our character persisted.

    tRPC GET; the input rides as a urlencoded ``?input={"json":{...}}`` query
    param (standard tRPC GET convention — see UNCERTAINTY in the spike docstring /
    final report). Session-cookie auth. FREE.
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

    print("[spike] --- read-back assertions ---", flush=True)
    print(f"[spike]   entityId         = {entity_id}", flush=True)
    print(f"[spike]   entityType       = {entity_type}  (expect CHARACTER) -> {type_ok}", flush=True)
    print(f"[spike]   displayName      = {info.get('displayName')}", flush=True)
    print(
        f"[spike]   imageRef workflowIds = {ref_workflow_ids}  "
        f"(expect contains {workflow_id}) -> {ref_ok}",
        flush=True,
    )
    return bool(type_ok and ref_ok)


async def _run(*, profile_dir: Path, headless: bool, project_id: str) -> int:
    t0 = time.monotonic()
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        entity_id = await _create_entity(client, project_id)
        workflow_id, media_id = await _generate_face(client, project_id)
        await _set_primary_image(
            client, project_id=project_id, workflow_id=workflow_id, media_id=media_id
        )
        await _save_character(
            client, project_id=project_id, entity_id=entity_id, workflow_id=workflow_id
        )
        ok = await _read_back(
            client, project_id=project_id, entity_id=entity_id, workflow_id=workflow_id
        )
    elapsed = time.monotonic() - t0
    if ok:
        print(f"[spike] PASS  create->persist->read-back verified in {elapsed:.1f}s", flush=True)
        return 0
    print(f"[spike] FAIL  read-back did not confirm the character ({elapsed:.1f}s)", flush=True)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flow Character create->persist validation spike.")
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
            "[spike] ERROR: no project id. Set GFLOW_SPIKE_PROJECT_ID or pass --project <uuid>.",
            file=sys.stderr,
        )
        return 2

    profile_dir = auth_mod.profile_dir(args.profile)
    if not profile_dir.exists():
        print(
            f"[spike] ERROR: no session for profile '{args.profile}'. "
            "Run `gflow auth login --profile denon82` first.",
            file=sys.stderr,
        )
        return 2

    _step("--", f"profile={args.profile} project={project_id} headless={args.headless}")
    print("[spike] NOTE: this run spends ~1 credit on the single face image.", flush=True)
    try:
        return asyncio.run(
            _run(profile_dir=profile_dir, headless=args.headless, project_id=project_id)
        )
    except KeyboardInterrupt:
        print("[spike] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
