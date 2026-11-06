from __future__ import annotations

import re
from pathlib import Path

from gflow_cli.telemetry import NOTICE_TEXT, NOTICE_VERSION
from gflow_cli.telemetry_upload import _ENDPOINT

ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "llms.txt",
    ROOT / "DISCLAIMER.md",
    ROOT / "AGENTS.md",
    ROOT / "CHANGELOG.md",
    *sorted((ROOT / "docs").rglob("*.md")),
    *sorted((ROOT / "skills").rglob("*.md")),
    *sorted((ROOT / "website" / "docs").rglob("*.md")),
)
STALE_CLAIMS = re.compile(
    r"\bno telemetry\b|\b(?:we )?don't upload telemetry\b|"
    r"\bdo not upload telemetry\b|\bdoes not collect\b|\bno usage stats\b|"
    r"\bnever phones home\b|\bno phone-home\b|\bnothing leaves the machine\b",
    re.IGNORECASE,
)


def _is_historical(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    # These records describe what was true at the time; changing history would falsify it.
    return relative.startswith("docs/superpowers/") or (
        relative.startswith("docs/") and path.name.startswith("LIVE_VERIFICATION_")
    )


def test_notice_version_matches_docs() -> None:
    notice = (ROOT / "docs" / "TELEMETRY.md").read_text(encoding="utf-8")
    match = re.search(r"^Notice version: (\d+)$", notice, re.MULTILINE)

    assert match is not None
    assert int(match.group(1)) == NOTICE_VERSION


def test_no_doc_says_no_telemetry() -> None:
    failures: list[str] = []
    for path in DOC_PATHS:
        if not path.is_file() or _is_historical(path):
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if path.name == "CHANGELOG.md":
            # Released entries are historical; the Unreleased section describes current behavior.
            start = next(i for i, line in enumerate(lines) if line.strip() == "## [Unreleased]")
            end = next(
                (i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")),
                len(lines),
            )
            lines = lines[start:end]
        for line_number, line in enumerate(lines, 1):
            if STALE_CLAIMS.search(line):
                failures.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")

    assert not failures, "Stale no-telemetry claims:\n" + "\n".join(failures)


def test_notice_copy_matches_the_doc() -> None:
    notice = (ROOT / "docs" / "TELEMETRY.md").read_text(encoding="utf-8")
    fenced_blocks = re.findall(r"```[^\n]*\n(.*?)```", notice, re.DOTALL)

    assert NOTICE_TEXT in [block.rstrip("\n") for block in fenced_blocks]


def test_endpoint_matches_code() -> None:
    notice = (ROOT / "docs" / "TELEMETRY.md").read_text(encoding="utf-8")

    assert _ENDPOINT in notice
