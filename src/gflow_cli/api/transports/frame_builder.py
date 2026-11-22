"""The ``ogiZ0b`` (image generation) batchexecute body, built from domain values.

A pure function: no I/O, no clock, no randomness. The caller supplies the per-request
values (``batch_id``, ``session_counter``) so the output is deterministic under test.

Wire shape (proven 2026-11-22, docs/superpowers/spikes/2026-11-22-ogiz0b-minimum-viable-frame.md):

    ctx   = [null, 22, null, null, null, project_id, null, null, null, null, [token, 1]]
    entry = [null, null, null, session_counter, 2, model, null, copy(ctx), [[[prompt]]],
             null, null, null]
    inner = [null, [entry], 1, ctx, [batch_id]]
    body  = urlencode({"f.req": [[["ogiZ0b", json(inner), null, "generic"]]], "at": at})
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

_ASPECT_PORTRAIT = 2  # Flow's 2026-11-22 submit capture sends 2 for portrait 9:16.


def build_ogiz0b_body(
    *,
    prompt: str,
    model: str,
    project_id: str,
    recaptcha_token: str,
    at_token: str,
    batch_id: str,
    session_counter: int,
) -> str:
    """Return the form-encoded ``f.req`` + ``at`` body for one T2I ``ogiZ0b`` POST."""
    ctx: list[object] = [
        None,
        22,
        None,
        None,
        None,
        project_id,
        None,
        None,
        None,
        None,
        [recaptcha_token, 1],
    ]
    entry: list[object] = [
        None,
        None,
        None,
        session_counter,
        _ASPECT_PORTRAIT,
        model,
        None,
        ctx.copy(),
        [[[prompt]]],
        None,
        None,
        None,
    ]
    inner = [None, [entry], 1, ctx, [batch_id]]
    f_req = json.dumps([[["ogiZ0b", json.dumps(inner, separators=(",", ":")), None, "generic"]]])
    return urlencode({"f.req": f_req, "at": at_token})
