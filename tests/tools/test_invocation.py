from __future__ import annotations

import pytest
from pydantic import ValidationError

from gflow_cli.tools.invocation import (
    ToolInvocation,
    applied_tool_from_spec,
    config_hash,
    tool_specs_from_invocations,
)
from gflow_cli.tools.spec import ToolConfig, ToolSpec


def _spec() -> ToolSpec:
    return ToolSpec(
        name="creative-director",
        title="Creative Director",
        description="d",
        category="both",
        version="3",
        config=ToolConfig(system_template="t", model="gemini-2.5-flash"),
    )


def test_applied_tool_from_spec_snapshots_fields() -> None:
    at = applied_tool_from_spec(_spec(), {"style": "cinema"})
    assert at.name == "creative-director"
    assert at.version == "3"
    assert at.model == "gemini-2.5-flash"
    assert at.params == (("style", "cinema"),)
    assert at.params_dict() == {"style": "cinema"}
    assert len(at.config_hash) == 64  # sha256 hexdigest


def test_applied_tool_is_frozen_hashable() -> None:
    at = applied_tool_from_spec(_spec(), {})
    assert at.params == ()
    # Frozen + hashable (params is a tuple of pairs, not a dict).
    assert isinstance(hash(at), int)


def test_config_hash_is_stable_and_sensitive() -> None:
    spec_a = _spec()
    spec_b = _spec().model_copy(update={"version": "99"})  # version not in config
    # Same config → same hash regardless of spec-level version.
    assert config_hash(spec_a.config) == config_hash(spec_b.config)
    spec_c = ToolSpec(
        name="x",
        title="x",
        description="d",
        category="both",
        version="1",
        config=ToolConfig(system_template="DIFFERENT", model="gemini-2.5-flash"),
    )
    assert config_hash(spec_a.config) != config_hash(spec_c.config)


def test_tool_invocation_to_spec() -> None:
    assert ToolInvocation(name="creative-director").to_spec() == "creative-director"
    assert (
        ToolInvocation(name="creative-director", options={"style": "cinema"}).to_spec()
        == "creative-director:style=cinema"
    )


def test_tool_invocation_rejects_bad_name() -> None:
    with pytest.raises(ValidationError):
        ToolInvocation(name="Bad Name!")


def test_tool_specs_from_invocations_adapts_list() -> None:
    assert tool_specs_from_invocations(None) == ()
    assert tool_specs_from_invocations([]) == ()
    specs = tool_specs_from_invocations(
        [
            {"name": "creative-director", "options": {"style": "cinema"}},
            {"name": "creative-director"},
        ]
    )
    assert specs == ("creative-director:style=cinema", "creative-director")


def test_tool_specs_from_invocations_raises_on_malformed() -> None:
    with pytest.raises(ValidationError):
        tool_specs_from_invocations([{"options": {"style": "cinema"}}])  # missing name
    with pytest.raises(ValidationError):
        tool_specs_from_invocations([{"name": "Bad Name!"}])
