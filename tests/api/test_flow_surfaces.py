"""Each FlowApiClient surface runs on the session core by itself, in the documented order (#1249).

``docs/ARCHITECTURE.md`` describes the composition: four surfaces in one chain on ``FlowSession``
(workspace → generation → media edit → characters) and ``AccountBootstrap`` as a second branch.
A surface built on its own must get ``FlowSession.__init__`` and the primitives its methods use.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gflow_cli.api._flow_bootstrap import AccountBootstrap
from gflow_cli.api._flow_characters import CharacterSurface
from gflow_cli.api._flow_generation import GenerationSurface
from gflow_cli.api._flow_media_edit import MediaEditSurface
from gflow_cli.api._flow_session import FlowSession
from gflow_cli.api._flow_workspace import WorkspaceSurface
from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import Settings

# Every class between the facade and FlowSession, read from the MRO so a new surface is covered.
SURFACES = FlowApiClient.__mro__[1 : FlowApiClient.__mro__.index(FlowSession)]


def test_the_composition_is_the_documented_one() -> None:
    """A dropped or reordered base changes what each surface can reach; the docs name this."""
    assert FlowApiClient.__bases__ == (CharacterSurface, AccountBootstrap)
    assert CharacterSurface.__bases__ == (MediaEditSurface,)
    assert MediaEditSurface.__bases__ == (GenerationSurface,)
    assert GenerationSurface.__bases__ == (WorkspaceSurface,)
    assert WorkspaceSurface.__bases__ == (FlowSession,)
    assert AccountBootstrap.__bases__ == (FlowSession,)


@pytest.mark.parametrize("surface", SURFACES, ids=lambda c: c.__name__)
def test_a_surface_built_on_its_own_has_the_session_core(
    surface: type, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in [k for k in os.environ if k.startswith("GFLOW_CLI_")]:
        monkeypatch.delenv(name)  # a stray developer setting must not decide this test
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    obj = surface(tmp_path / "profile", headless=True, settings=settings)
    assert obj.profile_dir == tmp_path / "profile"
    assert obj.headless is True
    assert obj.settings is settings
    with pytest.raises(RuntimeError, match="not entered"):  # FlowSession.page before any browser
        _ = obj.page
    for primitive in ("_post_json", "_get_json", "_patch_json", "_checkout_page"):
        assert callable(getattr(obj, primitive))
