"""The ``ogiZ0b`` (image generation) batchexecute body, built from domain values.

A pure function: no I/O, no clock, no randomness. The caller supplies the per-request
values (``batch_id``, ``entry_seeds``) so the output is deterministic under test.

Wire shape (proven 2026-11-22, docs/superpowers/spikes/2026-11-22-direct-image-gate0.md):

    ctx   = [null, 22, null, null, null, project_id, null, null, null, null, [token, 1]]
    entry = [null, null, null, seed, aspect, model, null, copy(ctx), [[[prompt]]],
             null, null, null]
    inner = [null, [entry, ...], 1, ctx, [batch_id]]   # one entry per image, one POST
    body  = urlencode({"f.req": [[["ogiZ0b", json(inner), null, "generic"]]], "at": at})
"""

from __future__ import annotations

import base64
import json
from urllib.parse import urlencode

from gflow_cli.api.image import Aspect

# entry[4], as Flow's own submit sends it (route-aborted captures, 2026-11-22). 9:16 and
# 16:9 are also proven by direct POSTs (768x1376, 1376x768); the other three by capture.
_ASPECT_WIRE: dict[Aspect, int] = {
    Aspect.SQUARE: 1,
    Aspect.PORTRAIT: 2,
    Aspect.LANDSCAPE: 3,
    Aspect.PORTRAIT_THREE_FOUR: 4,
    Aspect.LANDSCAPE_FOUR_THREE: 5,
}


def build_ogiz0b_body(
    *,
    prompt: str,
    model: str,
    project_id: str,
    recaptcha_token: str,
    at_token: str,
    batch_id: str,
    entry_seeds: tuple[int, ...],
    aspect: Aspect = Aspect.PORTRAIT,
    references: tuple[tuple[str, str], ...] = (),
    characters: tuple[tuple[str, str], ...] = (),
) -> str:
    """Return the form-encoded ``f.req`` + ``at`` body for one ``ogiZ0b`` POST.

    One entry per image: ``len(entry_seeds)`` is the image count (two entries returned two
    images in one POST, measured 2026-11-22). ``references`` are in-project images as
    ``(media_id, display_name)``: Flow's own I2I submit carries each at ``entry[2]`` and
    as a chip ahead of the prompt text, which then starts with two spaces (captured
    2026-11-22; multi-image repetition per entry captured and proven live 2026-11-25).
    ``characters`` are ``(entity_id, name)``: each at ``entry[10]`` and as a character
    chip, the text then starting with one space (captured 2026-11-23; multi-image
    repetition per entry captured and proven live 2026-11-25). The two are never combined:
    that shape is unmeasured.
    """
    if not entry_seeds:
        msg = "entry_seeds must name at least one image"
        raise ValueError(msg)
    ctx: list[object] = [None, 22, None, None, None, project_id, None, None, None, None]
    ctx.append([recaptcha_token, 1])
    if references and characters:
        msg = "references and characters together are not a measured ogiZ0b shape"
        raise ValueError(msg)
    media = [[media_id, None, None, None, 1] for media_id, _ in references] or None
    entities = [[entity_id] for entity_id, _ in characters] or None
    document = _prompt_document(prompt, references, characters)
    entries = [
        [None, None, media, seed, _ASPECT_WIRE[aspect], model, None, ctx.copy(), document]
        + [None, entities, None]
        for seed in entry_seeds
    ]
    inner = [None, entries, 1, ctx, [batch_id]]
    f_req = json.dumps([[["ogiZ0b", json.dumps(inner, separators=(",", ":")), None, "generic"]]])
    return urlencode({"f.req": f_req, "at": at_token})


def build_maseq_body(
    *,
    project_id: str,
    recaptcha_token: str,
    at_token: str,
    image_bytes: bytes,
    mime: str,
    display_name: str,
    request_ids: tuple[str, str],
) -> str:
    """Return the form-encoded body for one ``maseQ`` (image upload) POST.

    The frame Flow's own upload sends (captured 2026-11-23; a direct POST of it returned the
    new media id): the project context with the token, the bytes inline as base64, the MIME
    type, ``1``, four nulls, the display name, a null and two client-generated ids.
    """
    ctx: list[object] = [None, 22, None, None, None, project_id, None, None, None, None]
    ctx.append([recaptcha_token, 1])
    data = base64.b64encode(image_bytes).decode("ascii")
    frame = [ctx, data, mime, 1, None, None, None, None, display_name, None, *request_ids]
    f_req = json.dumps([[["maseQ", json.dumps(frame, separators=(",", ":")), None, "generic"]]])
    return urlencode({"f.req": f_req, "at": at_token})


def _prompt_document(
    prompt: str,
    references: tuple[tuple[str, str], ...],
    characters: tuple[tuple[str, str], ...],
) -> list[object]:
    """``entry[8]``: the prompt as Flow's composer serialises it, chips first."""
    if references:
        return [[[None, [[media_id, name] for media_id, name in references]], [f"  {prompt}"]]]
    if characters:
        chips = [[None, None, [entity_id, name]] for entity_id, name in characters]
        return [[[None, *chips], [f" {prompt}"]]]
    return [[[prompt]]]
