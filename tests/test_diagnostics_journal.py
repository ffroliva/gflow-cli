"""Unit tests for bounded incident journals, timing map, and listener
bookkeeping (Task 3 — S16/S17/S18 core)."""

from __future__ import annotations

from dataclasses import asdict

from gflow_cli.diagnostics import (
    ConsoleRecord,
    IncidentJournal,
    ListenerBookkeeping,
    NetworkRecord,
    PageErrorRecord,
    RequestTimingMap,
)


def _net(i: int) -> NetworkRecord:
    return NetworkRecord(
        ts_monotonic=float(i),
        ts_utc=f"2026-07-22T21:00:{i % 60:02d}Z",
        method="POST",
        host_category="aisandbox",
        route="/v1/flow/uploadImage",
        resource_type="xhr",
        status_or_failure="500",
        duration_ms=12,
    )


def _con(i: int) -> ConsoleRecord:
    return ConsoleRecord(
        ts_utc=f"2026-07-22T21:00:{i % 60:02d}Z",
        level="error",
        category="console_error",
        length=42,
        source_category="flow_app",
        line=10,
        column=3,
    )


def _err(i: int) -> PageErrorRecord:
    return PageErrorRecord(
        ts_utc=f"2026-07-22T21:00:{i % 60:02d}Z", error_class="TypeError", length=i
    )


class TestRings:
    def test_rings_enforce_exact_caps(self) -> None:
        j = IncidentJournal()
        for i in range(150):
            j.add_network(_net(i))
            j.add_console(_con(i))
        for i in range(80):
            j.add_page_error(_err(i))
        snap = j.snapshot()
        assert len(snap.network) == 100
        assert len(snap.console) == 100
        assert len(snap.page_errors) == 50
        # Oldest evicted, newest kept.
        assert snap.network[-1].ts_monotonic == 149.0
        assert snap.network[0].ts_monotonic == 50.0
        assert snap.page_errors[0].length == 30

    def test_events_after_freeze_are_ignored(self) -> None:
        """S17: late callbacks after detach/freeze must be no-ops."""
        j = IncidentJournal()
        j.add_network(_net(1))
        j.freeze()
        j.add_network(_net(2))
        j.add_console(_con(1))
        j.add_page_error(_err(1))
        snap = j.snapshot()
        assert len(snap.network) == 1
        assert len(snap.console) == 0
        assert len(snap.page_errors) == 0

    def test_snapshot_is_primitive_only(self) -> None:
        j = IncidentJournal()
        j.add_network(_net(1))
        j.add_console(_con(1))
        j.add_page_error(_err(1))
        snap = j.snapshot()

        def leaves(obj: object) -> list[object]:
            if isinstance(obj, dict):
                out: list[object] = []
                for v in obj.values():  # type: ignore[union-attr]
                    out.extend(leaves(v))
                return out
            if isinstance(obj, (list, tuple)):
                out = []
                for v in obj:  # type: ignore[union-attr]
                    out.extend(leaves(v))
                return out
            return [obj]

        all_records = [*snap.network, *snap.console, *snap.page_errors]
        for record in all_records:
            for leaf in leaves(asdict(record)):
                assert isinstance(leaf, (str, int, float, bool, type(None)))


class TestRequestTimingMap:
    def test_finish_returns_duration(self) -> None:
        m = RequestTimingMap()
        m.start("req-1", 100.0)
        assert m.finish("req-1", 100.25) == 250.0

    def test_unknown_key_returns_none(self) -> None:
        assert RequestTimingMap().finish("ghost", 1.0) is None

    def test_timing_map_bounded_under_10k_synthetic_events(self) -> None:
        """S18: long video polls must not grow the map unboundedly, and no
        Playwright-like object is ever retained — keys/values are primitives."""
        m = RequestTimingMap()
        for i in range(10_000):
            m.start(f"req-{i}", float(i))
            if i % 3 == 0:
                m.finish(f"req-{i}", float(i) + 0.5)
        assert m.size() <= 256

    def test_expired_entries_are_dropped(self) -> None:
        m = RequestTimingMap()
        m.start("old", 0.0)
        # 601 s later the entry is expired: correlation is unsafe → None.
        assert m.finish("old", 601.0) is None

    def test_at_cap_new_starts_are_dropped_not_evicting_live(self) -> None:
        m = RequestTimingMap()
        for i in range(256):
            m.start(f"live-{i}", 100.0)
        m.start("overflow", 100.1)
        assert m.finish("overflow", 100.2) is None  # never tracked
        assert m.finish("live-0", 100.3) is not None  # live entry survived


class TestListenerBookkeeping:
    def test_listener_bookkeeping_attach_idempotent(self) -> None:
        """S16: attach at most once, detach exactly once per target."""
        b = ListenerBookkeeping()
        assert b.mark_attached(111) is True
        assert b.mark_attached(111) is False
        assert b.mark_attached(222) is True
        assert b.mark_detached(111) is True
        assert b.mark_detached(111) is False
        assert b.mark_detached(999) is False
