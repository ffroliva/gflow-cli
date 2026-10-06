"""Google ``batchexecute`` envelope + Flow's generation record (migrated host).

The migrated ``flow.google.com`` frontend (``AiSandboxAngularFrontend``) talks to
its backend through ``POST …/data/batchexecute?rpcids=<id>``. Responses are the
anti-XSSI ``)]}'`` envelope: chunk-length lines interleaved with JSON arrays whose
``["wrb.fr", "<rpcid>", "<json string>", …]`` items carry one RPC reply each.

Three rpcids matter for a generation (spike 2026-09-05-migrated-host-wire-protocol):

* ``YhhmEf`` — the submit; wraps the record as ``[null, N, [[media…]], [[record]]]``
* ``jwpduf`` — the app's own 5 s status poll; ``[null, N, [[record]]]``
* ``as29s`` — the result; the bare record, now carrying signed CDN URLs

The record itself is ``[workflow_id, project_id, media_id, <marker>, null, DETAILS, null,
MEDIA_INFO]`` (the marker was ``"CAE"``, ``null`` since 2026-10, #948) and is located **by
that shape**, not by position, so a wrapper change does not break the parser.
``DETAILS[8]`` is ``[status]`` (6 submitted, 2 running, 3 done), ``DETAILS[10]`` the
signed **poster** (JPEG) URL once done, ``DETAILS[13]`` the mp4 byte size;
``MEDIA_INFO[0][8]`` the signed **video** URL (``MEDIA_INFO[0][12]`` carries the model
key, e.g. ``abra_t2v_8s`` — model and duration in one string — which the driver does
not need). Which URL is which was settled by downloading both on 2026-09-05:
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


@dataclass(frozen=True)
class ImageGenerationRecord:
    """One completed image from the migrated host's ``ogiZ0b`` reply.

    Image generation is synchronous at this RPC boundary: the measured response arrives
    after the render and already carries its signed CDN URL. It is deliberately distinct
    from :class:`GenerationRecord`; image records do not use the video record shape.
    """

    media_id: str
    workflow_id: str
    project_id: str
    seed: int
    prompt: str
    image_url: str
    dimensions: tuple[int, int]
    display_name: str | None = None


def _as_list(node: object) -> list[Any] | None:
    return cast("list[Any]", node) if isinstance(node, list) else None


def _wrb_rows(text: str) -> list[list[Any]]:
    """Every ``["wrb.fr", "<rpcid>", …]`` row in a batchexecute body, undecoded.

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
    rows: list[list[Any]] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("["):
            continue
        try:
            decoded, _ = decoder.raw_decode(line)
        except ValueError:
            continue
        for raw_item in _as_list(decoded) or []:
            item = _as_list(raw_item)
            if (
                item is not None
                and len(item) >= 3
                and item[0] == "wrb.fr"
                and isinstance(item[1], str)
            ):
                rows.append(item)
    return rows


def parse_frames(text: str) -> list[tuple[str, Any]]:
    """Every ``wrb.fr`` frame in a batchexecute body as ``(rpcid, decoded payload)``.

    A frame whose payload is null is an error envelope, not a reply: it is left out
    here and read by :func:`rpc_errors`.
    """
    frames: list[tuple[str, Any]] = []
    for item in _wrb_rows(text):
        if not isinstance(item[2], str):
            continue
        try:
            payload: Any = json.loads(item[2])
        except ValueError:
            payload = None
        frames.append((item[1], payload))
    return frames


@dataclass(frozen=True)
class RpcError:
    """A ``wrb.fr`` frame with a null payload: the RPC failed, and slot 5 says how.

    ``code`` is the gRPC status, ``reasons`` the ``google.rpc.ErrorInfo`` reasons. The
    refusal shape, captured 2026-09-27 on a submit Flow refused (HTTP 200)::

        ["wrb.fr","ogiZ0b",null,null,null,
         [7,null,[["type.googleapis.com/google.rpc.ErrorInfo",["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]],
         "generic"]

    A status with no reason is NOT a refusal by itself: the #723 entity submit Flow
    queued and ran replies ``[5]``.
    """

    rpcid: str
    code: int | None
    reasons: tuple[str, ...]


def _strings(node: object) -> list[str]:
    """Every string anywhere under ``node``, depth-first."""
    if isinstance(node, str):
        return [node]
    return [s for child in _as_list(node) or [] for s in _strings(child)]


def rpc_errors(text: str) -> list[RpcError]:
    """Every error envelope in a batchexecute body — the frames :func:`parse_frames` skips."""
    errors: list[RpcError] = []
    for item in _wrb_rows(text):
        status = _as_list(item[5]) if item[2] is None and len(item) > 5 else None
        if not status:
            continue
        code = status[0] if isinstance(status[0], int) else None
        details = status[2] if len(status) > 2 else None
        reasons = tuple(s for s in _strings(details) if not s.startswith("type.googleapis.com/"))
        errors.append(RpcError(item[1], code, reasons))
    return errors


def _is_record(node: list[Any]) -> bool:
    # Slot 3 is deliberately unchecked: it was "CAE" until Flow started sending null there
    # (#948, measured 2026-10-06 on YhhmEf + jwpduf), and a marker that moved once can move
    # again — after billing. Three UUIDs plus the DETAILS list at slot 5 identify a record.
    if len(node) < 6 or not isinstance(node[5], list):
        return False
    return all(isinstance(node[i], str) and _UUID_RE.match(node[i]) for i in (0, 1, 2))


def _find_record(node: object, want: tuple[int, str] | None = None) -> list[Any] | None:
    """First record-shaped list, depth-first; with ``want=(slot, id)``, the first whose
    id slot matches — a project-wide reply can list another clip's record first."""
    items = _as_list(node)
    if items is None:
        return None
    if _is_record(items) and (want is None or items[want[0]] == want[1]):
        return items
    for child in items:
        found = _find_record(child, want)
        if found is not None:
            return found
    return None


def _walk_lists(node: object) -> list[list[Any]]:
    items = _as_list(node)
    if items is None:
        return []
    return [items, *(child for item in items for child in _walk_lists(item))]


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


def generation_record(
    rpcid: str,
    payload: Any,
    *,
    workflow_id: str | None = None,
    media_id: str | None = None,
) -> GenerationRecord:
    """Locate and decode the generation record inside one frame's payload.

    Pass ``workflow_id`` or ``media_id`` to pick that clip's record out of a reply that
    lists several; without one, the first record wins (right for a submit reply).

    Raises :class:`WireFormatError` (with a redacted discovery head) when no
    record-shaped list exists — the migrated backend changed its envelope.
    """
    want: tuple[int, str] | None = None
    if workflow_id is not None:
        want = (0, workflow_id)
    elif media_id is not None:
        want = (2, media_id)
    rec = _find_record(payload, want)
    if rec is None:
        raise WireFormatError(
            detail=(
                f"batchexecute {rpcid}: no generation record "
                f"([uuid, uuid, uuid, _, _, [details…], …]) in the reply"
            ),
            # #948: the reporters' runs that ended here were each accepted and billed.
            remediation_hint=(
                "Flow may already have accepted and billed this generation. Open the "
                "project in Flow before re-running: a blind resubmit can bill twice. File "
                "a bug at https://github.com/ffroliva/gflow-cli/issues with this error."
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


def image_records(rpcid: str, payload: Any) -> list[ImageGenerationRecord]:
    """Decode every completed image in the migrated ``ogiZ0b`` payload.

    The media tuple is identified by invariants measured on both T2I and I2I:
    UUID media/workflow ids at slots 0/2, generation details at ``[6][0]``, a signed
    HTTPS URL at details slot 13, and integer dimensions at ``[6][2]``. Sibling
    workflow tuples supply the project id and display name. A shape change fails loud
    with a redacted discovery head instead of guessing positional fallbacks.
    """
    workflow_meta: dict[str, tuple[str, str | None]] = {}
    lists = _walk_lists(payload)
    for node in lists:
        if len(node) < 5 or not isinstance(node[0], str) or not _UUID_RE.match(node[0]):
            continue
        meta = _as_list(node[3])
        project_id = node[4]
        if meta is None or not isinstance(project_id, str) or not _UUID_RE.match(project_id):
            continue
        title = meta[0] if meta and isinstance(meta[0], str) else None
        workflow_meta[node[0]] = (project_id, title)

    records: list[ImageGenerationRecord] = []
    for node in lists:
        if len(node) < 7:
            continue
        media_id, workflow_id = node[0], node[2]
        if not (
            isinstance(media_id, str)
            and _UUID_RE.match(media_id)
            and isinstance(workflow_id, str)
            and _UUID_RE.match(workflow_id)
        ):
            continue
        container = _as_list(node[6])
        if container is None:
            continue
        details = _as_list(_at(container, 0))
        dims = _as_list(_at(container, 2))
        if details is None or dims is None or len(details) <= 13 or len(dims) < 2:
            continue
        image_url = _url(details[13])
        seed, prompt = _at(details, 1), _at(details, 7)
        width, height = dims[0], dims[1]
        meta = workflow_meta.get(workflow_id)
        if not (
            image_url
            and isinstance(seed, int)
            and isinstance(prompt, str)
            and isinstance(width, int)
            and isinstance(height, int)
            and meta is not None
        ):
            continue
        records.append(
            ImageGenerationRecord(
                media_id=media_id,
                workflow_id=workflow_id,
                project_id=meta[0],
                seed=seed,
                prompt=prompt,
                image_url=image_url,
                dimensions=(width, height),
                display_name=meta[1],
            )
        )
    if not records:
        raise WireFormatError(
            detail=(
                f"batchexecute {rpcid}: no completed image record "
                "([media_uuid, …, workflow_uuid, …, details-with-https-url]) in the reply"
            ),
            route=f"batchexecute:{rpcid}",
            discovery={"rpcid": rpcid, "payload_head": _discovery_head(payload)},
        )
    return records
