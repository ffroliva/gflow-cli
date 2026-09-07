"""E2E: a drained account reports exit 37, never selector drift (23).

Flow does not disable the submit control when an account is out of Veo credits — it
**replaces** it. ``arrow_forward`` disappears and a ``prompt-warning-button`` carrying
``aria-label='Insufficient credits warning'`` takes its place, so the anchor's absence
tracks the wallet and not the frontend. Reported as ``UiSelectorDriftError`` this told
users *"Google may have updated their frontend … file a bug"* for an empty wallet, which
also manufactures frontend-drift reports no code change can fix.

The unit half of this lives in ``tests/api/transports/test_migrated_composer.py`` and
proves the branch. It cannot prove that Flow still renders that warning, or that our
selector still matches it — only a drained live account can, which is what this is for::

    GFLOW_CLI_E2E_DRAINED_PROFILE=<profile with 0 Veo credits> \\
    GFLOW_CLI_E2E_DRAINED_PROJECT=<project-uuid on that account> \\
        uv run pytest -m e2e tests/e2e/test_insufficient_credits_e2e.py -v

**Cost: zero, and structurally so.** The account has no credits to spend; the run stops
before any submit. That is also why it carries ``e2e_auth`` rather than ``e2e_video``.

Deliberately gated on its own env vars rather than ``GFLOW_CLI_E2E_PROFILE``: the normal
e2e profile is a FUNDED one (the rest of the suite needs it to be), and pointing this at
a funded account would silently pass by never reaching the branch — a green that proves
nothing, which is the failure mode this whole change exists to remove.
"""

from __future__ import annotations

import os

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode
from gflow_cli.errors import InsufficientCreditsError, UiSelectorDriftError

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_PROFILE_ENV = "GFLOW_CLI_E2E_DRAINED_PROFILE"
_PROJECT_ENV = "GFLOW_CLI_E2E_DRAINED_PROJECT"


def _drained() -> tuple[str, str]:
    profile = os.environ.get(_PROFILE_ENV, "").strip()
    project = os.environ.get(_PROJECT_ENV, "").strip()
    if not profile or not project:
        pytest.skip(
            f"needs {_PROFILE_ENV} and {_PROJECT_ENV} — a Flow account with ZERO Veo "
            "credits and a project on it. Verify with `gflow credits user --profile "
            "<name>` before running; a funded account makes this test vacuous."
        )
    return profile, project


async def test_a_drained_account_reports_insufficient_credits_not_drift() -> None:
    profile, project = _drained()

    async with FlowApiClient(profile=profile) as client:
        with pytest.raises(InsufficientCreditsError) as caught:
            await client.generate_video(
                GenerateVideoRequest(
                    prompt="a teal origami crane on a wooden table, slow push in",
                    mode=Mode.T2V,
                    aspect=Aspect.LANDSCAPE,
                ),
                project_id=project,
            )

    # The point of the change, asserted directly rather than implied by the type: a
    # drained wallet must never be reported as a moved frontend.
    assert not isinstance(caught.value, UiSelectorDriftError)
    assert "credit" in str(caught.value).lower()
