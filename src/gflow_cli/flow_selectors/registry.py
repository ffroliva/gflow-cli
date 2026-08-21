"""The Flow DOM elements gflow depends on, with the context that makes drift readable.

Scope: families with incident history, present on a FRESHLY LOADED editor.
State-gated entries (sidebar close) wait for `Reach` — see spec R4.
"""

from __future__ import annotations

from gflow_cli.api.transports import mode_control, ui_automation
from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.model import Selector, Surface

SURFACES: dict[str, Surface] = {
    "editor": Surface(
        key="editor",
        url_template="https://labs.google/fx/{locale}/tools/flow/project/{project_id}",
        viewport=(1920, 1080),
    ),
}

SELECTORS: tuple[Selector, ...] = (
    Selector(
        key="editor.composer.input",
        surface="editor",
        candidates=tuple(ui_automation.PROMPT_INPUT_SELECTORS),
        note="#493 — the expanded chat sidebar hid this entirely.",
    ),
    Selector(
        key="editor.composer.submit",
        surface="editor",
        candidates=tuple(ui_automation.SUBMIT_BUTTON_SELECTORS),
        note="NOT expect_unique: scripts/dev/capture_i2v_post_bind_state.py "
        "exists because this can legitimately match a top-level submit, an "
        "in-panel submit and a Send-to-Agent submit at once.",
    ),
    Selector(
        key="editor.agent_toggle",
        surface="editor",
        candidates=(mode_control.AGENT_TOGGLE_SELECTOR,),
        expect_unique=True,
        note="#313 — agent settings panel became sticky.",
    ),
    Selector(
        key="editor.crop_control",
        surface="editor",
        candidates=tuple(mode_control.CROP_SELECTORS),
        mode=UiMode.CLASSIC,
        note="Classic-mode INDICATOR (factory.py:116). Absent on the agentic arm "
        "by design — grading that MISS as drift is the error this registry exists "
        "to prevent — which is why observed_mode is a REQUIRED grader argument.",
    ),
)


def for_surface(surface_key: str) -> tuple[Selector, ...]:
    """Every entry on a surface, in registry order.

    Deliberately does NOT filter by mode. Mode belongs to grading, not
    selection: a mode-scoped entry absent on the other arm must still appear in
    the report as EXPECTED_ABSENT, or the report silently shrinks and nobody
    notices coverage was skipped.
    """
    return tuple(s for s in SELECTORS if s.surface == surface_key)
