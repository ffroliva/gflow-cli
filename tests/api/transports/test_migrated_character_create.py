"""Building a character from local files on flow.google.com (#639, slice 3 of #987).

Two layers. The reply readers (``verify_patch``, ``created_entity``, ``bound_upload``) are
pure. The driver's decisions are pinned with its I/O seams replaced: what it does when a
gesture's outcome is lost or late, which a live run reaches only by chance. The gestures
themselves are covered by ``tests/e2e/test_character_create_from_files_e2e.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports import migrated_character as mc
from gflow_cli.api.transports import migrated_composer
from gflow_cli.errors import (
    ConfigurationError,
    MediaUploadRejectedError,
    ReferenceNotFoundError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
    exit_code_for,
)

PID = "00000000-0000-4000-8000-000000000003"
EID = "00000000-0000-4000-8000-000000000004"
_MEDIA, _WF = "00000000-0000-4000-8000-0000000000a1", "00000000-0000-4000-8000-0000000000a2"


def _upload_reply(entity: str | None) -> list[Any]:
    workflow: list[Any] = [_WF, None, None, ["p.jpg", [1], None, None, _MEDIA], PID]
    if entity is not None:
        workflow.append(entity)
    return [[_MEDIA, PID, _WF, None, None, [[1]], [None, [], [720, 1280]]], workflow]


def _create_reply() -> list[Any]:
    return [[PID, EID, None, [1, "Untitled character", []]]]


# --- pure reply readers --------------------------------------------------------------


class TestPatchVerification:
    """``rzMKMb`` echoes the whole entity; a write is confirmed from that echo."""

    @staticmethod
    def _reply(name: str = "Nora", voice: str | None = "aoede", pers: str | None = "calm") -> Any:
        parts: list[Any] = [[[_WF]], [[None, voice]] if voice else None]
        if pers is not None:
            parts.append(pers)
        return [[PID, EID, None, [1, name, parts], None, None, [1, 0], [2, 0]]]

    def test_an_echo_matching_every_requested_field_is_confirmed(self) -> None:
        char = mc.verify_patch(self._reply(), entity_id=EID, name="Nora", voice="Aoede")
        assert (char.display_name, char.voice, char.personality) == ("Nora", "Aoede", "calm")

    @pytest.mark.parametrize(
        ("field", "value"),
        [("name", "Laura"), ("voice", "Charon"), ("personality", "loud")],
    )
    def test_an_echo_that_differs_is_wire_format_error(self, field: str, value: str) -> None:
        with pytest.raises(WireFormatError) as exc:
            mc.verify_patch(self._reply(), entity_id=EID, **{field: value})
        assert exc.value.route == "batchexecute:rzMKMb"
        assert field in str(exc.value)

    @pytest.mark.parametrize(("field", "value"), [("name", "nora"), ("personality", "CALM")])
    def test_name_and_personality_are_compared_exactly(self, field: str, value: str) -> None:
        # Only the voice id changes case on the wire; a dropped case-only rename must not
        # read as applied.
        with pytest.raises(WireFormatError):
            mc.verify_patch(self._reply(), entity_id=EID, **{field: value})

    def test_an_echo_for_another_entity_is_wire_format_error(self) -> None:
        with pytest.raises(WireFormatError, match="names entity"):
            mc.verify_patch(
                self._reply(), entity_id="00000000-0000-4000-8000-0000000000aa", name="Nora"
            )

    @pytest.mark.parametrize("payload", [None, [], [[PID, EID, None, [7, "Nora", []]]]])
    def test_an_echo_with_no_character_is_wire_format_error(self, payload: Any) -> None:
        with pytest.raises(WireFormatError, match="no character"):
            mc.verify_patch(payload, entity_id=EID, name="Nora")


class TestCreateReplies:
    """``C4BZMd`` names the new entity; ``maseQ`` names the workflow and, when the upload
    was bound, the entity it went into."""

    def test_the_create_reply_names_the_new_entity(self) -> None:
        reply = [[PID, EID, None, [1, "Untitled character", []], None, None, [1], [1]]]
        assert mc.created_entity(reply, project_id=PID) == EID

    def test_a_create_reply_for_another_project_is_drift(self) -> None:
        reply = [["00000000-0000-4000-8000-0000000000ff", EID, None, [1, "x", []]]]
        with pytest.raises(WireFormatError) as exc:
            mc.created_entity(reply, project_id=PID)
        assert exc.value.route == "batchexecute:C4BZMd"

    def test_a_bound_upload_reply_yields_its_workflow_and_media(self) -> None:
        assert mc.bound_upload(_upload_reply(EID), entity_id=EID) == (_WF, _MEDIA)

    def test_an_upload_bound_to_nothing_is_refused(self) -> None:
        # The prompt box's add-menu Upload sends no binding: plain project media.
        with pytest.raises(WireFormatError, match="not bound"):
            mc.bound_upload(_upload_reply(None), entity_id=EID)

    def test_an_upload_bound_to_another_entity_is_refused(self) -> None:
        with pytest.raises(WireFormatError):
            mc.bound_upload(_upload_reply("00000000-0000-4000-8000-0000000000bb"), entity_id=EID)

    def test_an_upload_reply_with_no_media_is_drift(self) -> None:
        with pytest.raises(WireFormatError, match="no media"):
            mc.bound_upload([], entity_id=EID)


# --- the driver's decisions, with its I/O seams replaced ------------------------------


class _Locator:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.first = self
        self.last = self

    def nth(self, _: int) -> _Locator:
        return self

    def locator(self, _: str) -> _Locator:
        return self

    async def click(self, **_: Any) -> None:
        self.page.clicks += 1

    async def wait_for(self, **_: Any) -> None:
        return None

    async def is_visible(self) -> bool:
        return False

    async def count(self) -> int:
        return 0


class _Page:
    def __init__(self, url: str = f"https://flow.google.com/project/{PID}/character") -> None:
        self.url = url
        self.clicks = 0
        self.wait_url_error: Exception | None = None
        self.editor_error: Exception | None = None  # raised by the navigation to the editor
        self.redirect_to: str | None = None  # where every navigation lands, when set
        self.keyboard = self
        self.escapes = 0

    async def goto(self, url: str, **_: Any) -> None:
        if self.editor_error and not url.endswith("/character"):
            raise self.editor_error
        if self.redirect_to is not None:
            self.url = self.redirect_to
            return
        if "labs.google" not in self.url:
            self.url = url

    def locator(self, _: str) -> Any:
        return _Locator(self)

    async def wait_for_url(self, *_: Any, **__: Any) -> None:
        if self.wait_url_error:
            raise self.wait_url_error

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def press(self, _key: str) -> None:
        self.escapes += 1


@pytest.fixture(autouse=True)
def _no_characters_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """The project read before a create: an empty project unless a test says otherwise."""

    async def none(*_: Any) -> set[str]:
        return set()

    monkeypatch.setattr(mc, "_character_ids", none)
    monkeypatch.setattr(mc, "READ_SETTLE_S", 0.0)
    monkeypatch.setattr(mc, "_REREAD_S", 0.0)


async def _create(page: _Page, tmp_path: Path, **kwargs: Any) -> mc.CreatedCharacter:
    return await mc.create_from_files(
        page,  # type: ignore[arg-type]
        PID,
        name="Nora",
        portrait=tmp_path / "p.png",
        **kwargs,
    )


def _replies(reply: dict[str, Any], *, sent: tuple[str, ...] = ()) -> Any:
    async def replies(*_: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("sent") is not None:
            kwargs["sent"].update(sent)
        return reply

    return replies


_BOTH = ("C4BZMd", "maseQ")


class TestCreateFromFilesKeepsTheOrphanId:
    async def test_a_browser_timeout_after_the_create_names_the_entity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        page = _Page()
        page.editor_error = PlaywrightTimeoutError("Timeout 30000ms exceeded")
        monkeypatch.setattr(mc, "_rpc_replies", _replies({"C4BZMd": _create_reply()}))

        with pytest.raises(UiSelectorDriftError) as exc:
            await _create(page, tmp_path)
        assert EID in (exc.value.remediation_hint or "")
        assert "does not delete" in (exc.value.remediation_hint or "")

    async def test_a_flow_error_after_the_create_keeps_its_own_hint_too(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def reopen(*_: Any) -> None:
            return None

        async def refused(*_: Any, **__: Any) -> None:
            raise ReferenceNotFoundError(detail="no such voice", remediation_hint="pick another")

        monkeypatch.setattr(mc, "_rpc_replies", _replies({"C4BZMd": _create_reply()}))
        monkeypatch.setattr(mc, "_open_editor", reopen)
        monkeypatch.setattr(mc, "patch_character", refused)
        with pytest.raises(ReferenceNotFoundError) as exc:
            await _create(_Page(), tmp_path)
        hint = exc.value.remediation_hint or ""
        assert EID in hint and hint.endswith("pick another")

    async def test_a_project_served_elsewhere_is_refused_before_any_upload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        called: list[str] = []

        async def replies(*_: Any, **__: Any) -> dict[str, Any]:
            called.append("rpc")
            return {}

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(ConfigurationError, match="not ported"):
            await _create(_Page(url="https://labs.google/fx/tools/flow/project/x"), tmp_path)
        assert called == []


class TestCreateFromFilesHappyPath:
    async def test_portrait_name_body_and_voice_are_set_in_that_order(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        steps: list[str] = []

        async def reopen(*_: Any) -> None:
            steps.append("editor")

        async def patch(*_: Any, **fields: Any) -> None:
            steps.append("patch:" + ",".join(k for k, v in fields.items() if v is not None))

        async def body(*_: Any) -> tuple[str, str | None]:
            steps.append("body")
            return "wf-body", "m-body"

        replies = {"C4BZMd": _create_reply(), "maseQ": _upload_reply(EID)}
        monkeypatch.setattr(mc, "_rpc_replies", _replies(replies))
        monkeypatch.setattr(mc, "_open_editor", reopen)
        monkeypatch.setattr(mc, "patch_character", patch)
        monkeypatch.setattr(mc, "_upload_body", body)

        created = await _create(
            _Page(), tmp_path, body=tmp_path / "b.png", personality="calm", voice="Aoede"
        )

        assert steps == ["editor", "patch:name,personality", "body", "patch:voice"]
        assert created == mc.CreatedCharacter(EID, (_WF, "wf-body"), (_MEDIA, "m-body"))

    async def test_a_lost_upload_reply_reads_the_portrait_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def nothing(*_: Any, **__: Any) -> None:
            return None

        async def settled(*_: Any, **__: Any) -> tuple[str, ...]:
            return ("wf-portrait",)

        monkeypatch.setattr(mc, "_rpc_replies", _replies({"C4BZMd": _create_reply()}))
        monkeypatch.setattr(mc, "_open_editor", nothing)
        monkeypatch.setattr(mc, "patch_character", nothing)
        monkeypatch.setattr(mc, "_settled_refs", settled)

        created = await _create(_Page(), tmp_path)
        assert created == mc.CreatedCharacter(EID, ("wf-portrait",), ("",))

    async def test_a_portrait_never_bound_is_refused_with_the_orphan_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def nothing(*_: Any, **__: Any) -> None:
            return None

        async def unbound(*_: Any, **__: Any) -> tuple[str, ...]:
            return ()

        monkeypatch.setattr(mc, "_rpc_replies", _replies({"C4BZMd": _create_reply()}))
        monkeypatch.setattr(mc, "_open_editor", nothing)
        monkeypatch.setattr(mc, "patch_character", nothing)
        monkeypatch.setattr(mc, "_settled_refs", unbound)

        with pytest.raises(MediaUploadRejectedError) as exc:
            await _create(_Page(), tmp_path)
        assert EID in (exc.value.remediation_hint or "")


class TestUploadBody:
    async def test_a_reply_bound_to_the_character_is_returned(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(mc, "_rpc_replies", _replies({"maseQ": _upload_reply(EID)}))
        got = await mc._upload_body(_Page(), PID, EID, tmp_path / "b.jpg")  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert got == (_WF, _MEDIA)

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
        got = await mc._upload_body(_Page(), PID, EID, tmp_path / "b.jpg")  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert got == ("wf-body", None)
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
            await mc._upload_body(_Page(), PID, EID, tmp_path / "b.jpg")  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert attempts == [1, 1]


class TestLostCreateReply:
    """``C4BZMd`` can go missing while ``maseQ`` answered: the reply was lost, not the
    create. The driver recovers the id before failing."""

    async def test_the_upload_reply_names_the_created_character(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        page = _Page()
        page.editor_error = PlaywrightTimeoutError("stop after recovery")
        monkeypatch.setattr(mc, "_rpc_replies", _replies({"maseQ": _upload_reply(EID)}))

        with pytest.raises(UiSelectorDriftError) as exc:  # stops at the next step, by design
            await _create(page, tmp_path)
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
            await _create(page, tmp_path)
        assert EID in (exc.value.remediation_hint or "")

    async def test_an_unbound_upload_with_no_create_reply_is_not_uploaded_again(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        attempts: list[int] = []

        async def replies(*_: Any, **kwargs: Any) -> dict[str, Any]:
            attempts.append(1)
            kwargs["sent"].update(_BOTH)
            return {"maseQ": _upload_reply(None)}  # uploaded, but into no character

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(WireFormatError, match="no reply proved") as exc:
            await _create(_Page(), tmp_path)
        assert attempts == [1]
        assert (exc.value.retryable, exc.value.next_action) == (False, "reconcile")

    async def test_nothing_sent_is_retried_once_then_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        attempts: list[int] = []

        async def replies(*_: Any, **__: Any) -> dict[str, Any]:
            attempts.append(1)
            return {}  # no request left the page

        monkeypatch.setattr(mc, "_rpc_replies", replies)
        with pytest.raises(WireFormatError, match="created no character"):
            await _create(_Page(), tmp_path)
        assert attempts == [1, 1]


class TestBothCreateRepliesLost:
    """With ``C4BZMd`` and ``maseQ`` both lost after the requests left the page, a retry
    could make a second character, and a character the project gained cannot be tied to
    this upload. The run stops, naming that character, and edits nothing."""

    async def test_a_character_the_project_gained_is_named_not_edited(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        uploads: list[int] = []
        reads = iter([{"old"}, {"old", EID}])
        edited: list[str] = []

        async def no_reply(*_: Any, **kwargs: Any) -> dict[str, Any]:
            uploads.append(1)
            kwargs["sent"].update(_BOTH)
            return {}

        async def ids(*_: Any) -> set[str]:
            return next(reads)

        async def editor(*_: Any) -> None:
            edited.append("editor")

        monkeypatch.setattr(mc, "_rpc_replies", no_reply)
        monkeypatch.setattr(mc, "_character_ids", ids)
        monkeypatch.setattr(mc, "_open_editor", editor)
        with pytest.raises(WireFormatError) as exc:
            await _create(_Page(), tmp_path)
        assert (uploads, edited) == ([1], [])
        assert EID in (exc.value.remediation_hint or "")
        assert exc.value.next_action == "reconcile"

    async def test_two_new_characters_are_refused_not_added_to(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        reads = iter([set(), {"a", "b"}])

        async def ids(*_: Any) -> set[str]:
            return next(reads)

        monkeypatch.setattr(mc, "_rpc_replies", _replies({}, sent=_BOTH))
        monkeypatch.setattr(mc, "_character_ids", ids)
        with pytest.raises(WireFormatError, match="gained 2 characters"):
            await _create(_Page(), tmp_path)

    async def test_no_new_character_within_the_settle_window_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        uploads: list[int] = []

        async def no_reply(*_: Any, **kwargs: Any) -> dict[str, Any]:
            uploads.append(1)
            kwargs["sent"].update(_BOTH)
            return {}

        monkeypatch.setattr(mc, "_rpc_replies", no_reply)
        with pytest.raises(WireFormatError, match="no reply proved") as exc:
            await _create(_Page(), tmp_path)
        assert uploads == [1]
        assert "may have created a character" in (exc.value.remediation_hint or "")


class TestDriftBeforeTheCreate:
    async def test_a_browser_timeout_before_the_create_is_drift_with_nothing_orphaned(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def chooser_timeout(*_: Any, **__: Any) -> dict[str, Any]:
            raise PlaywrightTimeoutError("Timeout 15000ms exceeded waiting for filechooser")

        monkeypatch.setattr(mc, "_rpc_replies", chooser_timeout)
        with pytest.raises(UiSelectorDriftError, match="before the character was created") as exc:
            await _create(_Page(), tmp_path)
        assert exit_code_for(exc.value) == 23
        assert "does not delete" not in (exc.value.remediation_hint or "")


class TestRouteNotFollowedAfterCreate:
    async def test_a_page_left_on_the_draft_is_not_a_failed_create(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A create recovered from the upload reply can leave the page on the draft, so the
        route wait times out. The character exists; the run goes on to open its editor
        (where this fake stops it) instead of failing at the wait."""
        page = _Page()
        page.wait_url_error = PlaywrightTimeoutError("route never followed")
        page.editor_error = PlaywrightTimeoutError("reached the editor")
        monkeypatch.setattr(mc, "_rpc_replies", _replies({"C4BZMd": _create_reply()}))

        with pytest.raises(UiSelectorDriftError, match="reached the editor"):
            await _create(page, tmp_path)


# --- voice picker ----------------------------------------------------------------------


class _VoiceLocator(_Locator):
    def __init__(self, page: _Page, shown: list[str]) -> None:
        super().__init__(page)
        self.shown = shown

    async def inner_text(self, **_: Any) -> str:
        return self.shown.pop(0) if len(self.shown) > 1 else self.shown[0]

    async def fill(self, _: str) -> None:
        return None

    def locator(self, _: str) -> _VoiceLocator:
        return self

    def nth(self, _: int) -> _VoiceLocator:
        return self

    async def is_enabled(self) -> bool:
        return True


class _VoicePage(_Page):
    """The voice button reads ``shown`` in turn; the last value then sticks."""

    def __init__(self, *shown: str) -> None:
        super().__init__()
        self.shown = list(shown) or ["voice_selection"]

    def locator(self, _: str) -> Any:
        return _VoiceLocator(self, self.shown)


def _one_hit(page: _VoicePage) -> Any:
    async def one_hit(*_: Any, **__: Any) -> tuple[Any, list[int]]:
        return _VoiceLocator(page, ["Aoede\nFemale, breezy"]), [0]

    return one_hit


def _enabled(*states: bool) -> Any:
    it = iter(states)

    async def enabled(*_: Any, **__: Any) -> bool:
        return next(it)

    return enabled


class TestPickVoice:
    async def test_a_voice_already_shown_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = _VoicePage("Aoede\nFemale, breezy")
        await mc._pick_voice(page, EID, "aoede", timeout_s=1)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert page.clicks == 0

    async def test_a_voice_the_picker_does_not_show_is_drift_exit_23(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = _VoicePage()

        async def no_hit(*_: Any, **__: Any) -> tuple[Any, list[int]]:
            return _VoiceLocator(page, page.shown), []

        monkeypatch.setattr(mc, "_voice_hits", no_hit)
        with pytest.raises(UiSelectorDriftError, match="no single settled option"):
            await mc._pick_voice(page, EID, "Nobody", timeout_s=1)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert page.escapes == 1

    async def test_a_confirm_that_never_enables_is_retried_once_then_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = _VoicePage()
        monkeypatch.setattr(mc, "_voice_hits", _one_hit(page))
        monkeypatch.setattr(mc, "_confirm_enabled", _enabled(False, False))
        with pytest.raises(ReferenceNotFoundError, match="2 attempts"):
            await mc._pick_voice(page, EID, "Aoede", timeout_s=1)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert page.escapes == 2

    async def test_a_confirm_that_enables_on_the_second_pick_is_confirmed_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = _VoicePage()
        confirms: list[str] = []

        async def reply(_page: Any, _rpc: str, action: Any, **__: Any) -> Any:
            await action()
            confirms.append("confirm")
            return [[PID, EID, None, [1, "Nora", [[], [[None, "aoede"]]]]]]

        monkeypatch.setattr(mc, "_voice_hits", _one_hit(page))
        monkeypatch.setattr(mc, "_confirm_enabled", _enabled(False, True))
        monkeypatch.setattr(migrated_composer, "rpc_reply", reply)
        await mc._pick_voice(page, EID, "Aoede", timeout_s=1)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert confirms == ["confirm"]

    async def test_a_lost_confirm_is_accepted_when_the_button_shows_the_voice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The confirm click can time out while Flow has set the voice and closed the
        picker. A voice the editor's button shows is set."""
        page = _VoicePage("voice_selection", "voice_selection", "aoede")

        async def lost(*_: Any, **__: Any) -> Any:
            raise PlaywrightTimeoutError("Locator.click: Timeout 15000ms exceeded.")

        monkeypatch.setattr(mc, "_voice_hits", _one_hit(page))
        monkeypatch.setattr(mc, "_confirm_enabled", _enabled(True))
        monkeypatch.setattr(migrated_composer, "rpc_reply", lost)
        await mc._pick_voice(page, EID, "Aoede", timeout_s=1)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    async def test_a_lost_confirm_with_no_voice_shown_still_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = _VoicePage()

        async def lost(*_: Any, **__: Any) -> Any:
            raise PlaywrightTimeoutError("Locator.click: Timeout 15000ms exceeded.")

        monkeypatch.setattr(mc, "_voice_hits", _one_hit(page))
        monkeypatch.setattr(mc, "_confirm_enabled", _enabled(True))
        monkeypatch.setattr(migrated_composer, "rpc_reply", lost)
        monkeypatch.setattr(mc, "VOICE_SHOWN_WAIT_S", 0.3)
        with pytest.raises(PlaywrightTimeoutError):
            await mc._pick_voice(page, EID, "Aoede", timeout_s=1)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]


# --- text fields ----------------------------------------------------------------------


class _FieldLocator(_Locator):
    async def input_value(self) -> str:
        return "Untitled character"

    async def fill(self, _: str) -> None:
        return None

    async def press(self, _: str) -> None:
        return None


class _FieldPage(_Page):
    def locator(self, _: str) -> Any:
        return _FieldLocator(self)


class TestPatchCharacter:
    async def test_an_unanswered_edit_is_redone_once_after_a_reload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    async def test_an_edit_unanswered_twice_is_a_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def silent(*_: Any, **__: Any) -> Any:
            raise TransportTimeoutError(detail="no rzMKMb in 30s")

        async def nothing(*_: Any, **__: Any) -> None:
            return None

        monkeypatch.setattr(migrated_composer, "rpc_reply", silent)
        monkeypatch.setattr(mc, "_open_editor", nothing)
        monkeypatch.setattr(mc, "_wait_form_loaded", nothing)
        with pytest.raises(TransportTimeoutError):
            await mc.patch_character(_FieldPage(), PID, EID, personality="calm")  # type: ignore[arg-type]

    async def test_a_field_already_showing_the_value_sends_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def never(*_: Any, **__: Any) -> Any:
            raise AssertionError("an unchanged field must not wait for a reply")

        async def nothing(*_: Any, **__: Any) -> None:
            return None

        monkeypatch.setattr(migrated_composer, "rpc_reply", never)
        monkeypatch.setattr(mc, "_wait_form_loaded", nothing)
        await mc.patch_character(_FieldPage(), PID, EID, name="Untitled character")  # type: ignore[arg-type]

    async def test_a_control_that_never_appears_is_drift_not_an_unhandled_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def never_loads(*_: Any, **__: Any) -> None:
            raise PlaywrightTimeoutError("Timeout 30000ms exceeded")

        monkeypatch.setattr(mc, "_wait_form_loaded", never_loads)
        with pytest.raises(UiSelectorDriftError, match="while editing the character") as exc:
            await mc.patch_character(_Page(), PID, EID, name="Nora")  # type: ignore[arg-type]
        assert exit_code_for(exc.value) == 23


# --- the gestures' own waits ------------------------------------------------------------


def _body(*frames: tuple[str, Any]) -> str:
    import json

    rows = [
        ["wrb.fr", rid, json.dumps(payload), None, None, None, "generic"] for rid, payload in frames
    ]
    return ")]}'\n" + json.dumps(rows) + "\n"


class _Response:
    def __init__(self, url: str, text: str | Exception) -> None:
        self.url = url
        self._text = text

    async def text(self) -> str:
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


class _WirePage:
    """Delivers ``responses`` to the registered listener when the action runs."""

    def __init__(self, *responses: _Response) -> None:
        self.responses = responses
        self.listener: Any = None
        self.removed = False

    def on(self, event: str, listener: Any) -> None:
        if event == "response":
            self.listener = listener

    def remove_listener(self, _event: str, _listener: Any) -> None:
        self.removed = True

    async def act(self) -> None:
        for response in self.responses:
            await self.listener(response)


class TestRpcReplies:
    async def test_the_first_reply_of_each_rpcid_is_collected(self) -> None:
        page = _WirePage(
            _Response("https://x/other", "ignored"),
            _Response("https://x/batchexecute", RuntimeError("body gone")),
            _Response("https://x/batchexecute", _body(("maseQ", [1]), ("zzz", [9]))),
            _Response("https://x/batchexecute", _body(("C4BZMd", [2]), ("maseQ", [3]))),
        )
        got = await mc._rpc_replies(page, ("C4BZMd", "maseQ"), page.act, timeout_s=1)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert got == {"maseQ": [1], "C4BZMd": [2]}
        assert page.removed

    async def test_a_missing_reply_is_absent_not_raised_after_the_grace(self) -> None:
        page = _WirePage(_Response("https://x/batchexecute", _body(("maseQ", [1]))))
        got = await mc._rpc_replies(  # pyright: ignore[reportPrivateUsage]
            page,  # type: ignore[arg-type]
            ("C4BZMd", "maseQ"),
            page.act,
            timeout_s=1,
            grace_s=0.01,
        )
        assert got == {"maseQ": [1]}

    async def test_nothing_at_all_waits_out_the_timeout_and_returns_empty(self) -> None:
        page = _WirePage()
        got = await mc._rpc_replies(page, ("maseQ",), page.act, timeout_s=0.01)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert got == {}


class _NameBox:
    def __init__(self, *values: str) -> None:
        self.values = list(values)
        self.first = self

    async def wait_for(self, **_: Any) -> None:
        return None

    async def input_value(self) -> str:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


class _FormPage(_Page):
    def __init__(self, box: _NameBox) -> None:
        super().__init__()
        self.box = box

    def locator(self, _: str) -> Any:
        return self.box


class TestEditorWaits:
    async def test_the_form_is_loaded_once_the_name_is_filled(self) -> None:
        await mc._wait_form_loaded(_FormPage(_NameBox("", "", "Untitled")), timeout_s=1)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    async def test_a_name_that_never_loads_is_drift(self) -> None:
        with pytest.raises(UiSelectorDriftError, match="never loaded a name"):
            await mc._wait_form_loaded(_FormPage(_NameBox("")), timeout_s=0.5)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    async def test_a_page_that_never_idles_is_driven_anyway(self) -> None:
        class _Busy(_Page):
            async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
                raise PlaywrightTimeoutError("never idle")

        await mc._settle(_Busy(), timeout_s=0.01)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    async def test_a_confirm_is_reported_enabled_or_not(self) -> None:
        class _Button:
            def __init__(self, *states: bool) -> None:
                self.states = list(states)

            async def is_enabled(self) -> bool:
                return self.states.pop(0)

        assert await mc._confirm_enabled(_Button(False, True), timeout_s=1)  # pyright: ignore[reportPrivateUsage]
        assert not await mc._confirm_enabled(_Button(False, False), timeout_s=0.5)  # pyright: ignore[reportPrivateUsage]


class _Options:
    """Each read returns the next snapshot; the last one then sticks."""

    def __init__(self, *snapshots: list[str]) -> None:
        self.snapshots = list(snapshots)

    async def all_inner_texts(self) -> list[str]:
        return self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]


class _Dialog:
    def __init__(self, *snapshots: list[str]) -> None:
        self.options = _Options(*snapshots)

    def locator(self, _: str) -> _Options:
        return self.options


class TestVoiceHits:
    async def test_one_option_whose_line_is_the_voice_is_a_hit(self) -> None:
        dialog = _Dialog(["Aoede\nFemale, breezy", "Aoedeish\nother", "Charon\nMale"])
        _, hits = await mc._voice_hits(dialog, "aoede", timeout_s=1)  # pyright: ignore[reportPrivateUsage]
        assert hits == [0]

    async def test_the_index_comes_from_the_settled_filtered_list(self) -> None:
        # The unfiltered list names Aoede at 2; once the search has filtered it, at 0.
        unfiltered = ["Charon\nMale", "Puck\nMale", "Aoede\nFemale"]
        dialog = _Dialog(unfiltered, ["Aoede\nFemale"])
        _, hits = await mc._voice_hits(dialog, "Aoede", timeout_s=1)  # pyright: ignore[reportPrivateUsage]
        assert hits == [0]

    async def test_a_list_that_never_settles_is_no_hit(self) -> None:
        dialog = _Dialog(*[[f"Aoede\n{i}", *["x"] * i] for i in range(20)])
        _, hits = await mc._voice_hits(dialog, "Aoede", timeout_s=0.5)  # pyright: ignore[reportPrivateUsage]
        assert hits == []

    async def test_no_matching_line_is_no_hit(self) -> None:
        _, hits = await mc._voice_hits(_Dialog(["Charon\nMale"]), "Aoede", timeout_s=0.6)  # pyright: ignore[reportPrivateUsage]
        assert hits == []


class TestSettledRefs:
    async def test_the_read_is_repeated_until_the_images_show(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.character import Character

        def char(*wfs: str) -> mc.ProjectRead:
            c = Character(
                entity_id=EID,
                display_name="Nora",
                project_id=PID,
                workflow_ids=wfs,
                voice=None,
                personality=None,
                thumbnail_media_id=None,
            )
            return mc.ProjectRead(characters=(c,), voices=())

        reads = iter([char("wf-0"), char("wf-0", "wf-1")])

        async def read(*_: Any, **__: Any) -> mc.ProjectRead:
            return next(reads)

        monkeypatch.setattr(mc, "read_project", read)
        monkeypatch.setattr(mc, "READ_SETTLE_S", 5.0)
        refs = await mc._settled_refs(_Page(), PID, EID, want=2)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert refs == ("wf-0", "wf-1")

    async def test_a_character_absent_from_the_read_has_no_refs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def read(*_: Any, **__: Any) -> mc.ProjectRead:
            return mc.ProjectRead(characters=(), voices=())

        monkeypatch.setattr(mc, "read_project", read)
        monkeypatch.setattr(mc, "READ_SETTLE_S", 0.0)
        assert await mc._settled_refs(_Page(), PID, EID, want=1) == ()  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]


# --- review round on #1405 ------------------------------------------------------------


class _DelayedWirePage(_WirePage):
    """Delivers ``(delay_s, response)`` pairs in the background, as a slow upload would."""

    def __init__(self, *timed: tuple[float, _Response]) -> None:
        super().__init__()
        self.timed = timed
        self.tasks: list[Any] = []

    async def act(self) -> None:
        import asyncio

        async def later(delay: float, response: _Response) -> None:
            await asyncio.sleep(delay)
            await self.listener(response)

        self.tasks = [asyncio.ensure_future(later(d, r)) for d, r in self.timed]


def _refusal_body(rpcid: str) -> str:
    import json

    row = ["wrb.fr", rpcid, None, None, None, [3, None, [["x", ["IMAGE_REJECTED"]]]], "generic"]
    return ")]}'\n" + json.dumps([row]) + "\n"


class TestUploadBudget:
    async def test_a_create_reply_first_does_not_cut_the_upload_short(self) -> None:
        """``C4BZMd`` can answer before a large portrait finishes uploading; the upload's
        reply is still waited for, past the grace, on the full budget."""
        page = _DelayedWirePage(
            (0.0, _Response("https://x/batchexecute", _body(("C4BZMd", [1])))),
            (0.2, _Response("https://x/batchexecute", _body(("maseQ", [2])))),
        )
        got = await mc._rpc_replies(  # pyright: ignore[reportPrivateUsage]
            page,  # type: ignore[arg-type]
            ("C4BZMd", "maseQ"),
            page.act,
            timeout_s=5,
            grace_s=0.01,
        )
        assert got == {"C4BZMd": [1], "maseQ": [2]}


class TestRefusedRpc:
    async def test_an_error_frame_ends_the_wait_with_flows_reason(self) -> None:
        import time

        page = _WirePage(_Response("https://x/batchexecute", _refusal_body("maseQ")))
        started = time.monotonic()
        got = await mc._rpc_replies(page, ("C4BZMd", "maseQ"), page.act, timeout_s=5)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert time.monotonic() - started < 1
        assert isinstance(got["maseQ"], mc.RpcError)

    async def test_a_refused_portrait_upload_is_not_retried_and_says_why(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from gflow_cli.api.transports.batchexecute import rpc_errors

        attempts: list[int] = []
        err = rpc_errors(_refusal_body("maseQ"))[0]

        async def refused(*_: Any, **__: Any) -> dict[str, Any]:
            attempts.append(1)
            return {"maseQ": err}

        monkeypatch.setattr(mc, "_rpc_replies", refused)
        with pytest.raises(MediaUploadRejectedError, match="refused maseQ") as exc:
            await _create(_Page(), tmp_path)
        assert attempts == [1]
        assert "IMAGE_REJECTED" in str(exc.value)
        assert exc.value.retryable is False

    async def test_a_refused_create_is_raised_with_its_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from gflow_cli.api.transports.batchexecute import rpc_errors

        err = rpc_errors(_refusal_body("C4BZMd"))[0]
        monkeypatch.setattr(mc, "_rpc_replies", _replies({"C4BZMd": err}))
        with pytest.raises(WireFormatError, match="refused C4BZMd"):
            await _create(_Page(), tmp_path)


class _DialogPage(_Page):
    """``[role='dialog']`` counts ``before`` until the file is chosen, then ``after``."""

    def __init__(self, before: int, after: int) -> None:
        super().__init__()
        self.counts = [before, after]

    def locator(self, selector: str) -> Any:
        page = self

        class _Counted(_Locator):
            async def count(self) -> int:
                return page.counts.pop(0) if len(page.counts) > 1 else page.counts[0]

        return _Counted(self) if selector == mc.DIALOG else _Locator(self)


class TestUploadTermsDialog:
    async def test_a_dialog_with_no_reply_is_the_terms_dialog_not_a_retry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        attempts: list[int] = []

        async def silent(*_: Any, **__: Any) -> dict[str, Any]:
            attempts.append(1)
            return {}

        monkeypatch.setattr(mc, "_rpc_replies", silent)
        with pytest.raises(MediaUploadRejectedError, match="upload-terms") as exc:
            await _create(_DialogPage(before=0, after=1), tmp_path)
        assert attempts == [1]
        assert exc.value.remediation_hint == mc.UPLOAD_TERMS_REMEDIATION

    async def test_no_new_dialog_is_not_blamed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(mc, "_rpc_replies", _replies({}))
        with pytest.raises(WireFormatError, match="created no character"):
            await _create(_DialogPage(before=1, after=1), tmp_path)


class TestNoBlindRetryAfterTheUpload:
    async def test_a_create_reply_for_another_project_says_a_character_may_exist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        other = [["00000000-0000-4000-8000-0000000000ff", EID, None, [1, "x", []]]]
        monkeypatch.setattr(mc, "_rpc_replies", _replies({"C4BZMd": other}))
        with pytest.raises(WireFormatError) as exc:
            await _create(_Page(), tmp_path)
        assert "may have created a character" in (exc.value.remediation_hint or "")
        assert (exc.value.retryable, exc.value.next_action) == (False, "reconcile")

    async def test_a_read_that_times_out_after_the_upload_is_not_retryable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls = iter([set()])

        async def ids(*_: Any) -> set[str]:
            try:
                return next(calls)
            except StopIteration:
                raise PlaywrightTimeoutError("Timeout 45000ms exceeded") from None

        monkeypatch.setattr(mc, "_rpc_replies", _replies({}, sent=_BOTH))
        monkeypatch.setattr(mc, "_character_ids", ids)
        with pytest.raises(UiSelectorDriftError, match="after the portrait upload") as exc:
            await _create(_Page(), tmp_path)
        assert exc.value.retryable is False


class TestOrphanIsNotRetryable:
    async def test_a_timeout_after_the_create_says_reconcile_not_retry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def nothing(*_: Any) -> None:
            return None

        async def unanswered(*_: Any, **__: Any) -> None:
            raise TransportTimeoutError(detail="no rzMKMb in 30s")

        monkeypatch.setattr(mc, "_rpc_replies", _replies({"C4BZMd": _create_reply()}))
        monkeypatch.setattr(mc, "_open_editor", nothing)
        monkeypatch.setattr(mc, "patch_character", unanswered)
        with pytest.raises(TransportTimeoutError) as exc:
            await _create(_Page(), tmp_path)
        assert (exc.value.retryable, exc.value.next_action) == (False, "reconcile")
        assert EID in (exc.value.remediation_hint or "")


class TestDraftLanding:
    async def test_a_sign_in_landing_is_diagnosed_not_called_unported(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from gflow_cli.errors import AuthExpiredError

        page = _Page()
        page.redirect_to = "https://accounts.google.com/v3/signin"

        async def landing(landed: Any, **__: Any) -> None:
            assert landed.url.startswith("https://accounts.google.com/")
            raise AuthExpiredError(detail="landed on the Google sign-in page")

        async def footer_missing(*_: Any, **__: Any) -> dict[str, Any]:
            raise PlaywrightTimeoutError("waiting for footer upload")

        monkeypatch.setattr(mc, "_create_attempt", footer_missing)
        monkeypatch.setattr(mc, "raise_if_known_landing", landing)
        with pytest.raises(AuthExpiredError):
            await _create(page, tmp_path)


class TestBrowserErrors:
    def test_a_closed_page_is_a_closed_session_not_drift(self) -> None:
        from playwright.async_api import Error as PlaywrightError

        from gflow_cli.errors import BrowserSessionClosedError

        exc = PlaywrightError("Target page, context or browser has been closed")
        assert isinstance(mc._browser_error(exc, "x"), BrowserSessionClosedError)  # pyright: ignore[reportPrivateUsage]

    def test_a_network_failure_is_a_network_error(self) -> None:
        from playwright.async_api import Error as PlaywrightError

        from gflow_cli.errors import NetworkError

        exc = PlaywrightError("page.goto: net::ERR_INTERNET_DISCONNECTED at https://x")
        assert isinstance(mc._browser_error(exc, "x"), NetworkError)  # pyright: ignore[reportPrivateUsage]

    def test_a_timeout_is_drift(self) -> None:
        exc = PlaywrightTimeoutError("Timeout 30000ms exceeded")
        assert exit_code_for(mc._browser_error(exc, "x")) == 23  # pyright: ignore[reportPrivateUsage]


class _TypedBox(_FieldLocator):
    typed: list[str] = []

    async def fill(self, value: str) -> None:
        _TypedBox.typed.append(value)


class TestPersonalityLineEnds:
    async def test_crlf_is_typed_and_compared_as_lf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _TypedBox.typed = []

        class _TypingPage(_Page):
            def locator(self, _: str) -> Any:
                return _TypedBox(self)

        async def reply(_page: Any, _rpc: str, action: Any, **__: Any) -> Any:
            await action()
            return [[PID, EID, None, [1, "Nora", [[], None, "calm\nprecise"]]]]

        async def loaded(_: Any) -> None:
            return None

        monkeypatch.setattr(migrated_composer, "rpc_reply", reply)
        monkeypatch.setattr(mc, "_wait_form_loaded", loaded)
        await mc.patch_character(_TypingPage(), PID, EID, personality="calm\r\nprecise")  # type: ignore[arg-type]
        assert _TypedBox.typed == ["calm\nprecise"]


# --- CodeRabbit round on #1405 ---------------------------------------------------------


class _RequestWirePage(_WirePage):
    """Fires ``requests`` (URLs) at the request listener, then the responses."""

    def __init__(self, requests: tuple[str, ...], *responses: _Response) -> None:
        super().__init__(*responses)
        self.requests = requests
        self.request_listener: Any = None

    def on(self, event: str, listener: Any) -> None:
        if event == "request":
            self.request_listener = listener
        else:
            super().on(event, listener)

    async def act(self) -> None:
        class _Request:
            def __init__(self, url: str) -> None:
                self.url = url

        for url in self.requests:
            self.request_listener(_Request(url))
        await super().act()


class TestRequestDispatch:
    async def test_the_requests_that_left_the_page_are_recorded(self) -> None:
        page = _RequestWirePage(
            ("https://x/batchexecute?rpcids=maseQ", "https://x/other?rpcids=C4BZMd"),
        )
        sent: set[str] = set()
        await mc._rpc_replies(  # pyright: ignore[reportPrivateUsage]
            page,  # type: ignore[arg-type]
            ("C4BZMd", "maseQ"),
            page.act,
            timeout_s=0.05,
            sent=sent,
        )
        assert sent == {"maseQ"}

    async def test_a_dialog_after_a_sent_upload_is_not_called_the_terms_dialog(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(mc, "_rpc_replies", _replies({}, sent=("maseQ",)))
        with pytest.raises(WireFormatError, match="no reply proved") as exc:
            await _create(_DialogPage(before=0, after=1), tmp_path)
        assert exc.value.next_action == "reconcile"


class TestVoiceOptionClick:
    async def test_an_option_that_no_longer_names_the_voice_is_not_clicked(self) -> None:
        page = _VoicePage()
        option = _VoiceLocator(page, ["Charon\nMale"])
        with pytest.raises(UiSelectorDriftError, match="changed before it was clicked"):
            await mc._click_option(option, "Aoede")  # pyright: ignore[reportPrivateUsage]
        assert page.clicks == 0

    async def test_the_voice_readback_is_bounded_by_the_wait(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        timeouts: list[int] = []

        class _Slow(_Locator):
            async def inner_text(self, *, timeout: int) -> str:
                timeouts.append(timeout)
                raise PlaywrightTimeoutError("slow")

        class _SlowPage(_Page):
            def locator(self, _: str) -> Any:
                return _Slow(self)

        monkeypatch.setattr(mc, "VOICE_SHOWN_WAIT_S", 0.3)
        started = time.monotonic()
        assert not await mc._voice_shown(_SlowPage(), "Aoede")  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert time.monotonic() - started < 1.5
        assert timeouts and all(0 < t <= 300 for t in timeouts)


class TestBodyRefusal:
    async def test_a_refused_body_upload_keeps_flows_reason(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from gflow_cli.api.transports.batchexecute import rpc_errors

        err = rpc_errors(_refusal_body("maseQ"))[0]
        monkeypatch.setattr(mc, "_rpc_replies", _replies({"maseQ": err}))
        with pytest.raises(MediaUploadRejectedError, match="IMAGE_REJECTED"):
            await mc._upload_body(_Page(), PID, EID, tmp_path / "b.jpg")  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
