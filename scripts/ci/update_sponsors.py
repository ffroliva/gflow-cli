#!/usr/bin/env python3
"""Regenerate the sponsor hall of fame in README.md and docs/SPONSORS.md.

Reads GitHub Sponsors through the `gh` CLI and rewrites the block between the
`sponsors:start` / `sponsors:end` markers in every page in TARGETS. Placement follows the
published tiers (see docs/SPONSORS.md): company tiers get logos, backers get avatars, everyone
else gets their name. One-time sponsors keep their place permanently; a monthly sponsor who
stops moves to "Past sponsors".

Trust boundary — this publishes data about real people into public pages:
- only PUBLIC sponsorships are rendered, even though the query already excludes private ones;
- display names are sponsor-controlled text, so everything is HTML-escaped;
- profile links are built from a validated login, and avatars are only loaded from GitHub's
  avatar host.

Usage (needs GH_TOKEN with the `read:user` scope — the workflow's SPONSORS_READ_TOKEN):
    python scripts/ci/update_sponsors.py
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAINTAINER = "ffroliva"
TARGETS = ("README.md", "docs/SPONSORS.md")
START = "<!-- sponsors:start -->"
END = "<!-- sponsors:end -->"
# Gold promises a logo at the TOP of each page, above the hall of fame further down.
GOLD_START = "<!-- sponsors-gold:start -->"
GOLD_END = "<!-- sponsors-gold:end -->"

_REPO = Path(__file__).resolve().parents[2]
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_AVATAR_HOST = "https://avatars.githubusercontent.com/"
_CHECKOUT = f"https://github.com/sponsors/{MAINTAINER}/sponsorships"

# (key, heading, avatar width or None for names) — declaration order is page order.
GROUPS: tuple[tuple[str, str, int | None], ...] = (
    ("gold", "🥇 Gold", 120),
    ("silver", "🥈 Silver", 90),
    ("bronze", "🥉 Bronze", 64),
    ("backer", "🙌 Backers", 48),
    ("supporter", "💖 Supporters", None),
    ("past", "Past sponsors", None),
)
_RANK = {key: rank for rank, (key, _, _) in enumerate(GROUPS)}

_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    sponsorshipsAsMaintainer(first: 100, after: $cursor, activeOnly: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        createdAt isActive isOneTimePayment privacyLevel
        tier { monthlyPriceInDollars }
        sponsorEntity {
          ... on User { login name avatarUrl(size: 240) }
          ... on Organization { login name avatarUrl(size: 240) }
        }
      }
    }
  }
}"""

Runner = Callable[[Sequence[str]], str]


@dataclass(frozen=True)
class Sponsor:
    login: str
    display_name: str
    avatar_url: str | None
    amount_usd: int
    one_time: bool
    active: bool
    since: str

    @property
    def placement(self) -> str:
        if self.one_time:
            return "backer" if self.amount_usd >= 100 else "supporter"
        if not self.active:
            return "past"
        for floor, key in ((1000, "gold"), (250, "silver"), (100, "bronze"), (15, "backer")):
            if self.amount_usd >= floor:
                return key
        return "supporter"


def run_gh(args: Sequence[str]) -> str:
    # stderr is not captured, so gh's own reason (expired token, missing scope) reaches the log.
    return subprocess.run(
        ["gh", *args], stdout=subprocess.PIPE, text=True, encoding="utf-8", check=True
    ).stdout


def fetch(runner: Runner = run_gh) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        args = ["api", "graphql", "-f", f"query={_QUERY}", "-f", f"login={MAINTAINER}"]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        connection = json.loads(runner(args))["data"]["user"]["sponsorshipsAsMaintainer"]
        nodes += connection["nodes"]
        if not connection["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = connection["pageInfo"]["endCursor"]


def parse(nodes: list[dict[str, Any]]) -> list[Sponsor]:
    sponsors: list[Sponsor] = []
    for n in nodes:
        entity = n.get("sponsorEntity")
        if n.get("privacyLevel") != "PUBLIC" or not entity:
            continue  # private sponsorship, or the sponsor's account was deleted
        login = entity.get("login") or ""
        if not _LOGIN.fullmatch(login):
            continue
        avatar = entity.get("avatarUrl") or ""
        sponsors.append(
            Sponsor(
                login=login,
                # Collapsed: a blank line in a name would end the raw HTML block in markdown.
                display_name=" ".join((entity.get("name") or login).split()),
                avatar_url=avatar if avatar.startswith(_AVATAR_HOST) else None,
                amount_usd=(n.get("tier") or {}).get("monthlyPriceInDollars") or 0,
                one_time=bool(n.get("isOneTimePayment")),
                active=bool(n.get("isActive")),
                since=n.get("createdAt") or "",
            )
        )
    return sponsors


def group(sponsors: list[Sponsor]) -> dict[str, list[Sponsor]]:
    def rank(s: Sponsor) -> tuple[int, int]:
        return _RANK[s.placement], -s.amount_usd

    best: dict[str, Sponsor] = {}
    for s in sponsors:
        current = best.get(s.login)
        if current is None or rank(s) < rank(current):
            best[s.login] = s
    groups: dict[str, list[Sponsor]] = {key: [] for key, _, _ in GROUPS}
    for s in best.values():
        groups[s.placement].append(s)
    for members in groups.values():
        members.sort(key=lambda s: (-s.amount_usd, s.since, s.login.casefold()))
    return groups


def _entry(s: Sponsor, width: int | None) -> str:
    profile = f"https://github.com/{s.login}"
    name = html.escape(s.display_name)
    if width is None or s.avatar_url is None:
        return f'<a href="{profile}">{name}</a>'
    src = html.escape(s.avatar_url)
    return f'<a href="{profile}"><img src="{src}" width="{width}" alt="{name}"></a>'


def render(sponsors: list[Sponsor]) -> str:
    groups = group(sponsors)
    if not any(groups.values()):
        one_time = html.escape(f"{_CHECKOUT}?frequency=one-time&amount=5")
        return (
            "No sponsors yet. "
            f'<a href="{one_time}">Be the first</a> — every public sponsor is listed here.'
        )
    sections: list[str] = []
    for key, heading, width in GROUPS:
        members = groups[key]
        if not members:
            continue
        separator = " " if width is not None else " · "
        entries = separator.join(_entry(s, width) for s in members)
        sections.append(f"<h4>{heading}</h4>\n<p>{entries}</p>")
    return "\n\n".join(sections)


def render_gold(sponsors: list[Sponsor]) -> str:
    """The top-of-page Gold row. Empty, and so invisible, until there is a Gold sponsor."""
    gold = group(sponsors)["gold"]
    if not gold:
        return ""
    width = next(w for key, _, w in GROUPS if key == "gold")
    entries = " ".join(_entry(s, width) for s in gold)
    return f"<p><strong>🥇 Gold sponsors</strong><br>\n{entries}</p>"


def replace_block(text: str, block: str, start: str = START, end: str = END) -> str:
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise ValueError(f"expected exactly one {start} ... {end} marker pair")
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{start}\n{block}\n{end}{tail}"


def main(root: Path = _REPO, runner: Runner = run_gh) -> int:
    sponsors = parse(fetch(runner))
    hall, gold = render(sponsors), render_gold(sponsors)
    for relative in TARGETS:
        path = root / relative
        before = path.read_text(encoding="utf-8")
        after = replace_block(replace_block(before, hall), gold, GOLD_START, GOLD_END)
        if after == before:
            print(f"unchanged: {relative}")
            continue
        path.write_text(after, encoding="utf-8", newline="\n")
        print(f"updated: {relative}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
