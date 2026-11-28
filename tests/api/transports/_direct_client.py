"""Drive ``DirectHttpTransport`` the way the retry loop does, for transport-level tests.

The transport never runs the UI path itself (Task 13): it raises ``DirectAttemptEnded``.
For one attempt with no relaunch, the loop (``api/_flow_retry.py``) runs the UI fallback
method for ``NotSent`` and raises the attempt's typed error otherwise. These helpers apply
that rule, so a transport test keeps asserting the observable result of one attempt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import ImageRef
from gflow_cli.api.submit_policy import DirectAttemptEnded, NotSent
from gflow_cli.api.transports.direct_http import DirectHttpTransport


def _past_the_send(ended: DirectAttemptEnded) -> BaseException:
    assert ended.error is not None, f"an ended attempt past the send carries its error: {ended}"
    return ended.error


async def generate_like_the_client(
    transport: DirectHttpTransport, **kwargs: Any
) -> list[GeneratedImage]:
    try:
        return await transport.generate_images(**kwargs)
    except DirectAttemptEnded as ended:
        if not isinstance(ended.outcome, NotSent):
            raise _past_the_send(ended) from ended
    return await transport.generate_images_ui(**kwargs)


async def upload_like_the_client(
    transport: DirectHttpTransport, *, project_id: str, path: Path
) -> ImageRef | None:
    try:
        return await transport.upload_reference(project_id=project_id, path=path)
    except DirectAttemptEnded as ended:
        if not isinstance(ended.outcome, NotSent):
            raise _past_the_send(ended) from ended
    return await transport.upload_reference_ui(project_id=project_id, path=path)
