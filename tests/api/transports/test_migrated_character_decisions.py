"""Decision logic of the flow.google.com character driver, with its I/O seams replaced.

The DOM gestures are proven live (tests/e2e/test_character_portability_bdd.py); what these
pin is what the driver DECIDES when a gesture's outcome is lost or late — the branches a
live run only hits by luck (council D1/D4/D6, branch review 2026-10-16)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports import migrated_character as mc
from gflow_cli.errors import ConfigurationError, MediaUploadRejectedError, UiSelectorDriftError

PID = "00000000-0000-4000-8000-000000000003"
EID = "00000000-0000-4000-8000-000000000004"


class _Locator:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.first = self

    def nth(self, _: int) -> _Locator:
        return self

    async def click(self, **_: Any) -> None:
        self.page.clicks += 1

    async def wait_for(self, **_: Any) -> None:
        return None


class _Page:
    def __init__(self, url: str = "https://flow.google.com/project/x/character") -> None:
        self.url = url
        self.clicks = 0
        self.wait_url_error: Exception | None = None

    async def goto(self, url: str, **_: Any) -> None:
        if "labs.google" not in self.url:
            self.url = url

    def locator(self, _: str) -> _Locator:
        return _Locator(self)

    async def wait_for_url(self, *_: Any, **__: Any) -> None:
        if self.wait_url_error:
            raise self.wait_url_error


def _create_reply() -> list[Any]:
    return [[PID, EID, None, [1, "Personnage sans titre", []]]]


class TestCreateFromFilesKeepsTheOrphanId:
    async def test_a_browser_timeout_after_the_create_names_the_entity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        page = _Page()
        page.wait_url_error = PlaywrightTimeoutError("Timeout 30000ms exceeded")

        async def replies(*_: Any, **__: Any) -> dict[str, Any]:
            return {"C4BZMd": _create_reply()}

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(UiSelectorDriftError) as exc:
            await mc.create_from_files(page, PID, name="Nora", portrait=tmp_path / "p.jpg")  # type: ignore[arg-type]
        assert EID in (exc.value.remediation_hint or "")
        assert "does not delete" in (exc.value.remediation_hint or "")

    async def test_a_project_served_elsewhere_is_refused_before_any_upload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        page = _Page(url="https://labs.google/fx/tools/flow/project/x")
        called: list[str] = []

        async def replies(*_: Any, **__: Any) -> dict[str, Any]:
            called.append("rpc")
            return {}

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(ConfigurationError, match="not ported yet"):
            await mc.create_from_files(page, PID, name="Nora", portrait=tmp_path / "p.jpg")  # type: ignore[arg-type]
        assert called == []


class TestWaitAfterCreate:
    async def test_a_stalled_editor_is_reloaded_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reloads: list[str] = []

        async def stalled(_: Any) -> None:
            raise PlaywrightTimeoutError("name field never visible")

        async def reload(*_: Any) -> None:
            reloads.append("reload")

        monkeypatch.setattr(mc, "_wait_form_loaded", stalled)
        monkeypatch.setattr(mc, "_open_editor", reload)
        await mc._wait_after_create(_Page(), PID, EID)  # type: ignore[arg-type]
        assert reloads == ["reload"]


class TestUploadBody:
    async def test_a_lost_reply_that_is_bound_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        attempts: list[int] = []

        async def no_reply(*_: Any, **__: Any) -> dict[str, Any]:
            attempts.append(1)
            return {}

        async def settled(*_: Any, **__: Any) -> tuple[str, ...]:
            return ("wf-portrait", "wf-body")

        monkeypatch.setattr(mc, "_rpc_replies", no_reply)
        monkeypatch.setattr(mc, "_settled_refs", settled)
        workflow, media = await mc._upload_body(_Page(), PID, EID, tmp_path / "b.jpg")  # type: ignore[arg-type]
        assert (workflow, media) == ("wf-body", None)
        assert attempts == [1]

    async def test_an_unbound_lost_reply_is_retried_once_then_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        attempts: list[int] = []

        async def no_reply(*_: Any, **__: Any) -> dict[str, Any]:
            attempts.append(1)
            return {}

        async def unbound(*_: Any, **__: Any) -> tuple[str, ...]:
            return ("wf-portrait",)

        async def reopen(*_: Any) -> None:
            return None

        monkeypatch.setattr(mc, "_rpc_replies", no_reply)
        monkeypatch.setattr(mc, "_settled_refs", unbound)
        monkeypatch.setattr(mc, "_open_editor", reopen)
        with pytest.raises(MediaUploadRejectedError):
            await mc._upload_body(_Page(), PID, EID, tmp_path / "b.jpg")  # type: ignore[arg-type]
        assert attempts == [1, 1]
