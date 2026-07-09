#!/usr/bin/env python3
"""Verdict-match eval for the deterministic PR-triage gate — must be 100%.

Runs ``should_review()`` over ``eval/pr_triage_fixtures.json`` and asserts
every verdict matches its pinned expectation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "autopilot"))

from pr_triage_gate import should_review  # noqa: E402


def main() -> int:
    fixtures = json.loads((ROOT / "eval" / "pr_triage_fixtures.json").read_text(encoding="utf-8"))
    cases = fixtures["cases"]
    failures: list[tuple[str, str, str]] = []
    for case in cases:
        result = should_review(case["pr"])
        got, want = result["verdict"], case["expected"]
        ok = got == want
        print(f"[{'ok' if ok else 'MISMATCH'}] {case['name']}: want={want} got={got}")
        if not ok:
            failures.append((case["name"], want, got))
            print(f"        reasons: {result['reasons']}")
    passed = len(cases) - len(failures)
    print(f"\n{passed}/{len(cases)} verdicts match.")
    if failures:
        print(f"FAIL: {len(failures)} mismatch(es): {failures}", file=sys.stderr)
        return 1
    print("PASS: deterministic gate matches all pinned verdicts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
