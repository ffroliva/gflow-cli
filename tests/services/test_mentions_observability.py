from __future__ import annotations

import hashlib

from structlog.testing import capture_logs

from gflow_cli.config import get_settings
from gflow_cli.services.mentions import (
    AssetIndex,
    parse_mentions,
    resolve_mentions,
)


def test_observability_resolved_unredacted(monkeypatch) -> None:
    # Setup settings to 'store' (unredacted)
    monkeypatch.setattr(get_settings(), "history_prompts", "store")

    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro")

    with capture_logs() as logs:
        res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro")

    # Verify event emission
    assert len(logs) == 1
    assert logs[0]["event"] == "mention_resolved"
    assert logs[0]["name"] == "Zoro"
    assert logs[0]["kind"] == "entity"
    assert logs[0]["id"] == "e1-uuid-12345678901234567890123456"

    # Verify metadata JSON payload
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro"
    assert res.mentions[0].kind == "entity"
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"


def test_observability_resolved_redacted(monkeypatch) -> None:
    # Setup settings to 'redacted'
    monkeypatch.setattr(get_settings(), "history_prompts", "redacted")

    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro")

    with capture_logs() as logs:
        res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro")

    expected_hash = hashlib.sha256(b"Zoro").hexdigest()

    # Verify redacted event emission (name is hashed)
    assert len(logs) == 1
    assert logs[0]["event"] == "mention_resolved"
    assert logs[0]["name"] == expected_hash
    assert logs[0]["kind"] == "entity"
    assert logs[0]["id"] == "e1-uuid-12345678901234567890123456"

    # Verify metadata JSON payload (name is hashed)
    assert len(res.mentions) == 1
    assert res.mentions[0].name == expected_hash
    assert res.mentions[0].kind == "entity"
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"


def test_observability_unresolved() -> None:
    index = AssetIndex(entities=[], media_assets=[])
    tokens = parse_mentions("Hello @nonexistent")

    with capture_logs() as logs:
        try:
            resolve_mentions(
                tokens, index, path="image", model="nano2", prompt="Hello @nonexistent"
            )
        except Exception:
            pass

    # Verify unresolved event emission
    unresolved_logs = [log for log in logs if log["event"] == "mention_unresolved"]
    assert len(unresolved_logs) == 1
    assert unresolved_logs[0]["name"] == "nonexistent"
