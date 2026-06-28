from __future__ import annotations

import pytest
from pydantic import ValidationError

from gflow_cli.tools.spec import DomainMode, ToolConfig, ToolSpec


def _spec() -> ToolSpec:
    return ToolSpec(
        name="creative-director",
        title="Creative Director",
        description="Expand a prompt via the 5-component formula.",
        category="both",
        version="1",
        requires_env=("GFLOW_CLI_GEMINI_API_KEY",),
        options_schema={"style": "domain mode name"},
        config=ToolConfig(
            system_template="Rewrite: ",
            banned_keywords=("8k",),
            domains=(DomainMode(name="cinema", vocabulary="ARRI Alexa, teal/orange"),),
        ),
    )


def test_spec_round_trips_and_supports() -> None:
    spec = _spec()
    assert spec.supports("image") and spec.supports("video")
    assert spec.config.domain("cinema").vocabulary.startswith("ARRI")
    assert spec.config.domain("missing") is None
    assert spec.config.domain(None) is None


def test_category_validated() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(
            name="x",
            title="X",
            description="d",
            category="audio",
            version="1",
            config=ToolConfig(system_template="t"),
        )


def test_image_only_does_not_support_video() -> None:
    spec = _spec().model_copy(update={"category": "image"})
    assert spec.supports("image")
    assert not spec.supports("video")


def test_name_slug_validated() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(
            name="Bad Name!",
            title="X",
            description="d",
            category="both",
            version="1",
            config=ToolConfig(system_template="t"),
        )
