"""Pure tests for video value objects."""

from __future__ import annotations

import pytest

from gflow_cli.api.video import Aspect


class TestAspectEnum:
    def test_portrait_wire_value(self) -> None:
        assert Aspect.PORTRAIT.wire() == "VIDEO_ASPECT_RATIO_PORTRAIT"

    def test_landscape_wire_value(self) -> None:
        assert Aspect.LANDSCAPE.wire() == "VIDEO_ASPECT_RATIO_LANDSCAPE"

    def test_square_wire_value(self) -> None:
        assert Aspect.SQUARE.wire() == "VIDEO_ASPECT_RATIO_SQUARE"

    def test_from_cli_value(self) -> None:
        assert Aspect.from_cli("9:16") == Aspect.PORTRAIT
        assert Aspect.from_cli("16:9") == Aspect.LANDSCAPE
        assert Aspect.from_cli("1:1") == Aspect.SQUARE

    def test_from_cli_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="3:2"):
            Aspect.from_cli("3:2")
