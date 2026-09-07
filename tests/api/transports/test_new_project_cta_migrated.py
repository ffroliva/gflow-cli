"""The migrated gallery DOES have a "+ New project" CTA — it just carries a different ligature.

This file exists as the correction to a mistake, and keeps the measurement that settled it.

#739 shipped a guard asserting that `flow.google.com` "renders no '+ New project' control
gflow can drive", raising `FlowHostMigratedError` when the cascade missed. That was an
**unproven negative**, generalised from a sweep of two OTHER surfaces (the project composer
and the character editor) to a third that was never probed. A live run disproved it in one
click: `_enter_editor` with no project id created a project on the migrated host.

Measured on the gallery afterwards (denon82, control 47 ligature nodes, `$0`):

    add* ligatures      {"mat-icon|add": 1}      <- the CTA carries `add`
    [0] add_2 / <i>     0
    [1] add_2 / i       0
    [2] add_2 / role    0
    [4] has-text('New project')  1               <- the ONLY match

So the CTA is present, and before this fix it survived purely on the **English text
fallback** — the anti-pattern Tier 1 exists to avoid, and one that fails outright on a
non-EN migrated profile. The guard would then have told that user "this host has no such
control, pass --project", which is false and is a dead end.

The fix is a Tier-1 anchor on `add`, class-only so it covers both carriers. These tests pin
that the structural tier — not localised text — is what matches.
"""

from __future__ import annotations

import pytest

from gflow_cli.api.transports.ui_automation import NEW_PROJECT_SELECTORS


class TestNewProjectCtaAnchors:
    def test_tier_one_covers_the_migrated_add_ligature(self) -> None:
        """The measured ligature on the migrated gallery is `add`, not `add_2`."""
        tier1 = [s for s in NEW_PROJECT_SELECTORS if "google-symbols" in s]
        assert any(":text-is('add')" in s for s in tier1), (
            "the migrated gallery CTA carries the `add` ligature (measured 2026-09-07); "
            "without a Tier-1 entry for it the CTA is reachable only by English text"
        )

    def test_the_add_anchors_are_carrier_agnostic(self) -> None:
        """Class-only, so one entry covers labs `<i>` and migrated `<mat-icon>` alike."""
        for sel in NEW_PROJECT_SELECTORS:
            if ":text-is('add')" in sel or ":text-is('add_2')" in sel:
                assert "i.google-symbols" not in sel, (
                    f"{sel!r} is tag-qualified — it cannot match the migrated <mat-icon>"
                )

    def test_a_structural_anchor_precedes_every_localised_text_entry(self) -> None:
        """Tier 1 before Tier 2, or the CTA is found by locale and breaks off-English.

        This is the property the bug violated in practice: every structural entry missed on
        the migrated host, so `button:has-text('New project')` was doing the work.
        """
        first_text = next(
            (i for i, s in enumerate(NEW_PROJECT_SELECTORS) if "has-text(" in s), None
        )
        first_struct = next(
            (i for i, s in enumerate(NEW_PROJECT_SELECTORS) if "google-symbols" in s), None
        )
        assert first_struct is not None, "the cascade must carry a structural tier"
        assert first_text is not None
        assert first_struct < first_text

    @pytest.mark.parametrize("sel", NEW_PROJECT_SELECTORS)
    def test_every_entry_is_a_valid_selector_shape(self, sel: str) -> None:
        """`:has-text()` is invalid inside `:has()` — the repo's documented trap."""
        if ":has(" in sel:
            assert ":has-text(" not in sel
