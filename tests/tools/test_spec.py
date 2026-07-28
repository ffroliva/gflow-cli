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
        requires_env=("GFLOW_CLI_LLM_API_KEY",),
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


def test_domain_category_gating() -> None:
    """Two domains can share a name when their categories differ; ``domain()``
    disambiguates by the requested category (review fold-in)."""
    cfg = ToolConfig(
        system_template="t",
        domains=(
            DomainMode(name="product", vocabulary="IMAGE product vocab", category="image"),
            DomainMode(name="product", vocabulary="VIDEO product vocab", category="video"),
            DomainMode(name="cinema", vocabulary="image cinema", category="image"),
        ),
    )
    # category=None → first name match (backward compatible).
    assert cfg.domain("product").vocabulary == "IMAGE product vocab"
    # category gates which "product" is returned.
    assert cfg.domain("product", "image").vocabulary == "IMAGE product vocab"
    assert cfg.domain("product", "video").vocabulary == "VIDEO product vocab"
    # an image-only domain is invisible to a video request.
    assert cfg.domain("cinema", "video") is None
    assert cfg.domain("cinema", "image").vocabulary == "image cinema"


def test_domain_both_category_matches_any() -> None:
    cfg = ToolConfig(
        system_template="t",
        domains=(DomainMode(name="universal", vocabulary="v"),),  # default category="both"
    )
    assert cfg.domain("universal", "image").vocabulary == "v"
    assert cfg.domain("universal", "video").vocabulary == "v"


def test_options_schema_is_immutable() -> None:
    spec = _spec()
    with pytest.raises(TypeError):
        spec.options_schema["style"] = "mutated"  # type: ignore[index]
