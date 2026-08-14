# SPDX-License-Identifier: MIT
"""#501: the known-issues resource must be bounded.

KNOWN_ISSUES.md is ~70 KB and grows every release; the old resource returned
it whole on every read — ~68 KB of context injected per client fetch. The
default read is now a small index (titles + status), and one parameterized
fetch returns a single section's full text.
"""

from __future__ import annotations

import pytest

_SAMPLE = """# Known issues

Intro prose.

## Open

### First issue title here

- **Status:** **Open** — waiting on something
- **Severity:** High

Body of the first issue. UNIQUE-BODY-MARKER-ONE.

### Second issue

- **Status:** Mitigated

Second body. UNIQUE-BODY-MARKER-TWO.

## Resolved

### Old fixed thing

- **Status:** **RESOLVED in v0.1.0**

Ancient body text.
"""


def test_index_lists_titles_and_slugs_without_bodies() -> None:
    from gflow_cli.mcp.resources import _build_known_issues_index

    index = _build_known_issues_index(_SAMPLE)
    assert "First issue title here" in index
    assert "first-issue-title-here" in index
    assert "UNIQUE-BODY-MARKER-ONE" not in index
    assert "UNIQUE-BODY-MARKER-TWO" not in index


def test_section_fetch_returns_full_body() -> None:
    from gflow_cli.mcp.resources import _extract_known_issue_section

    text = _extract_known_issue_section(_SAMPLE, "first-issue-title-here")
    assert text is not None
    assert "UNIQUE-BODY-MARKER-ONE" in text
    assert "UNIQUE-BODY-MARKER-TWO" not in text


def test_section_fetch_unknown_slug_returns_none() -> None:
    from gflow_cli.mcp.resources import _extract_known_issue_section

    assert _extract_known_issue_section(_SAMPLE, "nope") is None


def test_oversized_section_is_capped() -> None:
    from gflow_cli.mcp.resources import _SECTION_CAP_BYTES, _cap_section

    capped = _cap_section("x" * (_SECTION_CAP_BYTES + 1000))
    assert len(capped.encode()) <= _SECTION_CAP_BYTES + 200
    assert "truncated" in capped


@pytest.mark.asyncio
async def test_live_resource_reads_are_bounded() -> None:
    from gflow_cli.mcp.resources import (
        _iter_issue_sections,
        _known_issues_text,
        known_issues,
        known_issues_section,
    )

    # The real file must parse into sections at all — if the heading style
    # ever drifts from '### ', the resource would silently serve an empty
    # index (post-merge review: the old slug extraction grabbed the '<slug>'
    # placeholder and passed vacuously through the unknown-slug path).
    text = _known_issues_text()
    assert text is not None
    sections = _iter_issue_sections(text)
    assert len(sections) > 0, "#501: real KNOWN_ISSUES.md yielded no sections"

    index = await known_issues()
    # 16 KB: the index grows a line per issue; 8 KB had ~1 KB headroom and
    # would start failing docs-only commits within a few releases.
    assert len(index.encode()) < 16 * 1024, "#501: index read must stay small"
    assert "gflow://docs/known-issues/" in index  # tells the agent how to drill in

    # Drill into a REAL slug (from the parsed sections, not the index header's
    # '<slug>' placeholder) and require actual section content back.
    slug = sections[0][0]
    assert f"`{slug}`" in index
    section = await known_issues_section(slug)
    assert section is not None and section.startswith("### ")
    assert "Unknown section slug" not in section
