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


def test_expand_prompt_disabled_returns_identity() -> None:
    from gflow_cli._cli_helpers import expand_prompt

    sent, original = expand_prompt("cat in space", enabled=False)
    assert sent == "cat in space"
    assert original is None


def test_expand_prompt_no_key_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_GEMINI_API_KEY", raising=False)
    reset_settings()
    from gflow_cli._cli_helpers import expand_prompt

    sent, original = expand_prompt("cat in space", enabled=True)
    assert sent == "cat in space"
    assert original is None


def test_expand_prompt_success_returns_expanded_and_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api import prompt_expander as pe
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_GEMINI_API_KEY", "fake-key")
    reset_settings()

    class _StubExpander:
        def expand(self, prompt: str) -> pe.ExpansionResult:
            return pe.ExpansionResult(
                original=prompt,
                expanded="a vivid, fully expanded prompt",
                was_expanded=True,
            )

    monkeypatch.setattr(
        pe.PromptExpander,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _StubExpander()),  # noqa: ARG005
    )
    from gflow_cli._cli_helpers import expand_prompt

    sent, original = expand_prompt("cat in space", enabled=True)
    assert sent == "a vivid, fully expanded prompt"
    assert original == "cat in space"


def test_expand_prompt_quiet_suppresses_stdout_notice(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """quiet=True (the --json path) must not write Rich notices to stdout."""
    from gflow_cli.api import prompt_expander as pe
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_GEMINI_API_KEY", "fake-key")
    reset_settings()

    class _StubExpander:
        def expand(self, prompt: str) -> pe.ExpansionResult:
            return pe.ExpansionResult(original=prompt, expanded="expanded", was_expanded=True)

    monkeypatch.setattr(
        pe.PromptExpander,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _StubExpander()),  # noqa: ARG005
    )
    from gflow_cli._cli_helpers import expand_prompt

    sent, original = expand_prompt("cat in space", enabled=True, quiet=True)
    assert sent == "expanded"
    assert original == "cat in space"
    assert capsys.readouterr().out == ""
