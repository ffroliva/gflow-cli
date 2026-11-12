"""``skip_on_migrated_host`` skips only the two signals of a surface not ported yet."""

from __future__ import annotations

import pytest

from gflow_cli.errors import FlowHostMigratedError, WireFormatError
from tests.e2e.conftest import skip_on_migrated_host


def _raising(exc: Exception):  # noqa: ANN202 - a throwaway coroutine factory
    @skip_on_migrated_host
    async def body() -> None:
        raise exc

    return body


async def test_the_migrated_host_error_skips() -> None:
    with pytest.raises(pytest.skip.Exception):
        await _raising(FlowHostMigratedError(detail="moved"))()


async def test_a_retired_labs_rpc_skips() -> None:
    exc = WireFormatError(detail="Flow RPCs have been deprecated and disabled. Flow has migrated.")
    with pytest.raises(pytest.skip.Exception):
        await _raising(exc)()


async def test_any_other_wire_error_still_fails() -> None:
    with pytest.raises(WireFormatError):
        await _raising(WireFormatError(detail="missing key 'workflows'"))()
