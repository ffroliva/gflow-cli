# SPDX-License-Identifier: MIT
"""One walk of the Click tree, shared by the parity and capability coverage tests."""

from __future__ import annotations

import click


def cli_leaves(group: click.Group) -> set[str]:
    """Every invokable CLI path: plain commands + invoke_without_command groups."""
    leaves: set[str] = set()

    def _walk(grp: click.Group, prefix: str) -> None:
        for name, cmd in grp.commands.items():
            path = f"{prefix}{name}"
            if isinstance(cmd, click.Group):
                if cmd.invoke_without_command:
                    leaves.add(path)
                _walk(cmd, f"{path} ")
            else:
                leaves.add(path)

    _walk(group, "")
    return leaves
