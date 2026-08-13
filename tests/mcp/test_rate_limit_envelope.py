# SPDX-License-Identifier: MIT
"""#498: the two generate tools must refuse rate-limited calls with the SAME
RFC 9457 problem-details envelope, and gflow_list_projects must paginate
honestly (offset in, has_more/next_offset out) instead of hardcoding offset=0
and reporting the page size as "total"."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest


async def _deny() -> bool:
    return False


@pytest.mark.asyncio
async def test_rate_limited_envelope_identical_across_generate_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.mcp import tools

    monkeypatch.setattr(tools._rate_limiter, "acquire", _deny)
    image = await tools.gflow_generate_image(prompt="x")
    video = await tools.gflow_generate_video(prompt="x", mode="t2v")

    assert image == video, "#498: refusal envelopes must be identical"
    assert image["status"] == "rate_limited"
    err = image["error"]
    assert err["type"] == "https://gflow-cli.dev/errors/rate-limited"
    assert err["status"] == 429


def _fake_rows(n: int) -> list[Any]:
    return [
        SimpleNamespace(
            project_id=f"p{i}",
            title=f"t{i}",
            profile="default",
            created_at=datetime(2026, 8, 13, tzinfo=UTC),
            image_count=0,
            video_count=0,
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_list_projects_paginates_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.mcp import tools

    all_rows = _fake_rows(3)

    def fake_query(*, db_path: Any, profile: Any, limit: int, offset: int) -> list[Any]:
        return all_rows[offset : offset + limit]

    monkeypatch.setattr(tools, "list_projects", fake_query)

    page1 = await tools.gflow_list_projects(limit=2)
    assert [p["project_id"] for p in page1["projects"]] == ["p0", "p1"]
    assert page1["count"] == 2
    assert page1["offset"] == 0
    assert page1["has_more"] is True
    assert page1["next_offset"] == 2
    assert "total" not in page1, "#498: page size masquerading as table total"

    page2 = await tools.gflow_list_projects(limit=2, offset=page1["next_offset"])
    assert [p["project_id"] for p in page2["projects"]] == ["p2"]
    assert page2["has_more"] is False
    assert page2["next_offset"] is None
