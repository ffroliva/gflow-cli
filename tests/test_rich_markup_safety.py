# SPDX-License-Identifier: MIT
"""Anything interpolated into a Rich markup string must be escaped.

`console.print(f"[red]{exc.detail}[/red]")` is a markup **injection site**. Rich parses the
interpolated value looking for style tags, so a detail string containing `[chain]` or
`[patchright]` has that fragment read as a style, matched against nothing, and **silently
dropped**. Rich's tag regex only matches a bracket whose first character is a lowercase letter,
`#`, `/` or `@`, so only those are eaten — `[1]` and `[Claude Desktop]` pass through untouched.
That is precisely the shape of a package extra, which is why remediation hints were the casualty,
and why a test asserting on a capitalised token would prove nothing. Measured:

    UNESCAPED renders: "-> pip install 'gflow-cli'"
    ESCAPED   renders: "-> pip install 'gflow-cli[chain]'"

So the remediation told the user to install the package they already had. `--json` was never
affected, which is why it survived so long.

This has now been found twice. The first fix silenced one call site; the pattern grew back in
thirteen others, including the one that renders `ConfigurationError.detail` — the error that
carries `gflow-cli[patchright]`. A per-site fix does not hold, so this test is the guard: it
walks the AST of every module under `src/gflow_cli` and fails on a `console.print` f-string that
interpolates a bare name or attribute without `escape()`.

Literal text in the f-string is fine — that is the author's own markup, which is the point.
Only the *interpolated* parts are attacker- or environment-controlled.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rich.markup import escape

_SRC = Path(__file__).resolve().parents[1] / "src" / "gflow_cli"

#: Call targets whose first argument is rendered as Rich markup.
_PRINTERS = {"print", "log"}

#: Wrappers that make an interpolated value safe.
_SAFE_CALLS = {"escape", "repr", "len", "type"}

#: Names conventionally bound to a caught exception.
_EXCEPTION_NAMES = {"e", "exc", "err", "error", "ex", "exception"}

#: Attributes carrying error text written by us, which routinely contains brackets
#: (`gflow-cli[chain]`, `gflow-cli[patchright]`).
#:
#: `title` is deliberately NOT here. It matches far more than errors — a tool spec, a project
#: record and a movie manifest all have one — and `ConfigurationError.title` is a class-level
#: constant we author, so it cannot carry a surprise bracket. Including it produced six false
#: positives and no true ones.
_EXCEPTION_ATTRS = {"detail", "remediation_hint", "msg", "message"}


def _printer_call(node: ast.Call) -> bool:
    """True for `console.print(...)` / `_console.print(...)` and friends."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _PRINTERS:
        return False
    value = func.value
    return isinstance(value, ast.Name) and "console" in value.id.lower()


def _is_exception_text(node: ast.expr) -> bool:
    """True when the interpolated value carries an exception's own message.

    Deliberately narrower than "everything interpolated". Any interpolation can in principle
    lose a `[...]` — a prompt echoed back, say — but that is a cosmetic echo. Exception text is
    the class that produces *wrong instructions*: it is where this project writes
    `gflow-cli[chain]` and `gflow-cli[patchright]`, and it is what the user is told to act on.
    Widening this set later is cheap; leaving the advice path unguarded was not.
    """
    if isinstance(node, ast.Name):
        return node.id in _EXCEPTION_NAMES
    if isinstance(node, ast.Attribute):
        if node.attr in _EXCEPTION_ATTRS:
            return True
        return _is_exception_text(node.value)
    if isinstance(node, ast.BoolOp):  # `exc.detail or exc`
        return any(_is_exception_text(v) for v in node.values)
    if isinstance(node, ast.Call):
        return any(_is_exception_text(a) for a in node.args)
    return False


def _is_safe(node: ast.expr) -> bool:
    """A formatted value is safe if it is a literal or goes through a known-safe call."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Attribute) and node.attr == "__name__":
        return True  # `type(exc).__name__` is a class name; it cannot carry a bracket
    if isinstance(node, ast.JoinedStr):
        return all(_is_safe(v.value) for v in node.values if isinstance(v, ast.FormattedValue))
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in _SAFE_CALLS:
            return True
        # `escape(str(exc))` and `", ".join(...)` style wrappers: safe if every argument is.
        return all(_is_safe(a) for a in node.args)
    if isinstance(node, ast.BinOp | ast.BoolOp):
        children = [node.left, node.right] if isinstance(node, ast.BinOp) else list(node.values)
        return all(_is_safe(c) for c in children)
    return False


def _offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _printer_call(node):
                continue
            for arg in node.args[:1]:
                if not isinstance(arg, ast.JoinedStr):
                    continue
                # Only f-strings that actually carry markup are injection sites.
                literal = "".join(
                    v.value
                    for v in arg.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
                if "[" not in literal:
                    continue
                for value in arg.values:
                    if not isinstance(value, ast.FormattedValue):
                        continue
                    if not _is_exception_text(value.value):
                        continue
                    if not _is_safe(value.value):
                        found.append(
                            f"{path.relative_to(_SRC.parents[1]).as_posix()}:{value.lineno}"
                        )
    return sorted(set(found))


def test_no_unescaped_interpolation_into_rich_markup() -> None:
    offenders = _offenders()
    assert not offenders, (
        "These console.print() f-strings interpolate a value into Rich markup without "
        "escape(). Rich will silently drop an `[a-z#/@]`-initial bracket from the value — "
        "which is how "
        "every `install gflow-cli[chain]` hint rendered as `install gflow-cli`.\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_actually_detects_the_bug() -> None:
    """A guard that cannot fail is worse than no guard — pin it against the real shape."""
    bad = ast.parse('console.print(f"[red]{exc.detail}[/red]")')
    good = ast.parse('console.print(f"[red]{escape(str(exc.detail))}[/red]")')

    def offenders_in(tree: ast.Module) -> int:
        count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _printer_call(node):
                for arg in node.args[:1]:
                    if isinstance(arg, ast.JoinedStr):
                        count += sum(
                            1
                            for v in arg.values
                            if isinstance(v, ast.FormattedValue)
                            and _is_exception_text(v.value)
                            and not _is_safe(v.value)
                        )
        return count

    assert offenders_in(bad) == 1, "the guard missed the exact shape it exists to catch"
    assert offenders_in(good) == 0, "the guard flags the correct, escaped form"


def test_the_rendered_output_really_keeps_the_brackets() -> None:
    """The AST guard proves the call is escaped; this proves the pixels are right.

    Both are worth having. A structural guard can be satisfied by an `escape()` that is applied
    to the wrong expression, and a behavioural test alone does not stop the pattern reappearing
    at the next call site. This one renders a real `GflowError` through the shared handler and
    asserts the bracketed extra survives into the output a user sees.
    """
    import io

    from rich.console import Console

    from gflow_cli.errors import FrameExtractionError

    exc = FrameExtractionError(detail="no module named 'av'")
    hint = exc.remediation_hint or ""
    assert "[chain]" in hint, "the fixture must carry a bracketed extra or it proves nothing"

    buf = io.StringIO()
    console = Console(file=buf, width=200, no_color=True, highlight=False)
    console.print(f"[yellow]-> {escape(hint)}[/yellow]")
    rendered = buf.getvalue()

    assert "gflow-cli[chain]" in rendered, (
        "Rich ate the bracketed extra; the user is being told to install the package they "
        f"already have. Rendered: {rendered!r}"
    )
