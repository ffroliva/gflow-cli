"""Live e2e: an ENABLED agent instruction card steers a t2i generation through
the agentic transport. Spends ~1 Flow image credit. Skipped by default; opt in
with ``GFLOW_CLI_E2E_PROFILE=<agentic profile>`` and ``-m e2e_image``.

Background — instructions spike (2026-07-08; see the plan's ``spike-findings.md``):
instruction cards only steer output through the agent's *reasoning* path. The old
imperative ``"Generate N image(s): {prompt}"`` directive was passed to the image
tool verbatim, so cards were ignored; a conversational request makes the agent
rewrite the tool prompt and fold in enabled cards. This test drives the REAL
`gflow` agentic path (force-agent + REST reconcile + conversational submit) and
asserts:

  * the **agentic** driver was bound (not classic) — ``ui_driver.bound mode=agentic``;
  * the instruction **reconcile PATCH ran** — ``agentic_driver.reconcile_instructions.patch``
    fired and NO ``…patch_failed`` warning (regression guard for the silent-400
    content-type bug);
  * generation succeeded and produced a **valid image on disk**.

A programmatic "is this a crayon drawing?" assertion isn't feasible in-process,
so the style itself is confirmed visually: the saved PNG path is logged for a
human (or vision-capable agent) to eyeball. The conversational-phrasing and
content-type regressions are locked down by unit tests
(tests/api/transports/drivers/test_agentic.py).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import AgentInstruction, Aspect, GenerateImageRequest, Model

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"

# Style-neutral prompt — it must contain NO style words, so any style in the
# output can only have come from the enabled instruction card.
_NEUTRAL_PROMPT = "a cat sitting on a wooden chair next to a window"

# Enabled card carrying a distinctive, unmistakable style. If the agent applies
# it (via the reasoning path), the output is a crayon drawing rather than the
# photorealistic default.
_CRAYON_CARD = AgentInstruction(
    text=(
        "Every image MUST be rendered as a flat 2D children's crayon drawing on "
        "textured paper, with visible waxy strokes and a bright primary palette."
    ),
    enabled=True,
    title="Crayon style",
)


def _image_kind(path: Path) -> str | None:
    with path.open("rb") as f:
        head = f.read(12)
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    return None


@pytest.mark.asyncio
async def test_enabled_card_steers_agentic_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """LIVE: `gflow` agentic t2i with an enabled crayon card produces a valid
    image, exercising the reconcile PATCH + conversational submit end-to-end."""
    from gflow_cli.config import reset_settings

    # Opt out of the autouse home-redirect so the REAL logged-in profile resolves;
    # keep a throwaway DB so the real catalog is never polluted.
    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / "e2e_instructions.db"))
    # Force the agentic composer so the driver binds deterministically regardless
    # of the server-side A/B cohort flap.
    monkeypatch.setenv("GFLOW_CLI_FORCE_AGENT_UI", "1")
    reset_settings()

    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(f"set {_E2E_PROFILE_ENV} to a logged-in agentic profile, run with -m e2e_image")
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    profile = _resolve_profile_dir(name)
    if not profile.exists():
        pytest.skip(f"profile not found: {profile} — run `gflow auth login --profile {name}`")

    req = GenerateImageRequest(
        prompt=_NEUTRAL_PROMPT,
        aspect=Aspect.LANDSCAPE,
        model=Model.NARWHAL,
        instructions=(_CRAYON_CARD,),
    )

    async with FlowApiClient(profile_dir=profile, out_dir=tmp_path) as client:
        project = await client.create_project(title="gflow-cli e2e instructions")
        image = await client.generate_image(project_id=project.project_id, req=req)

        assert image.media_name, "generation returned no media_name"
        assert image.fife_url.startswith("http"), f"unexpected fife_url: {image.fife_url!r}"

        out_path = tmp_path / f"{image.media_name}.png"
        saved = await client.download_image(image, out_path)
        saved_path = Path(str(saved))
        assert saved_path.exists() and saved_path.stat().st_size > 0, "no image bytes written"
        assert _image_kind(saved_path) is not None, "downloaded bytes are not a known image format"

    # --- Evidence: the AGENTIC path (not classic) ran and reconcile succeeded. ---
    events = [e.get("event") for e in install_log_capture.entries]
    bound = [e for e in install_log_capture.entries if e.get("event") == "ui_driver.bound"]
    assert any(e.get("mode") == "agentic" for e in bound), (
        f"expected the agentic driver to bind; ui_driver.bound events: {bound}"
    )
    assert "agentic_driver.reconcile_instructions.patch" in events, (
        "instruction reconcile PATCH never ran — cards were not synced"
    )
    assert "agentic_driver.reconcile_instructions.patch_failed" not in events, (
        "instruction reconcile PATCH failed (content-type / auth regression)"
    )

    # The style itself is a visual check — surface the saved path for inspection.
    print(f"\n[VISUAL CHECK] Expect a CRAYON drawing (not photorealistic): {saved_path}")  # noqa: T201
