import pytest

from gflow_cli.errors import EXIT_CODE_MAP, AisandboxAuthError, AuthExpiredError


def _exit_code_for(err: Exception) -> int:
    """Mirror cli.py's EXIT_CODE_MAP isinstance walk."""
    return next(
        (code for cls, code in EXIT_CODE_MAP.items() if isinstance(err, cls)),
        1,
    )


@pytest.mark.unit
def test_aisandbox_auth_error_is_distinct_but_inherits_exit_code_3():
    err = AisandboxAuthError("create_scene returned 401 after SAPISID refresh")
    # Distinct, catchable class
    assert isinstance(err, AisandboxAuthError)
    assert issubclass(AisandboxAuthError, AuthExpiredError)
    # Inherits AuthExpiredError's exit code (3) via the isinstance walk
    assert _exit_code_for(err) == 3
    # Has its own remediation, not the generic one
    assert "SAPISID" in err.remediation_hint
    # No standalone EXIT_CODE_MAP entry needed (inherits parent's)
    assert AisandboxAuthError not in EXIT_CODE_MAP
