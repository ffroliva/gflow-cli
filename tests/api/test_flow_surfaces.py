"""Each FlowApiClient surface runs on the session core by itself (#1249).

The split made every surface a class of its own. ``FlowApiClient`` reaches ``FlowSession`` through
more than one parent, so the facade alone cannot show that each surface carries the session core.
Built on its own, a surface must get ``FlowSession.__init__`` and the state and primitives that
its methods use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api._flow_bootstrap import AccountBootstrap
from gflow_cli.api._flow_characters import CharacterSurface
from gflow_cli.api._flow_generation import GenerationSurface
from gflow_cli.api._flow_media_edit import MediaEditSurface
from gflow_cli.api._flow_workspace import WorkspaceSurface
from gflow_cli.config import Settings

SURFACES = [
    AccountBootstrap,
    WorkspaceSurface,
    GenerationSurface,
    MediaEditSurface,
    CharacterSurface,
]


@pytest.mark.parametrize("surface", SURFACES, ids=lambda c: c.__name__)
def test_a_surface_built_on_its_own_has_the_session_core(surface: type, tmp_path: Path) -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    obj = surface(tmp_path / "profile", headless=True, settings=settings)
    assert obj.profile_dir == tmp_path / "profile"
    assert obj.headless is True
    assert obj.settings is settings
    with pytest.raises(RuntimeError, match="not entered"):  # FlowSession.page before any browser
        _ = obj.page
    for primitive in ("_post_json", "_get_json", "_patch_json", "_checkout_page"):
        assert callable(getattr(obj, primitive))
