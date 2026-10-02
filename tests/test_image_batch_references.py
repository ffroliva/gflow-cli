"""Intra-batch references: strict validation and a stable dependency order (#913).

``order_batch_rows`` replaces the #317 resolver, which nothing called and which
reshuffled valid manifests (``[0, 1→0, 2]`` came out ``[0, 2, 1]``, measured).
Every refusal is a ``ConfigurationError`` (exit 11) naming the row.
"""

from __future__ import annotations

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import BatchPromptItem, batch_parent, order_batch_rows


def _row(index: int, ref: str | None = None, count: int = 1) -> BatchPromptItem:
    return BatchPromptItem(text=f"prompt {index}", index=index, ref=ref, count=count)


def _order(*rows: BatchPromptItem) -> list[int]:
    return [r.index for r in order_batch_rows(list(rows))]


def test_no_references_keeps_file_order() -> None:
    assert _order(_row(0), _row(1), _row(2)) == [0, 1, 2]


def test_valid_manifest_is_not_reshuffled() -> None:
    # The #317 resolver returned [0, 2, 1] here.
    assert _order(_row(0), _row(1, "batch:0"), _row(2)) == [0, 1, 2]


def test_forward_reference_defers_only_the_child() -> None:
    assert _order(_row(0, "batch:2"), _row(1), _row(2)) == [1, 2, 0]


def test_chain_runs_parents_first() -> None:
    assert _order(_row(0, "batch:1"), _row(1, "batch:2"), _row(2)) == [2, 1, 0]


def test_two_children_of_one_parent() -> None:
    assert _order(_row(0), _row(1, "batch:0"), _row(2, "batch:0")) == [0, 1, 2]


@pytest.mark.parametrize(
    "ref",
    ["batch:", "batch:x", "batch:-1", "batch: 1", "batch:+1", "batch:01", "batch:1_0", "batch:١"],
)
def test_malformed_reference_is_refused(ref: str) -> None:
    with pytest.raises(ConfigurationError, match=r"prompts\[1\]\.ref"):
        order_batch_rows([_row(0), _row(1, ref)])


def test_out_of_range_reference_is_refused() -> None:
    with pytest.raises(ConfigurationError, match=r"prompts\[1\]\.ref.*batch:5"):
        order_batch_rows([_row(0), _row(1, "batch:5")])


def test_self_reference_is_refused() -> None:
    with pytest.raises(ConfigurationError, match=r"prompts\[1\]\.ref.*itself"):
        order_batch_rows([_row(0), _row(1, "batch:1")])


@pytest.mark.parametrize(
    "rows",
    [
        [_row(0, "batch:1"), _row(1, "batch:0")],
        [_row(0, "batch:2"), _row(1, "batch:0"), _row(2, "batch:1")],
    ],
)
def test_cycle_is_refused(rows: list[BatchPromptItem]) -> None:
    with pytest.raises(ConfigurationError, match="cycle"):
        order_batch_rows(rows)


def test_parent_with_several_images_is_refused() -> None:
    with pytest.raises(ConfigurationError, match=r"prompts\[0\] makes 2 images"):
        order_batch_rows([_row(0, count=2), _row(1, "batch:0")])


def test_child_with_several_images_is_allowed() -> None:
    assert _order(_row(0), _row(1, "batch:0", count=3)) == [0, 1]


def test_batch_parent_reads_the_index() -> None:
    assert batch_parent(_row(3, "batch:12")) == 12
    assert batch_parent(_row(3)) is None
