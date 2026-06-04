"""Dev-scoped ``FlowApiClient`` subclass that records the browser context.

NOT imported by the ``gflow_cli`` package. It adds Playwright video recording to
the client's persistent context purely via the core
``FlowApiClient._persistent_context_kwargs()`` seam, so no recording concern
lives in core. Used only by ``scripts/dev`` recorders and their tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gflow_cli.api.client import FlowApiClient  # noqa: E402

# Match the base persistent context viewport (client.py) so the recorded frame
# is 1:1 with what the automation sees.
_VIDEO_SIZE = {"width": 1280, "height": 720}


class RecordingFlowApiClient(FlowApiClient):
    """``FlowApiClient`` that records its browser context to ``record_video_dir``.

    Playwright finalizes one ``.webm`` per context page when the context closes.
    Keep ``Settings.concurrency == 1`` (the default) so exactly one video — the
    slot-0 editor page — is produced.
    """

    def __init__(self, *args: Any, record_video_dir: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._record_video_dir = record_video_dir

    def _persistent_context_kwargs(self) -> dict[str, Any]:
        kwargs = super()._persistent_context_kwargs()
        kwargs["record_video_dir"] = str(self._record_video_dir)
        kwargs["record_video_size"] = dict(_VIDEO_SIZE)
        return kwargs
