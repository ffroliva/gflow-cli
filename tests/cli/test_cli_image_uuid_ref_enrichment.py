"""Regression tests for #393 — `gflow image i2i --ref <UUID>` reference binding.

The #393 report was that a UUID `--ref` typed the raw UUID into the picker's
name search, found nothing, and generated WITHOUT the reference. Live runs
against real Flow on 2026-07-27 (v0.44.0) established the true behavior:

* Flow's picker search genuinely does not index UUIDs. A live DOM capture showed
  tiles labelled with Flow's short ``displayName``, not the generation prompt.
  That name can filter the picker when the catalog retained it; the UUID still
  identifies the exact surfaced tile.
* An unfindable ref does NOT generate silently: it raises and exits non-zero
  (verified live, exit 9). That contract is pinned in
  ``tests/api/transports/test_ui_automation_video.py``.
* The CLI now hands the transport both the catalog name and any recorded local
  file, so a named picker miss can still upload the exact image bytes.

These tests pin the enrichment so it can't silently regress.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from gflow_cli.api.image import ImageRef
from gflow_cli.data.models import AssetKind
from gflow_cli.errors import DataStoreError

if TYPE_CHECKING:
    from collections.abc import Callable

_UUID = "5a80906f-31cc-4a87-9782-95f14bb165ce"


def _asset(
    *,
    local_path: Path | None,
    kind: AssetKind = AssetKind.IMAGE,
    display_name: str | None = None,
    recorded_bytes: int | None = None,
    recorded_sha256: str | None = None,
) -> SimpleNamespace:
    content = local_path.read_bytes() if local_path is not None and local_path.is_file() else None
    return SimpleNamespace(
        kind=kind,
        metadata_json={"display_name": display_name} if display_name else {},
        local_files=(
            [
                SimpleNamespace(
                    path=local_path,
                    storage_provider=None,
                    bytes=recorded_bytes if recorded_bytes is not None else len(content or b""),
                    sha256=(
                        recorded_sha256
                        if recorded_sha256 is not None
                        else hashlib.sha256(content).hexdigest()
                        if content is not None
                        else None
                    ),
                )
            ]
            if local_path is not None
            else []
        ),
    )


@pytest.fixture
def patch_asset(monkeypatch: pytest.MonkeyPatch) -> Callable[[object], None]:
    """Point the enrichment's catalog lookup at a canned result.

    The fake repository may be given an asset lookup, ``None`` (unknown asset),
    or an exception instance to raise (catalog unavailable).
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

            def get_asset_by_flow_media_id(self, _profile: str, _media_id: str) -> object:
                if isinstance(result, Exception):
                    raise result
                return result

        monkeypatch.setattr(cli_image.DataStore, "open", staticmethod(lambda _p: _FakeStore()))
        monkeypatch.setattr(cli_image, "DataRepository", _FakeRepo)

    return _apply


class TestEnrichUuidRef:
    def test_populates_catalog_display_name_for_picker_search(
        self, patch_asset: Callable[[object], None], tmp_path: Path
    ) -> None:
        """A bare UUID becomes name-searchable before the browser opens.

        UUID remains the exact tile identity; ``display_name`` is only the
        browser picker's search key.
        """
        from gflow_cli.cli_image import _enrich_uuid_refs

        local = tmp_path / f"{_UUID}_1.jpg"
        local.write_bytes(b"\xff\xd8\xff")

        patch_asset(
            _asset(
                local_path=local,
                display_name="Brass key on wooden bench",
            )
        )

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched.display_name == "Brass key on wooden bench"
        assert enriched.local_path == str(local)
        assert enriched.local_sha256 == hashlib.sha256(local.read_bytes()).hexdigest()

    def test_populates_local_path_as_upload_fallback(
        self, patch_asset: Callable[[object], None], tmp_path: Path
    ) -> None:
        """The catalog's on-disk file becomes the transport's upload fallback
        for a tile the picker can't reach (#393)."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        local = tmp_path / f"{_UUID}_1.jpg"
        local.write_bytes(b"\xff\xd8\xff")
        patch_asset(_asset(local_path=local))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched.name == _UUID
        assert enriched.local_path == str(local)

    def test_missing_local_file_is_not_offered_as_fallback(
        self, patch_asset: Callable[[object], None], tmp_path: Path
    ) -> None:
        """A stale catalog path must not send the transport into uploading a
        file that no longer exists — better to fail loud than to fail deep."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        patch_asset(_asset(local_path=tmp_path / "deleted.jpg"))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched.local_path == ""

    def test_asset_without_local_file_left_untouched(
        self, patch_asset: Callable[[object], None]
    ) -> None:
        """A cataloged asset that was never downloaded has no bytes to fall
        back on; the picker lookup remains the only path."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        patch_asset(_asset(local_path=None))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched == ImageRef(name=_UUID)

    def test_unknown_asset_leaves_ref_untouched(
        self, patch_asset: Callable[[object], None]
    ) -> None:
        """A UUID from another machine/profile isn't in the catalog. The ref is
        passed through unchanged — the transport still tries the picker and
        still fails loud if it can't bind it."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        patch_asset(None)

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched == ImageRef(name=_UUID)

    def test_video_asset_leaves_image_ref_untouched(
        self, patch_asset: Callable[[object], None], tmp_path: Path
    ) -> None:
        """A UUID is not intrinsically an image UUID; never feed a cataloged
        video file into the I2I picker/upload path."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        local = tmp_path / f"{_UUID}.mp4"
        local.write_bytes(b"video")
        patch_asset(_asset(local_path=local, kind=AssetKind.VIDEO))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched == ImageRef(name=_UUID)

    def test_catalog_failure_is_best_effort(self, patch_asset: Callable[[object], None]) -> None:
        """Enrichment is an optimization: a broken/locked catalog must never
        break a generation that would otherwise work."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        patch_asset(DataStoreError(detail="catalog locked"))

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched == ImageRef(name=_UUID)

    def test_preserves_mention_display_name(
        self, patch_asset: Callable[[object], None], tmp_path: Path
    ) -> None:
        """An `@mention`-resolved ref already carries the media's display name;
        enrichment adds the fallback file without disturbing it."""
        from gflow_cli.cli_image import _enrich_uuid_refs

        local = tmp_path / f"{_UUID}_1.jpg"
        local.write_bytes(b"\xff\xd8\xff")
        patch_asset(_asset(local_path=local))

        enriched = _enrich_uuid_refs(
            [ImageRef(name=_UUID, display_name="Shop Interior")], "ffroliva"
        )[0]

        assert enriched.display_name == "Shop Interior"
        assert enriched.local_path == str(local)
        assert enriched.local_sha256 == hashlib.sha256(local.read_bytes()).hexdigest()

    def test_rejects_mutated_recorded_local_fallback(
        self, patch_asset: Callable[[object], None], tmp_path: Path
    ) -> None:
        from gflow_cli.cli_image import _enrich_uuid_refs

        local = tmp_path / "mutated.png"
        local.write_bytes(b"private replacement")
        patch_asset(
            _asset(
                local_path=local,
                display_name="Brass key",
                recorded_bytes=8,
                recorded_sha256="0" * 64,
            )
        )

        enriched = _enrich_uuid_refs([ImageRef(name=_UUID)], "ffroliva")[0]

        assert enriched.display_name == "Brass key"
        assert enriched.local_path == ""


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

            def get_asset_by_flow_media_id(self, _profile: str, _media_id: str) -> object:
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
