"""Regression tests for #393 — `gflow image i2i --ref <UUID>` reference binding.

The #393 report was that a UUID `--ref` typed the raw UUID into the picker's
name search, found nothing, and generated WITHOUT the reference. Live runs
against real Flow on 2026-07-27 (v0.44.0) established the true behavior:

* Flow's picker search genuinely does not index UUIDs — the UUID search tiers
  return zero tiles, which is what the reporter screenshotted. A live DOM
  capture also showed tiles labelled with a short Flow-authored caption
  (``alt="Box tied with crimson ribbon"``), NOT the generation prompt, so no
  catalog-derived search term can rescue the lookup either.
* An unfindable ref does NOT generate silently: it raises and exits non-zero
  (verified live, exit 9). That contract is pinned in
  ``tests/api/transports/test_ui_automation_video.py``.
* What was missing is the rescue: the CLI never handed the transport the
  asset's recorded local file, so a tile the picker can't reach had no
  fallback and failed the whole generation.

These tests pin the enrichment so it can't silently regress.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gflow_cli.api.image import ImageRef
from gflow_cli.data.models import AssetKind, SeedImage
from gflow_cli.errors import DataStoreError

if TYPE_CHECKING:
    from collections.abc import Callable

_UUID = "5a80906f-31cc-4a87-9782-95f14bb165ce"


def _seed(*, local_path: Path | None) -> SeedImage:
    return SeedImage(
        profile_name="ffroliva",
        flow_project_id="336c35c1-eee3-4789-a274-0c04a7bdab2e",
        flow_media_id=_UUID,
        flow_workflow_id=None,
        kind=AssetKind.IMAGE,
        width=None,
        height=None,
        local_path=local_path,
        prompt="a serene contented moment behind a worn wooden counter",
        model="NARWHAL",
        aspect_ratio=None,
        created_at="2026-07-27T12:00:00+00:00",
    )


@pytest.fixture
def patch_seed(monkeypatch: pytest.MonkeyPatch) -> Callable[[object], None]:
    """Point the enrichment's catalog lookup at a canned result.

    ``resolve_seed_image`` may be given a ``SeedImage``, ``None`` (unknown
    asset), or an exception instance to raise (catalog unavailable).
    """

    def _apply(result: object) -> None:
        import gflow_cli.cli_image as cli_image

        class _FakeStore:
            def __enter__(self) -> _FakeStore:
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

        class _FakeRepo:
            def __init__(self, _store: object) -> None: ...

            def resolve_seed_image(self, _profile: str, _media_id: str) -> object:
                if isinstance(result, Exception):
                    raise result
                return result

        monkeypatch.setattr(cli_image.DataStore, "open", staticmethod(lambda _p: _FakeStore()))
        monkeypatch.setattr(cli_image, "DataRepository", _FakeRepo)

    return _apply


class TestEnrichUuidRef:
    def test_populates_local_path_as_upload_fallback(
        self, patch_seed: Callable[[object], None], tmp_path: Path
    ) -> None:
        """The catalog's on-disk file becomes the transport's upload fallback
        for a tile the picker can't reach (#393)."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        local = tmp_path / f"{_UUID}_1.jpg"
        local.write_bytes(b"\xff\xd8\xff")
        patch_seed(_seed(local_path=local))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched.name == _UUID
        assert enriched.local_path == str(local)

    def test_missing_local_file_is_not_offered_as_fallback(
        self, patch_seed: Callable[[object], None], tmp_path: Path
    ) -> None:
        """A stale catalog path must not send the transport into uploading a
        file that no longer exists — better to fail loud than to fail deep."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        patch_seed(_seed(local_path=tmp_path / "deleted.jpg"))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched.local_path == ""

    def test_asset_without_local_file_left_untouched(
        self, patch_seed: Callable[[object], None]
    ) -> None:
        """A cataloged asset that was never downloaded has no bytes to fall
        back on; the picker lookup remains the only path."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        patch_seed(_seed(local_path=None))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched == ImageRef(name=_UUID)

    def test_unknown_asset_leaves_ref_untouched(self, patch_seed: Callable[[object], None]) -> None:
        """A UUID from another machine/profile isn't in the catalog. The ref is
        passed through unchanged — the transport still tries the picker and
        still fails loud if it can't bind it."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        patch_seed(None)

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched == ImageRef(name=_UUID)

    def test_catalog_failure_is_best_effort(self, patch_seed: Callable[[object], None]) -> None:
        """Enrichment is an optimization: a broken/locked catalog must never
        break a generation that would otherwise work."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        patch_seed(DataStoreError(detail="catalog locked"))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched == ImageRef(name=_UUID)

    def test_preserves_mention_display_name(
        self, patch_seed: Callable[[object], None], tmp_path: Path
    ) -> None:
        """An `@mention`-resolved ref already carries the media's display name;
        enrichment adds the fallback file without disturbing it."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        local = tmp_path / f"{_UUID}_1.jpg"
        local.write_bytes(b"\xff\xd8\xff")
        patch_seed(_seed(local_path=local))

        enriched = _enrich_uuid_refs(
            [ImageRef(name=_UUID, display_name="Shop Interior")], "ffroliva"
        )[0]

        assert enriched.display_name == "Shop Interior"
        assert enriched.local_path == str(local)


class TestEnrichUuidRefsBatching:
    def test_opens_the_catalog_once_for_many_refs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`DataStore.open` runs the migration check, and nano2 allows 10 refs
        per call — enriching must not repeat that work per ref."""
        import gflow_cli.cli_image as cli_image
        from gflow_cli.cli_image import _enrich_uuid_refs

        opens: list[int] = []

        class _FakeStore:
            def __enter__(self) -> _FakeStore:
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

        class _FakeRepo:
            def __init__(self, _store: object) -> None: ...

            def resolve_seed_image(self, _profile: str, _media_id: str) -> object:
                return None

        def _open(_path: object) -> _FakeStore:
            opens.append(1)
            return _FakeStore()

        monkeypatch.setattr(cli_image.DataStore, "open", staticmethod(_open))
        monkeypatch.setattr(cli_image, "DataRepository", _FakeRepo)

        refs = [ImageRef(name=f"{i:08d}-1111-2222-3333-444444444444") for i in range(10)]
        assert _enrich_uuid_refs(refs, "ffroliva") == refs
        assert len(opens) == 1, f"opened the catalog {len(opens)}x for 10 refs"

    def test_empty_list_touches_no_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A pure-local-path i2i must not open the catalog at all."""
        import gflow_cli.cli_image as cli_image
        from gflow_cli.cli_image import _enrich_uuid_refs

        def _boom(_path: object) -> object:
            raise AssertionError("catalog opened for an empty ref list")

        monkeypatch.setattr(cli_image.DataStore, "open", staticmethod(_boom))

        assert _enrich_uuid_refs([], "ffroliva") == []
