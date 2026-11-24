# SPDX-License-Identifier: MIT
"""Live proof that ``POST /v1/images/generations`` on ``gflow serve`` returns a real image (#1365).

Drives the route through ``build_app`` (the same app ``gflow serve`` serves) with a daemon
token, so the request crosses the auth layer, the adapter and the MCP tool's queued path.

Cost: $0 on any account (Flow daily image quota, zero Veo credits).
Opt-in: ``-m e2e_image`` with ``GFLOW_CLI_E2E_PROFILE`` set.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from gflow_cli.config import get_settings
from gflow_cli.mcp.server import build_app

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]

_PROMPT = "a single blue paper boat on still water, minimalist, plain background"
#: Not a credential — a fixed literal for this in-process app.
_TOKEN = "e2e-daemon-token"


def test_openai_images_generations_returns_an_image(
    e2e_profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_PROFILE must name a logged-in profile")
    monkeypatch.setenv("GFLOW_CLI_PROFILE", profile)
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))  # never the real folder
    monkeypatch.delenv("GFLOW_MCP_NO_SPEND", raising=False)
    get_settings.cache_clear()

    app = build_app(transport="http", host="127.0.0.1", token=_TOKEN)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        resp = client.post(
            "/v1/images/generations",
            json={"prompt": _PROMPT, "size": "1024x1024", "n": 1},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data) == 1, data
    image = base64.b64decode(data[0]["b64_json"])
    assert image[:8] == b"\x89PNG\r\n\x1a\n" or image[:3] == b"\xff\xd8\xff", image[:16]
