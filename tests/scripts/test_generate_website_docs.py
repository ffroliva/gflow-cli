"""Offline coverage for the website-docs mirror generator
(scripts/ci/generate_website_docs.py) + the sync it enforces.

Two guarantees: (1) the generator's anonymization map produces PII-clean output
(every FORBIDDEN token from check_website_docs_pii.py is rewritten), and (2) the
committed mirror is actually in sync with what the generator produces — a
regression net for the whole docs/→website/docs pipeline."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _load(name: str) -> object:
    path = _REPO / "scripts" / "ci" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_gen = _load("generate_website_docs")
_pii = _load("check_website_docs_pii")


def test_committed_mirror_is_in_sync() -> None:
    """The same check CI runs — the published mirror must equal a fresh
    regeneration from canonical (no hand-drift)."""
    assert _gen.main(["--check"]) == 0


def test_render_strips_every_forbidden_pii_token() -> None:
    """Anything the PII guard forbids must be rewritten by the generator's map,
    on a synthetic doc packed with every private token."""
    dirty = (
        "Profile `denon82`, user `/home/ffrol/x`, dir `profile_ffroliva`, "
        "email `ffroliva@gmail.com`, name `flavio.oliva`, `Flavio`.\n"
        "Public URL github.com/ffroliva/gflow-cli must survive.\n"
    )
    out = _gen.render("SYNTHETIC.md", dirty)
    assert not _pii.find_pii(out), f"generator left PII: {_pii.find_pii(out)}"
    # Placeholders present; public handle preserved.
    assert "my-profile" in out and "your-user" in out and "your.name" in out
    assert "github.com/ffroliva/gflow-cli" in out  # public reference untouched


def test_security_reporting_row_is_semantically_rewritten() -> None:
    """SECURITY's maintainer-email reporting row becomes GitHub private
    reporting — not a placeholder email (a token swap would be wrong). Feeds the
    override's exact canonical literal, which also proves the override target
    still exists in the map (the generator raises if canonical drifts from it)."""
    # Feed ALL of SECURITY's canonical override rows (render fails loud if any
    # override target is absent — a feature that catches canonical drift).
    canonical = "\n".join(old for old, _new in _gen.FILE_OVERRIDES["SECURITY.md"]) + "\n"
    out = _gen.render("SECURITY.md", canonical)
    assert "private vulnerability reporting" in out
    assert "gmail.com" not in out  # the email is GONE, not placeholdered
    assert "@" not in out


def test_bespoke_pages_are_never_generated() -> None:
    """The site's distinct landing/onboarding pages have no canonical source."""
    for name in ("index.md", "agents.md", "installation.md", "onboarding.md"):
        assert _gen._source_for(name) is None  # noqa: SLF001
