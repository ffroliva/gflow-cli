"""Helper relocation tests + negative import test (drift prevention).

Step 4b.0 findings (recorded for future maintainers):

* The pre-relocation implementations of ``_resolve_profile`` and
  ``_make_provider_dir`` in ``src/gflow_cli/cli_image.py:81-104`` and
  ``src/gflow_cli/cli_video.py:37-60`` were **byte-identical** — no drift
  to reconcile.
* ``_resolve_profile(profile)``:
    - returns *profile* verbatim if truthy;
    - otherwise delegates to ``profile_store.resolve_profile(None)`` which
      follows this precedence chain (see ``profile_store.py:161-174``):
      ``GFLOW_CLI_PROFILE`` env > config.toml default >
      single discovered profile under $GFLOW_CLI_HOME > raise.
    - The helper does **not** consult ``Settings()`` / pydantic-settings
      auto-binding — the env-var read happens inside ``profile_store``
      via ``os.environ.get``.
* ``_make_provider_dir(profile_name)``:
    - calls ``auth_mod.profile_dir(name)`` which resolves to
      ``$GFLOW_CLI_HOME/profile_<name>``;
    - **exits with code 2 if the directory does not exist** (it does NOT
      create one). It does NOT read ``GFLOW_CLI_OUTPUT_DIR``.

The test for ``_make_provider_dir`` therefore pre-creates the profile
directory under ``GFLOW_CLI_HOME`` rather than expecting the helper to
mkdir, as the plan's Step 4b.1 boilerplate had assumed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Anchor module paths on this test file's location so the AST-based negative
# import test works regardless of pytest's CWD (H_py-rev fix from the plan).
# tests/cli/test_helpers.py -> repo root is two levels up.
_REPO_ROOT = Path(__file__).parent.parent.parent
_CLI_IMAGE_PATH = _REPO_ROOT / "src" / "gflow_cli" / "cli_image.py"
_CLI_VIDEO_PATH = _REPO_ROOT / "src" / "gflow_cli" / "cli_video.py"


def _toplevel_function_names(path: Path) -> set[str]:
    """Return the set of top-level ``def`` names in *path* (no classes, no nested)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # Match BOTH FunctionDef and AsyncFunctionDef — a future regression that
    # re-introduces the helper as ``async def`` would otherwise silently pass.
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_helpers_relocated_to_cli_helpers_module() -> None:
    """Both helpers must be importable from ``gflow_cli._cli_helpers``."""
    from gflow_cli import _cli_helpers

    assert callable(_cli_helpers._resolve_profile)
    assert callable(_cli_helpers._make_provider_dir)


@pytest.mark.parametrize(
    "module_path",
    [_CLI_IMAGE_PATH, _CLI_VIDEO_PATH],
    ids=["cli_image", "cli_video"],
)
def test_no_local_helper_definitions_in_cli_modules(module_path: Path) -> None:
    """Negative test — drift prevention. After T4b, neither ``cli_image.py``
    nor ``cli_video.py`` defines ``_resolve_profile`` or ``_make_provider_dir``
    locally; they import from ``gflow_cli._cli_helpers``.
    """
    assert module_path.exists(), f"Expected source file at {module_path}"
    names = _toplevel_function_names(module_path)
    assert "_resolve_profile" not in names, (
        f"{module_path} still defines _resolve_profile locally — "
        "import from gflow_cli._cli_helpers instead."
    )
    assert "_make_provider_dir" not in names, (
        f"{module_path} still defines _make_provider_dir locally — "
        "import from gflow_cli._cli_helpers instead."
    )


def test_resolve_profile_returns_explicit_when_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the caller passes a non-empty *profile* arg, ``_resolve_profile``
    must return it verbatim — no env, config.toml, or filesystem lookup."""
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    reset_settings()
    try:
        from gflow_cli._cli_helpers import _resolve_profile

        assert _resolve_profile("experiments") == "experiments"
    finally:
        reset_settings()


def test_resolve_profile_falls_back_to_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When *profile* is None, ``_resolve_profile`` delegates to
    ``profile_store.resolve_profile`` which reads ``GFLOW_CLI_PROFILE`` second
    in its precedence chain (after the CLI flag, before the config.toml default).
    """
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_PROFILE", "work")
    reset_settings()
    try:
        from gflow_cli._cli_helpers import _resolve_profile

        assert _resolve_profile(None) == "work"
    finally:
        reset_settings()


def test_make_provider_dir_returns_existing_profile_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_make_provider_dir`` returns ``$GFLOW_CLI_HOME/profile_<name>`` when
    that directory already exists. It does NOT create it — that responsibility
    belongs to ``gflow auth login`` (see ``auth.login``)."""
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    reset_settings()
    try:
        # Pre-create the profile dir to simulate a prior `gflow auth login`.
        pdir = tmp_path / "profile_experiments"
        pdir.mkdir(parents=True, exist_ok=True)

        from gflow_cli._cli_helpers import _make_provider_dir

        result = _make_provider_dir("experiments")
        assert result == pdir
        assert result.exists() and result.is_dir()
    finally:
        reset_settings()


def test_make_provider_dir_exits_when_profile_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_make_provider_dir`` calls ``sys.exit(2)`` if the profile dir is
    absent (user must run ``gflow auth login`` first). This is the documented
    behaviour from the pre-relocation implementations."""
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    reset_settings()
    try:
        from gflow_cli._cli_helpers import _make_provider_dir

        with pytest.raises(SystemExit) as excinfo:
            _make_provider_dir("nonexistent")
        assert excinfo.value.code == 2
    finally:
        reset_settings()


def test_apply_tool_option_no_tools_is_identity() -> None:
    from gflow_cli._cli_helpers import apply_tool_option

    sent, original, applied = apply_tool_option("cat", (), category="image", quiet=True)
    assert sent == "cat"
    assert original is None
    assert applied is None


def test_apply_tool_option_unknown_tool_raises_usage_error() -> None:
    import click
    import pytest

    from gflow_cli._cli_helpers import apply_tool_option

    with pytest.raises(click.UsageError):
        apply_tool_option("cat", ("nope",), category="image", quiet=True)


def test_apply_tool_option_runs_creative_director(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli import _cli_helpers
    from gflow_cli.tools.expander import ExpansionResult

    monkeypatch.setattr(
        _cli_helpers,
        "apply_tool",
        lambda spec, text, options, **kw: ExpansionResult(
            original=text, expanded="EXPANDED", was_expanded=True
        ),
    )
    sent, original, applied = _cli_helpers.apply_tool_option(
        "cat", ("creative-director",), category="image", quiet=True
    )
    assert sent == "EXPANDED"
    assert original == "cat"
    # The applied-tool snapshot is built from the real spec (not apply_tool's
    # monkeypatched output) for metadata_json.tool recording.
    assert applied is not None
    assert applied.name == "creative-director"
    assert applied.version == "1"
    assert len(applied.config_hash) == 64


def test_apply_tool_option_wrong_category_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import click
    import pytest

    from gflow_cli._cli_helpers import apply_tool_option
    from gflow_cli.tools.spec import ToolConfig, ToolSpec

    video_only = ToolSpec(
        name="vid",
        title="Vid",
        description="d",
        category="video",
        version="1",
        config=ToolConfig(system_template="t"),
    )
    # get_tool is imported function-locally, so patch the registry source.
    monkeypatch.setattr("gflow_cli.tools.registry.get_tool", lambda name: video_only)
    with pytest.raises(click.UsageError):
        apply_tool_option("cat", ("vid",), category="image", quiet=True)


def test_parse_tool_spec_handles_options() -> None:
    from gflow_cli._cli_helpers import _parse_tool_spec

    assert _parse_tool_spec("creative-director") == ("creative-director", {})
    assert _parse_tool_spec("creative-director:style=cinema") == (
        "creative-director",
        {"style": "cinema"},
    )
    assert _parse_tool_spec("name:") == ("name", {})
    assert _parse_tool_spec("name:a=1,b=2") == ("name", {"a": "1", "b": "2"})


def test_apply_tool_option_unknown_option_key_raises_usage_error() -> None:
    """An option key not declared in the tool's options_schema → UsageError."""
    import click

    from gflow_cli._cli_helpers import apply_tool_option

    with pytest.raises(click.UsageError, match="unknown option"):
        apply_tool_option(
            "cat",
            ("creative-director:unknown_key=foo",),
            category="image",
            quiet=True,
        )


def test_apply_tool_option_unknown_style_raises_usage_error() -> None:
    """A style value that is not a declared domain → UsageError."""
    import click

    from gflow_cli._cli_helpers import apply_tool_option

    with pytest.raises(click.UsageError, match="unknown image style"):
        apply_tool_option(
            "cat",
            ("creative-director:style=cinmaaatic",),
            category="image",
            quiet=True,
        )


def test_apply_tool_option_rejects_cross_category_style() -> None:
    """An image generation must reject a video-only style and vice versa
    (category-gated domain resolution, review fold-in)."""
    import click

    from gflow_cli._cli_helpers import apply_tool_option

    # "cinematic" is a video domain — invalid on an image command.
    with pytest.raises(click.UsageError, match="unknown image style 'cinematic'"):
        apply_tool_option(
            "cat", ("creative-director:style=cinematic",), category="image", quiet=True
        )
    # "cinema" is an image domain — invalid on a video command.
    with pytest.raises(click.UsageError, match="unknown video style 'cinema'"):
        apply_tool_option("cat", ("creative-director:style=cinema",), category="video", quiet=True)


def test_apply_tool_option_valid_style_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recognized style value must pass validation and reach apply_tool."""
    from gflow_cli import _cli_helpers
    from gflow_cli.tools.expander import ExpansionResult

    monkeypatch.setattr(
        _cli_helpers,
        "apply_tool",
        lambda spec, text, options, **kw: ExpansionResult(
            original=text, expanded="EXPANDED", was_expanded=True
        ),
    )
    sent, original, _applied = _cli_helpers.apply_tool_option(
        "cat",
        ("creative-director:style=cinema",),
        category="image",
        quiet=True,
    )
    assert sent == "EXPANDED"
    assert original == "cat"
