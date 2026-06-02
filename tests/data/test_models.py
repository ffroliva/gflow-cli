"""Unit tests for gflow_cli.data.models enums and dataclasses."""

from __future__ import annotations

import pytest

from gflow_cli.data.models import OperationKind


@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (OperationKind.UPLOAD_IMAGE, "upload_image"),
        (OperationKind.T2I, "t2i"),
        (OperationKind.I2I, "i2i"),
        (OperationKind.T2V, "t2v"),
        (OperationKind.I2V, "i2v"),
        (OperationKind.R2V, "r2v"),
        (OperationKind.SCENE_CREATE, "scene_create"),
        (OperationKind.CHARACTER, "character"),
    ],
)
def test_operation_kind_values(member: OperationKind, expected_value: str) -> None:
    assert member.value == expected_value
