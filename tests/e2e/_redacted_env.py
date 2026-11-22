"""Redacted environment mapping for test isolation (#1382)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
        unmasked values if converted to a plain ``dict``. Subprocess environments
        and test helpers must not unpack or rebuild a plain ``dict`` from
        ``e2e_env``. An offline guard test enforces this across ``tests/e2e/``.
    """

    def __repr__(self) -> str:
        return repr({k: "***" for k in self})

    def copy(self) -> RedactedEnv:
        return RedactedEnv(self)

    def __or__(self, other: Mapping[str, Any]) -> RedactedEnv:  # pyright: ignore[reportIncompatibleMethodOverride]
        res = RedactedEnv(self)
        res.update({k: str(v) for k, v in other.items()})
        return res

    def __ror__(self, other: Mapping[str, Any]) -> RedactedEnv:  # pyright: ignore[reportIncompatibleMethodOverride]
        res = RedactedEnv({k: str(v) for k, v in other.items()})
        res.update(self)
        return res

    def __ior__(self, other: Mapping[str, Any]) -> RedactedEnv:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.update({k: str(v) for k, v in other.items()})
        return self
