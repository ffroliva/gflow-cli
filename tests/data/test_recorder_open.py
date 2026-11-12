"""``OperationRecorder.open`` stays a classmethod on the final class (#1249 wave 2).

Five CLI tests patch ``OperationRecorder.open`` through their import sites, so it must be
defined on ``OperationRecorder`` itself, not inherited from one of its parts.
"""

from __future__ import annotations

import inspect

from gflow_cli.data.recorder import OperationRecorder


def test_open_is_a_classmethod_defined_on_operation_recorder() -> None:
    assert isinstance(inspect.getattr_static(OperationRecorder, "open"), classmethod)
    assert "open" in vars(OperationRecorder)
