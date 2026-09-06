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


# The e2e-evidence rule is stated in three places that must not drift apart:
# the PR template asks for it, AGENTS.md states it for agents, CONTRIBUTING.md
# for humans. A silently deleted line here is how the rule dies — PR #671
# arrived with eight offline test files and zero e2e because nothing asked.
def test_pull_request_template_requires_e2e_evidence() -> None:
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")

    assert "**E2E evidence provided**" in template
    assert "`tests/e2e/`" in template


def test_e2e_evidence_rule_is_stated_for_agents_and_contributors() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    # Scoped to Flow surfaces — a docs-only change cannot produce an e2e run.
    assert "required for every behavior change that touches a Flow surface" in agents
    # live-verify is a narrative ledger, not a re-runnable regression; the two
    # are complementary and the docs must keep saying so.
    assert "never runs `pytest -m e2e`" in agents
    assert "**E2E is required, not optional.**" in contributing
    assert "E2E is the evidence layer." in contributing


# `gflow video batch` was removed as a nonfunctional stub (production-readiness
# hardening, task A1). These are the operator-facing docs that must not tell
# a user to run a command that no longer exists. Historical text (CHANGELOG
# entries for past releases, docs/LIVE_VERIFICATION_*.md, docs/superpowers/
# specs and plans) is deliberately excluded — it records what happened, not
# what to do today.
CURRENT_OPERATOR_DOCS: tuple[str, ...] = (
    "README.md",
    "AGENTS.md",
    # llms.txt is ingested verbatim by LLMs, which makes a stale claim in it more
    # costly than in any prose doc — yet it sat outside this gate for 22 releases
    # while still advertising `gflow video batch` as "queued for Phase B". Found
    # by the v0.63.0 doc-review council; the one file the gate most needed.
    "llms.txt",
    "docs/USAGE.md",
    "docs/USER_GUIDE.md",
    "docs/CONFIGURATION.md",
    ".env.template",
    "skills/gflow-cli/SKILL.md",
    "KNOWN_ISSUES.md",
    "docs/INDEX.md",
    "docs/ARCHITECTURE.md",
    "docs/AUTHENTICATION.md",
    "docs/PROJECT_STATUS.md",
    "website/docs/USAGE.md",
    "website/docs/ARCHITECTURE.md",
    "website/docs/AUTHENTICATION.md",
    "website/docs/CONFIGURATION.md",
    "website/docs/KNOWN_ISSUES.md",
    "website/docs/USER_GUIDE.md",
)


def test_current_docs_do_not_instruct_video_batch() -> None:
    for rel_path in CURRENT_OPERATOR_DOCS:
        text = (ROOT / rel_path).read_text(encoding="utf-8")

        assert "gflow video batch" not in text, f"{rel_path} still references gflow video batch"


def test_changelog_unreleased_notes_video_batch_removal() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # The video batch removal shipped in v0.41.0; check the release section.
    start = changelog.index("## [0.41.0]")
    end = changelog.index("\n## [", start + 1)
    section = changelog[start:end].lower()

    assert "video batch" in section
    assert "removed" in section


# Production-readiness hardening (task F1, design spec §9): headed real Chrome
# is the production default for UI automation, not headless. Stale docs
# claimed headless was the default and that flipping to headed was merely an
# occasional reCAPTCHA workaround; `config.py`'s `headless` field defaults to
# `False`. CONFIGURATION.md is the canonical reference doc and must state
# both facts plainly.
def test_current_docs_describe_headed_default_and_waf_safe_mode() -> None:
    text = (ROOT / "docs/CONFIGURATION.md").read_text(encoding="utf-8")
    assert "GFLOW_CLI_HEADLESS=false" in text
    assert "headed" in text.lower()
    assert "**Default:** `false`" in text
