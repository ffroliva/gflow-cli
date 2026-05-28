"""Regression tests for documentation as a required merge gate."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_runs_markdown_link_check() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "scripts/ci/check_doc_links.py" in workflow


def test_impeccable_routine_includes_documentation_gate() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "uv run python scripts/ci/check_doc_links.py" in agents
    assert "Documentation is a first-class deliverable" in agents


def test_agent_guide_documents_production_ready_checklist() -> None:
    guide = (ROOT / "docs/AGENT_GUIDE.md").read_text(encoding="utf-8")

    assert "## Production-ready checklist" in guide
    assert "Memory is updated" in guide


def test_pull_request_template_requires_documentation_review() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")

    assert "Documentation updated or explicitly marked not applicable" in template
    assert "`uv run python scripts/ci/check_doc_links.py`" in template
