# SPDX-License-Identifier: MIT
"""The Click-tree walk as a set of paths, for the parity and capability coverage tests.

A shim over ``gflow_cli._cli_helpers.cli_leaves``, the walker ``--help --json`` uses
(#1203), so the tests and the command tree can never walk different surfaces.
"""

from __future__ import annotations

import click

from gflow_cli._cli_helpers import cli_leaves as _walk


def cli_leaves(group: click.Group) -> set[str]:
    """Every invokable CLI path: plain commands + invoke_without_command groups."""
    return {" ".join(path) for path, _, _ in _walk(group)}
