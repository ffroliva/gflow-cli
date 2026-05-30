#!/usr/bin/env python3
"""
SkillOpt mock harness for gflow-cli skills.

Runs a scored task suite against a skill document to measure how well
agents guided by that skill answer gflow-cli usage questions.
Mimics the SkillOpt rollout→score loop without the automated edit step,
making it easy to measure baseline accuracy before / after manual edits.

Usage:
    # Dry-run: print formatted prompts without calling the API
    python scripts/dev/skillopt/harness.py --dry-run

    # Live run against Claude (requires ANTHROPIC_API_KEY)
    ANTHROPIC_API_KEY=sk-ant-... python scripts/dev/skillopt/harness.py

    # Point at a different skill version
    python scripts/dev/skillopt/harness.py --skill skills/gflow-cli/SKILL.md

    # Filter by tag
    python scripts/dev/skillopt/harness.py --tags auth,video --dry-run

    # Use a specific model
    python scripts/dev/skillopt/harness.py --model claude-haiku-4-5-20251001
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_PATH = Path(__file__).parent / "tasks.json"
DEFAULT_SKILL_PATH = REPO_ROOT / "skills" / "gflow-cli" / "SKILL.md"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_TEMPLATE = """\
You are an agent assistant helping users operate gflow-cli, the unofficial terminal CLI for Google Flow (Veo video generation and Imagen image generation).

Your job is to answer the user's question with the exact CLI command(s) or code they should run.
- Output only the command(s) / code snippet — no markdown fences unless showing multi-line code.
- If the task asks a yes/no or remediation question, give a concise direct answer followed by the command.
- Do not add lengthy explanations unless the task is specifically about concepts.

Use the skill reference below as your authoritative source.

=== SKILL REFERENCE (gflow-cli v{version}, epoch {epoch}) ===
{skill_body}
=== END SKILL REFERENCE ==="""


def load_tasks(path: Path, tags: list[str] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = json.loads(path.read_text())
    if tags:
        tag_set = set(tags)
        items = [t for t in items if tag_set.intersection(t.get("tags", []))]
    return items


def load_skill(path: Path) -> tuple[str, str, int]:
    """Return (full_text, version, epoch) from the skill file."""
    text = path.read_text()
    version = "unknown"
    epoch = 0
    for line in text.splitlines():
        if line.startswith("version:"):
            version = line.split(":", 1)[1].strip().strip('"')
        elif line.startswith("skillopt_epoch:"):
            try:
                epoch = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return text, version, epoch


def score_response(response: str, expected: dict[str, Any]) -> tuple[float, list[str]]:
    """Score a response. Returns (score 0.0–1.0, list of reason strings)."""
    resp_lower = response.lower()
    reasons: list[str] = []

    must_include: list[str] = expected.get("must_include", [])
    must_not_include: list[str] = expected.get("must_not_include", [])
    partial_credit: list[str] = expected.get("partial_credit", [])

    hits = sum(1 for item in must_include if item.lower() in resp_lower)
    misses = [item for item in must_include if item.lower() not in resp_lower]
    base = hits / len(must_include) if must_include else 1.0

    forbidden_hits = [p for p in must_not_include if p.lower() in resp_lower]
    penalty = len(forbidden_hits) * 0.3

    bonus_hits = [p for p in partial_credit if p.lower() in resp_lower]
    bonus = min(len(bonus_hits) * 0.1, 0.3)

    score = max(0.0, min(1.0, base - penalty + bonus))

    if misses:
        reasons.append(f"MISS: {misses}")
    if forbidden_hits:
        reasons.append(f"FORBIDDEN hit: {forbidden_hits}")
    if bonus_hits:
        reasons.append(f"BONUS: {bonus_hits}")

    return score, reasons


def format_prompt(skill_text: str, version: str, epoch: int, task: dict[str, Any]) -> tuple[str, str]:
    system = SYSTEM_TEMPLATE.format(
        version=version, epoch=epoch, skill_body=skill_text
    )
    question = task["question"]
    context = task.get("context")
    user = f"Context: {context}\n\n{question}" if context else question
    return system, user


def run_dry(tasks: list[dict[str, Any]], skill_text: str, version: str, epoch: int) -> None:
    print(f"=== DRY RUN — {len(tasks)} task(s) — skill v{version} epoch {epoch} ===\n")
    for task in tasks:
        system, user = format_prompt(skill_text, version, epoch, task)
        print(f"--- [{task['id']}] {task['question'][:80]} ---")
        print(f"USER PROMPT:\n{user}\n")
        print(f"SCORING: {task['expected']}\n")
        print(f"NOTES: {task.get('notes', '')}\n")
        print()


def run_live(
    tasks: list[dict[str, Any]],
    skill_text: str,
    version: str,
    epoch: int,
    model: str,
    api_key: str,
) -> None:
    try:
        import anthropic
    except ImportError:
        sys.exit(
            "anthropic package not found. Install it:\n"
            "  uv pip install anthropic\n"
            "or:\n"
            "  pip install anthropic"
        )

    client = anthropic.Anthropic(api_key=api_key)
    results: list[dict[str, Any]] = []

    print(f"=== LIVE RUN — {len(tasks)} task(s) — skill v{version} epoch {epoch} — model {model} ===\n")

    for i, task in enumerate(tasks, 1):
        system, user = format_prompt(skill_text, version, epoch, task)
        print(f"[{i}/{len(tasks)}] {task['id']}: {task['question'][:70]}...")

        message = client.messages.create(
            model=model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        response = message.content[0].text.strip()
        score, reasons = score_response(response, task["expected"])

        status = "PASS" if score >= 0.8 else ("PARTIAL" if score >= 0.4 else "FAIL")
        print(f"  Score: {score:.2f} [{status}]  {' | '.join(reasons) if reasons else 'OK'}")
        print(f"  Response: {response[:120].replace(chr(10), ' ')}")

        results.append(
            {
                "id": task["id"],
                "tags": task.get("tags", []),
                "score": score,
                "status": status,
                "response_preview": response[:200],
                "reasons": reasons,
            }
        )

    _print_summary(results)


def _print_summary(results: list[dict[str, Any]]) -> None:
    total = len(results)
    if total == 0:
        print("\nNo results.")
        return

    avg = sum(r["score"] for r in results) / total
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = [r for r in results if r["status"] == "FAIL"]

    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed}/{total} passed  avg score {avg:.3f}")

    if failed:
        print(f"\nFailed tasks ({len(failed)}):")
        for r in failed:
            print(f"  {r['id']}: {r['reasons']}")

    tag_scores: dict[str, list[float]] = {}
    for r in results:
        for tag in r.get("tags", []):
            tag_scores.setdefault(tag, []).append(r["score"])

    if tag_scores:
        print("\nPer-tag averages:")
        for tag, scores in sorted(tag_scores.items()):
            print(f"  {tag:<20} {sum(scores)/len(scores):.3f}  (n={len(scores)})")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SkillOpt mock harness for gflow-cli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skill",
        type=Path,
        default=DEFAULT_SKILL_PATH,
        help="Path to the skill markdown file (default: skills/gflow-cli/SKILL.md)",
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        default=TASKS_PATH,
        help="Path to the tasks JSON file (default: scripts/dev/skillopt/tasks.json)",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default=None,
        help="Comma-separated tag filter, e.g. 'auth,video'",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"Claude model ID (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts and scoring spec without calling the API",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("ANTHROPIC_API_KEY"),
        help="Anthropic API key (default: $ANTHROPIC_API_KEY)",
    )
    args = parser.parse_args()

    if not args.skill.exists():
        sys.exit(f"Skill file not found: {args.skill}")
    if not args.tasks.exists():
        sys.exit(f"Tasks file not found: {args.tasks}")

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    tasks = load_tasks(args.tasks, tags)
    if not tasks:
        sys.exit("No tasks matched the given filter.")

    skill_text, version, epoch = load_skill(args.skill)

    if args.dry_run:
        run_dry(tasks, skill_text, version, epoch)
    else:
        if not args.api_key:
            sys.exit("No API key. Set ANTHROPIC_API_KEY or pass --api-key.")
        run_live(tasks, skill_text, version, epoch, args.model, args.api_key)


if __name__ == "__main__":
    main()
