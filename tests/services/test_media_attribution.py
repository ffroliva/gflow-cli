"""Server-side attribution: what model ACTUALLY generated a media (issue #586).

gflow records which model produced an image from two different sources:

* classic — the `batchGenerateImages` RESPONSE (`dto.py:183`) — observed truth
* agentic — the REQUEST we sent (`agentic.py:914`) — our own intent

Both land in the same catalog column (`recorder.py:503`), indistinguishably.

Observed live on 2026-08-26: an agentic run requested `GEM_PIX_2`
(Nano Banana Pro) and Flow generated with `NARWHAL`. The CLI exited 0, printed
`GEM_PIX_2`, and the catalog recorded `GEM_PIX_2` with `seed=0`/`0x0` sentinels.
The server, asked directly, said `NARWHAL` with `seed=93862`.

The agentic driver documents these fields as unobservable because they live in a
Web-Worker SSE stream Playwright cannot see. That is true of the DOM and of
page-level network events — and it does not follow that they are unknowable.
`flow.projectInitialData` returns every one of them, keyed by the media UUID the
scraper already extracts, through a fetcher gflow already has
(`client.fetch_project_listing`, ~0.5s, cookie auth, free).

The fixture is a real captured payload from an agentic run (PR #389 live
verification, 2026-07-27), trimmed to the media array.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflow_cli.services.catalog_sync import parse_media_attribution

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "flow_project_listing_media.json"
_MEDIA = "67d7bf3a-79b3-4125-9ac7-a19cd1d0a598"


def _payload() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_extracts_observed_attribution_keyed_by_media_uuid() -> None:
    attrs = parse_media_attribution(_payload())

    assert _MEDIA in attrs, f"expected {_MEDIA} in {list(attrs)}"
    a = attrs[_MEDIA]
    assert a.model_name_type == "NARWHAL"
    assert a.seed == 308519
    assert a.aspect_ratio == "IMAGE_ASPECT_RATIO_SQUARE"


def test_seed_is_int_not_the_wire_string() -> None:
    """The wire sends seed as a STRING ('308519'); the catalog column is an int.

    Leaving it as a string would compare unequal to a recorded int forever, so a
    drift check built on it would fire on every row.
    """
    assert isinstance(parse_media_attribution(_payload())[_MEDIA].seed, int)


def test_missing_generated_image_is_omitted_not_faked() -> None:
    """A media row without generatedImage yields NO entry.

    Absence must stay absent. Emitting a placeholder here would recreate exactly
    the bug this exists to detect — a synthesised value indistinguishable from an
    observed one.
    """
    payload = {
        "result": {"data": {"json": {"projectContents": {"media": [{"name": "no-image-row"}]}}}}
    }
    assert parse_media_attribution(payload) == {}


def test_unparseable_envelope_raises_rather_than_returning_empty() -> None:
    """Same contract as parse_project_listing: an unrecognised envelope fails loudly.

    Returning {} would read as "the server attributes nothing", which a caller
    would take as agreement.
    """
    with pytest.raises(ValueError, match="projectContents"):
        parse_media_attribution({"result": {"data": {"json": {}}}})


def test_non_media_rows_do_not_break_the_parse() -> None:
    payload = _payload()
    payload["result"]["data"]["json"]["projectContents"]["media"].append(
        {"name": "junk", "image": "not-a-dict"}
    )
    attrs = parse_media_attribution(payload)
    assert _MEDIA in attrs
