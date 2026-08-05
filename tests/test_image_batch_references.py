"""Unit tests for intra-batch prompt references and dependency ordering (#317)."""

from __future__ import annotations

import pytest

from gflow_cli.errors import BatchIntegrityError, ConfigurationError
from gflow_cli.image_batch import BatchPromptItem, resolve_batch_dependencies


def test_batch_prompt_item_supports_ref_field() -> None:
    item = BatchPromptItem(
        text="A cyberpunk neon cityscape",
        ref="batch:0",
        reference_entity="media_12345",
    )
    assert item.ref == "batch:0"
    assert item.reference_entity == "media_12345"


def test_batch_dag_sort_orders_dependencies_correctly() -> None:
    item0 = BatchPromptItem(text="Prompt 0", index=0)
    item1 = BatchPromptItem(text="Prompt 1 referencing item 0", ref="batch:0", index=1)

    ordered = resolve_batch_dependencies([item1, item0])
    assert [item.index for item in ordered] == [0, 1]


def test_batch_dag_detects_circular_dependency() -> None:
    item0 = BatchPromptItem(text="Prompt 0", ref="batch:1", index=0)
    item1 = BatchPromptItem(text="Prompt 1", ref="batch:0", index=1)

    with pytest.raises(BatchIntegrityError, match="[Cc]ircular dependency"):
        resolve_batch_dependencies([item0, item1])


def test_batch_dag_validates_out_of_bounds_reference() -> None:
    item0 = BatchPromptItem(text="Prompt 0", ref="batch:5", index=0)

    with pytest.raises((ConfigurationError, BatchIntegrityError), match="[I|i]nvalid reference"):
        resolve_batch_dependencies([item0])


def test_batch_dag_self_reference_raises_error() -> None:
    item0 = BatchPromptItem(text="Prompt 0", ref="batch:0", index=0)

    with pytest.raises(BatchIntegrityError, match="[C|c]ircular dependency"):
        resolve_batch_dependencies([item0])


def test_batch_dag_invalid_reference_format_raises_error() -> None:
    item0 = BatchPromptItem(text="Prompt 0", ref="batch:invalid", index=0)

    with pytest.raises(ConfigurationError, match="[I|i]nvalid reference format"):
        resolve_batch_dependencies([item0])


def test_batch_dag_uses_reference_entity_if_ref_unset() -> None:
    item0 = BatchPromptItem(text="Prompt 0", index=0)
    item1 = BatchPromptItem(text="Prompt 1", reference_entity="batch:0", index=1)

    ordered = resolve_batch_dependencies([item1, item0])
    assert [item.index for item in ordered] == [0, 1]


def test_parse_batch_item_dict_with_ref_and_reference_entity() -> None:
    from gflow_cli.image_batch import parse_batch_item_dict

    d = {
        "text": "A futuristic city skyline",
        "ref": "batch:0",
        "reference_entity": "character_456",
    }
    item = parse_batch_item_dict(d, 0)
    assert item.ref == "batch:0"
    assert item.reference_entity == "character_456"


def test_parse_batch_item_dict_invalid_ref_type_raises_error() -> None:
    from gflow_cli.image_batch import parse_batch_item_dict

    d = {
        "text": "A futuristic city skyline",
        "ref": 12345,
    }
    with pytest.raises(ConfigurationError, match="prompts\\[0\\].ref must be a string"):
        parse_batch_item_dict(d, 0)


def test_parse_batch_item_dict_invalid_reference_entity_type_raises_error() -> None:
    from gflow_cli.image_batch import parse_batch_item_dict

    d = {
        "text": "A futuristic city skyline",
        "reference_entity": True,
    }
    msg = r"prompts\[0\].reference_entity must be a string"
    with pytest.raises(ConfigurationError, match=msg):
        parse_batch_item_dict(d, 0)
