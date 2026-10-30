"""The vendored pytest_speed helpers stay byte-identical to the pinned helper commit."""

import importlib.util
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "pytest_speed"


def _tree_hash(package: Path) -> str:
    spec = importlib.util.spec_from_file_location("_vendored_vendor", package / "vendor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tree_hash(package)


def test_vendored_copy_matches_its_version():
    fields = dict(
        line.split("=", 1) for line in (PACKAGE / "VERSION").read_text().splitlines() if line
    )
    assert re.fullmatch(r"[0-9a-f]{40}", fields["commit"])
    assert fields["sha256"] == _tree_hash(PACKAGE)
