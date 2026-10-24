"""Decision logic of the flow.google.com character driver, with its I/O seams replaced.

The DOM gestures are proven live (tests/e2e/test_character_portability_bdd.py); what these
pin is what the driver DECIDES when a gesture's outcome is lost or late — the branches a
live run only hits by luck (council D1/D4/D6, branch review 2026-10-16)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli._cli_helpers import _exit_code_for  # pyright: ignore[reportPrivateUsage]
from gflow_cli.api.transports import migrated_character as mc
from gflow_cli.errors import (
    ConfigurationError,
    MediaUploadRejectedError,
    UiSelectorDriftError,
    WireFormatError,
)

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
        self.editor_error: Exception | None = None  # raised by the navigation to the editor

    async def goto(self, url: str, **_: Any) -> None:
        if self.editor_error and not url.endswith("/character"):
            raise self.editor_error
        if "labs.google" not in self.url:
            self.url = url

    def locator(self, _: str) -> _Locator:
        return _Locator(self)

    async def wait_for_url(self, *_: Any, **__: Any) -> None:
        if self.wait_url_error:
            raise self.wait_url_error


@pytest.fixture(autouse=True)
def _no_characters_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """The project read before a create: an empty project unless a test says otherwise."""

    async def none(*_: Any) -> set[str]:
        return set()

    monkeypatch.setattr(mc, "_character_ids", none)


def _create_reply() -> list[Any]:
    return [[PID, EID, None, [1, "Personnage sans titre", []]]]


class TestCreateFromFilesKeepsTheOrphanId:
    async def test_a_browser_timeout_after_the_create_names_the_entity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        page = _Page()
        page.editor_error = PlaywrightTimeoutError("Timeout 30000ms exceeded")

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


def _upload_reply(entity: str | None) -> list[Any]:
    workflow: list[Any] = ["wf-0", None, None, ["p.png", [1], None, None, "m-0"], PID]
    if entity is not None:
        workflow.append(entity)
    return [["m-0", PID, "wf-0", None, None, [[1]], [None, [], [720, 1280]]], workflow]


class TestLostCreateReply:
    """A user run (2026-10-17) logged `missing=['C4BZMd']` after 90 s while `maseQ` had
    answered: the create reply was lost, not the create. Recover before failing."""

    async def test_the_upload_reply_names_the_created_character(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        page = _Page()
        page.editor_error = PlaywrightTimeoutError("stop after recovery")

        async def replies(*_: Any, **__: Any) -> dict[str, Any]:
            return {"maseQ": _upload_reply(EID)}

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(UiSelectorDriftError) as exc:  # stops at the next step, by design
            await mc.create_from_files(page, PID, name="Nora", portrait=tmp_path / "p.png")  # type: ignore[arg-type]
        assert EID in (exc.value.remediation_hint or "")

    async def test_the_page_url_names_the_created_character(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        page = _Page()
        page.editor_error = PlaywrightTimeoutError("stop after recovery")

        async def replies(*_: Any, **__: Any) -> dict[str, Any]:
            page.url = f"https://flow.google.com/project/{PID}/character/{EID}"
            return {}

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(UiSelectorDriftError) as exc:
            await mc.create_from_files(page, PID, name="Nora", portrait=tmp_path / "p.png")  # type: ignore[arg-type]
        assert EID in (exc.value.remediation_hint or "")

    async def test_nothing_created_is_retried_once_then_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        attempts: list[int] = []

        async def replies(*_: Any, **__: Any) -> dict[str, Any]:
            attempts.append(1)
            return {"maseQ": _upload_reply(None)}  # uploaded, but into no character

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(WireFormatError, match="created no character"):
            await mc.create_from_files(_Page(), PID, name="Nora", portrait=tmp_path / "p.png")  # type: ignore[arg-type]
        assert attempts == [1, 1]


class TestBothCreateRepliesLost:
    """PR #987 review: with `C4BZMd` AND `maseQ` lost, a blind retry uploads the portrait
    again and makes a second character. The project is re-read first."""

    async def test_a_character_the_project_gained_is_adopted_not_recreated(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        page = _Page()
        page.editor_error = PlaywrightTimeoutError("stop after recovery")
        uploads: list[int] = []
        reads = iter([{"old"}, {"old", EID}])

        async def no_reply(*_: Any, **__: Any) -> dict[str, Any]:
            uploads.append(1)
            return {}

        async def ids(*_: Any) -> set[str]:
            return next(reads)

        monkeypatch.setattr(mc, "_rpc_replies", no_reply)
        monkeypatch.setattr(mc, "_character_ids", ids)
        with pytest.raises(UiSelectorDriftError) as exc:  # stops at the next step, by design
            await mc.create_from_files(page, PID, name="Nora", portrait=tmp_path / "p.png")  # type: ignore[arg-type]
        assert uploads == [1]
        assert EID in (exc.value.remediation_hint or "")

    async def test_two_new_characters_are_refused_not_added_to(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        reads = iter([set(), {"a", "b"}])

        async def no_reply(*_: Any, **__: Any) -> dict[str, Any]:
            return {}

        async def ids(*_: Any) -> set[str]:
            return next(reads)

        monkeypatch.setattr(mc, "_rpc_replies", no_reply)
        monkeypatch.setattr(mc, "_character_ids", ids)
        with pytest.raises(WireFormatError, match="gained 2 characters"):
            await mc.create_from_files(_Page(), PID, name="Nora", portrait=tmp_path / "p.png")  # type: ignore[arg-type]


class TestDriftBeforeTheCreate:
    async def test_a_browser_timeout_before_the_create_is_drift_with_nothing_orphaned(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """PR #987 review, must-fix 4: it ended in the unhandled-error path, exit 1."""

        async def chooser_timeout(*_: Any, **__: Any) -> dict[str, Any]:
            raise PlaywrightTimeoutError("Timeout 15000ms exceeded waiting for filechooser")

        monkeypatch.setattr(mc, "_rpc_replies", chooser_timeout)
        with pytest.raises(UiSelectorDriftError, match="before the character was created") as exc:
            await mc.create_from_files(_Page(), PID, name="Nora", portrait=tmp_path / "p.png")  # type: ignore[arg-type]
        assert _exit_code_for(exc.value) == 23
        assert "does not delete" not in (exc.value.remediation_hint or "")


class _VoicePage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.keyboard = self
        self.escapes = 0

    async def press(self, _key: str) -> None:
        self.escapes += 1

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    def locator(self, _: str) -> Any:
        return _VoiceLocator(self)


class _VoiceLocator(_Locator):
    def __init__(self, page: _Page) -> None:
        super().__init__(page)
        self.last = self

    async def inner_text(self) -> str:
        return "voice_selection"  # the add-voice button: no voice set yet

    async def fill(self, _: str) -> None:
        return None

    def locator(self, _: str) -> _VoiceLocator:
        return self

    async def is_enabled(self) -> bool:
        return True


class TestPickVoice:
    """Cold-profile measurement (2026-10-17, 1 in 4): the confirm never became
    actionable. Click it only once enabled; redo the pick once."""

    async def test_a_confirm_that_never_enables_is_retried_once_then_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ReferenceNotFoundError

        page = _VoicePage()

        async def one_hit(*_: Any, **__: Any) -> tuple[Any, list[int], int]:
            return _VoiceLocator(page), [0], 1

        async def never(*_: Any, **__: Any) -> bool:
            return False

        monkeypatch.setattr(mc, "_voice_hits", one_hit)
        monkeypatch.setattr(mc, "_confirm_enabled", never)
        with pytest.raises(ReferenceNotFoundError, match="2 attempts"):
            await mc._pick_voice(page, EID, "Aoede", timeout_s=1)  # type: ignore[arg-type]
        assert page.escapes == 2

    async def test_a_confirm_that_enables_on_the_second_pick_is_confirmed_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.transports import migrated_composer

        page = _VoicePage()
        states = iter([False, True])
        confirms: list[str] = []

        async def one_hit(*_: Any, **__: Any) -> tuple[Any, list[int], int]:
            return _VoiceLocator(page), [0], 1

        async def enabled(*_: Any, **__: Any) -> bool:
            return next(states)

        async def reply(_page: Any, _rpc: str, action: Any, **__: Any) -> Any:
            await action()
            confirms.append("confirm")
            return [[PID, EID, None, [1, "Nora", [[], [[None, "aoede"]]]]]]

        monkeypatch.setattr(mc, "_voice_hits", one_hit)
        monkeypatch.setattr(mc, "_confirm_enabled", enabled)
        monkeypatch.setattr(migrated_composer, "rpc_reply", reply)
        await mc._pick_voice(page, EID, "Aoede", timeout_s=1)  # type: ignore[arg-type]
        assert confirms == ["confirm"]


class _FieldLocator(_Locator):
    async def input_value(self) -> str:
        return "Personnage sans titre"

    async def fill(self, _: str) -> None:
        return None

    async def press(self, _: str) -> None:
        return None


class _FieldPage(_Page):
    def locator(self, _: str) -> Any:
        return _FieldLocator(self)


class TestPatchRetry:
    async def test_an_unanswered_edit_is_redone_once_after_a_reload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.transports import migrated_composer
        from gflow_cli.errors import TransportTimeoutError

        calls: list[str] = []
        reopened: list[str] = []

        async def reply(_page: Any, _rpc: str, action: Any, **__: Any) -> Any:
            calls.append("send")
            if len(calls) == 1:
                raise TransportTimeoutError(detail="no rzMKMb in 30s")
            await action()
            return [[PID, EID, None, [1, "Nora", []]]]

        async def reopen(*_: Any) -> None:
            reopened.append("reload")

        async def loaded(_: Any) -> None:
            return None

        monkeypatch.setattr(migrated_composer, "rpc_reply", reply)
        monkeypatch.setattr(mc, "_open_editor", reopen)
        monkeypatch.setattr(mc, "_wait_form_loaded", loaded)
        page = _FieldPage(url=f"https://flow.google.com/project/{PID}/character/{EID}")
        await mc.patch_character(page, PID, EID, name="Nora")  # type: ignore[arg-type]
        assert (calls, reopened) == (["send", "send"], ["reload"])


class _VoiceSetLocator(_VoiceLocator):
    """After the confirm, the voice button already reads the voice."""

    reads = 0

    async def inner_text(self) -> str:
        _VoiceSetLocator.reads += 1
        return "voice_selection" if _VoiceSetLocator.reads == 1 else "aoede"


class _VoiceSetPage(_VoicePage):
    def locator(self, _: str) -> Any:
        return _VoiceSetLocator(self)


class TestVoiceConfirmLostReply:
    """e2e 2026-10-17 (MCP import): the confirm click timed out while Flow had set the
    voice and closed the picker — the screenshot shows `aoede` on the button. A voice the
    editor shows is set; a run reporting failure there orphans a finished character."""

    async def test_a_lost_confirm_is_accepted_when_the_button_shows_the_voice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.transports import migrated_composer

        _VoiceSetLocator.reads = 0
        page = _VoiceSetPage()

        async def one_hit(*_: Any, **__: Any) -> tuple[Any, list[int], int]:
            return _VoiceLocator(page), [0], 1

        async def enabled(*_: Any, **__: Any) -> bool:
            return True

        async def lost(*_: Any, **__: Any) -> Any:
            raise PlaywrightTimeoutError("Locator.click: Timeout 15000ms exceeded.")

        monkeypatch.setattr(mc, "_voice_hits", one_hit)
        monkeypatch.setattr(mc, "_confirm_enabled", enabled)
        monkeypatch.setattr(migrated_composer, "rpc_reply", lost)
        await mc._pick_voice(page, EID, "Aoede", timeout_s=1)  # type: ignore[arg-type]

    async def test_a_lost_confirm_with_no_voice_shown_still_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.transports import migrated_composer

        page = _VoicePage()

        async def one_hit(*_: Any, **__: Any) -> tuple[Any, list[int], int]:
            return _VoiceLocator(page), [0], 1

        async def enabled(*_: Any, **__: Any) -> bool:
            return True

        async def lost(*_: Any, **__: Any) -> Any:
            raise PlaywrightTimeoutError("Locator.click: Timeout 15000ms exceeded.")

        monkeypatch.setattr(mc, "_voice_hits", one_hit)
        monkeypatch.setattr(mc, "_confirm_enabled", enabled)
        monkeypatch.setattr(migrated_composer, "rpc_reply", lost)
        monkeypatch.setattr(mc, "VOICE_SHOWN_WAIT_S", 0.3)
        with pytest.raises(PlaywrightTimeoutError):
            await mc._pick_voice(page, EID, "Aoede", timeout_s=1)  # type: ignore[arg-type]


class TestRouteNotFollowedAfterCreate:
    async def test_a_page_left_on_the_draft_is_not_a_failed_create(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """CodeRabbit, PR #987: a create recovered from the upload reply can leave the page
        on the draft, so the route wait times out. The character exists; the run goes on
        to open its editor (where this fake stops it) instead of failing at the wait."""
        page = _Page()
        page.wait_url_error = PlaywrightTimeoutError("route never followed")
        page.editor_error = PlaywrightTimeoutError("reached the editor")

        async def replies(*_: Any, **__: Any) -> dict[str, Any]:
            return {"C4BZMd": _create_reply()}

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(UiSelectorDriftError, match="reached the editor"):
            await mc.create_from_files(page, PID, name="Nora", portrait=tmp_path / "p.png")  # type: ignore[arg-type]


class TestPatchDrift:
    async def test_a_control_that_never_appears_is_drift_not_an_unhandled_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PR #987 review, must-fix 4: `patch_character` on its own (`character create`
        without `--image`, the MCP patch) let a Playwright timeout reach exit 1."""

        async def never_loads(*_: Any, **__: Any) -> None:
            raise PlaywrightTimeoutError("Timeout 30000ms exceeded")

        async def nothing(*_: Any, **__: Any) -> None:
            return None

        monkeypatch.setattr(mc, "_wait_form_loaded", never_loads)
        monkeypatch.setattr(mc, "_clear_overlays", nothing)
        monkeypatch.setattr(mc, "_settle", nothing)
        with pytest.raises(UiSelectorDriftError, match="while editing the character") as exc:
            await mc.patch_character(_Page(), PID, EID, name="Nora")  # type: ignore[arg-type]
        assert _exit_code_for(exc.value) == 23


class TestProjectReadRetry:
    async def test_an_unanswered_project_load_is_reloaded_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.transports import migrated_composer
        from gflow_cli.errors import TransportTimeoutError

        calls: list[int] = []

        async def flaky(*_: Any, **__: Any) -> Any:
            calls.append(1)
            if len(calls) == 1:
                raise TransportTimeoutError(detail="no Zzl0ze within 45s")
            return []

        monkeypatch.setattr(migrated_composer, "rpc_reply", flaky)
        monkeypatch.setattr(mc, "parse_project_read", lambda _: mc.ProjectRead((), (), {}))
        read = await mc.read_project(_Page(), PID)  # type: ignore[arg-type]
        assert calls == [1, 1] and read.characters == ()
