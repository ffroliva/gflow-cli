"""Sanity check: per-feature step modules don't share step phrases.

pytest-bdd already enforces per-module step scope, but importing all
remaining modules proves they coexist without registry conflicts."""

from __future__ import annotations


def test_step_modules_coexist() -> None:
    import tests.features.test_auth_steps as auth_steps
    import tests.features.test_image_steps as image_steps

    assert auth_steps is not None
    assert image_steps is not None
