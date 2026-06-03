import pytest

from gflow_cli.api.client import _raise_for_non_retryable
from gflow_cli.errors import AuthExpiredError, WafRejectionError


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status


def test_403_maps_to_waf_rejection() -> None:
    with pytest.raises(WafRejectionError):
        _raise_for_non_retryable(_Resp(403), "{}", route="batchGenerateImages")


def test_401_still_maps_to_auth_expired() -> None:
    with pytest.raises(AuthExpiredError):
        _raise_for_non_retryable(_Resp(401), "{}", route="createEntity")
