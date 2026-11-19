"""E2E: an account short of credits reports exit 37, never selector drift (23).

Flow does not disable the submit control when an account cannot afford the model — it
**replaces** it. ``arrow_forward`` disappears and a ``prompt-warning-button`` carrying
``aria-label='Insufficient credits warning'`` takes its place, so the anchor's absence
tracks the wallet and not the frontend. Reported as ``UiSelectorDriftError`` this told
users *"Google may have updated their frontend … file a bug"* over a credit shortfall,
which also manufactures frontend-drift reports no code change can fix.

**Short of credits, not necessarily out of them.** Veo tiers cost different amounts, so
the threshold is per-model: measured 2026-09-07, ``ci-probe`` held **50** credits and
still rendered the warning. Flow's own aria-label says *insufficient*, not *none*.

The unit half of this lives in ``tests/api/transports/test_migrated_composer.py`` and
proves the branch. It cannot prove that Flow still renders that warning, or that our
selector still matches it — only a live account whose balance is short for the model
can, which is what this is for::

    GFLOW_CLI_E2E_DRAINED_PROFILE=<profile that cannot afford the model> \\
    GFLOW_CLI_E2E_DRAINED_PROJECT=<project-uuid on that account> \\
        uv run pytest -m e2e tests/e2e/test_insufficient_credits_e2e.py -v

**Cost: zero, and structurally so.** Flow refuses before any submit, so nothing is
ordered and nothing is billed. That is why this carries ``e2e_auth`` rather than
``e2e_video``.

Deliberately gated on its own env vars rather than ``GFLOW_CLI_E2E_PROFILE``: the normal
e2e profile is a FUNDED one (the rest of the suite needs it to be), and pointing this at
a funded account would silently pass by never reaching the branch — a green that proves
nothing, which is the failure mode this whole change exists to remove.
"""

from __future__ import annotations

import os

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.errors import InsufficientCreditsError, UiSelectorDriftError
from gflow_cli.paths import default_home, profile_subdir
from tests.e2e.conftest import until_retired

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_PROFILE_ENV = "GFLOW_CLI_E2E_DRAINED_PROFILE"
_PROJECT_ENV = "GFLOW_CLI_E2E_DRAINED_PROJECT"


def _drained() -> tuple[str, str]:
    profile = os.environ.get(_PROFILE_ENV, "").strip()
    project = os.environ.get(_PROJECT_ENV, "").strip()
    if not profile or not project:
        pytest.skip(
            f"needs {_PROFILE_ENV} and {_PROJECT_ENV} — a Flow account whose balance is "
            "SHORT for the model under test, and a project on it. Short, not "
            "necessarily zero: measured 2026-09-07, an account holding 50 credits still "
            "rendered Flow's insufficient-credits warning, because Veo tiers cost "
            "different amounts. Check with `gflow credits user --profile <name>`; an "
            "account with enough credits makes this test vacuous."
        )
    return profile, project


# #1375: gated, not re-pointed. A cheaper tier (Omni 1.2 at 10 s is about a third of
# veo-quality's 100) would submit and BILL on a part-drained profile such as the 50-credit
# ci-probe above. Re-point only after measuring the costliest tier Flow keeps (see #1379).
@until_retired(VideoModel.VEO_3_1_QUALITY)
async def test_a_drained_account_reports_insufficient_credits_not_drift() -> None:
    profile, project = _drained()

    # `profile_dir`, not `profile` — the ctor takes a Path. Shipped once as
    # `FlowApiClient(profile=...)`, which raised TypeError on the first line the first
    # time anyone ran it (2026-09-07). The test had been reviewed and reasoned about as
    # sound while being incapable of executing: an unrun test is a claim, not a test.
    profile_dir = profile_subdir(default_home(), profile)
    async with FlowApiClient(profile_dir=profile_dir) as client:
        with pytest.raises(InsufficientCreditsError) as caught:
            # Keyword-only, and the parameter is `req` on the client (`request` on the
            # transport). Both spellings were wrong here on first write.
            await client.generate_video(
                req=GenerateVideoRequest(
                    prompt="a teal origami crane on a wooden table, slow push in",
                    mode=Mode.T2V,
                    aspect=Aspect.LANDSCAPE,
                    # #745 item 6: the costliest tier (100 credits), so an account with a
                    # little left is still refused before submit. Unpinned, the default
                    # veo-lite (10) could submit and bill on this "zero-cost" test.
                    model=VideoModel.VEO_3_1_QUALITY,
                ),
                project_id=project,
                download=False,
            )

    # The point of the change, asserted directly rather than implied by the type: a
    # credit shortfall must never be reported as a moved frontend.
    assert not isinstance(caught.value, UiSelectorDriftError)
    assert "credit" in str(caught.value).lower()
