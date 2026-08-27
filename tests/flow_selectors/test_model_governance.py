"""Governance: our model selectors must still resolve against what Flow offers.

Model drift is invisible by construction. Flow removes an entry or adds a
near-duplicate on their schedule, with no signal on ours — no failing test, no
error, no changed line of our code. On 2026-08-26 a manual spike found three
distinct drifts that had been live for an unknown period:

    Flow offers: Nano Banana Pro / Nano Banana 2 / Nano Banana 2 Lite

    GEM_PIX_2   has-text('Nano Banana Pro')  -> HIT
    NARWHAL     has-text('Nano Banana 2')    -> AMBIGUOUS (also matches "2 Lite")
    IMAGEN_3_5  has-text('Imagen 4')         -> MISS (entry no longer exists)
    (unmodelled)                             -> "Nano Banana 2 Lite" is a tier we
                                                do not expose at all

This test is the guard. It runs offline in CI on every commit, grading our
selectors against a recorded inventory that the live probe refreshes. When the
probe records a new entry that makes a selector ambiguous, CI fails HERE rather
than a user being billed for the wrong model.

The recorded inventory is evidence, not configuration: it is what Flow actually
rendered, with provenance. Editing it to make this test pass, without a probe
run behind it, defeats the entire mechanism.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gflow_cli.api.image import Model
from gflow_cli.api.transports.ui_automation import IMAGE_MODEL_OPTION_SELECTORS
from gflow_cli.api.transports.ui_automation_video import VIDEO_MODEL_OPTION_SELECTORS
from gflow_cli.api.video import VideoModel

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "flow_model_inventory.json"

#: Entries Flow offers that we knowingly do not model. Each needs a reason and,
#: ideally, an issue. An empty waiver list is the goal, not the default.
#: Models Flow has REMOVED from its picker. Kept in the registry so the removal
#: stays visible and `--model` fails with a named reason rather than the model
#: silently vanishing. Each entry is a decision that needs closing out, not a
#: place to park failures.
_NO_LONGER_OFFERED: dict[Model, str] = {
    Model.IMAGEN_3_5: (
        "Flow removed 'Imagen 4' from the image picker — confirmed live 2026-08-26 "
        "(offered: Nano Banana Pro / 2 / 2 Lite). Until this model is retired or "
        "remapped, requesting it raises UiSelectorDriftError (exit 23) naming what "
        "Flow does offer, instead of silently generating on the default and billing "
        "for it. Follow-up: decide retire-vs-remap."
    ),
}

#: Video models Flow does not offer. Same contract as `_NO_LONGER_OFFERED`:
#: a named, dated decision — not a place to park failures.
_VIDEO_NOT_OFFERED: dict[VideoModel, str] = {
    VideoModel.VEO_3_1_LITE_LOWER_PRIORITY: (
        "'Veo 3.1 - Lite [Lower Priority]' was NOT offered to profile denon82 on "
        "2026-08-26 (picker rendered exactly: Omni Flash / Veo 3.1 - Lite / Fast / "
        "Quality). That is one account at one moment, NOT proof the tier does not "
        "exist — it may be cohort- or region-gated, which is precisely what #539 is "
        "open to establish. Requesting it now raises VideoModelSelectionError "
        "(exit 18) naming what Flow does offer, instead of silently generating on "
        "whatever model Flow had selected and CHARGING CREDITS for it."
    ),
}

_UNMODELLED_WAIVERS: dict[str, str] = {
    "🍌 Nano Banana 2 Lite": (
        "Discovered 2026-08-26. A lower-tier image model we do not expose. "
        "Waived pending a capability spike (cost/quality) before we ship it."
    ),
}


def _inventory() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _matches(selector: str, offered: list[str]) -> list[str]:
    """Model Playwright's text-matching faithfully, or this guard lies.

    Supported forms, because the real selectors use all three:
      :has-text('X')        substring, case-insensitive
      :text-is('X')         exact match on normalised whitespace
      :not(:has-text('Y'))  excludes entries containing Y

    Modelling this precisely is the whole point — 'Nano Banana 2' looks unique
    until you notice it is a SUBSTRING of 'Nano Banana 2 Lite'. A matcher that
    treated has-text as equality would have reported the pre-fix selectors clean
    and the guard would have been decorative.
    """
    excludes = [
        m.split("has-text('", 1)[1].split("')", 1)[0]
        for m in re.findall(r":not\(:has-text\('[^']+'\)\)", selector)
    ]
    positives = re.sub(r":not\(:has-text\('[^']+'\)\)", "", selector)

    exacts = re.findall(r":text-is\('([^']+)'\)", positives)
    subs = re.findall(r":has-text\('([^']+)'\)", positives)
    if not exacts and not subs:
        return []

    def _norm(v: str) -> str:
        return " ".join(v.split()).lower()

    out = []
    for item in offered:
        n = _norm(item)
        if exacts and not any(_norm(e) == n for e in exacts):
            continue
        if subs and not all(_norm(x) in n for x in subs):
            continue
        if any(_norm(x) in n for x in excludes):
            continue
        out.append(item)
    return out


def test_fixture_has_provenance() -> None:
    """A recorded inventory without provenance is indistinguishable from a guess."""
    prov = _inventory().get("_provenance", {})
    assert prov.get("captured_utc"), "inventory must record WHEN it was captured"
    assert prov.get("method"), "inventory must record HOW it was captured"


def test_every_model_has_a_picker_selector() -> None:
    """A model cannot be added without a way to select it.

    Without this, `gflow models` can advertise something the transport silently
    cannot choose.
    """
    missing = [m.value for m in Model if m not in IMAGE_MODEL_OPTION_SELECTORS]
    assert not missing, f"models with no picker selector: {missing}"


@pytest.mark.parametrize("model", list(Model))
def test_selector_matches_exactly_one_offered_entry(model: Model) -> None:
    """The core guard: exactly one, never zero, never more.

    - zero  => MISS. The old code silently generated on Flow's default and billed
               for it (the Imagen 4 case).
    - many  => AMBIGUOUS. `.first` picks by DOM order, so behaviour changes when
               Flow reorders, with no code change on our side (the
               'Nano Banana 2' / '2 Lite' case).
    """
    if model in _NO_LONGER_OFFERED:
        pytest.skip(f"{model.value}: {_NO_LONGER_OFFERED[model]}")

    offered = _inventory()["image"]
    selectors = IMAGE_MODEL_OPTION_SELECTORS.get(model, ())

    # A cascade is a FALLBACK CHAIN: the transport tries each in order and uses
    # the first that matches (ui_automation.py `for sel in option_sels`). Summing
    # across all of them would report a model with two equivalent selectors as
    # AMBIGUOUS, which is a false alarm — the second is never reached.
    total: list[str] = []
    for sel in selectors:
        total = _matches(sel, offered)
        if total:
            break

    assert total, (
        f"{model.value}: MISS — no offered entry matches {list(selectors)}.\n"
        f"Flow offered: {offered}\n"
        f"gflow would previously have generated on Flow's default model and billed for it."
    )
    assert len(total) == 1, (
        f"{model.value}: AMBIGUOUS — {len(total)} entries match: {total}\n"
        f"Selecting .first picks by DOM order and can bill a different model."
    )


def test_every_offered_entry_is_modelled_or_waived() -> None:
    """Flow offering something we do not model is drift too — the quiet kind.

    A new tier we never expose is a missed capability rather than a bug, but it
    should be a decision on the record, not something nobody noticed.
    """
    offered = _inventory()["image"]
    claimed = {
        m
        for model in Model
        for sel in IMAGE_MODEL_OPTION_SELECTORS.get(model, ())
        for m in _matches(sel, offered)
    }
    unexplained = [o for o in offered if o not in claimed and o not in _UNMODELLED_WAIVERS]
    assert not unexplained, (
        f"Flow offers entries we neither model nor waive: {unexplained}\n"
        f"Either add a Model for it, or record a waiver with a reason."
    )


def test_every_video_model_has_a_picker_selector() -> None:
    missing = [m.value for m in VideoModel if m not in VIDEO_MODEL_OPTION_SELECTORS]
    assert not missing, f"video models with no picker selector: {missing}"


@pytest.mark.parametrize("model", list(VideoModel))
def test_video_selector_matches_exactly_one_offered_entry(model: VideoModel) -> None:
    """Same guard as the image arm — and this one is the CREDIT-BEARING arm.

    An image miss burns a daily quota; a video miss burns credits (up to 100 for
    veo-quality against 10 for veo-lite). The video registry also carries the
    identical substring hazard the image arm was fixed for: `has-text('Veo 3.1 -
    Lite')` is a prefix of `Veo 3.1 - Lite [Lower Priority]`, which is why that
    selector needs its `:not(...)` guard to stay correct if the LP tier appears.
    """
    if model in _VIDEO_NOT_OFFERED:
        pytest.skip(f"{model.value}: {_VIDEO_NOT_OFFERED[model]}")

    offered = _inventory()["video"]
    assert offered is not None, "video inventory not captured — cannot grade"
    sel = VIDEO_MODEL_OPTION_SELECTORS[model]
    hits = _matches(sel, offered)

    assert hits, (
        f"{model.value}: MISS — no offered entry matches {sel!r}.\n"
        f"Flow offered: {offered}\n"
        f"gflow would generate on Flow's current model and SPEND CREDITS on it."
    )
    assert len(hits) == 1, (
        f"{model.value}: AMBIGUOUS — {len(hits)} entries match: {hits}\n"
        f"Resolving .first picks by DOM order and can charge a different tier."
    )


def test_every_offered_video_entry_is_modelled() -> None:
    """A video tier Flow offers that we cannot select is a missed capability."""
    offered = _inventory()["video"]
    claimed = {
        m for model in VideoModel for m in _matches(VIDEO_MODEL_OPTION_SELECTORS[model], offered)
    }
    unexplained = [o for o in offered if o not in claimed]
    assert not unexplained, f"Flow offers video entries we do not model: {unexplained}"


def test_lower_priority_absence_is_recorded_as_an_observation() -> None:
    """#539's trap, encoded: absence-on-one-account is not absence.

    The earlier capture read an empty menu and the emptiness was mistaken for
    proof the tier was gone. This asserts the fixture states WHERE and WHEN the
    tier was not offered, so the claim can never harden into "it does not exist".
    """
    note = _inventory()["_lower_priority_finding"]
    assert "NOT proof" in note, "the LP finding must not be recorded as proof of absence"
    assert "denon82" in note and "2026-08-26" in note, "must name the account and the date"
