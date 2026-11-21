"""E2E test for media trash and restore over the wire (#1363).

Binds ``tests/features/media_trash_live.feature`` (@e2e @e2e_image).
Verifies that trashing and restoring an item on flow.google.com via pGCYOe
spends zero credits, updates the project listing accordingly, and restores cleanly.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("../features/media_trash_live.feature")
