"""Opt-in live e2e for ``gflow video chain`` — last-frame I2V chaining.

Hits the **real Google Flow API** and therefore:
  - Is NOT collected by default ``pytest`` runs (gated behind ``e2e`` +
    ``e2e_video``). It NEVER runs in normal CI and spends NO credits unless you
    explicitly opt in.
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e_video``.
  - Requires a logged-in Chrome profile (Pro/Ultra account) AND the ``chain``
    optional extra (PyAV) for the last-frame extractor:
    ``pip install 'gflow-cli[chain]'``.
  - **Burns N Veo credits — one per link.** This test uses a 2-link manifest, so
    it spends ~2 credits per run. Do NOT run in CI without gating.

Criterion covered:
  CHAIN-E2E-1 — ``run_chain`` over a 2-link manifest (link 0 = T2V, link 1 = I2V
    seeded by link 0's extracted last frame) returns one ``ChainLinkResult`` per
    link, each pointing at an mp4 that exists on disk, and the seeded link's
    generate request is observed routing to the Start-image endpoint (NOT the
    text-only endpoint). The route invariant is asserted via the
    ``ui_automation_video.frame_attached`` structlog event, which fires only when
    a start frame is bound through the editor's media dialog — i.e. the i2v link
    actually fired ``...StartImage`` and not ``...Text`` (issue #125). The chain
    aborts loudly (``WireFormatError``) if a seeded link is misrouted to T2V, so
    a successful return is itself partial proof of the route; the explicit event
    assertion guards against a false positive where a clip comes back text-only.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio  # noqa: F401 — ensures asyncio mode is registered
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import Aspect, VideoModel
from gflow_cli.chain import ChainLinkResult, ChainLinkSpec, run_chain

# ---------------------------------------------------------------------------
# Module-level marker — every test in this file is e2e (opt-in only), and
# spends Veo credits, so it carries the e2e_video cost sub-marker. Both markers
# are default-deselected, so the file never runs in a plain ``pytest`` invocation.
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_video]

# ---------------------------------------------------------------------------
# Constants — tuned for minimum credit spend: veo-lite, 4 s, portrait, 2 links.
# Override via env for variation (per [[e2e-tests-parameterize]]).
# ---------------------------------------------------------------------------

_E2E_ASPECT_ENV = "GFLOW_CLI_E2E_VIDEO_ASPECT"
_E2E_MODEL_ENV = "GFLOW_CLI_E2E_VIDEO_MODEL"
_E2E_DURATION_ENV = "GFLOW_CLI_E2E_CHAIN_DURATION"

# Short, safe prompts — generic enough to pass content-policy, with motion so
# the seeded I2V link has something to continue.
_LINK0_PROMPT = "a calm forest clearing at dawn, slow push-in, cinematic"
_LINK1_PROMPT = "the camera continues drifting forward through the trees"

# The seeded (i2v) link MUST bind a start frame through the media dialog; this
# event fires only on that path. Its absence means the i2v link routed to the
# text-only endpoint (issue #125) — a false-positive chain.
_FRAME_ATTACHED_EVENT = "ui_automation_video.frame_attached"


def _aspect() -> Aspect:
    """Resolve the requested aspect from the environment variable.

    Defaults to PORTRAIT (the chain command's default ``--aspect 9:16``). Skips
    on unrecognised values so a typo doesn't silently burn credits on the wrong
    ratio.
    """
    raw = os.environ.get(_E2E_ASPECT_ENV, "portrait").strip().lower()
    if raw == "portrait":
        return Aspect.PORTRAIT
    if raw == "landscape":
        return Aspect.LANDSCAPE
    pytest.skip(f"Unsupported {_E2E_ASPECT_ENV}={raw!r} — set to 'portrait' or 'landscape'")


def _model() -> VideoModel:
    """Resolve the chain model. Defaults to veo-lite (cheapest i2v-capable Veo).

    omni-flash is intentionally NOT a default — it cannot seed i2v links and the
    chain rejects it before any spend (issue #125).
    """
    raw = os.environ.get(_E2E_MODEL_ENV, "veo-lite").strip().lower()
    model = VideoModel.from_cli(raw)
    if model is None:
        pytest.skip(f"{_E2E_MODEL_ENV}={raw!r} is not a known video model alias")
    if not model.supports_i2v_interpolation():
        pytest.skip(
            f"{_E2E_MODEL_ENV}={raw!r} cannot seed i2v chain links; "
            "use veo-lite / veo-fast / veo-quality / veo-lite-lp"
        )
    return model


def _duration() -> int:
    return int(os.environ.get(_E2E_DURATION_ENV, "4").strip())


@pytest.mark.asyncio
async def test_chain_two_link_seeds_i2v_from_last_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """CHAIN-E2E-1: a 2-link chain renders link 0 (T2V) then link 1 (I2V) seeded
    by link 0's extracted last frame.

    Asserts the 5-layer-style evidence shape:
      * one ``ChainLinkResult`` per link, in order;
      * every link's ``local_path`` mp4 exists on disk (file-count layer);
      * link 0 produced a seed frame on disk; the final link did not;
      * the seeded link bound a start frame through the media dialog — the
        ``frame_attached`` structlog event fired — proving the i2v request routed
        to ``...StartImage`` and NOT ``...Text`` (the issue-#125 route invariant).

    Spends ~2 Veo credits per run.
    """
    # Opt out of the autouse home-redirect so the REAL logged-in profile resolves
    # (per [[test-isolation-real-env-opt-out]]: without this the e2e silently
    # skips when intentionally run), but keep a throwaway DB so we never pollute
    # the real catalog.
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / "e2e_chain.db"))
    reset_settings()

    name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not name:
        pytest.skip("set GFLOW_CLI_E2E_PROFILE to a logged-in profile, then run with -m e2e_video")
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    profile = _resolve_profile_dir(name)
    if not profile.exists():
        pytest.skip(f"profile not found: {profile} — run `gflow auth login --profile {name}`")

    aspect = _aspect()
    model = _model()
    duration = _duration()

    links = [
        ChainLinkSpec(prompt=_LINK0_PROMPT, duration=duration),
        ChainLinkSpec(prompt=_LINK1_PROMPT, duration=duration),
    ]

    async with FlowApiClient(profile_dir=profile) as client:
        results: list[ChainLinkResult] = await run_chain(
            client=client,
            links=links,
            out_dir=tmp_path,
            model=model,
            aspect=aspect,
        )

    # --- file-count / shape layer -----------------------------------------
    assert len(results) == len(links), (
        f"expected one ChainLinkResult per link ({len(links)}), got {len(results)}"
    )
    for link in results:
        assert link.media_id, f"link {link.index} returned an empty media_id"
        assert link.local_path.exists(), (
            f"link {link.index} clip is missing on disk: {link.local_path!r}"
        )
        assert link.local_path.stat().st_size > 0, (
            f"link {link.index} clip is empty: {link.local_path!r}"
        )

    # --- seed-frame layer: non-final links extract a frame, the final one ---
    # does not (it seeds nothing).
    assert results[0].frame_path is not None and results[0].frame_path.exists(), (
        "link 0 must have extracted a last frame to seed link 1; "
        f"frame_path={results[0].frame_path!r}"
    )
    assert results[-1].frame_path is None, (
        "the final link seeds nothing and must not produce a seed frame; "
        f"frame_path={results[-1].frame_path!r}"
    )

    # --- route-invariant layer (issue #125) -------------------------------
    # The seeded link binds a start frame through the media dialog; this event
    # fires ONLY on that path, so its presence proves the i2v request routed to
    # ...StartImage and not the text-only ...Text endpoint. A misroute would have
    # already aborted the chain with WireFormatError before we got here, but the
    # explicit assertion guards the false positive where a text-only clip is
    # returned without the seed frame ever binding.
    events = [e["event"] for e in install_log_capture.entries]
    assert _FRAME_ATTACHED_EVENT in events, (
        f"expected a {_FRAME_ATTACHED_EVENT!r} event proving the seeded link bound "
        f"its start frame through the media dialog (i2v routed to ...StartImage, "
        f"not ...Text — issue #125); captured events: {events}"
    )
