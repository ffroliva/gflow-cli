"""Google ``batchexecute`` envelope + Flow's generation record (migrated host).

The migrated ``flow.google.com`` frontend (``AiSandboxAngularFrontend``) talks to
its backend through ``POST …/data/batchexecute?rpcids=<id>``. Responses are the
anti-XSSI ``)]}'`` envelope: chunk-length lines interleaved with JSON arrays whose
``["wrb.fr", "<rpcid>", "<json string>", …]`` items carry one RPC reply each.

Three rpcids matter for a generation (spike 2026-09-05-migrated-host-wire-protocol):

* ``YhhmEf`` — the submit; wraps the record as ``[null, N, [[media…]], [[record]]]``
* ``jwpduf`` — the app's own 5 s status poll; ``[null, N, [[record]]]``
* ``as29s`` — the result; the bare record, now carrying signed CDN URLs

The record itself is ``[workflow_id, project_id, media_id, "CAE", null, DETAILS, null,
MEDIA_INFO]`` and is located **by that shape**, not by position, so a wrapper change
does not break the parser. ``DETAILS[8]`` is ``[status]`` (6 submitted, 2 running,
3 done), ``DETAILS[10]`` the signed **poster** (JPEG) URL once done, ``DETAILS[13]``
the mp4 byte size; ``MEDIA_INFO[0][8]`` the signed **video** URL (``MEDIA_INFO[0][12]`` carries
the model key, e.g. ``abra_t2v_8s`` — model and duration in one string — which the
driver does not need). Which URL is which was settled by downloading both on 2026-09-05:
``DETAILS[10]`` came back as a 37 KB JPEG; the record's byte size matched the other.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from gflow_cli.data.redaction import redact_error_detail
from gflow_cli.errors import WireFormatError

_XSSI_PREFIX = ")]}'"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{120,}")

STATUS_RUNNING = 2
STATUS_DONE = 3
STATUS_SUBMITTED = 6


@dataclass(frozen=True)
class GenerationRecord:
    """One generation as Flow's migrated backend reports it."""

    workflow_id: str
    project_id: str
    media_id: str
    status: int | None
    video_url: str | None = None
    poster_url: str | None = None
    size_bytes: int | None = None

    @property
    def is_done(self) -> bool:
        return self.status == STATUS_DONE

    @property
    def is_running(self) -> bool:
        return self.status in (STATUS_RUNNING, STATUS_SUBMITTED)

    @property
    def is_failed(self) -> bool:
        """Any status the spike never observed on the happy path is a failure —
        the failure enum itself has not been captured yet, so it is surfaced raw."""
        return self.status is not None and not self.is_done and not self.is_running


def _as_list(node: object) -> list[Any] | None:
    return cast("list[Any]", node) if isinstance(node, list) else None


def parse_frames(text: str) -> list[tuple[str, Any]]:
    """Every ``wrb.fr`` frame in a batchexecute body as ``(rpcid, decoded payload)``.

    Lenient on purpose: chunk-length lines are skipped rather than trusted, each
    line that starts a JSON array is decoded on its own, and anything that is not
    an envelope yields an empty list instead of raising — a login page or an HTML
    error body must never masquerade as a wire-format failure at this layer.
    """
    if not text:
        return []
    body = text.lstrip()
    if body.startswith(_XSSI_PREFIX):
        body = body[len(_XSSI_PREFIX) :]
    decoder = json.JSONDecoder()
    frames: list[tuple[str, Any]] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("["):
            continue
        try:
            decoded, _ = decoder.raw_decode(line)
        except ValueError:
            continue
        chunk = _as_list(decoded)
        if chunk is None:
            continue
        for raw_item in chunk:
            item = _as_list(raw_item)
            if (
                item is not None
                and len(item) >= 3
                and item[0] == "wrb.fr"
                and isinstance(item[1], str)
                and isinstance(item[2], str)
            ):
                try:
                    payload: Any = json.loads(item[2])
                except ValueError:
                    payload = None
                frames.append((item[1], payload))
    return frames


def _is_record(node: list[Any]) -> bool:
    if len(node) < 6 or node[3] != "CAE":
        return False
    return all(isinstance(node[i], str) and _UUID_RE.match(node[i]) for i in (0, 1, 2))


def _find_record(node: object) -> list[Any] | None:
    items = _as_list(node)
    if items is None:
        return None
    if _is_record(items):
        return items
    for child in items:
        found = _find_record(child)
        if found is not None:
            return found
    return None


def _at(node: object, *path: int) -> Any:
    current: Any = node
    for i in path:
        items = _as_list(current)
        if items is None or i >= len(items):
            return None
        current = items[i]
    return current


def _url(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") else None


def _discovery_head(payload: Any) -> str:
    try:
        head = json.dumps(payload)[:200]
    except (TypeError, ValueError):
        head = repr(payload)[:200]
    return redact_error_detail(_TOKEN_RE.sub("<token>", head))


def generation_record(rpcid: str, payload: Any) -> GenerationRecord:
    """Locate and decode the generation record inside one frame's payload.

    Raises :class:`WireFormatError` (with a redacted discovery head) when no
    record-shaped list exists — the migrated backend changed its envelope.
    """
    rec = _find_record(payload)
    if rec is None:
        raise WireFormatError(
            detail=(
                f"batchexecute {rpcid}: no generation record "
                f"([uuid, uuid, uuid, 'CAE', …]) in the reply"
            ),
            route=f"batchexecute:{rpcid}",
            discovery={"rpcid": rpcid, "payload_head": _discovery_head(payload)},
        )
    status_cell = _as_list(_at(rec, 5, 8))
    status: Any = status_cell[0] if status_cell else None
    size: Any = _at(rec, 5, 13)
    return GenerationRecord(
        workflow_id=rec[0],
        project_id=rec[1],
        media_id=rec[2],
        status=status if isinstance(status, int) else None,
        video_url=_url(_at(rec, 7, 0, 8)),
        poster_url=_url(_at(rec, 5, 10)),
        size_bytes=size if isinstance(size, int) else None,
    )
