"""Unit tests for the sponsor hall-of-fame generator.

The generator publishes data about real people into a public README, so the tests that matter
most are the trust-boundary ones: a private sponsorship is never rendered, and sponsor-controlled
text (display names) can never inject markup.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import update_sponsors as us

AVATAR = "https://avatars.githubusercontent.com/u/1?v=4"


def node(
    login: str = "someone",
    *,
    amount: int | None = 5,
    one_time: bool = False,
    active: bool = True,
    privacy: str = "PUBLIC",
    name: str | None = None,
    since: str = "2026-09-01T00:00:00Z",
    avatar: str = AVATAR,
) -> dict[str, Any]:
    return {
        "createdAt": since,
        "isActive": active,
        "isOneTimePayment": one_time,
        "privacyLevel": privacy,
        "tier": None if amount is None else {"monthlyPriceInDollars": amount},
        "sponsorEntity": {
            "login": login,
            "name": name,
            "avatarUrl": avatar,
        },
    }


def placements(*nodes: dict[str, Any]) -> dict[str, list[str]]:
    groups = us.group(us.parse(list(nodes)))
    return {tier: [s.login for s in sponsors] for tier, sponsors in groups.items() if sponsors}


@pytest.mark.parametrize(
    ("sponsorship", "expected"),
    [
        (node(amount=1000), "gold"),
        (node(amount=250), "silver"),
        (node(amount=100), "bronze"),
        (node(amount=15), "backer"),
        (node(amount=5), "supporter"),
        (node(amount=100, one_time=True, active=False), "backer"),
        (node(amount=25, one_time=True, active=False), "supporter"),
        (node(amount=1000, one_time=True), "backer"),
        (node(amount=250, active=False), "past"),
        (node(amount=None), "supporter"),
        (node(amount=999), "silver"),
        (node(amount=249), "bronze"),
        (node(amount=99), "backer"),
        (node(amount=14), "supporter"),
        (node(amount=99, one_time=True), "supporter"),
    ],
)
def test_placement_follows_the_published_tiers(sponsorship: dict[str, Any], expected: str) -> None:
    assert placements(sponsorship) == {expected: ["someone"]}


@pytest.mark.parametrize("privacy", ["PRIVATE", "UNKNOWN", ""])
def test_only_public_sponsorships_are_ever_rendered(privacy: str) -> None:
    # An allowlist, not a denylist: a privacy level GitHub adds later must stay hidden.
    sponsors = us.parse([node("hidden", privacy=privacy), node("shown")])

    assert [s.login for s in sponsors] == ["shown"]
    assert "hidden" not in us.render(sponsors)


def test_deleted_sponsor_accounts_are_skipped() -> None:
    deleted = node("ghost")
    deleted["sponsorEntity"] = None

    assert us.parse([deleted, node("alive")])[0].login == "alive"
    assert len(us.parse([deleted])) == 0


def test_a_repeat_sponsor_appears_once_at_their_best_placement() -> None:
    assert placements(
        node("fan", amount=5, one_time=True, active=False),
        node("fan", amount=15),
    ) == {"backer": ["fan"]}


def test_within_a_group_bigger_and_then_earlier_sponsors_come_first() -> None:
    assert placements(
        node("late", amount=5, since="2026-09-10T00:00:00Z"),
        node("early", amount=5, since="2026-09-01T00:00:00Z"),
        node("bigger", amount=10, since="2026-09-12T00:00:00Z"),
    ) == {"supporter": ["bigger", "early", "late"]}


def test_empty_hall_of_fame_invites_the_first_sponsor() -> None:
    block = us.render([])

    assert "No sponsors yet" in block
    assert "every public sponsor" in block
    assert (
        "https://github.com/sponsors/ffroliva/sponsorships?frequency=one-time&amp;amount=5" in block
    )


def test_render_uses_logos_avatars_and_names_by_group() -> None:
    block = us.render(
        us.parse(
            [
                node("acme", amount=1000, name="Acme"),
                node("backer", amount=15),
                node("friend", amount=5, name="A Friend"),
                node("former", amount=5, active=False),
            ]
        )
    )

    assert (
        '<img src="https://avatars.githubusercontent.com/u/1?v=4" width="120" alt="Acme">' in block
    )
    assert 'width="48" alt="backer"' in block
    assert '<a href="https://github.com/friend">A Friend</a>' in block
    assert "Past sponsors" in block
    assert (
        block.index("Gold")
        < block.index("Backers")
        < block.index("Supporters")
        < block.index("Past")
    )
    assert "No sponsors yet" not in block


@pytest.mark.parametrize("amount", [5, 15])
def test_sponsor_names_cannot_inject_markup(amount: int) -> None:
    # $5 renders the name as link text; $15 puts it inside an <img alt="..."> attribute.
    block = us.render(
        us.parse(
            [node("mallory", amount=amount, name='<script>x</script>" onerror="x"><img src=x>')]
        )
    )

    assert "<script>" not in block
    assert '" onerror=' not in block
    assert '"><img src=x>' not in block
    assert "&lt;script&gt;" in block
    assert "&quot;" in block


def test_line_breaks_in_names_cannot_end_the_html_block() -> None:
    # A blank line ends a raw HTML block in CommonMark, letting the rest render as markdown.
    block = us.render(us.parse([node("m", name="x\n\n[phish](https://evil.example)")]))

    assert "\n\n[phish]" not in block
    assert '<a href="https://github.com/m">x [phish](https://evil.example)</a>' in block


def test_avatars_from_unexpected_hosts_fall_back_to_a_name() -> None:
    block = us.render(
        us.parse([node("acme", amount=1000, avatar="https://evil.example/pixel.png")])
    )

    assert "evil.example" not in block
    assert '<a href="https://github.com/acme">acme</a>' in block


@pytest.mark.parametrize("login", ["bad login/../x", "a" * 40, "trailing\n", "-leading"])
def test_invalid_logins_are_skipped(login: str) -> None:
    assert [s.login for s in us.parse([node(login), node("ok")])] == ["ok"]


def test_replace_block_is_idempotent_and_keeps_surrounding_text() -> None:
    text = f"before\n{us.START}\nold\n{us.END}\nafter\n"

    once = us.replace_block(text, "new")

    assert once == f"before\n{us.START}\nnew\n{us.END}\nafter\n"
    assert us.replace_block(once, "new") == once


@pytest.mark.parametrize(
    "text", ["no markers", f"{us.START} only", f"{us.START}\n{us.END}\n{us.START}\n{us.END}"]
)
def test_replace_block_requires_exactly_one_marker_pair(text: str) -> None:
    with pytest.raises(ValueError, match="marker"):
        us.replace_block(text, "new")


def page(nodes: list[dict[str, Any]], cursor: str | None) -> str:
    return json.dumps(
        {
            "data": {
                "user": {
                    "sponsorshipsAsMaintainer": {
                        "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
                        "nodes": nodes,
                    }
                }
            }
        }
    )


def test_fetch_follows_pagination() -> None:
    calls: list[list[str]] = []

    def runner(args: Sequence[str]) -> str:
        calls.append(list(args))
        if any(a == "cursor=c1" for a in args):
            return page([node("second")], None)
        return page([node("first")], "c1")

    nodes = us.fetch(runner)

    assert [n["sponsorEntity"]["login"] for n in nodes] == ["first", "second"]
    assert len(calls) == 2


def test_gold_block_is_empty_until_there_is_a_gold_sponsor() -> None:
    assert us.render_gold(us.parse([node("silver", amount=250), node("backer", amount=15)])) == ""
    assert us.render_gold([]) == ""


def test_gold_block_shows_only_gold_logos() -> None:
    block = us.render_gold(
        us.parse([node("acme", amount=1000, name="Acme"), node("beta", amount=250)])
    )

    assert 'width="120" alt="Acme"' in block
    assert "beta" not in block
    assert "Gold sponsors" in block


def test_replace_block_accepts_other_marker_pairs() -> None:
    text = f"top\n{us.GOLD_START}\n\n{us.GOLD_END}\nrest {us.START}\nx\n{us.END}\n"

    once = us.replace_block(text, "logos", us.GOLD_START, us.GOLD_END)

    assert once == f"top\n{us.GOLD_START}\nlogos\n{us.GOLD_END}\nrest {us.START}\nx\n{us.END}\n"


def test_main_rewrites_every_target_and_reports_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for relative in us.TARGETS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# page\n{us.GOLD_START}\n\n{us.GOLD_END}\n{us.START}\nstale\n{us.END}\n",
            encoding="utf-8",
        )

    def runner(args: Sequence[str]) -> str:
        return page([node("friend"), node("acme", amount=1000, name="Acme")], None)

    assert us.main(root=tmp_path, runner=runner) == 0
    for relative in us.TARGETS:
        text = (tmp_path / relative).read_bytes().decode("utf-8")
        assert "\r\n" not in text
        gold = text.split(us.GOLD_START)[1].split(us.GOLD_END)[0]
        hall = text.split(us.START)[1].split(us.END)[0]
        assert "Acme" in gold
        assert "friend" not in gold
        assert "friend" in hall
        assert "Acme" in hall
    assert "updated" in capsys.readouterr().out

    assert us.main(root=tmp_path, runner=runner) == 0
    assert "unchanged" in capsys.readouterr().out


@pytest.mark.parametrize("relative", us.TARGETS)
def test_shipped_pages_carry_exactly_one_of_each_block(relative: str) -> None:
    text = (Path(__file__).resolve().parents[2] / relative).read_text(encoding="utf-8")

    assert us.replace_block(text, "probe").count(us.START) == 1
    assert us.replace_block(text, "probe", us.GOLD_START, us.GOLD_END).count(us.GOLD_START) == 1


@pytest.mark.parametrize("relative", us.TARGETS)
def test_gold_block_sits_above_everything_but_the_page_header(relative: str) -> None:
    # The Gold tier promises a logo at the TOP; the hall of fame is further down.
    text = (Path(__file__).resolve().parents[2] / relative).read_text(encoding="utf-8")

    assert text.index(us.GOLD_START) < text.index("\n## ")
    assert text.index(us.GOLD_START) < text.index(us.START)
