from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gflow_cli.config import reset_settings
from gflow_cli.tools.expander import ExpansionResult, PromptExpander
from gflow_cli.tools.registry import get_tool, reset_registry
from gflow_cli.tools.runtime import (
    _apply_multimodal_reverse_engineering,
    _collect_frames,
    apply_tool,
    build_instruction,
)


def setup_function() -> None:
    reset_registry()


def test_build_instruction_appends_domain() -> None:
    cfg = get_tool("creative-director").config
    instr = build_instruction(cfg, "cinema")
    assert "cinema" in instr.lower() or "ARRI" in instr  # domain vocab injected
    base = build_instruction(cfg, None)
    assert len(instr) > len(base)


def test_apply_tool_unwraps_secret_llm_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default-expander path must hand PromptExpander the RAW key string,
    not the SecretStr wrapper (issue #474) — otherwise the gateway would get
    'Authorization: Bearer **********'."""
    monkeypatch.setenv("GFLOW_CLI_LLM_API_KEY", "sk-raw-unwrapped")
    reset_settings()
    captured: dict[str, object] = {}

    def fake_expander(api_key: object, **kwargs: object) -> MagicMock:
        captured["api_key"] = api_key
        mock = MagicMock()
        mock.expand.return_value = ExpansionResult(
            original="cat", expanded="cat", was_expanded=False
        )
        return mock

    with patch("gflow_cli.tools.runtime.PromptExpander", side_effect=fake_expander):
        apply_tool(get_tool("creative-director"), "cat", {})

    assert captured["api_key"] == "sk-raw-unwrapped"


def test_apply_tool_strips_banned_from_output() -> None:
    spec = get_tool("creative-director")

    def transport(url, payload, timeout):  # noqa: ANN001
        return {"choices": [{"message": {"content": "a hyperrealistic 8k cat scene"}}]}

    instruction = build_instruction(spec.config, None)
    expander = PromptExpander("key", transport=transport, system_instruction=instruction)
    result = apply_tool(spec, "cat", {}, expander=expander)
    assert isinstance(result, ExpansionResult)
    assert result.was_expanded
    assert "hyperrealistic" not in result.expanded.lower()
    assert "8k" not in result.expanded.lower()


def test_apply_tool_uses_per_tool_banned_keywords() -> None:
    """apply_tool must strip from spec.config.banned_keywords, not the global list.

    A spec that only bans "photorealism" must strip "photorealism" but leave
    "8k" intact (8k is in the global BANNED_KEYWORDS but not in this spec).
    """
    from gflow_cli.tools.spec import ToolConfig, ToolSpec

    spec = ToolSpec(
        name="test-tool",
        title="Test Tool",
        description="d",
        category="both",
        version="1",
        config=ToolConfig(
            system_template="expand: ",
            banned_keywords=("photorealism",),
        ),
    )

    def transport(url, payload, timeout):  # noqa: ANN001
        return {"choices": [{"message": {"content": "a photorealism 8k landscape"}}]}

    expander = PromptExpander("key", transport=transport, system_instruction="expand: ")
    result = apply_tool(spec, "landscape", {}, expander=expander)
    assert result.was_expanded
    # "photorealism" is in spec.config.banned_keywords → must be stripped
    assert "photorealism" not in result.expanded.lower()
    # "8k" is NOT in spec.config.banned_keywords → must survive
    assert "8k" in result.expanded.lower()


# ---------------------------------------------------------------------------
# _collect_frames
# ---------------------------------------------------------------------------


def test_collect_frames_returns_empty_when_watch_py_missing() -> None:
    with patch("gflow_cli.tools.runtime.DEFAULT_CLAUDE_VIDEO_DIR", "/nonexistent/dir"):
        frames = _collect_frames("http://example.com/video.mp4")
    assert frames == []


def test_collect_frames_returns_empty_on_subprocess_failure(tmp_path: object) -> None:
    from pathlib import Path

    fake_dir = Path(str(tmp_path)) / "claude-video"
    scripts = fake_dir / "scripts"
    scripts.mkdir(parents=True)
    watch_py = scripts / "watch.py"
    watch_py.write_text("import sys; sys.exit(1)")

    with patch("gflow_cli.tools.runtime.DEFAULT_CLAUDE_VIDEO_DIR", str(fake_dir)):
        frames = _collect_frames("http://example.com/video.mp4")

    assert frames == []


def test_collect_frames_selects_up_to_five_frames(tmp_path: object) -> None:
    from pathlib import Path

    fake_dir = Path(str(tmp_path)) / "claude-video"
    scripts = fake_dir / "scripts"
    scripts.mkdir(parents=True)
    watch_py = scripts / "watch.py"

    # watch.py that creates 10 fake frames under out_dir/frames/
    watch_py.write_text(
        "import sys, pathlib, os\n"
        "out_dir = pathlib.Path(sys.argv[sys.argv.index('--out-dir') + 1])\n"
        "frames_dir = out_dir / 'frames'\n"
        "frames_dir.mkdir(parents=True, exist_ok=True)\n"
        "for i in range(10):\n"
        "    (frames_dir / f'frame_{i:02d}.jpg').write_bytes(b'JPEG')\n"
    )

    with patch("gflow_cli.tools.runtime.DEFAULT_CLAUDE_VIDEO_DIR", str(fake_dir)):
        frames = _collect_frames("http://example.com/video.mp4")

    assert len(frames) == 5


# ---------------------------------------------------------------------------
# _apply_multimodal_reverse_engineering
# ---------------------------------------------------------------------------


def _make_spec(banned: tuple[str, ...] = ()) -> object:
    from gflow_cli.tools.spec import ToolConfig, ToolSpec

    return ToolSpec(
        name="reverse-engineer",
        title="Reverse Engineer",
        description="d",
        category="both",
        version="1",
        config=ToolConfig(system_template="analyse: ", banned_keywords=banned),
    )


def test_apply_multimodal_image_path_success(tmp_path: object) -> None:
    from pathlib import Path

    img = Path(str(tmp_path)) / "ref.jpg"
    img.write_bytes(b"JPEG")

    def transport(url, payload, timeout):  # noqa: ANN001
        return {"choices": [{"message": {"content": "cinematic wide shot"}}]}

    spec = _make_spec()
    expander = PromptExpander("key", transport=transport)
    result = _apply_multimodal_reverse_engineering(spec, str(img), expander)  # type: ignore[arg-type]

    assert result is not None
    assert result.was_expanded is True
    assert "cinematic" in result.expanded


def test_apply_multimodal_returns_none_when_expansion_fails(tmp_path: object) -> None:
    from pathlib import Path

    img = Path(str(tmp_path)) / "ref.jpg"
    img.write_bytes(b"JPEG")

    # Transport raises → expand_multimodal returns was_expanded=False
    def transport(url, payload, timeout):  # noqa: ANN001
        raise OSError("network down")

    spec = _make_spec()
    expander = PromptExpander("key", transport=transport, sleep=lambda _: None)
    result = _apply_multimodal_reverse_engineering(spec, str(img), expander)  # type: ignore[arg-type]

    assert result is None


def test_apply_multimodal_returns_none_when_no_frames() -> None:
    """For a URL with no watch.py available, _collect_frames returns [] → None."""
    spec = _make_spec()
    expander = PromptExpander("key", transport=MagicMock())

    with patch("gflow_cli.tools.runtime.DEFAULT_CLAUDE_VIDEO_DIR", "/nonexistent"):
        result = _apply_multimodal_reverse_engineering(
            spec,  # type: ignore[arg-type]
            "http://example.com/video.mp4",
            expander,
        )

    assert result is None


def test_apply_multimodal_strips_banned_keywords(tmp_path: object) -> None:
    from pathlib import Path

    img = Path(str(tmp_path)) / "ref.jpg"
    img.write_bytes(b"JPEG")

    def transport(url, payload, timeout):  # noqa: ANN001
        return {"choices": [{"message": {"content": "hyperrealistic cinematic shot"}}]}

    spec = _make_spec(banned=("hyperrealistic",))
    expander = PromptExpander("key", transport=transport)
    result = _apply_multimodal_reverse_engineering(spec, str(img), expander)  # type: ignore[arg-type]

    assert result is not None
    assert "hyperrealistic" not in result.expanded


def test_apply_multimodal_exception_returns_none(tmp_path: object) -> None:
    """An unexpected exception inside _apply_multimodal must return None, not raise."""
    spec = _make_spec()
    expander = MagicMock()
    expander.expand_multimodal.side_effect = RuntimeError("unexpected!")

    from pathlib import Path

    img = Path(str(tmp_path)) / "ref.jpg"
    img.write_bytes(b"JPEG")

    result = _apply_multimodal_reverse_engineering(spec, str(img), expander)  # type: ignore[arg-type]

    assert result is None
