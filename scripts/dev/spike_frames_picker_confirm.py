"""$0 spike (#792): does Flow's Frames picker carry an explicit confirm button?

Two cohorts disagree. The reporter on #792 measured a picker that STAYS OPEN after an
asset is clicked and needs an "Add to prompt" confirm; the maintainer's cohort still
auto-closes (tests/e2e/test_migrated_i2v_e2e.py passes unchanged). Before gflow clicks a
confirm it has never seen, measure whether the element EXISTS here and what anchors it.

The r2v spike (2026-09-05-migrated-r2v-attach-surface.md:72) named it
`button.detail-add-to-prompt-btn` on the @-mention entry into the same popover component.
This checks the FRAMES entry.

Closed vocabulary only: tag names and class tokens. Never text, aria-label, alt or src —
a signed-in Flow page carries the account email in aria-label (the _CLICK_POSTMORTEM_JS
rule, one surface over).

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<uuid> \
        .venv/Scripts/python.exe scripts/dev/spike_frames_picker_confirm.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import default_out_path  # noqa: E402

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    PICKER,
    PICKER_OPTION,
    PICKER_SEARCH,
    MigratedComposer,
)
from gflow_cli.api.transports.ui_automation import UiAutomationTransport  # noqa: E402

# Tag + class tokens only. No text, no attributes that can carry identity.
_DUMP_JS = r"""
(sel) => {
  const root = document.querySelector(sel);
  if (!root) return {present: false, buttons: []};
  return {
    present: true,
    buttons: [...root.querySelectorAll('button')].map(b => ({
      classes: [...b.classList],
      role: b.getAttribute('role'),
      disabled: b.disabled,
      visible: !!(b.offsetWidth || b.offsetHeight),
    })),
  };
}
"""


async def main() -> int:
    profile = os.environ["GFLOW_CLI_E2E_PROFILE"]
    project = os.environ["GFLOW_CLI_E2E_PROJECT"]
    from PIL import Image, ImageDraw

    from gflow_cli.config import Settings

    img = Image.new("RGB", (256, 256), (70, 70, 70))
    ImageDraw.Draw(img).ellipse((64, 64, 192, 192), fill=(40, 90, 220))
    frame = Path(default_out_path("spike_frame", ".png"))
    img.save(frame, format="PNG")

    out: dict[str, object] = {"project": "<redacted>"}
    transport = UiAutomationTransport()
    try:
        await transport.setup(Settings(_env_file=None).profile_subdir(profile))  # pyright: ignore[reportCallIssue]
        page = transport._page  # noqa: SLF001
        assert page is not None
        composer = MigratedComposer()
        await composer.ensure_editor(page, project, timeout_s=45.0)
        from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode

        await composer.apply_video_settings(
            page,
            GenerateVideoRequest(
                prompt="spike", mode=Mode.I2V, aspect=Aspect.LANDSCAPE, start_image=frame
            ),
        )
        media_id = await composer._upload_via_toolbar(page, project, frame)  # noqa: SLF001
        out["media_id_len"] = len(media_id)

        picker = await composer._open_frame_picker(page)  # noqa: SLF001
        await picker.locator(PICKER_SEARCH).first.click(timeout=4000)
        await page.keyboard.insert_text(frame.name)
        opts = picker.locator(PICKER_OPTION)
        await opts.first.wait_for(state="visible", timeout=8000)
        out["options_before"] = await opts.count()
        out["picker_before_click"] = await page.evaluate(_DUMP_JS, PICKER)

        await opts.first.click(timeout=4000)
        await asyncio.sleep(1.5)  # the grace window a confirm-click fix would use
        out["picker_after_click"] = await page.evaluate(_DUMP_JS, PICKER)
        out["picker_still_present"] = bool(await page.locator(PICKER).count())
    finally:
        await transport.teardown()

    path = default_out_path("frames_picker_confirm")
    Path(path).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {path}")
    print(json.dumps({k: v for k, v in out.items() if k != "picker_before_click"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
