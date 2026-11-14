"""A lost transfer must reach an MCP agent as advice, not as a hash (#895).

Both MCP twins of the download flatten this failure *worse* than the CLI does, and for
different reasons — so they are two surfaces and get two tests:

* ``gflow_download_media`` runs through ``_guarded``, whose ``except Exception`` backstop
  replaces an untyped error with *"Unexpected Error; details were logged server-side"* and
  **no remediation key at all**;
* ``gflow_generate_video`` runs through the worker queue, where ``daemon.py``'s non-GFlowError
  branch replaces the message with ``detail: "sha256:<hash>"`` and emits neither
  ``remediation_hint`` nor ``retryable``.

An agent cannot read a ``--help`` it was never given. A hash tells it nothing, so it either
gives up on a clip the user has already paid for, or re-generates and bills again. Typing
the error at the transport (see ``transports/_common.get_signed_media``) is what makes both
envelopes carry the recovery instead.
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Error as PwError

from gflow_cli.errors import NetworkError

MEDIA_ID = "9ad33c78-5762-4cbc-bcbe-07a4c3b061c7"


def _transport_failure() -> NetworkError:
    """The error `get_signed_media` now raises when a transfer dies for good."""
    from gflow_cli.api.transports._common import retry_the_recovery_hint

    return NetworkError(
        detail=(
            f"the signed media URL for {MEDIA_ID} dropped the connection on all 3 attempts (Error)"
        ),
        remediation_hint=retry_the_recovery_hint(MEDIA_ID),
        route="flow-content.google",
    )


class TestDirectToolEnvelope:
    """`gflow_download_media` — the `_guarded` surface."""

    def test_a_typed_failure_carries_its_remediation(self) -> None:
        from gflow_cli.mcp.tools import _gflow_error_dict  # pyright: ignore[reportPrivateUsage]

        payload = _gflow_error_dict(_transport_failure())

        assert payload["retryable"] is True
        assert MEDIA_ID in payload["remediation_hint"]
        assert MEDIA_ID in payload["message"]

    def test_it_leaks_no_signed_url_credentials(self) -> None:
        """`detail` reaches the agent verbatim — `_gflow_error_dict` does not redact, and
        neither does the CLI's stderr path. The transport is what must keep the URL out."""
        from gflow_cli.mcp.tools import _gflow_error_dict  # pyright: ignore[reportPrivateUsage]

        rendered = " ".join(str(v) for v in _gflow_error_dict(_transport_failure()).values())
        assert "Signature=" not in rendered
        assert "Expires=" not in rendered

    def test_an_untyped_failure_would_reach_the_agent_as_nothing(self) -> None:
        """The control, and the reason this issue exists: left untyped, the same failure
        is masked to a sentence with no media id and no remediation key. If this ever
        starts carrying advice, the masking changed and the test above proves less."""
        from gflow_cli.mcp.tools import (
            _masked_unexpected_dict,  # pyright: ignore[reportPrivateUsage]
        )

        masked = _masked_unexpected_dict(PwError("apiRequest.get: read ECONNRESET"))

        assert "remediation_hint" not in masked
        assert MEDIA_ID not in str(masked)


class TestQueuedTaskEnvelope:
    """`gflow_generate_video` — the daemon surface, which is *different code*."""

    @staticmethod
    def _daemon_payload(exc: BaseException) -> dict[str, Any]:
        """The branch `worker/daemon.py` takes for a failed task, in isolation.

        Mirrors `daemon.py`'s own dispatch rather than booting a daemon: the decision
        under test is only *which* branch a transport failure lands in.
        """
        from gflow_cli.data.redaction import redact_error_detail
        from gflow_cli.errors import EXIT_CODE_MAP, GFlowError, is_retryable

        if isinstance(exc, GFlowError):
            payload = dict(exc.to_problem_details())
            payload["exit_code"] = next(
                (code for cls, code in EXIT_CODE_MAP.items() if isinstance(exc, cls)), 1
            )
            payload["retryable"] = is_retryable(exc)
            if "detail" in payload:
                payload["detail"] = redact_error_detail(str(payload["detail"]))
            return payload
        return {"title": "Unknown Error", "detail": "sha256:...", "exit_code": 1}

    def test_a_typed_failure_survives_the_queue(self) -> None:
        payload = self._daemon_payload(_transport_failure())

        assert payload["exit_code"] == 6
        assert payload["retryable"] is True
        assert MEDIA_ID in payload["remediation_hint"]
        assert not str(payload["detail"]).startswith("sha256:")

    def test_an_untyped_failure_reaches_the_agent_as_a_hash(self) -> None:
        payload = self._daemon_payload(PwError("apiRequest.get: read ECONNRESET"))

        assert str(payload["detail"]).startswith("sha256:")
        assert "remediation_hint" not in payload
        assert "retryable" not in payload


class TestToolDocstringIsTrue:
    def test_it_warns_against_an_agent_stacking_its_own_retry_loop(self, mcp_server: Any) -> None:
        """With `retryable: true` an agent will call again. Immediately re-calling opens a
        second browser under the per-profile lease and fails on *that* instead — a worse
        error than the one it is retrying. The description is the only place an agent can
        learn this, so its absence is a real defect, not a doc nit."""
        # The agent sees the `description=` passed to `@server.tool`, NOT the Python
        # docstring — reading `__doc__` here would have asserted against text no client
        # ever receives, and passed while the real description said nothing.
        registered = mcp_server._tool_manager._tools["gflow_download_media"]  # pyright: ignore[reportPrivateUsage]
        description = (registered.description or "").lower()

        assert "retries" in description, "agents are not told the transfer self-heals"
        assert "lease" in description, "agents are not warned that an immediate re-call fails"
