# SPDX-License-Identifier: MIT
"""MCP→worker payload-key round trip (#628).

``tests/mcp/test_cli_parity.py`` enforces parity at the *command* and *option*
level. Neither can see the third place the two surfaces drift: the queue payload
itself. An MCP tool writes ``payload["foo"]``, the worker reads
``payload.get("fooo")``, and the key is silently dropped — it type-checks, it
lints, every existing test passes, and the option does nothing at runtime.

That is not hypothetical. A v0.48.0 pre-release audit found a dead ``output``
param the queue never read (#495), and #626 shipped a parallel drift on the
docstring axis through a fully green pipeline.

This module closes the mechanically checkable half:

* **written** — every constant key ``mcp/tools.py`` puts into a queue payload,
  whether in a dict literal or a later ``payload[...] = ...`` store.
* **read** — every constant key anything under ``worker/`` pulls back out, via
  ``payload.get(...)``, ``payload[...]`` or ``"..." in payload``.

The reader set is deliberately the **whole worker package**, not just
``codec.py``. ``codec.py`` builds the request objects, but ``daemon.py`` consumes
``project_id``, ``project_name``, ``tool_specs`` and ``output_file`` directly, so
diffing tools.py against codec.py alone would report four false positives.

Writing this gate immediately found one: ``mcp/tools.py`` wrote ``project_name``
while ``daemon.py`` read ``project_title``, a key nothing in the repository has
ever written. Every agent-supplied project name had been dropped since the
parameter shipped, and each fresh project was created as the hardcoded fallback.

Only one direction is an error. A key written by an MCP tool and read by nobody
is a silent no-op, which is the defect. The reverse — a key the worker reads that
the MCP tools never write — is legitimate: the CLI ``gflow serve`` path and
``worker/queue.py`` enqueue tasks too, and some keys (``headless``, ``transport``,
``out_dir``) come from there or fall back to settings.

Case 3 from #628 (a docstring asserting a restriction the CLI no longer has) is
not mechanically checkable and stays with ``doc-review``, which grades a false
MCP claim as release-blocking.
"""

from __future__ import annotations

import ast
from pathlib import Path

import gflow_cli

_SRC = Path(str(gflow_cli.__file__)).resolve().parent
_TOOLS = _SRC / "mcp" / "tools.py"
_WORKER = _SRC / "worker"


def _is_payload_target(node: ast.expr) -> bool:
    """True for ``payload``/``versioned_payload``/``task.payload``-shaped expressions."""
    if isinstance(node, ast.Name):
        return "payload" in node.id
    if isinstance(node, ast.Attribute):
        return "payload" in node.attr
    return False


def _const_str(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _written_keys(source: str) -> set[str]:
    """Constant keys written into a payload dict by ``source``."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        # payload: dict[str, Any] = {"k": ...}   /   payload = {"k": ...}
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = ([node.target], node.value)

        for target in targets:
            if isinstance(target, ast.Subscript) and _is_payload_target(target.value):
                # payload["k"] = ...
                if (key := _const_str(target.slice)) is not None:
                    keys.add(key)
            elif _is_payload_target(target) and isinstance(value, ast.Dict):
                for k in value.keys:
                    if (key := _const_str(k)) is not None:
                        keys.add(key)
    return keys


def _read_keys(source: str) -> set[str]:
    """Constant keys read back out of a payload dict by ``source``."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        # payload.get("k") / task.payload.get("k")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_payload_target(node.func.value)
            and node.args
            and (key := _const_str(node.args[0])) is not None
        ):
            keys.add(key)
        # payload["k"] in a load position
        elif (
            isinstance(node, ast.Subscript)
            and _is_payload_target(node.value)
            and isinstance(node.ctx, ast.Load)
            and (key := _const_str(node.slice)) is not None
        ):
            keys.add(key)
        # "k" in payload
        elif isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                if (
                    isinstance(op, ast.In)
                    and _is_payload_target(comparator)
                    and (key := _const_str(node.left)) is not None
                ):
                    keys.add(key)
    return keys


def _worker_read_keys() -> set[str]:
    keys: set[str] = set()
    for path in sorted(_WORKER.rglob("*.py")):
        keys |= _read_keys(path.read_text(encoding="utf-8"))
    return keys


# --------------------------------------------------------------------------
# The extractors must be able to FAIL. A gate that cannot go red proves nothing,
# so these pin the discriminating power against synthetic sources rather than
# leaving it to a one-off manual check (memory: ab-control-before-shipping-a-fix).
# --------------------------------------------------------------------------

_SYNTHETIC_WRITER = """
def build() -> dict[str, object]:
    payload: dict[str, object] = {"prompt": p, "aspect": a}
    if seed is not None:
        payload["seed"] = seed
    versioned_payload = dict(payload)
    versioned_payload["schema_version"] = 2
    return versioned_payload
"""

_SYNTHETIC_READER = """
def decode(payload: dict[str, object]) -> None:
    prompt = payload["prompt"]
    aspect = payload.get("aspect")
    if "schema_version" in payload:
        pass
    depth = task.payload.get("depth", 0)
"""


def test_the_writer_extractor_sees_literals_and_later_stores() -> None:
    assert _written_keys(_SYNTHETIC_WRITER) == {"prompt", "aspect", "seed", "schema_version"}


def test_the_reader_extractor_sees_all_three_access_shapes() -> None:
    assert _read_keys(_SYNTHETIC_READER) == {"prompt", "aspect", "schema_version", "depth"}


def test_a_written_but_unread_key_is_detected() -> None:
    """The defect this gate exists for: ``seed`` is written and never read back."""
    written = _written_keys(_SYNTHETIC_WRITER)
    read = _read_keys(_SYNTHETIC_READER)
    assert written - read == {"seed"}


def test_a_misspelled_reader_does_not_count_as_reading_the_key() -> None:
    """``payload["prompt"]`` written, ``payload.get("promptt")`` read, is still a drop."""
    written = _written_keys('payload = {"prompt": p}')
    read = _read_keys('x = payload.get("promptt")')
    assert written - read == {"prompt"}


# --------------------------------------------------------------------------
# The gate itself.
# --------------------------------------------------------------------------


def test_every_payload_key_an_mcp_tool_writes_is_read_by_the_worker() -> None:
    written = _written_keys(_TOOLS.read_text(encoding="utf-8"))
    read = _worker_read_keys()

    assert written, "extracted no payload keys from mcp/tools.py — the extractor is broken"

    unread = written - read
    assert not unread, (
        "MCP tools write queue-payload keys the worker never reads, so they are "
        f"silent no-ops at runtime: {sorted(unread)}.\n"
        "Either read the key in worker/ (codec.py for request fields, daemon.py for "
        "run-level concerns), or stop writing it. See #628, and #495 for the "
        "precedent this gate exists to prevent."
    )
