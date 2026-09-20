"""Live proof that a retired labs tRPC route diagnoses itself honestly (#875).

Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` set. One read-only GET.
**$0** — no generation, no credits, no writes to the account.

Why this test exists as an e2e and not only as a unit test: the unit tests in
``tests/api/test_client_errors.py`` feed ``_raise_for_non_retryable`` a captured
body and assert the remediation. That proves our classifier does what we think.
It cannot prove Flow still answers the way we captured it — which is the whole
premise of this repo. If Flow un-retires the route, changes the wording, or
starts answering 401 instead, the unit tests stay green and the user-facing
diagnosis silently goes wrong again. Only a live call can tell.

No project fixture on purpose. The route is retired *before* any project lookup
happens, so the diagnosis is project-independent and a syntactically valid id is
enough — which is what lets this run on any profile in the nightly canary with
no extra configuration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import WireFormatError

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

#: Syntactically valid, deliberately not a real project. The retired route never
#: gets as far as resolving it (measured 2026-09-20: the 404 is identical for a
#: project the account owns).
_ANY_PROJECT = "00000000-0000-4000-8000-000000000000"

#: What v0.79.0 and earlier told the user, verbatim from ``WireFormatError``'s
#: class default. Each phrase is wrong on this response in its own way.
_PAYLOAD_CLAIM = "Check request payload parameters"
_PROMPT_CLAIM = "simpler prompt"
_BUG_CLAIM = "File a bug"


async def test_a_retired_labs_route_does_not_blame_the_payload_or_a_prompt(
    e2e_profile_dir: Path,
) -> None:
    """``character list`` is a READ. It has no prompt and no payload to simplify.

    Two outcomes are correct and this asserts the invariant across both, which is
    what lets it run on a cohort we do not control and cannot choose:

    * the route answers and characters come back (an account still served the
      labs tRPC API), or
    * the route is retired, and the failure says so **without** telling the user
      to check a payload that is fine, simplify a prompt that does not exist, or
      file a bug for a condition Flow documents in the very body we classified.
    """
    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        try:
            characters = await client.list_characters(_ANY_PROJECT)
        except WireFormatError as exc:
            if "deprecated" not in (exc.detail or "").lower():
                # A different wire failure. Not this test's subject, and asserting
                # on it would make an unrelated outage look like a #875 regression.
                pytest.skip(f"not the retirement refusal: {exc.detail!r}")

            hint = exc.remediation_hint or ""
            assert _PAYLOAD_CLAIM not in hint, f"blames a payload that is fine: {hint}"
            assert _PROMPT_CLAIM not in hint, (
                f"asks to simplify a prompt that does not exist: {hint}"
            )
            assert _BUG_CLAIM not in hint, f"asks for a bug report Flow already answered: {hint}"

            # It must still say something actionable rather than merely stop.
            assert "projectInitialData" in hint, f"does not name the retired route: {hint}"
            assert "639" in hint, f"does not point at the port that would fix it: {hint}"

            # MEASURED 2026-09-20 on denon82, both on `auto` and with the host pinned:
            # GFLOW_CLI_FLOW_HOST=flow.google.com returns the SAME 404, because this
            # read has no migrated arm. Suggesting it would repeat the exact failure
            # #875 fixed, one field over.
            assert "GFLOW_CLI_FLOW_HOST" not in hint, (
                f"suggests a setting measured not to help: {hint}"
            )
        else:
            # A served route is a correct outcome, but it exercised none of #875.
            # Skip rather than pass: the canary runs -m e2e_auth nightly, and a
            # silent green here would let a cohort change retire this test without
            # anyone noticing — the failure mode `skip_on_migrated_host` was written
            # for, in reverse.
            assert isinstance(characters, list)
            pytest.skip("labs tRPC still served for this account — #875 branch not exercised")
