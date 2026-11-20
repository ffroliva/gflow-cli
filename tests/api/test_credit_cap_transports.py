from gflow_cli.api.transports.agent_only_composer import gate_quoted_credits
from gflow_cli.flow_selectors.registry import SELECTORS


def test_credit_quote_selector_uses_measured_custom_element_anchor() -> None:
    selector = next(item for item in SELECTORS if item.key == "migrated.composer.credit_cost")
    assert selector.candidates == (
        ".cdk-overlay-pane flow-prompt-box-settings div.settings-content > "
        "flow-credit-cost-label.settings-credit-cost",
    )


def test_agent_gate_quote_is_last_integer_token() -> None:
    assert gate_quoted_credits("Generate 1 video, costing 4 credits?") == 4
    assert gate_quoted_credits("Generate video") is None
