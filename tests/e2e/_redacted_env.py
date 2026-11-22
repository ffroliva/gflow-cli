"""Redacted environment mapping for test isolation (#1382)."""

from __future__ import annotations

from collections.abc import Mapping


class RedactedEnv(dict[str, str]):
    """Environment mapping whose repr and str conceal variable values (#1382).

    Retains the exact mapping behavior required by ``subprocess.run(env=...)``
    while replacing all values with ``'***'`` in formatted output, ensuring
    failing tests and pytest failure reports (including ``--showlocals``)
    never leak environment secrets or API tokens.

    Guarantees:
      - ``__repr__`` and ``str()`` mask all values as ``'***'``.
      - ``copy()`` returns a new ``RedactedEnv`` with masked repr/str.
      - ``|`` (union), ``__ror__``, and ``|=`` return or update a ``RedactedEnv``
        with masked repr/str.

    Non-guarantees:
      - Views (``.items()``, ``.values()``) and equality assertion diffs yield
        unmasked values if converted to a plain ``dict``. Top-level pytest
        failure reports (tracebacks, ``-l`` locals, captured output) mask
        secret-looking environment values via the test report scrubber hook
        in ``tests/conftest.py``; ``RedactedEnv`` provides defense-in-depth by
        keeping the fixture's own repr and string formatting masked.
    """

    def __repr__(self) -> str:
        return repr({k: "***" for k in self})

    def copy(self) -> RedactedEnv:
        return RedactedEnv(self)

    def __or__(self, other: object) -> RedactedEnv:  # pyright: ignore[reportIncompatibleMethodOverride]
        if not isinstance(other, Mapping):
            return NotImplemented
        res = RedactedEnv(self)
        res.update(other)
        return res

    def __ror__(self, other: object) -> RedactedEnv:  # pyright: ignore[reportIncompatibleMethodOverride]
        if not isinstance(other, Mapping):
            return NotImplemented
        res = RedactedEnv(other)
        res.update(self)
        return res

    def __ior__(self, other: object) -> RedactedEnv:  # pyright: ignore[reportIncompatibleMethodOverride]
        if not isinstance(other, Mapping):
            return NotImplemented
        self.update(other)
        return self
