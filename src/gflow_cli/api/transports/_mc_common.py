"""Selectors, constants and helpers shared by the MigratedComposer mixins (#1249 wave 2)."""

# The private helpers here are used by the _mc_* parts, not in this module (#1249).
# pyright: reportUnusedFunction=false, reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import unquote_plus, urlsplit
from uuid import uuid4

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports._common import (
    extract_project_id,
    raise_if_known_landing,
    safe_page_url,
)
from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    generation_records_matching,
    parse_frames,
    rpc_errors,
)
from gflow_cli.api.video import Aspect, Mode, VideoModel
from gflow_cli.errors import (
    CONTENT_SAFETY_REASONS,
    ConfigurationError,
    ContentPolicyError,
    InsufficientCreditsError,
    RpcResponseUnavailableError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WafRejectionError,
    WireFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from playwright.async_api import Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest

log = structlog.get_logger(__name__)

MIGRATED_PROJECT_URL = "https://flow.google.com/project/{project_id}"
#: The project listing the app reads on load: every clip, with the request that made it.
LISTING_RPC = "Zzl0ze"
READY_ANCHOR = ".settings-trigger-button"
OVERLAY = ".cdk-overlay-pane"
#: Playwright's own visibility engine — the detached overlays Angular leaves behind are
#: in the DOM but not visible, and only the visible ones can cover the composer.
VISIBLE_OVERLAY = f"{OVERLAY}:visible"
#: Escapes `_close_pane` will spend: exactly the stack depth measured after a model
#: switch (the menu, then the settings pane). No headroom — a third stacked overlay is
#: not something to absorb quietly, and `strict=True` turns it into a named failure.
PANE_CLOSE_ESCAPES = 2
RADIOGROUP = "[role='radiogroup']"
RADIO = "[role='radio']"
MENU_ITEM = "[role='menuitem']"
COMPOSER = "[contenteditable='true']"
#: Named contract for the migrated Angular settings surface. Diagnostics include this
#: value so a future Flow DOM variant gets a new contract instead of a fuzzy fallback.
MIGRATED_SETTINGS_CONTRACT = "migrated-angular-settings-v1"
#: Flow's agent-mode chip. Pressed, `.settings-trigger-button` stays in the DOM but gains
#: a bare `hidden` (display:none, 0x0, not hit-testable) — which is why every gate on it
#: waits for VISIBILITY and never `count()` (#749).
#:
#: Three constraints put this exact selector here, none of them cosmetic: the component
#: class, because in agent mode a SECOND `button[aria-pressed]` appears
#: (`agent-action-button`) and the bare attribute selector is ambiguous there;
#: `aria-pressed` rather than the label, because the label is translated; and `='true'`
#: specifically, which makes the locator self-guarding — it matches only when there is
#: something to undo, so the recovery cannot click a healthy composer INTO agent mode.
#:
#: `mode_control.py` handles this same split on labs and is deliberately not reused: it
#: refuses this host (`raise_if_migrated`), and its `AGENT_TOGGLE_SELECTOR` requires a
#: `span.content` this chip does not have.
#:
#: Accounts, measurements and the transition inventory:
#: docs/superpowers/spikes/2026-09-08-migrated-composer-agent-mode-hides-settings.md
AGENT_MODE_CHIP = "button.agent-mode-chip[aria-pressed='true']"
#: The same chip in ANY state. :data:`AGENT_MODE_CHIP` matches only a PRESSED chip, so it
#: cannot tell "this account has no agent/classic split" from "the split exists and is
#: currently off" — the distinction #799 turns on. Never click this one: it is not
#: self-guarding, and a click would toggle a healthy composer INTO agent mode.
AGENT_MODE_CHIP_ANY = "button.agent-mode-chip"
#: How long the classic composer gets to come back after the chip is clicked. The swap is
#: a local Angular re-render, not a navigation — measured well under a second on both
#: accounts — so this is headroom, not an expectation.
AGENT_RECOVERY_S = 20.0
#: The first slice of the readiness wait. Long enough for Angular to mount the composer
#: after `domcontentloaded` (the chip was reliably absent before that — see
#: `ensure_editor`), short enough that #799's agent-only composer, whose trigger never
#: becomes visible, is reported without paying the whole gate. A page that is neither
#: gets the rest of the gate on a re-entry, so slow loads keep their full budget.
AGENT_ONLY_EARLY_S = 5.0
#: A positive agent-only verdict is re-asked after this long: the verdict rests on the chip's
#: ABSENCE, and a slow mount can put the trigger in the DOM before the chip.
AGENT_ONLY_SETTLE_S = 2.0
#: Free reloads spent looking for the classic composer when a request needs it. One
#: observation so far (2026-11-04): an account normally given the classic composer was
#: served the agent-only one once, and the classic one on the next nine loads.
AGENT_ONLY_RELOADS = 3
AGENT_ONLY_RELOAD_WAIT_S = 5.0
#: The agent-only composer's own Agent-settings button — the thing its driver needs anyway.
AGENT_ONLY_ANCHOR = "flow-creative-agent-prompt-box button:has(mat-icon:text-is('tune'))"
#: What `ensure_editor` found: the classic migrated composer, or #799's agent-only one.
ComposerKind = Literal["classic", "agent_only"]
#: What :meth:`MigratedComposer._exit_agent_mode` did. `"blocked"` — the chip was there
#: and the click did not land — is not `"clicked"`: nothing was toggled, so there is
#: nothing to wait :data:`AGENT_RECOVERY_S` for.
AgentModeExit = Literal["clicked", "blocked", "absent"]
#: The submode radio that renders the Start/End frame chips (i2v).
FRAMES_LIGATURE = "crop_free"
DIALOG = "[role='dialog']"
# What Flow puts WHERE the submit button was when the account cannot afford the model.
# Short of credits, not necessarily out of them: measured 2026-09-07, 50 credits held
# against a 100-credit veo-quality request still rendered this. Anchored on the component
# class only: the measured element also carried an English aria-label, but this host
# localises aria-labels (`aria-label="Formatar"`, #745), so that half was a text selector.
# Measured 2026-09-07 by A/B on a short vs a funded account
# (scripts/dev/spike_migrated_submit_anchor.py). Finding, with the arithmetic and
# four things named as NOT measured -- including that the positive match on
# prompt-warning-button was seen exactly once:
# docs/superpowers/spikes/2026-09-07-credit-shortfall-looks-like-selector-drift.md
CREDITS_WARNING = "button.prompt-warning-button"
DIALOG_CLOSE = f"{DIALOG} button:has(mat-icon:text-is('close'))"

#: Google's `glue` consent bar — a Google-wide component, not one of Flow's. It is
#: `position: fixed`, `z-index: 1000`, and Flow's composer is bottom-anchored in the
#: same band, so the bar lands ON the settings trigger and on the submit button:
#: `elementFromPoint` over each returned the bar's label span in 5/5 rendered samples
#: (2026-09-11, `ci-probe`, same profile and project where the click had landed 3/3 the
#: day before). The labs driver survives this by accident — `_bypass_onboarding`'s
#: Tier 2 carries `button:has-text('Agree')` — and this host had no equivalent.
#: Evidence: docs/superpowers/spikes/2026-09-11-migrated-cookie-bar-blocks-the-composer.md
COOKIE_BAR = "#glue-cookie-notification-bar-1, .glue-cookie-notification-bar"
#: REJECT, not accept. Both buttons remove the bar and unblock the composer identically,
#: and only one of them answers a consent question on the operator's behalf. The class is
#: structural (glue's own BEM modifier), so this stays locale-invariant where matching
#: "No thanks" would not.
COOKIE_BAR_REJECT = "button.glue-cookie-notification-bar__reject"
#: The notice-only bar has no reject at all — just ``OK, got it`` (measured under
#: ``hl=vi``, 2026-10-10, found by the #963 spike). Acknowledging a notice answers no
#: consent question, so this is the fallback only when :data:`COOKIE_BAR_REJECT` is absent.
COOKIE_BAR_ACCEPT = "button.glue-cookie-notification-bar__accept"

#: ``YhhmEf`` is the text-to-video submit; ``eb1hJf`` the image-to-video one (a bound
#: Start chip switches the app between them — 2026-09-05 frames spike).
#: t2v submits on ``YhhmEf``, i2v on ``eb1hJf``, and an Ingredients (r2v) run on
#: ``MZZa6b`` — measured 2026-09-05. Watching only the first is why r2v looked
#: for several rounds like it never submitted at all.
SUBMIT_RPCS = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif")
#: The start+end (interpolation) submit. Same composer, different contract: the model
#: key is ``veo_3_1_interpolation_lite``, the body carries BOTH frame ids, and the rpc
#: name travels in the ``f.req`` body — the URL carries no ``rpcids`` query param
#: (captured 2026-09-16, issue #639). The reply embeds the standard generation record,
#: so status/terminal/download are shared with the other submit rpcs.
INTERPOLATION_SUBMIT_RPC = "nprQif"
IMAGE_SUBMIT_RPC = "ogiZ0b"
STATUS_RPCS = ("jwpduf", "as29s")
UPLOAD_RPC = "maseQ"
#: The model key Flow puts in the submit body. Two observed shapes: mode-infixed
#: (``abra_t2v_8s``, ``veo_3_1_i2v_lite``, ``veo_3_1_r2v_lite_low_priority``) and
#: mode-less (``veo_3_1_lite_lower_priority`` — what a plain t2v run sends). The second
#: alternative is not decoration: the case the r2v diagnostic exists to name is *a t2v
#: key on an r2v submit*, and that key carries no infix to match on. A mode-only
#: alternation reported "no model key" for exactly that body.
MODEL_KEY = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*_(?:t2v|i2v|r2v)_[a-z0-9_]+|veo_[a-z0-9_]+")
#: Omni 1.2's start+end family, ``*_i2v_fl_<n>s[_<res>]`` (#1178), as whole segments.
_I2V_FIRST_LAST_TOKEN = re.compile(r"(?:^|_)i2v_fl(?:_|$)")
#: A duration segment inside a model key, e.g. the ``_4s_`` of
#: ``veo_3_1_t2v_lite_4s_low_priority``. The base tier carries none.
_DURATION_IN_KEY = re.compile(r"_\d+s_")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# --- i2v: the toolbar upload path and the Frames picker ---------------------------
#: The toolbar `+` — the only add affordance OUTSIDE the prompt box (the box has its
#: own `add` icons). XPath because CSS cannot express "no such ancestor".
TOOLBAR_ADD = "xpath=//button[.//mat-icon[normalize-space()='add']][not(ancestor::flow-prompt-box)]"
UPLOAD_MENU_ITEM = f"{OVERLAY} {MENU_ITEM}:has(mat-icon:text-is('upload'))"
EMPTY_CHIP = "flow-prompt-box button.empty-chip"
BOUND_CHIP = "flow-prompt-box button.chip-container:has(img)"
PICKER = "flow-add-menu-popover-content"
PICKER_SEARCH = "input[type='text']"
PICKER_OPTION = "button.asset-item[role='option']"
#: The `/asb/<token>` of the grid tile whose `data-media-id` is the argument (#913).
#: The id is passed as an argument and compared in JS, never formatted into a selector.
_GRID_TOKEN_JS = (
    "(id) => { for (const e of document.querySelectorAll('img[data-media-id]')) {"  # noqa: S105 - not a password: a redaction marker or a JS snippet name
    " if (e.getAttribute('data-media-id') === id) {"
    " const m = (e.getAttribute('src') || '').match(/\\/asb\\/([A-Za-z0-9_-]+)/);"
    " return m ? m[1] : ''; } } return ''; }"
)
#: The `/asb/<token>` of each picker option, in display order.
_OPTION_TOKENS_JS = (
    "(sel) => [...document.querySelectorAll(sel)].map(o => {"
    " const img = o.querySelector('img');"
    " const m = ((img && img.getAttribute('src')) || '').match(/\\/asb\\/([A-Za-z0-9_-]+)/);"
    " return m ? m[1] : ''; })"
)
#: Each picker option's own title (the asset's name — user data, not a UI label).
_OPTION_TITLES_JS = (
    "(sel) => [...document.querySelectorAll(sel)].map(o => {"
    " const t = o.querySelector('.asset-title');"
    " return ((t && t.textContent) || '').trim(); })"
)
#: Collapse the selection to the END of the composer (#997). A click lands wherever the
#: composer's centre is, which with three chips on the line is chip 2 (measured,
#: docs/superpowers/spikes/2026-10-19-fourth-ref-caret.md). Not the End key: per the
#: reporter it stops at the end of the visual line once the chips wrap.
_CARET_TO_END_JS = (
    "(sel) => { const ce = document.querySelector(sel); if (!ce) return;"
    " ce.focus(); const r = document.createRange(); r.selectNodeContents(ce);"
    " r.collapse(false); const s = window.getSelection();"
    " s.removeAllRanges(); s.addRange(r); }"
)
#: Budget for a just-generated image to become mentionable, across editor reloads
#: (#913). Checked after each attempt, so a row can overrun it by one attempt.
EXISTING_REF_WAIT_S = 90.0
#: Pause before each editor reload while waiting for it.
EXISTING_REF_RELOAD_PAUSE_S = 5.0
#: ArrowDown loads the highlighted asset's detail (`UpteDb`); an Enter sent before it
#: settles fails (measured, scripts/dev/capture_migrated_attach_rpcs.py).
ARROW_SETTLE_MS = 3500
#: Pause between ArrowDowns on the way to a character option; only the last one settles.
ARROW_STEP_MS = 150


async def _visible_model_picker(page: Page, pane: Any, *, what: str) -> Any:
    """The settings pane's model picker button, visible, or a refusal.

    A picker present but hidden (#749's agent-mode shape) used to fall through and have
    its text read: that text "matched" the requested model, so the run went on and billed
    whatever Flow had bound (#788). A hidden control is refused, never read.
    """
    button = pane.locator("button").filter(has=_ligature(page, "arrow_drop_down")).first
    try:
        await button.wait_for(state="visible", timeout=4000)
    except Exception:
        hidden = bool(await button.count())
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the {what} (arrow_drop_down) is "
                f"{'hidden' if hidden else 'missing'} in the settings pane, so the model "
                "cannot be checked or chosen (host=migrated)"
            ),
            # Hidden is #749's agent-mode shape, the one cause with a user action.
            remediation_hint=(
                "Nothing was submitted. If the Agent chip is on in this project, turn it "
                "off in a browser and re-run."
                if hidden
                else None
            ),
        ) from None
    return button


#: The Ingredients sub-mode holds references; Frames holds the i2v chips.
INGREDIENTS_LIGATURE = "chrome_extension"
#: Safe base duration for Veo/unspecified-model reference-to-video. Measured 2026-09-06 at
#: $0 on a cohort that DOES render a duration row for Veo 3.1 Lite [Lower Priority]
#: (`capture_migrated_r2v_production_submit.py`, three route-blocked runs, same account,
#: model, references and gestures — only the duration changed):
#:
#:     4s -> veo_3_1_t2v_lite_4s_low_priority   mentions FLATTENED to prompt text
#:     6s -> veo_3_1_t2v_lite_6s_low_priority   mentions FLATTENED to prompt text
#:     8s -> veo_3_1_r2v_lite_low_priority      mentions intact, ids in the media slot
#:
#: At 8s the key drops its duration segment entirely — 8s IS the base tier, which is why
#: a cohort that renders no duration row at all (the maintainer's) has always submitted
#: r2v correctly. Below the base tier the app does not refuse: it silently degrades to
#: text-to-video and bills a clip with the file NAMES typed into the prompt and none of
#: the images on it.
R2V_DURATION_S = 8
#: A mention chip in the prompt document — how a reference is represented once
#: attached. Its ``data-reference-type`` decides which wire slot carries the id.
MENTION_CHIP = ".mention-chip"
FRAME_PICKER_OPEN_S = 8.0
#: The picker search is server-side (``UpteDb``) and a fresh upload is not always
#: indexed by the first query: on 2026-09-05 a project holding 30+ assets missed it
#: twice within 8 s and listed it on the third search. Each attempt reopens the popover.
FRAME_SEARCH_ATTEMPTS = 3
FRAME_SEARCH_RETRY_PAUSE_S = 2.0
#: ``maseQ`` answered in 1–3 s for a 120 KB PNG; a 20 MB file on a slow link needs more.
FRAME_UPLOAD_S = 60.0
#: The picker's own confirm ("Add to prompt"). Anchored on the class, never the label —
#: the copy is translated on this host. Measured on the FRAMES entry into the picker on
#: 2026-09-13 (``scripts/dev/spike_frames_picker_confirm.py``): present, visible and
#: enabled on the maintainer's cohort, where the option click ALSO commits, so it is
#: simply never needed there. #792 reports a cohort where the option click does NOT
#: commit and this button is the only way through. The r2v spike
#: (``2026-09-05-migrated-r2v-attach-surface.md:72``) named the same class on the
#: ``@``-mention entry into the same component.
#:
#: Do not replace this with "the picker button that is not an asset option": the other
#: visible button in that popover is ``header-close-btn``, so that guess clicks CLOSE.
PICKER_CONFIRM = "button.detail-add-to-prompt-btn"
FRAME_COMMIT_HIDDEN_S = 15.0
#: How long a picker is allowed to close on its own before the confirm is looked for. A
#: cohort that auto-closes is gone well inside this; one that does not is still up, and
#: clicking a confirm that a closing picker no longer has is a no-op count()==0.
FRAME_COMMIT_GRACE_S = 1.5
FRAME_THUMB_VISIBLE_S = 5.0
#: What a click that expired may be asked about — Playwright's four actionability
#: conditions, read back after the fact. See :meth:`MigratedComposer._click`.
#:
#: **Everything returned here is a closed vocabulary.** Tag names, booleans, and class
#: tokens matching a fixed framework prefix — never ``outerHTML``, ``textContent``,
#: ``aria-label``, ``title``, ``alt``, ``src`` or ``href``. An occluding element on a
#: signed-in Flow page routinely carries the account email in ``aria-label`` and a signed
#: media URL in ``src``, and this string is printed raw to the console, shipped through
#: structlog, emitted under ``--json``, and invited into a GitHub issue by the error
#: class's own remediation hint. PR #777 fixed exactly this bug one surface over.
_CLICK_POSTMORTEM_JS = r"""
(el) => {
  const cs = getComputedStyle(el);
  const box = el.getBoundingClientRect();
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  const top = (box.width && box.height) ? document.elementFromPoint(cx, cy) : null;
  const hit = !!top && (top === el || el.contains(top) || top.contains(el));
  // Angular/CDK, Material, Flow's own components, and Google's `glue` design system.
  // A layout class on a bare <div> is an accident; `cdk-overlay-backdrop` is a
  // component boundary and says what the thing IS, in any locale.
  const structural = (n) =>
    [...n.classList].filter(c => /^(cdk|mat|mdc|flow|glue)-/.test(c)).slice(0, 3).join('.');
  // The element on top is often an inner node that names nothing — a consent bar's
  // label is a bare <span> whose identity lives in an `id` the allowlist deliberately
  // drops, so the report read "it is covered by span". Climb to the nearest ancestor
  // that DOES name itself. Bounded by <body>; when nothing on the chain qualifies the
  // tag name is still reported, exactly as before.
  const carrier = (n) => {
    let cur = n;
    while (cur && cur !== document.body && !structural(cur)) cur = cur.parentElement;
    return (cur && structural(cur)) ? cur : n;
  };
  return {
    visible: cs.display !== 'none' && cs.visibility !== 'hidden'
             && box.width > 0 && box.height > 0,
    hidden_attr: el.hasAttribute('hidden'),
    enabled: !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true',
    hit_testable: hit,
    // The #593 mechanism. Measured on labs.google, never on this host — kept because a
    // reading that never fires costs nothing, and a missing one costs a wrong answer.
    body_blocked: getComputedStyle(document.body).pointerEvents === 'none',
    occluder: (top && !hit)
      ? ((c) => c.tagName.toLowerCase() + (structural(c) ? '.' + structural(c) : ''))(
          carrier(top))
      : null,
  };
}
"""

#: Names the submit control in a message. No CSS string can express the `arrow_forward`
#: ligature filter that builds it, so there is nothing here to rot into a selector.
SUBMIT_BUTTON = "the submit button (ligature 'arrow_forward')"

#: The submit reply arrived 4.0–4.6 s after the click in both measured runs.
SUBMIT_REPLY_BUDGET_S = 60.0
#: How long the lost-reply fallback waits for the project listing in its second tab.
LISTING_READ_BUDGET_S = 30.0
#: How far ``created`` may sit from the click when a t2v clip is found by its prompt (#1141).
#: It reads before the click by truncation alone (spike), and a re-run takes longer than
#: this to reach its own click. A clock running behind is undetectable here; the listing's
#: "exactly one" check is what covers it.
PROMPT_MATCH_MARGIN_S = 15.0
# Angular enables the arrow_forward button ~100 ms after `insert_text` lands in the
# composer (measured 2026-09-05, #670); checking it synchronously reads the stale state.
SUBMIT_ENABLE_BUDGET_S = 5.0
SUBMIT_ENABLE_POLL_S = 0.1
#: A ``jwpduf`` poll reports status 3 first; the record that carries the signed
#: URLs (``as29s``) followed 2–5 s later in every measured run. Wait that long
#: for it before settling for the URL-less record.
RESULT_URL_GRACE_S = 20.0
IMAGE_REPLY_BUDGET_S = 180.0
#: After a lost ``ogiZ0b`` body, how long the project grid may take to show the new
#: tile(s). Flow made the image on every #1075 run; only the reply body was lost.
IMAGE_RECOVERY_S = 60.0
#: Once the grid shows enough new tiles, how long it must hold still before they are
#: taken as this run's: another run's tile can land first (#1075 review).
IMAGE_RECOVERY_SETTLE_S = 3.0

#: Product names read back verbatim from the live migrated menu (v0.62.1's refusal
#: diagnostic, corroborated by the 2026-09-05 spike). These are the tiers a *not yet
#: moved* account may be routed to the new host for — see :func:`migrated_can_serve`.
VIDEO_MODEL_MENU_LABELS: dict[VideoModel, str] = {
    VideoModel.OMNI_FLASH: "Omni 1.1 Flash",
    VideoModel.OMNI_1_2_FLASH: "Omni 1.2 Flash",
    VideoModel.VEO_3_1_LITE: "Veo 3.1 - Lite",
    VideoModel.VEO_3_1_FAST: "Veo 3.1 - Fast",
    VideoModel.VEO_3_1_QUALITY: "Veo 3.1 - Quality",
}

#: The suffix Flow appends to a tier it is serving at lower priority. The labs driver
#: has matched ``veo_3_1_lite_lower_priority`` by this tag alone since #539
#: (``[role='menuitem']:has-text('[Lower Priority]')``), because through v0.67.0 no
#: capture had ever rendered the entry — the 2026-08-14 two-account capability matrix,
#: #650's duration capture and v0.61.0's refusal A/B all recorded a picker MISS.
#:
#: It has since been captured: on 2026-09-05 a migrated account rendered
#: ``Veo 3.1 - Lite [Lower Priority]``, and its picker was *defaulted* to that tier —
#: which is presumably why the labs captures missed it, having been taken on accounts
#: Flow was not throttling. Matching stays on the tag rather than moving to that label:
#: it is one account's rendering, the labs driver keys off the same tag, and a tag that
#: Flow appends to whichever tier it throttles survives it moving to another one.
#: Capture: ``docs/superpowers/spikes/2026-09-05-migrated-model-menu-lower-priority.md``.
LOWER_PRIORITY_TAG = "[Lower Priority]"


@dataclass(frozen=True)
class ModelMenuMatcher:
    """How one model's entry is recognised in the migrated model menu.

    ``contains`` must appear in the entry's text and ``excludes`` must not. Every
    ordinary tier excludes :data:`LOWER_PRIORITY_TAG`, because Flow's lower-priority
    entry is its sibling's label plus that suffix: matched as a bare substring,
    ``Veo 3.1 - Lite`` also matches ``Veo 3.1 - Lite [Lower Priority]``. That is the
    ambiguity #539 fixed on labs.google (whose selectors carry
    ``:not(:has-text('[Lower Priority]'))``) and which this port had dropped.
    """

    contains: str
    excludes: str | tuple[str, ...] | None = LOWER_PRIORITY_TAG

    def matches(self, text: str) -> bool:
        folded = text.casefold()
        if self.contains.casefold() not in folded:
            return False
        if self.excludes is None:
            return True
        exclusions = (self.excludes,) if isinstance(self.excludes, str) else self.excludes
        return all(exclusion.casefold() not in folded for exclusion in exclusions)


#: Every model the migrated menu can be *driven* to, including the lower-priority Lite
#: tier the routing gate above deliberately does not list.
VIDEO_MODEL_MENU_MATCHERS: dict[VideoModel, ModelMenuMatcher] = {
    **{model: ModelMenuMatcher(label) for model, label in VIDEO_MODEL_MENU_LABELS.items()},
    # No label to exclude a sibling by, and none needed: the tag IS the entry.
    VideoModel.VEO_3_1_LITE_LOWER_PRIORITY: ModelMenuMatcher(LOWER_PRIORITY_TAG, excludes=None),
}
ASPECT_LIGATURE: dict[Aspect, str] = {
    Aspect.LANDSCAPE: "crop_16_9",
    Aspect.PORTRAIT: "crop_9_16",
}
#: Ligature per aspect radio in the migrated composer's image settings.
IMAGE_ASPECT_LIGATURE: dict[ImageAspect, str] = {
    ImageAspect.LANDSCAPE: "crop_16_9",
    ImageAspect.PORTRAIT: "crop_9_16",
    ImageAspect.SQUARE: "crop_square",
    ImageAspect.LANDSCAPE_FOUR_THREE: "crop_landscape",
    ImageAspect.PORTRAIT_THREE_FOUR: "crop_portrait",
}

#: The aspects actually enumerated in that radiogroup. Four on 2026-09-08
#: (docs/superpowers/spikes/2026-09-08-migrated-image-submit-wire.md); five on
#: 2026-09-17, when ``crop_portrait`` (3:4) appeared between ``crop_square`` and
#: ``crop_9_16`` (scripts/dev/spike_migrated_aspect_radios.py, #864).
IMAGE_ASPECT_LIGATURE_MEASURED: frozenset[ImageAspect] = frozenset(IMAGE_ASPECT_LIGATURE)
IMAGE_MODEL_MENU_MATCHERS: dict[ImageModel, ModelMenuMatcher] = {
    # Exact enough to exclude the separate "Nano Banana 2 Lite" entry without
    # depending on the decorative banana glyph that precedes both live labels.
    ImageModel.NARWHAL: ModelMenuMatcher("Nano Banana 2", excludes=("Lite",)),
    ImageModel.GEM_PIX_2: ModelMenuMatcher("Nano Banana Pro"),
    ImageModel.HARBOR_SEAL: ModelMenuMatcher("Nano Banana 2 Lite"),
}

#: Wire tokens an ``ogiZ0b`` body may carry for a model, where not just its own name.
#: "Nano Banana 2.1" REPLACED "Nano Banana 2" in the menu and sends ``BELUGA``; Flow's
#: catalogue no longer lists ``NARWHAL`` at all (#958, measured on two accounts 2026-10-08,
#: ``scripts/dev/spike_nano_banana_21_wire.py``). Accepting BELUGA is only safe while the
#: two are never offered side by side: the "already selected" shortcut in
#: ``_select_image_model`` would then let a ``nano2`` request run 2.1 unnoticed.
IMAGE_MODEL_WIRE_TOKENS: dict[ImageModel, tuple[str, ...]] = {
    ImageModel.NARWHAL: ("NARWHAL", "BELUGA"),
}


def _unported_form(request: GenerateVideoRequest) -> str | None:
    """The noun for what this request asks of the new host that slice 1 does not
    drive, or ``None`` when the composer takes it. i2v is ported for **local**
    start AND end frames: the Frames picker on this host lists assets by display
    name with no UUID in its DOM (2026-09-05 spike), so a frame given by media UUID
    or ``@Name`` has nothing to anchor on yet. The End chip itself was measured on
    2026-09-15 (scripts/dev/spike_migrated_end_frame.py): two empty chips, Start
    and End by text, and clicking the End chip opens the library picker."""
    # Character entities are a different attach surface (a chip with an entity_id, in a
    # different wire slot), ported for r2v only. This check is MODE-INDEPENDENT and must stay
    # ahead of every early return (#716): it used to live inside the R2V branch, so a
    # t2v request returned `None` here without its entities ever being inspected, and
    # nothing downstream attaches one — `attach_start_frame` is i2v-only,
    # `attach_references` is r2v-only, the `read_chips` verification is r2v-only. The
    # result was a BILLED generation with the entity silently dropped, which is strictly
    # worse than exit 36: the refusal is free and the clip is a stranger.
    #
    # `migrated_can_serve` also refuses on `reference_entities`, but it only feeds
    # `prefer_migrated`, and an account Flow has already moved is routed by its URL
    # without consulting it — so that refusal is unreachable for exactly the accounts
    # that need it. This one is on the path every request takes.
    if request.reference_entities and request.mode is not Mode.R2V:
        return "character references"
    if len(request.reference_entities) > len(request.reference_entity_names):
        # Attached by name through the `@` picker; an id alone has nothing to type, and
        # would otherwise fail later as a confusing chip-count mismatch.
        return "a character given by entity id without a name"
    # R2V serves characters (#723): `run_video` attaches them through the `@` picker
    # and `read_chips` verifies each entity chip before the billed click. The earlier
    # refusal rested on a null submit reply (`MZZa6b -> [..., [5], "generic"]`,
    # measured 2026-09-07) that no longer reproduces: on 2026-10-26 an entity-bound
    # r2v run went end to end through the stock observer (exit 0). If that reply
    # comes back, the run ends as an honest unknown outcome (exit 9), not a guess.
    if request.mode is Mode.T2V:
        return None
    if request.mode is Mode.R2V:
        # Local files only, for the same reason i2v is: the picker lists assets by
        # display name and exposes no media id, so a reference gflow did not upload
        # itself has nothing to anchor on. Character entities are the exception above:
        # they are addressed by name on purpose, and verified by entity id after the fact.
        if not request.reference_images and not request.reference_entities:
            return "references given by name rather than a local file"
        return None
    if request.mode is not Mode.I2V:  # pragma: no cover - a fourth Mode would land here
        return f"the {request.mode.value} mode"
    if request.end_image_ref_id:
        return "an end frame given by Flow media UUID"
    if request.end_image_ref_name:
        return "an end frame given by @Name"
    if request.start_image_ref_id:
        return "a frame given by Flow media UUID"
    if request.start_image_ref_name:
        return "a frame given by @Name"
    if not isinstance(request.start_image, Path):
        return "image-to-video without a local start frame"
    return None


def migrated_can_serve(request: GenerateVideoRequest, project_id: str | None) -> bool:
    """Can the migrated composer take this request as it stands? Text-to-video, or
    image-to-video / reference-to-video from **local** files (start and end frames
    alike), in an existing project, with a model the new host offers (or none).
    Everything else — a frame or reference by UUID or ``@Name``, character
    references, a model this host has not been observed to offer — is not ported
    yet, so an account still served labs.google keeps the labs driver for it. A
    request with no project is not decided here: ``FlowApiClient.generate_video``
    creates one before any route is chosen (#864).

    Note what that last clause does NOT claim. A 2026-09-14 survey found
    ``labs.google/fx/tools/flow`` returning **HTTP 308** on the three profiles here
    that still hold a live Flow session — so none of *those* is served labs today.
    An account that is has been driven before: v0.67.0 ran a pt-locale profile on
    the labs route end to end (``docs/PROJECT_STATUS.md``, and
    ``tests/api/transports/test_migrated_dispatch.py`` pins the behaviour). So this
    branch is live code with live precedent, not a legacy arm.

    Gated on :data:`VIDEO_MODEL_MENU_LABELS`, not on the wider
    :data:`VIDEO_MODEL_MENU_MATCHERS`: this decides whether to *move* a request off
    labs.google, and pulling one there for a tier no capture has ever seen rendered
    would trade a working driver for an unverified one. An account Flow has already
    moved is routed by its URL and never reaches this question — for it,
    ``--model veo-lite-lp`` is driven by its matcher instead of refused outright."""
    if _unported_form(request) is not None or not project_id:
        return False
    if request.reference_entities:
        return False
    return request.model is None or request.model in VIDEO_MODEL_MENU_LABELS


def _unported_image_form(request: GenerateImageRequest) -> str | None:
    if request.refs and request.ref_paths:
        return "existing-image references mixed with local files"
    if any(not ref.in_project for ref in request.refs):
        # Referenced in place only when this run generated it here (#913) or the
        # catalog records it in this project (#995); any other UUID is not ported.
        return "a Flow media UUID the catalog does not record in this --project"
    if any(not ref.display_name for ref in request.refs):
        # Found by its Flow caption, then matched by thumbnail; Flow returned none.
        return "a reference to an image Flow returned without a caption"
    # Character references are attached by name through the `@` picker and verified as
    # entity chips before submit (#723). An id with no name has nothing to type, and
    # `run_images` attaches only named characters, so it is refused rather than dropped.
    if len(request.reference_entities) > len(request.reference_entity_names):
        return "a character given by entity id without a name"
    if request.instructions:
        return "Agent instructions"
    if request.model not in IMAGE_MODEL_MENU_MATCHERS:
        return f"the {request.model.value} model"
    if request.aspect not in IMAGE_ASPECT_LIGATURE_MEASURED:
        # Every aspect gflow knows is measured today (#864). Kept so a new aspect
        # added to the enum is refused as unported (exit 36), never left to miss its
        # radio and report frontend drift (exit 23) about a frontend behaving fine.
        return f"the {request.aspect.value} aspect ratio"
    return None


def migrated_images_prefer(
    request: GenerateImageRequest, *, page_url: str | None = None, project_id: str | None = None
) -> bool:
    """Whether an image request should prefer the migrated composer on ``auto``.

    Three independently necessary answers, so a miss in any one of them keeps
    the labs driver instead of stranding the run on the wrong host:

    - the migrated composer serves this request at all
      (:func:`_unported_image_form` names anything it does not);
    - a project is named or the page already sits in one — the labs driver
      auto-creates a project, the migrated composer needs ``--project``;
    - the page is not already inside an editor — there the served host rules.

    Pure predicate over the request and the URL: no browser, no spend. Routing
    only: the client no longer consults it for a reCAPTCHA mint (#891).
    """
    if _unported_image_form(request) is not None:
        return False
    if request.reference_entities or request.reference_entity_names:
        # Characters are driven on flow.google.com only when Flow SERVED it (#723); a
        # page served labs keeps the labs driver, which attaches them by entity id.
        return False
    editor_pid: str | None = None
    if isinstance(page_url, str):
        editor_pid = extract_project_id(page_url)
        if editor_pid is not None:
            # Already inside an editor: the served host rules.
            return False
    elif page_url is not None:
        # Test doubles and malformed URLs take the served host: preferring
        # migrated on an unreadable URL would route mocked labs-driver tests
        # (and any real page we failed to read) at the migrated composer.
        # Same totality discipline as :func:`flow_host_kind`.
        return False
    if project_id is None and editor_pid is None:
        return False
    return True


def _exact(label: str) -> re.Pattern[str]:
    return re.compile(r"^\s*" + re.escape(label) + r"\s*$")


def _duration(label: str) -> re.Pattern[str]:
    """A duration radio by its number — ``8s`` also matches ``8 giây`` (#963).

    The unit is a translated word, the number is not: the same exception, and the same
    bar, as ``migrated_upscale.menu_token_pattern``. Siblings in the pane share the
    leading digit: ``480p`` is refused because a digit follows, ``4K`` because a resolution
    suffix does, and the old count label ``4x`` because ``x`` does. A miss still raises
    exit 11 from :meth:`MigratedComposer._select` rather than clicking a wrong radio.
    """
    number = label.removesuffix("s")
    return re.compile(r"^\s*" + re.escape(number) + r"(?!\d)(?!\s*[pPkKxX]\b)\D*$")


def _unique_display_name(image_path: Path) -> str:
    """The name Flow will list an upload under — the file's own, plus a random tag.

    Every local file this driver uploads is found again **by display name**: the Frames
    picker searches it (i2v) and the ``@`` mention queries it (r2v). Flow lists an upload
    under the name it was given, so a second run of one file used to leave two identical
    entries and the lookup could bind the stale one. i2v broke the tie on the picker's
    newest-first order — a sort assumption about someone else's app, and it lost (#792:
    the submit-body check fired with ``eb1hJf does not carry the uploaded start frame``).

    The tag makes the match exact by construction, for both callers, trusting no ordering.
    The stem is kept so the asset stays recognisable in the user's library.
    """
    return f"{image_path.stem}-{uuid4().hex[:8]}{image_path.suffix}"


def _picker_pane(page: Any) -> Any:
    """The **live** library popover.

    ``.last`` is load-bearing: a search miss re-opens the picker (up to
    ``FRAME_SEARCH_ATTEMPTS`` times), so an earlier detached-but-hidden pane can still be
    in the DOM, and a ``.first`` hidden-wait would pass while the live picker is up.
    """
    return page.locator(OVERLAY).filter(has=page.locator(PICKER)).last


def _ligature(page: Any, name: str) -> Any:
    """A ``mat-icon`` whose ligature text is exactly ``name`` — for ``filter(has=…)``."""
    return page.locator("mat-icon").filter(has_text=_exact(name))


#: Google's ErrorInfo reason on a submit reCAPTCHA Enterprise scored as a bot. The labs
#: REST path has always raised WafRejectionError for it (KNOWN_ISSUES, HTTP 403).
UNUSUAL_ACTIVITY_REASON = "PUBLIC_ERROR_UNUSUAL_ACTIVITY"


def _outcome_unknown(detail: str) -> TransportTimeoutError:
    """A failure AFTER the submit click: Flow may have made and billed the generation.

    #1010 found exactly that on every arm (a finished clip, an extra image), so the
    error must not invite a retry, and the hint must not be the class's 30 s default."""
    return TransportTimeoutError(
        detail=detail,
        retryable=False,
        remediation_hint=(
            "Submit was already clicked, so Flow may have made and billed this "
            "generation. Open the project in Flow and check before re-running: "
            "a re-run bills again."
        ),
    )


async def _grid_images(page: Page) -> dict[str, str] | None:
    """The project grid's image tiles, media id -> tile src; ``None`` when unreadable."""
    from gflow_cli.api.transports import agent_only_composer as agent  # noqa: PLC0415 - cycle

    try:
        tiles = await page.evaluate(agent._TILES_JS)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    except Exception:  # noqa: BLE001 - a grid we cannot read recovers nothing
        return None
    if not isinstance(tiles, list):
        return None
    found: dict[str, str] = {}
    for tile in cast("list[dict[str, str]]", tiles):
        uuid = (
            agent._tile_uuid(tile, "image")  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            if tile.get("tile") == "flow-image-tile"
            else None
        )
        if uuid:
            found.setdefault(uuid, tile.get("poster", ""))
    return found


async def _new_grid_images(page: Page, baseline: set[str]) -> dict[str, str]:
    return {u: src for u, src in ((await _grid_images(page)) or {}).items() if u not in baseline}


async def _recover_images(
    page: Page, request: GenerateImageRequest, baseline: set[str] | None
) -> list[GeneratedImage]:
    """The images a submit made, read off the grid after its reply body was lost (#1075).

    Exactly ``request.count`` new tiles, unchanged across a settle read, is the only
    answer: more cannot be told apart from another run's, and without a readable
    before-picture every old tile would look new, so both stay "outcome unknown"."""
    from gflow_cli.api.transports import agent_only_composer as agent  # noqa: PLC0415 - cycle

    lost = f"migrated host: the {IMAGE_SUBMIT_RPC} submit reply arrived but its body was unreadable"
    if baseline is None:
        raise _outcome_unknown(f"{lost}, and the grid of {safe_page_url(page.url)} was unreadable")
    deadline = time.monotonic() + IMAGE_RECOVERY_S
    fresh = await _new_grid_images(page, baseline)
    while len(fresh) < request.count and time.monotonic() < deadline:
        await asyncio.sleep(min(1.0, max(deadline - time.monotonic(), 0.01)))
        fresh = await _new_grid_images(page, baseline)
    if len(fresh) == request.count:
        await asyncio.sleep(IMAGE_RECOVERY_SETTLE_S)
        fresh = await _new_grid_images(page, baseline)
    if len(fresh) != request.count or not all(fresh.values()):
        raise _outcome_unknown(
            f"{lost}, and the grid of {safe_page_url(page.url)} showed {len(fresh)} new "
            f"image(s) where {request.count} were requested"
        )
    log.info("migrated.image_recovered_from_grid", count=len(fresh))
    images: list[GeneratedImage] = []
    for uuid, src in fresh.items():
        url = agent._full_size_src(src)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        images.append(
            GeneratedImage(
                media_name=uuid,
                workflow_id="",
                seed=0,
                prompt=request.prompt,
                model_name_type=None,  # #789: nothing here observed the model either
                aspect_ratio=request.aspect.value,
                fife_url=url,
                dimensions=await agent._natural_size(page, url),  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            )
        )
    return images


def _submit_refusal(
    text: str, rpcids: tuple[str, ...]
) -> WafRejectionError | ContentPolicyError | None:
    """The typed refusal a submit reply states on the wire, or ``None``.

    flow.google.com refuses a submit with HTTP 200 and an error envelope whose payload
    is null, so ``parse_frames`` sees nothing and the run used to end as a video
    timeout or an image "no frame" wire fault, while the grid showed "We noticed some
    unusual activity". Measured 2026-09-27 (spike
    2026-09-27-migrated-refusal-is-on-the-wire). Only REASONS decide: a bare status
    is not a refusal, since the #723 entity submit Flow queued and ran replies ``[5]``.
    """
    for err in rpc_errors(text):
        if err.rpcid not in rpcids:
            continue
        route = f"batchexecute:{err.rpcid}"
        if err.reasons:
            log.info("migrated.submit_refused", rpc=err.rpcid, code=err.code, reasons=err.reasons)
        if UNUSUAL_ACTIVITY_REASON in err.reasons:
            # Same class as the labs raise sites for the same reason, with no per-site
            # override, so retryability is the class default there and here: a
            # per-profile WAF score that decays (KNOWN_ISSUES).
            return WafRejectionError(
                detail=f"Flow refused the submit: {UNUSUAL_ACTIVITY_REASON} (gRPC {err.code})",
                route=route,
                remediation_hint=(
                    "Flow's bot check scored this browser profile as unusual activity; "
                    "the prompt is not the cause and nothing was charged. Wait a few "
                    "hours before retrying on this profile, or use another profile. "
                    "See KNOWN_ISSUES: PUBLIC_ERROR_UNUSUAL_ACTIVITY."
                ),
            )
        # Same reason set the REST path maps to ContentPolicyError. Not yet observed on
        # this host; the class default (exit 5, not retryable) is the REST path's answer.
        safety = next((r for r in err.reasons if r in CONTENT_SAFETY_REASONS), None)
        if safety is not None:
            return ContentPolicyError(
                detail=f"Flow refused the submit: {safety} (gRPC {err.code})", route=route
            )
    return None


async def _raise_if_out_of_credits(page: Any) -> None:
    """Raise :class:`InsufficientCreditsError` when Flow has swapped the submit control
    for its insufficient-credits warning.

    Called from every path that concludes "the submit anchor is unusable", because an
    credit shortfall and a moved frontend are indistinguishable at that point -- and only
    one of them is a bug in gflow. Silent when the warning is absent or hidden (a node
    kept mounted on a funded account must not turn real drift into exit 37, #745), so
    genuine selector drift still surfaces as drift.
    """
    if await page.locator(CREDITS_WARNING).first.is_visible():
        log.info("migrated.submit_blocked_by_credits")
        raise InsufficientCreditsError(
            detail=(
                "migrated host: Flow replaced the submit control with its "
                "insufficient-credits warning, so this account cannot start a "
                "generation right now (host=migrated)"
            ),
        )


def url_rpcid(url: str) -> str | None:
    m = re.search(r"[?&]rpcids=([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def body_rpcid(body: str) -> str | None:
    """The rpc name inside a ``batchexecute`` POST body, or ``None``.

    The interpolation submit (``nprQif``) carries no ``rpcids`` query param — the
    name lives at the head of ``f.req``. ``request_body`` form-decodes first, so the
    match runs on plain text either way.
    """
    m = re.search(r'f\.req=\[\[\["([A-Za-z0-9]+)"', body or "")
    return m.group(1) if m else None


@dataclass
class _ClipByPrompt:
    """Finds a t2v clip after a lost submit reply, or refuses (#1141).

    Polls only collect candidates, because a per-reply decision cannot see a second
    same-prompt run. The listing, the one global view, decides."""

    project_id: str
    prompt: str
    model_key: str
    clicked_at: float
    candidates: dict[str, GenerationRecord] = field(default_factory=lambda: {})

    def _in_window(self, created: int | None) -> bool:
        if created is None:
            return False
        return (
            self.clicked_at - PROMPT_MATCH_MARGIN_S
            <= created
            <= time.time() + PROMPT_MATCH_MARGIN_S
        )

    def _is_older(self, created: int | None) -> bool:
        return created is not None and created < self.clicked_at - PROMPT_MATCH_MARGIN_S

    def _matches(self, rid: str, payload: Any) -> list[tuple[GenerationRecord, int | None]]:
        return generation_records_matching(
            rid,
            payload,
            project_id=self.project_id,
            prompt=self.prompt,
            model_key=self.model_key,
        )

    def _refusal(self, reason: str) -> TransportTimeoutError:
        log.warning("migrated.video_recovery_refused", reason=reason)
        return _outcome_unknown(
            f"migrated host: the submit reply body was lost and {reason}, so this run cannot "
            f"tell its clip from another one; it may still finish in "
            f"{MIGRATED_PROJECT_URL.format(project_id=self.project_id)}"
        )

    def note_poll(self, rid: str, payload: Any) -> TransportTimeoutError | None:
        """Collect running matches. A finished one is another run's, not a candidate."""
        for rec, created in self._matches(rid, payload):
            if rec.workflow_id in self.candidates:
                # A clip can finish during the listing read, and no poll names it again.
                self.candidates[rec.workflow_id] = rec
                continue
            if not rec.is_running:
                continue
            if not self._in_window(created):
                return self._refusal(f"running clip {rec.media_id} is older than this run")
            self.candidates[rec.workflow_id] = rec
        return None

    def decide(self, listing: Any | None) -> GenerationRecord:
        """The one clip, or the refusal. A listing that did not answer proves nothing."""
        if listing is None:
            raise self._refusal("the project listing did not answer")
        listed: set[str] = set()
        for rec, created in self._matches(LISTING_RPC, listing):
            if self._in_window(created):
                listed.add(rec.workflow_id)
                seen = self.candidates.get(rec.workflow_id)
                if seen is None or seen.is_running:
                    self.candidates[rec.workflow_id] = rec
            elif rec.is_running or not self._is_older(created):
                # Newer than the clock allows means the clock is behind, so the window
                # cannot be trusted to have excluded an older finished clip.
                raise self._refusal(f"clip {rec.media_id} falls outside this run's window")
        if len(self.candidates) != 1:
            ids = ", ".join(sorted(r.media_id for r in self.candidates.values()))
            raise self._refusal(f"{len(self.candidates)} clips match its prompt ({ids})")
        ((workflow_id, rec),) = self.candidates.items()
        if workflow_id not in listed:
            raise self._refusal(f"the project listing does not name clip {rec.media_id}")
        return rec


def _unreadable_rpc_error(rpcid: str, *, location: str) -> RpcResponseUnavailableError:
    """The response arrived but its body could not be read; writes may have committed."""
    return RpcResponseUnavailableError(
        detail=f"{rpcid} response body was unavailable at {location}",
        route=f"batchexecute:{rpcid}",
    )


async def _project_snapshot(
    page: Page, project_id: str, *, upload_name: str | None = None
) -> tuple[str, list[dict[str, str]]]:
    """Read persisted project state in a fresh tab without resetting the composer."""
    probe = await page.context.new_page()
    try:
        await probe.goto(
            MIGRATED_PROJECT_URL.format(project_id=project_id),
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        field = await _wait_anchor(
            probe,
            PROJECT_TITLE_INPUT,
            named="project title input",
            requested=project_id,
            at="migrated.rpc_readback",
            timeout_s=15,
        )
        deadline = time.monotonic() + 15.0
        while True:
            tiles = cast(
                "list[dict[str, str]]",
                await probe.evaluate(
                    """() => [...document.querySelectorAll('flow-image-tile')]
                .map(t => ({name: (t.querySelector('flow-tile-hover-footer .footer-title')
                    ?.textContent || '').trim(), id: t.querySelector('img[data-media-id]')
                    ?.getAttribute('data-media-id') || ''}))"""
                ),
            )
            # Wait for the media id too: the name can render before `img[data-media-id]`.
            if upload_name is None or any(
                t["name"] == upload_name and UUID_RE.fullmatch(t["id"]) for t in tiles
            ):
                break
            if time.monotonic() >= deadline:
                break  # the caller's exactly-one check turns this into "outcome unknown"
            # The project grid is a load-time asset listing, not a live subscription.
            # The upload's follow-up library write may finish after its response event.
            await probe.wait_for_timeout(1000)
            await probe.reload(wait_until="domcontentloaded", timeout=15_000)
            await field.wait_for(state="visible", timeout=15_000)
        return await field.input_value(), tiles
    finally:
        await probe.close()
        await page.bring_to_front()  # the composer's typing and `@` picker need focus


def _first_uuid(text: str) -> str | None:
    """The first UUID in a ``batchexecute`` reply — for ``maseQ`` that is the new
    media id (``[media_id, project_id, …]``, measured 2026-09-05)."""
    for rid, payload in parse_frames(text):
        if rid != UPLOAD_RPC:
            continue
        m = UUID_RE.search(str(payload))
        if m:
            return m.group(0)
    return None


def request_body(request: Any) -> str:
    """The POST body as text, or ``""`` when Playwright cannot decode it —
    a listener must not raise.

    Form-decoded, because ``batchexecute`` sends ``f.req=<percent-encoded JSON>``: raw, the
    model key reads as ``%5C%22veo_3_1_t2v_lite_4s_low_priority%5C%22`` and the diagnostic
    quoted it back to a user as ``22veo_3_1_t2v_lite_4s_low_priority``. UUIDs survive
    either way (nothing in them is escaped), so this is about what the message says, not
    about whether the assertion works.
    """
    try:
        body = str(request.post_data or "")
    except Exception:  # noqa: BLE001 - Playwright's post_data raises on undecodable bytes
        return ""
    return unquote_plus(body) if body else ""


def _i2v_body_problem(
    body: str, rpcid: str, media_id: str, end_media_id: str | None = None
) -> str | None:
    """Why this submit body is NOT the image-to-video generation the user asked for,
    or ``None``. A t2v key means the chip was empty at submit time; a body without
    the uploaded id means the picker bound some other asset. When ``end_media_id``
    is given the body must also carry the bound end frame."""
    if not body:
        return (
            f"migrated host: the {rpcid} submit body could not be read, so the "
            "image-to-video request could not be confirmed before Flow acted on it"
        )
    key = MODEL_KEY.search(body)
    key_text = key.group(0) if key else "no model key"
    if "_t2v_" in body or "_i2v_" not in body:
        return (
            f"migrated host: the submit went out on {rpcid} with a text-to-video model key "
            f"({key_text}) for an image-to-video (i2v) request — the start frame was not "
            "bound when the app submitted (the labs #125 shape on this host)"
        )
    if media_id not in body:
        other = [u for u in UUID_RE.findall(body) if u.lower() != media_id.lower()]
        return (
            f"migrated host: the {rpcid} submit body does not carry the uploaded start "
            f"frame {media_id} (ids in the body: {', '.join(other[:4]) or 'none'}) — the "
            "picker bound a different asset"
        )
    if end_media_id is not None and end_media_id not in body:
        other = [u for u in UUID_RE.findall(body) if u.lower() != end_media_id.lower()]
        return (
            f"migrated host: the {rpcid} submit body does not carry the uploaded end "
            f"frame {end_media_id} (ids in the body: {', '.join(other[:4]) or 'none'}) — the "
            "picker bound a different asset"
        )
    return None


def _interpolation_body_problem(
    body: str, rpcid: str, start_media_id: str | None, end_media_id: str | None
) -> str | None:
    """Why this submit body is NOT the start+end interpolation run, or ``None``.

    Interpolation is keyed off the bound END frame: without one the app must never
    send this rpc, so seeing it on a start-only run is a wire-format error rather
    than a run to adopt. With both frames bound the body must carry the
    interpolation model key and both uploaded ids (captured 2026-09-16, #639).
    """
    if start_media_id is None or end_media_id is None:
        return (
            f"migrated host: the app submitted on {rpcid} (start+end interpolation) "
            "for a run with no bound end frame — refusing to bill an interpolation "
            "the request never asked for"
        )
    if not body:
        return (
            f"migrated host: the {rpcid} submit body could not be read, so the "
            "start+end interpolation request could not be confirmed before Flow "
            "acted on it"
        )
    key = MODEL_KEY.search(body)
    key_text = key.group(0) if key else "no model key"
    # Interpolation keys differ per model: veo uses ``*_interpolation_*`` and omni
    # uses ``*_i2v_*s_first_last``. Both are MEASURED, not inferred —
    # ``veo_3_1_interpolation_lite`` on the contributor's account (#831) and
    # ``omni_flash_i2v_8s_first_last`` here on 2026-09-17 (see
    # docs/superpowers/spikes/2026-09-17-migrated-end-frame-submit-contract.md).
    # Matching on key SHAPE rather than a pinned literal is deliberate: the two
    # cohorts disagree on the key, so a literal would refuse a valid run on one of
    # them. A plain i2v/t2v key means a frame dropped before the app submitted.
    # Omni 1.2 sends ``macaron_cookie_i2v_fl_8s[_360p]`` (#1178, measured 2026-11-08,
    # docs/superpowers/spikes/2026-11-08-omni12-interpolation-key.md).
    if (
        "interpolation" not in key_text
        and "first_last" not in key_text
        and not _I2V_FIRST_LAST_TOKEN.search(key_text)
    ):
        return (
            f"migrated host: the submit went out on {rpcid} with {key_text} for a "
            "start+end (interpolation) request — expected an interpolation "
            "model key (one of the frames was not bound when the app submitted)"
        )
    for label, media_id in (("start", start_media_id), ("end", end_media_id)):
        if media_id not in body:
            other = [u for u in UUID_RE.findall(body) if u.lower() != media_id.lower()]
            return (
                f"migrated host: the {rpcid} submit body does not carry the uploaded "
                f"{label} frame {media_id} (ids in the body: "
                f"{', '.join(other[:4]) or 'none'}) — the picker bound a different asset"
            )
    return None


def _r2v_body_problem(body: str, rpcid: str, media_ids: tuple[str, ...]) -> str | None:
    """Why this submit body is NOT the references run the user asked for, or ``None``.

    The i2v twin of this check exists because a chip that was empty at submit time
    still submits — just without the frame. References fail the same way: the picker
    can close having inserted nothing, and the run then generates a clip with none of
    them, at full price.
    """
    if not body:
        return (
            f"migrated host: the {rpcid} submit body could not be read, so the "
            "references could not be confirmed before Flow acted on it"
        )
    key = MODEL_KEY.search(body)
    key_text = key.group(0) if key else "no model key"
    if "_r2v_" not in body:
        # A duration segment in a t2v key is the measured cause, not a guess: below
        # R2V_DURATION_S this host flattens the mention chips to plain prompt text and
        # submits t2v. Say so — "no reference was bound" alone sent the first reporter
        # looking at the attach code, which was working correctly.
        duration_hint = ""
        if key_text.startswith("veo_") and _DURATION_IN_KEY.search(key_text):
            duration_hint = (
                f" — the key names a duration, and this host offers r2v only at "
                f"{R2V_DURATION_S}s; below that it drops the references instead of "
                "refusing, so re-run without --duration"
            )
        return (
            f"migrated host: the submit went out on {rpcid} with {key_text} for a "
            f"references (r2v) request — no reference was bound when the app "
            f"submitted{duration_hint}"
        )
    missing = [m for m in media_ids if m not in body]
    if missing:
        known = {m.lower() for m in media_ids}
        other = [u for u in UUID_RE.findall(body) if u.lower() not in known]
        return (
            f"migrated host: the {rpcid} submit body is missing {len(missing)} of "
            f"{len(media_ids)} reference(s) ({', '.join(missing[:3])}; ids in the "
            f"body: {', '.join(other[:4]) or 'none'}) — the picker bound different assets"
        )
    return None


def _image_body_references(body: str) -> tuple[set[str], set[str]] | None:
    """The media and character ids an ``ogiZ0b`` body carries, or ``None`` if unreadable.

    Measured 2026-10-13 (``scripts/dev/spike_image_submit_references.py``, submit aborted
    in the browser): each request entry of the decoded ``f.req`` payload carries its MEDIA
    references at index 2 (``[[<media id>, null, null, null, 1], …]``) and its CHARACTER
    references at index 10 (``[[<entity id>], …]``). A media chip deleted from the prompt
    with Backspace still rode slot 2 — the prompt and the request state are not the same
    thing — so the guard reads the slots, not the prompt.
    """
    start = body.find("f.req=")
    if start < 0:
        return None
    try:
        outer, _ = json.JSONDecoder().raw_decode(body, start + len("f.req="))
        frames = [
            frame
            for batch in _as_list_any(outer)
            for frame in _as_list_any(batch)
            if _as_list_any(frame)[:1] == [IMAGE_SUBMIT_RPC]
        ]
        media: set[str] = set()
        entities: set[str] = set()
        for frame in frames:
            inner = _as_list_any(json.loads(str(_as_list_any(frame)[1])))
            for entry in _as_list_any(inner[1]):
                fields = _as_list_any(entry)
                for slot, into in ((2, media), (10, entities)):
                    for ref in _as_list_any(fields[slot] if len(fields) > slot else None):
                        ids = _as_list_any(ref)
                        if ids and isinstance(ids[0], str):
                            into.add(ids[0])
    except (ValueError, IndexError, TypeError):
        return None
    return (media, entities) if frames else None


def _image_body_problem(
    body: str,
    reference_ids: tuple[str, ...],
    model: ImageModel | None = None,
    entity_ids: tuple[str, ...] = (),
) -> str | None:
    """Why an ``ogiZ0b`` request is not the image run the caller asked for."""
    if not body:
        return (
            "migrated host: the image submit body could not be read, so the request "
            "could not be confirmed before Flow acted on it"
        )
    if model is not None and not any(
        token in body for token in IMAGE_MODEL_WIRE_TOKENS.get(model, (model.value,))
    ):
        return (
            f"migrated host: the image submit body does not carry requested model "
            f"{model.value} — refusing to report a generation made with persisted settings"
        )
    missing = [media_id for media_id in reference_ids if media_id not in body]
    if missing:
        return (
            "migrated host: the image submit body is missing uploaded reference(s) "
            f"{', '.join(missing[:4])} — refusing to report a text-only generation as i2i"
        )
    found = _image_body_references(body)
    if found is None:
        if reference_ids or entity_ids:
            return (
                "migrated host: the image submit body's reference slots could not be read, "
                "so it cannot be confirmed to carry exactly the requested references"
            )
        return None
    media, entities = found
    extra = sorted((media - set(reference_ids)) | (entities - set(entity_ids)))
    # Judged on the parsed slots, not the substring check above: an uploaded reference is
    # also named in the prompt document, so it can be "in the body" with slot 2 empty.
    lost = sorted((set(reference_ids) - media) | (set(entity_ids) - entities))
    if extra or lost:
        return (
            "migrated host: the image submit does not carry exactly the references asked "
            f"for (unexpected: {', '.join(extra[:4]) or 'none'}; missing: "
            f"{', '.join(lost[:4]) or 'none'}) — refusing to let Flow generate with them"
        )
    return None


#: Glob, not a Python predicate: a callable route sees every request (thumbnails,
#: video bytes) through Python for the whole poll (#1178 predict review).
BATCHEXECUTE_GLOB = "**/batchexecute**"
#: Remediation for a submit stopped by the route guard: it never left the browser.
ABORTED_BEFORE_SEND = (
    "Blocked before it left the browser: Flow received nothing and billed nothing. "
    "Safe to re-run; if it repeats, file a bug with the detail above."
)


def _submit_body_problem(
    rpcid: str,
    body: str,
    *,
    expect_media_id: str | None,
    expect_end_media_id: str | None,
    expect_reference_ids: tuple[str, ...],
) -> str | None:
    """Why this video submit body is not the run asked for, or ``None``. No side effects:
    the route guard and the request observer both call it on the same request."""
    if rpcid == INTERPOLATION_SUBMIT_RPC:
        return _interpolation_body_problem(body, rpcid, expect_media_id, expect_end_media_id)
    if expect_reference_ids:
        return _r2v_body_problem(body, rpcid, expect_reference_ids)
    if expect_media_id is not None:
        return _i2v_body_problem(body, rpcid, expect_media_id, end_media_id=expect_end_media_id)
    return None


async def _guard_image_submit(
    route: Any,
    request: Any,
    reference_ids: tuple[str, ...],
    model: ImageModel | None,
    entity_ids: tuple[str, ...] = (),
) -> str | None:
    """Abort an ``ogiZ0b`` submit that does not carry its references, before Flow acts.

    The observer path (``_image_body_problem`` in ``on_request``) only refuses to *report*
    such a run: by then Flow has generated it, spent quota and added an image (#913,
    SCENARIO #15). Returns the problem when aborted, ``None`` when let through.
    """
    problem = _image_body_problem(request_body(request), reference_ids, model, entity_ids)
    if problem is not None:
        await route.abort()
        return problem
    await route.continue_()
    return None


def _recovery_detail(page: Page, pressed: bool | None, error: Exception) -> str:
    """Why the trigger did not come back after the agent chip was clicked (#749)."""
    if pressed:
        return (
            f"migrated host: the account was in Flow's agent mode, the chip was "
            f"clicked, and it is STILL pressed {AGENT_RECOVERY_S:.0f}s later on "
            f"{safe_page_url(page.url)} (host=migrated) — the mode is pinned on this account; "
            f"turn the Agent chip off in a browser and re-run: {error}"
        )
    if pressed is None:
        return (
            f"migrated host: the account was in Flow's agent mode, the chip was "
            f"clicked, and the settings trigger ({READY_ANCHOR}) did not come "
            f"back within {AGENT_RECOVERY_S:.0f}s on {safe_page_url(page.url)} (host=migrated) "
            f"— the chip could not be read back, so whether the mode is still "
            f"on is unknown; check the Agent chip in a browser before filing "
            f"this as drift: {error}"
        )
    return (
        f"migrated host: Flow's agent mode was left, but the settings "
        f"trigger ({READY_ANCHOR}) still did not become visible within "
        f"{AGENT_RECOVERY_S:.0f}s on {safe_page_url(page.url)} (host=migrated) — the mode "
        f"is off, so this is ordinary selector drift: {error}"
    )


async def _select_resolution(page: Page, pane: Any, resolution: str) -> None:
    """Select 360p or 720p on models that offer a resolution control (Omni Flash)."""
    radios = pane.locator(RADIO)
    matches = radios.filter(has_text=re.compile(re.escape(resolution)))
    target = matches.first
    if not await target.count():
        groups = await pane.locator(RADIOGROUP).count()
        raise ConfigurationError(
            detail=(
                f"the migrated Flow host renders no resolution control offering {resolution!r} "
                f"for this account and model ({groups} option groups shown)"
            ),
            remediation_hint=(
                "Drop --resolution to accept Flow's default, or use a model whose "
                "settings pane shows a resolution row (Omni 1.1 Flash)."
            ),
        )
    if await target.get_attribute("aria-checked") == "true":
        log.info("migrated.resolution_already_selected", resolution=resolution)
        return
    await target.click(timeout=4000)
    await asyncio.sleep(0.2)
    if await matches.first.get_attribute("aria-checked") == "true":
        log.info("migrated.resolution_selected", resolution=resolution)
        return
    raise UiSelectorDriftError(
        detail=(
            f"migrated host: the resolution radio {resolution!r} did not become aria-checked "
            f"after the click (host=migrated)"
        ),
    )


# --- projects (#864) ---------------------------------------------------------------
#
# labs.google's `project.createProject` now answers 404 "Flow RPCs have been deprecated
# and disabled" (401 for a session carrying no labs token) on every profile measured, so
# a project is created and renamed here instead. Both operations were measured on
# 2026-09-17 (docs/superpowers/spikes/2026-09-17-project-create-on-flow-google-com.md),
# and both replies state the outcome, so success is read from the wire, not the URL.

MIGRATED_ROOT_URL = "https://flow.google.com/"
#: The projects page's floating `add` button — the page's only creation control.
NEW_PROJECT_BUTTON = "flow-projects-page button:has(mat-icon:text-is('add'))"
#: The project header's editable title.
PROJECT_TITLE_INPUT = "flow-editable-text input"
#: `["projects/*", [null, [title]], …]` -> `[project_id, [title]]`
CREATE_PROJECT_RPC = "jHPbke"
#: `["projects/<id>", [title], [["project_title"]], …]` -> `[title]`
RENAME_PROJECT_RPC = "o8DA4"


def _as_list_any(node: object) -> list[object]:
    return cast("list[object]", node) if isinstance(node, list) else []


#: Ceiling for a project create/rename reply. A slow (proxied) link measured a 40 s
#: create round trip (#1037); 30 s reported that as a failure while Flow made the project.
PAGE_RPC_TIMEOUT_S = 120.0
#: Selector waits stay short: under heavy throttling the anchors still rendered in < 5 s
#: (#1037 spike), so a longer wait would only delay a genuine selector-drift report.
ANCHOR_TIMEOUT_S = 30.0
#: The least time given to Flow's navigation to a new project after its reply was lost.
NAVIGATION_GRACE_S = 5.0


def _write_outcome_unknown(
    error: TransportTimeoutError | RpcResponseUnavailableError, rpcid: str, hint: str
) -> RpcResponseUnavailableError:
    """A write whose request was sent but whose result nobody saw: exit 7, never retried
    (the class is outside ``RETRYABLE_ERRORS``)."""
    return RpcResponseUnavailableError(
        detail=error.detail, route=f"batchexecute:{rpcid}", remediation_hint=hint
    )


async def _wait_anchor(
    page: Page, selector: str, *, named: str, requested: str, at: str, timeout_s: float
) -> Any:
    locator = page.locator(selector).first
    try:
        await locator.wait_for(state="visible", timeout=int(timeout_s * 1000))
    except PlaywrightTimeoutError as e:
        await raise_if_known_landing(page, requested=requested, at=at)
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the {named} ({selector}) did not become visible within "
                f"{timeout_s:.0f}s on {safe_page_url(page.url)} (host=migrated)"
            ),
        ) from e
    return locator


def _navigated_project_id(url: str, before: str) -> str | None:
    """The project id a create navigated to, read from the URL PATH on flow.google.com.

    Only the path counts: a redirect's query (``continue=``) can carry another project's
    URL. A URL the page already had before the click proves nothing about this create.
    """
    parts = urlsplit(url)
    if parts.path == urlsplit(before).path:
        return None
    if parts.scheme != "https" or parts.hostname != "flow.google.com":
        return None
    segments = parts.path.split("/")
    if len(segments) < 3 or segments[1] != "project" or not UUID_RE.fullmatch(segments[2]):
        return None
    return segments[2]


async def _rpc_reply(
    page: Page,
    rpcid: str,
    action: Callable[[], Awaitable[None]],
    *,
    timeout_s: float,
    write: bool = False,
) -> Any:
    """Run ``action`` and return the payload of the first ``rpcid`` frame the page gets.

    A refusal frame for ``rpcid`` raises at once instead of waiting out the timeout, and a
    body the page could not hand over is counted, so a timeout can say "no READABLE reply"
    rather than "Flow did not answer" (the body may have been sent and lost, #1009).
    With ``write``, a lost body for ``rpcid`` itself raises ``RpcResponseUnavailableError``
    at once: the write may have saved, so the caller reads the outcome back (#992).
    """
    reply: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    unreadable = [0]

    async def on_response(response: Any) -> None:
        url = str(getattr(response, "url", ""))
        if reply.done() or "batchexecute" not in url:
            return
        try:
            text = await response.text()
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal: it may not be ours
            unreadable[0] += 1
            ours = url_rpcid(url) == rpcid
            log.debug(
                "migrated.rpc_reply_body_unreadable",
                rpcid=rpcid,
                carries_rpcid=ours,
                error_type=type(exc).__name__,
            )
            if write and ours and not reply.done():
                # A write's reply arrived and was lost: the outcome is unknown, not late.
                reply.set_exception(_unreadable_rpc_error(rpcid, location=safe_page_url(page.url)))
            return
        for err in rpc_errors(text):
            if err.rpcid == rpcid and not reply.done():
                reply.set_exception(
                    WireFormatError(
                        detail=f"flow.google.com refused {rpcid} (code={err.code}, "
                        f"reasons={list(err.reasons)})",
                        route=f"batchexecute:{rpcid}",
                        remediation_hint=(
                            "Flow answered this request with an error frame, not with data. "
                            "Retry once; if it persists, report it with the code and reasons."
                        ),
                    )
                )
        for rid, payload in parse_frames(text):
            if rid == rpcid and not reply.done():
                reply.set_result(payload)

    page.on("response", on_response)
    try:
        await action()
        try:
            return await asyncio.wait_for(reply, timeout=timeout_s)
        except TimeoutError as e:
            raise TransportTimeoutError(
                detail=(
                    f"no readable {rpcid} reply from flow.google.com within {timeout_s:.0f}s "
                    f"on {safe_page_url(page.url)} (host=migrated; "
                    f"{unreadable[0]} batchexecute bodies unreadable)"
                ),
            ) from e
    finally:
        page.remove_listener("response", on_response)
