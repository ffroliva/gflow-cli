"""FlowApiClient — construction + lifecycle smoke tests (no live network).

End-to-end live tests against the real Flow API live in
`tests/api/test_client_live.py` (planned) and are gated by `GFLOW_LIVE=1`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flow_cli.api.client import FlowApiClient, FlowApiError, _default_project_title


class TestConstruction:
    def test_holds_profile_dir_and_headless_flag(self, tmp_path: Path) -> None:
        c = FlowApiClient(profile_dir=tmp_path / "prof", headless=False)
        assert c.profile_dir == tmp_path / "prof"
        assert c.headless is False

    def test_default_headless_true(self, tmp_path: Path) -> None:
        c = FlowApiClient(profile_dir=tmp_path / "prof")
        assert c.headless is True

    def test_page_property_raises_before_enter(self, tmp_path: Path) -> None:
        c = FlowApiClient(profile_dir=tmp_path / "prof")
        with pytest.raises(RuntimeError, match="not entered"):
            _ = c.page


class TestApiError:
    def test_includes_status_route_and_body_excerpt(self) -> None:
        e = FlowApiError(401, "unauthorized — fake body", route="https://example/foo")
        assert e.status == 401
        assert e.route == "https://example/foo"
        assert "401" in str(e)
        assert "unauthorized" in str(e)


class TestDefaultProjectTitle:
    def test_starts_with_flow_cli_prefix(self) -> None:
        title = _default_project_title()
        assert title.startswith("gflow-cli ")
