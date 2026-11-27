"""Model retirement and Flow's model catalog (#1375).

Flow marks the model families it is retiring: the model menu captions them "Leaving 5 Dec",
and its model catalog — the ``HTrJv`` batchexecute reply sent on every project load — carries
the date. Each family there is ``[display_name, usages, enabled, family_key, alias, _,
[year, month, day]]`` (measured 2026-11-19; the date slot is present on exactly the families the
menu captions as leaving). Usage entries carry more (cost per tier, duration, limits), but only
the family fields are relied on: the usage slots were read from one capture.

gflow carries the dates statically (:data:`RETIRES_ON`) and checks them, offline, against a
recorded catalog (``tests/fixtures/flow_htrjv_model_families.json``). Nothing here reads the
network, and nothing here refuses a request: a retiring model is warned about, and Flow's live
menu decides whether it is still offered (the pre-submit "not offered" refusal, exit 11).

:func:`catalog_drift` compares a catalog with gflow's mapping (#1550): the recorded fixture in
the test suite, Flow's live reply in ``gflow doctor`` (check ``models.catalog_drift``). Every
family is either mapped (:data:`FAMILY_KEY`, :data:`IMAGE_FAMILY`) or listed in
:data:`IGNORED_FAMILIES` with its reason. Video usage keys are not mapped one by one: gflow
selects a video model by family, so only the image keys gflow sends on the wire are checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import structlog

from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.video import VideoModel

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

log = structlog.get_logger(__name__)

#: When Flow retires a video model family, as its catalog and menu said on 2026-11-19.
RETIRES_ON: Mapping[VideoModel, date] = MappingProxyType(
    {
        VideoModel.OMNI_FLASH: date(2026, 12, 5),
        VideoModel.VEO_3_1_FAST: date(2026, 12, 5),
        VideoModel.VEO_3_1_QUALITY: date(2026, 12, 5),
    }
)

#: gflow's video model -> the family key Flow's catalog names it by (``HTrJv`` index 3).
#: The enum values are gflow's own names (``omni_flash``), not Flow's (``abra``).
FAMILY_KEY: Mapping[VideoModel, str] = MappingProxyType(
    {
        VideoModel.OMNI_FLASH: "abra",
        VideoModel.OMNI_1_2_FLASH: "macaron_cookie",
        VideoModel.VEO_3_1_LITE: "veo_3_1_lite",
        VideoModel.VEO_3_1_FAST: "veo_3_1_fast",
        VideoModel.VEO_3_1_QUALITY: "veo_3_1_quality",
        VideoModel.VEO_3_1_LITE_LOWER_PRIORITY: "veo_3_1_lite_low_priority",
    }
)

#: gflow's image model -> (the family key Flow's catalog lists it under, the usage key gflow
#: sends on the wire). "Nano Banana 2.1" replaced "Nano Banana 2" and sends BELUGA (#958).
IMAGE_FAMILY: Mapping[ImageModel, tuple[str, str]] = MappingProxyType(
    {
        ImageModel.NARWHAL: ("beluga_display", "BELUGA"),
        ImageModel.GEM_PIX_2: ("nano_banana_pro", "GEM_PIX_2"),
        ImageModel.HARBOR_SEAL: ("harbor_seal", "HARBOR_SEAL"),
    }
)

#: gflow image models Flow's catalog does not list, and why gflow keeps them.
UNLISTED_MODELS: Mapping[ImageModel, str] = MappingProxyType(
    {
        ImageModel.IMAGEN_3_5: "Imagen 4: gone from Flow's menu since 2026-08-26; the name stays"
        " so existing commands and configs still parse",
    }
)

#: Catalog families gflow deliberately does not map, each with the reason.
IGNORED_FAMILIES: Mapping[str, str] = MappingProxyType(
    {
        "veo_3_1_upsampler_1080p": "video upscale: chosen in Flow's download menu, not by key",
        "veo_3_1_upsampler_4k": "video upscale: chosen in Flow's download menu, not by key",
        "omni_upsampler_360p": "video upscale: chosen in Flow's download menu, not by key",
        "upsample_2k": "image upscale: chosen in Flow's download menu, not by key",
        "upsample_4k": "image upscale: chosen in Flow's download menu, not by key",
        "gemini_tts": "Flow's speech model: gflow audio narrate calls a TTS service directly",
    }
)

_DATE_SLOT = 6


def retires_on(model: VideoModel) -> date | None:
    """The day Flow retires ``model``, or ``None`` when it has not said it will."""
    return RETIRES_ON.get(model)


@dataclass(frozen=True)
class ModelFamily:
    """One model family as Flow's catalog lists it."""

    key: str
    display_name: str
    enabled: bool
    retires_on: date | None
    usages: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetirementNotice:
    """A selected model Flow has said it will retire. ``past``: the day has come."""

    model: VideoModel
    retires_on: date
    past: bool


def warn_if_retiring(model: VideoModel, *, today: date) -> RetirementNotice | None:
    """Log ``model.retiring`` for a retiring model and return the notice. Never raises.

    The date only warns. It carries no time zone and Flow may move it, so whether the model
    still runs is left to Flow's menu, read before submit.
    """
    when = retires_on(model)
    if when is None:
        return None
    notice = RetirementNotice(model=model, retires_on=when, past=today >= when)
    log.warning(
        "model.retiring",
        model=model.value,
        retires_on=when.isoformat(),
        past=notice.past,
    )
    return notice


def _as_list(node: object) -> list[object] | None:
    return cast("list[object]", node) if isinstance(node, list) else None


def _is_family(node: list[object]) -> bool:
    if len(node) < 4 or not isinstance(node[0], str) or not isinstance(node[3], str):
        return False
    usages = _as_list(node[1])
    first = _as_list(usages[0]) if usages else None
    return bool(first) and isinstance(first[0], str) if first else False


def _family_date(node: list[object]) -> date | None:
    slot = _as_list(node[_DATE_SLOT]) if len(node) > _DATE_SLOT else None
    if slot is None or len(slot) != 3:
        return None
    parts = [part for part in slot if type(part) is int]  # bool is an int subclass: excluded
    if len(parts) != 3:
        return None
    try:
        return date(*parts)
    except ValueError:
        return None


def _usage_keys(node: list[object]) -> tuple[str, ...]:
    entries = [_as_list(usage) for usage in _as_list(node[1]) or []]
    return tuple(str(usage[0]) for usage in entries if usage and isinstance(usage[0], str))


def parse_htrjv_families(payload: object) -> list[ModelFamily]:
    """Every model family in an ``HTrJv`` payload (or a list of family entries), in order.

    Tolerant by design: an entry whose shape changed contributes no date rather than an
    error, so a catalog that drifted shows up as a failing comparison, not a crash.
    """
    found: list[ModelFamily] = []

    def walk(value: object) -> None:
        node = _as_list(value)
        if node is None:
            return
        if _is_family(node):
            found.append(
                ModelFamily(
                    key=str(node[3]),
                    display_name=str(node[0]),
                    enabled=node[2] is True,
                    retires_on=_family_date(node),
                    usages=_usage_keys(node),
                )
            )
            return
        for child in node:
            walk(child)

    walk(payload)
    return found


def catalog_drift(families: Sequence[ModelFamily]) -> list[str]:
    """How ``families`` differ from gflow's mapping, one line each; empty when they agree.

    Reported: a family neither mapped nor ignored, a mapped or ignored family that is gone, an
    image wire key its family no longer lists, and a retirement date gflow does not carry.
    """
    by_key = {family.key: family for family in families}
    mapped = {key: model.value for model, key in FAMILY_KEY.items()}
    mapped |= {key: model.value for model, (key, _) in IMAGE_FAMILY.items()}
    expected_dates = {FAMILY_KEY[model]: when for model, when in RETIRES_ON.items()}
    drift = [
        f"added family {family.key} ({family.display_name}): not mapped in gflow"
        for family in families
        if family.key not in mapped and family.key not in IGNORED_FAMILIES
    ]
    drift += [
        f"removed family {key}: gflow maps {model}"
        for key, model in mapped.items()
        if key not in by_key
    ]
    drift += [
        f"removed family {key}: listed as ignored in gflow"
        for key in IGNORED_FAMILIES
        if key not in by_key
    ]
    drift += [
        f"family {key} no longer lists {wire}: gflow sends it for {model.value}"
        for model, (key, wire) in IMAGE_FAMILY.items()
        if key in by_key and wire not in by_key[key].usages
    ]
    for family in families:
        was, now = expected_dates.get(family.key), family.retires_on
        if was == now:
            continue
        if was is None:
            drift.append(f"{family.key}: new retirement date {now}")
        elif now is None:
            drift.append(f"{family.key}: retirement date {was} withdrawn")
        else:
            drift.append(f"{family.key}: retirement date moved {was} -> {now}")
    return drift
