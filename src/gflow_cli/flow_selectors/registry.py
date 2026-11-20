"""The Flow DOM elements gflow depends on, with the context that makes drift readable.

Scope: families with incident history, present on a FRESHLY LOADED editor.
State-gated entries (sidebar close) wait for `Reach` — see spec R4.

`editor` is the labs.google editor, which every account we hold now 308s away from;
`migrated_editor` is flow.google.com's, graded nightly by the canary's e2e (#1083).
"""

from __future__ import annotations

from gflow_cli.api.transports import migrated_composer, mode_control, ui_automation
from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.model import Selector, Surface

SURFACES: dict[str, Surface] = {
    "editor": Surface(
        key="editor",
        url_template="https://labs.google/fx/{locale}/tools/flow/project/{project_id}",
        viewport=(1920, 1080),
    ),
    "migrated_editor": Surface(
        key="migrated_editor",
        url_template="https://flow.google.com/project/{project_id}",
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
    Selector(
        key="migrated.composer.input",
        surface="migrated_editor",
        candidates=(migrated_composer.COMPOSER,),
        note="The prompt box every migrated run types into.",
    ),
    Selector(
        key="migrated.settings_trigger",
        surface="migrated_editor",
        candidates=(migrated_composer.READY_ANCHOR,),
        mode=UiMode.CLASSIC,
        note="ensure_editor's readiness anchor; #749 hid it under agent mode.",
    ),
    Selector(
        key="migrated.agent_chip",
        surface="migrated_editor",
        candidates=(migrated_composer.AGENT_MODE_CHIP_ANY,),
        mode=UiMode.CLASSIC,
        expect_unique=True,
        note=(
            "#749/#799/#1384: the Agent chip (2026-11-19) or switch (2026-11-20). Present on "
            "the classic composer; absent while the agent panel is open, and on the "
            "agent-only composer."
        ),
    ),
    Selector(
        key="migrated.project_title",
        surface="migrated_editor",
        candidates=(migrated_composer.PROJECT_TITLE_INPUT,),
        note="Project create/rename read the saved title back from it (#864, #992).",
    ),
    Selector(
        key="migrated.agent_only.settings",
        surface="migrated_editor",
        candidates=(migrated_composer.AGENT_ONLY_ANCHOR,),
        mode=UiMode.AGENTIC,
        note="#799's agent-only composer: its Agent-settings button.",
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
