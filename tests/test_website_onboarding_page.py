"""The onboarding page (website/docs/onboarding-mockup.html) renders and says true things.

MkDocs copies this hand-written page to the site unrendered, so nothing else checks it:
its doc links are not rewritten, its commands are not run, and its scroll motion,
print layout and colours are only seen in a browser. These tests load it in Chromium
and check it against the real CLI. Chromium is installed on the 3.13 CI shards; the
browser tests skip where it is absent.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from gflow_cli.cli import main
from gflow_cli.errors import EXIT_CODE_MAP, GFlowError

_DOCS = Path(__file__).resolve().parents[1] / "website" / "docs"
_PAGE = _DOCS / "onboarding-mockup.html"
_HTML = _PAGE.read_text(encoding="utf-8")

_STEP_MIN_OPACITY = """() => [...document.querySelectorAll('.step')].map(s =>
    Math.min(...[...s.children].map(c => +getComputedStyle(c).opacity)))"""


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - only the 3.13 CI shards install it
            # Skip only a missing browser; any other launch failure is a real failure.
            if "Executable doesn't exist" not in str(exc):
                raise
            pytest.skip(f"Playwright Chromium is not installed: {exc}")
        yield b
        b.close()


def _open(browser: Browser, *, javascript: bool = True) -> tuple[Page, list[str]]:
    page = browser.new_page(java_script_enabled=javascript, viewport={"width": 1100, "height": 800})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(_PAGE.as_uri())
    return page, errors


def test_doc_links_use_the_directory_urls_mkdocs_publishes() -> None:
    hrefs = re.findall(r'href="([^"]+)"', _HTML)
    local = [h for h in hrefs if not h.startswith(("http", "#", "mailto:"))]
    assert local, "expected the page to link to other docs"
    for href in local:
        # A .md target exists only as source; MkDocs publishes it as <name>/.
        assert href.endswith("/") and (_DOCS / f"{href.rstrip('/')}.md").is_file(), href


def test_print_shows_every_step_before_any_scroll(browser: Browser) -> None:
    page, _ = _open(browser)
    page.emulate_media(media="print")
    assert page.evaluate(_STEP_MIN_OPACITY) == [1] * len(page.query_selector_all(".step"))


def test_without_javascript_every_step_is_visible(browser: Browser) -> None:
    page, _ = _open(browser, javascript=False)
    assert all(o == 1 for o in page.evaluate(_STEP_MIN_OPACITY))


def test_scrolling_deals_in_every_step_and_marks_the_last_current(browser: Browser) -> None:
    page, errors = _open(browser)
    # Before scrolling, the steps below the fold are still hidden: the motion is on.
    assert min(page.evaluate(_STEP_MIN_OPACITY)) == 0
    for step in page.query_selector_all(".step"):
        step.scroll_into_view_if_needed()
        page.wait_for_function("s => s.classList.contains('in')", arg=step)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_function(
        """() => { const s = [...document.querySelectorAll('.step')];
        return s.at(-1).classList.contains('active')
            && s.slice(0, -1).every(x => x.classList.contains('done')); }"""
    )
    classes = page.evaluate("() => [...document.querySelectorAll('.step')].map(s => s.className)")
    assert all("in" in c.split() for c in classes), classes
    assert "active" in classes[-1].split() and all("done" in c for c in classes[:-1]), classes
    assert errors == []


def _luminance(rgb: tuple[int, int, int]) -> float:
    srgb = [c / 255 for c in rgb]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(_hex(a)), _luminance(_hex(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hex(value: str) -> tuple[int, int, int]:
    h = value.strip().lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def test_contrast_helper_matches_known_wcag_ratios() -> None:
    assert round(_contrast("#000000", "#ffffff"), 1) == 21.0
    assert round(_contrast("#b9740f", "#f6f3ec"), 1) == 3.4


@pytest.mark.parametrize("scheme", ["slate", "default"])
@pytest.mark.parametrize("token", ["--gf-ink", "--gf-muted", "--gf-faint", "--gf-amber"])
def test_text_tokens_meet_wcag_aa_in_both_themes(browser: Browser, scheme: str, token: str) -> None:
    page, _ = _open(browser)
    page.evaluate(f"document.documentElement.setAttribute('data-md-color-scheme', '{scheme}')")
    css = page.evaluate(
        """t => { const s = getComputedStyle(document.documentElement);
        return [s.getPropertyValue(t), s.getPropertyValue('--gf-ground'),
                s.getPropertyValue('--gf-surface')]; }""",
        token,
    )
    fg, ground, surface = css
    for bg in (ground, surface):
        assert _contrast(fg, bg) >= 4.5, f"{token} {fg} on {bg} ({scheme})"


def test_copy_button_copies_every_command_without_prompts(browser: Browser) -> None:
    page, _ = _open(browser)
    page.evaluate(
        "() => { window.__copied = []; navigator.clipboard.writeText = t => "
        "{ window.__copied.push(t); return Promise.resolve(); }; }"
    )
    # A DOM click fires each button's onclick without waiting out the reveal animation.
    page.evaluate("() => document.querySelectorAll('.copy-btn').forEach(b => b.click())")
    copied: list[str] = page.evaluate("window.__copied")
    # Each block's commands, in page order, as the reader sees them in the terminal.
    expected: list[str] = page.evaluate(
        """() => [...document.querySelectorAll('.copy-btn')].map(b =>
        [...b.closest('.terminal').querySelectorAll('.terminal-body .cmd')]
            .map(c => c.textContent.trim()).join('\\n'))"""
    )
    assert copied == expected and all(copied), copied
    assert "/plugin marketplace add ffroliva/gflow-cli\n/plugin install gflow@gflow-cli" in copied
    assert (
        "gflow mcp setup\ngflow mcp setup --target cursor\ngflow mcp setup --target vscode"
        in copied
    )
    assert all(not line.startswith(("$", ">")) for text in copied for line in text.splitlines())


def _gflow_commands() -> list[str]:
    cmds = re.findall(r'<span class="cmd">(gflow [^<]+)</span>', _HTML)
    return [c.replace("&quot;", '"').replace("&amp;", "&") for c in cmds]


@pytest.mark.parametrize("command", _gflow_commands())
def test_every_gflow_command_on_the_page_parses(command: str) -> None:
    args = shlex.split(command)[1:]
    if args == ["--version"]:
        return
    cmd: click.Command = main
    ctx = click.Context(main, info_name="gflow")
    while isinstance(cmd, click.Group) and args and not args[0].startswith("-"):
        sub = cmd.get_command(ctx, args[0])
        assert sub is not None, f"{command}: no command {args[0]!r}"
        cmd, args = sub, args[1:]
        ctx = click.Context(cmd, info_name=cmd.name, parent=ctx)
    # Parsing validates every option and choice without invoking the command.
    cmd.make_context(cmd.name, args, parent=ctx.parent)


def test_error_cards_name_real_exit_codes() -> None:
    cards = re.findall(r'<div class="error-title">(.+?) \(exit (\d+)\)</div>', _HTML)
    assert cards
    # A subclass such as ProfileLockedError inherits its parent's exit code.
    classes: list[type[GFlowError]] = [GFlowError]
    for cls in classes:
        classes.extend(cls.__subclasses__())
    codes = {
        cls: next((EXIT_CODE_MAP[c] for c in cls.__mro__ if c in EXIT_CODE_MAP), None)
        for cls in classes
    }
    by_name = {cls.__name__: code for cls, code in codes.items()}
    by_title = {(getattr(cls, "title", ""), code) for cls, code in codes.items()}
    for label, code in cards:
        if label == "No profiles found":
            assert code == "2"  # a usage exit, raised before any GFlowError exists
        elif label.endswith("Error"):
            assert by_name.get(label) == int(code), label
        else:
            assert (label, int(code)) in by_title, f"{label} (exit {code})"
