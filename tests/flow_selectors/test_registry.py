from __future__ import annotations

from gflow_cli.flow_selectors import registry


def test_every_selector_points_at_a_declared_surface() -> None:
    for sel in registry.SELECTORS:
        assert sel.surface in registry.SURFACES


def test_keys_are_unique() -> None:
    keys = [s.key for s in registry.SELECTORS]
    assert len(keys) == len(set(keys))


def test_state_gated_families_are_deliberately_absent() -> None:
    """Two incident families CANNOT live on a URL-only surface:

    - sidebar close needs the sidebar EXPANDED
    - #404's count tabs sit inside the generation-settings panel, which must be
      clicked open (`_open_gen_settings_panel`; `_is_settings_panel_open` exists
      because it is normally closed)

    Registering either grades MISS on every clean capture. That #404 — the
    incident this design leans on hardest — needs `Reach` is the argument FOR
    prioritising Reach, not for registering a selector that reds nightly.
    """
    keys = {s.key for s in registry.SELECTORS}
    assert "editor.sidebar.close" not in keys
    assert "editor.count_tabs" not in keys


def test_incident_families_are_registered() -> None:
    keys = {s.key for s in registry.SELECTORS}
    assert keys >= {
        "editor.composer.input",
        "editor.composer.submit",
        "editor.agent_toggle",
        "editor.crop_control",
    }


def test_for_surface_returns_every_entry_including_mode_scoped_ones() -> None:
    """Mode belongs to grading, not selection — a mode-scoped entry must still
    reach the report as EXPECTED_ABSENT rather than vanishing from it."""
    keys = {s.key for s in registry.for_surface("editor")}
    assert "editor.crop_control" in keys
