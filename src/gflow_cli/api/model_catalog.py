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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import structlog

from gflow_cli.api.video import VideoModel

if TYPE_CHECKING:
    from collections.abc import Mapping

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
                )
            )
            return
        for child in node:
            walk(child)

    walk(payload)
    return found
