"""Drive Flow's migrated ``flow.google.com`` editor (Angular Material).

Supported generation paths are t2v, i2v from a local start frame, r2v from local
reference files, t2i, and i2i from local reference files.

Google is moving accounts from ``labs.google/fx/tools/flow`` onto
``flow.google.com`` (issue #639). The migrated app is the same product on a
different widget toolkit: ligatures live in ``<mat-icon>`` instead of ``<i>``,
the settings popover is a ``cdk-overlay`` pane of ``[role=radiogroup]`` /
``[role=radio]`` buttons instead of ``role=menu`` tabs, the model picker is a
``[role=menu]`` of ``[role=menuitem]``s, and the composer is a ``contenteditable``
(the ``textarea`` next to it is not clickable). On the wire it is ``batchexecute``,
not aisandbox REST: video submit is ``YhhmEf``/``eb1hJf``/``MZZa6b`` and image submit
is ``ogiZ0b``. The app
then polls ``jwpduf`` every 5 s by itself and fetches the result with ``as29s`` — so
this driver **observes** the page's own traffic and adds none. A start frame goes in
through the editor's own upload (``maseQ``) and its library picker. Recon with
measurements: ``docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md``,
``docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md``, and
``docs/superpowers/spikes/2026-09-08-migrated-image-submit-wire.md``.

Every anchor here is structural or a Material Symbols ligature; the only text
matched is a numeric token (``8s``, ``x2``) or a product name (``Veo 3.1 - Lite``).
``aria-label`` values are translated on this host and are never used.

Selector trap recorded by the spike: Playwright's CSS ``:text-matches('\\s…')``
goes through CSS string escaping, which turns ``\\s`` into ``s`` — labels are
matched with a Python-side ``filter(has_text=re.compile(...))`` instead.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote_plus, urlsplit

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports._common import extract_project_id, raise_if_known_landing
from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    generation_record,
    image_records,
    parse_frames,
)
from gflow_cli.api.video import (
    I2V_DEFAULT_MODEL,
    Aspect,
    Mode,
    VideoModel,
    VideoResult,
    VideoStarted,
    VideoStatus,
)
from gflow_cli.errors import (
    ConfigurationError,
    FlowHostMigratedError,
    InsufficientCreditsError,
    MediaUploadRejectedError,
    ReferenceNotFoundError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)
from gflow_cli.redaction import redact_sensitive_text

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest, VideoStartedCallback

log = structlog.get_logger(__name__)

MIGRATED_PROJECT_URL = "https://flow.google.com/project/{project_id}"
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
#: How long the classic composer gets to come back after the chip is clicked. The swap is
#: a local Angular re-render, not a navigation — measured well under a second on both
#: accounts — so this is headroom, not an expectation.
AGENT_RECOVERY_S = 20.0
#: What :meth:`MigratedComposer._exit_agent_mode` did. `"blocked"` — the chip was there
#: and the click did not land — is not `"clicked"`: nothing was toggled, so there is
#: nothing to wait :data:`AGENT_RECOVERY_S` for.
AgentModeExit = Literal["clicked", "blocked", "absent"]
#: The submode radio that renders the Start/End frame chips (i2v).
FRAMES_LIGATURE = "crop_free"
DIALOG = "[role='dialog']"
# What Flow puts WHERE the submit button was when the account cannot afford the model.
# Short of credits, not necessarily out of them: measured 2026-09-07, 50 credits held
# against a 100-credit veo-quality request still rendered this. Both
# halves are structural (a component class and an ARIA label), not a display string, so
# this stays locale-invariant: the aria-label is the English attribute Angular emits, not
# rendered text. Measured 2026-09-07 by A/B on a short vs a funded account
# (scripts/dev/spike_migrated_submit_anchor.py). Finding, with the arithmetic and
# four things named as NOT measured -- including that the positive match on
# prompt-warning-button was seen exactly once:
# docs/superpowers/spikes/2026-09-07-credit-shortfall-looks-like-selector-drift.md
CREDITS_WARNING = "button.prompt-warning-button, [aria-label*='Insufficient credits']"
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

#: ``YhhmEf`` is the text-to-video submit; ``eb1hJf`` the image-to-video one (a bound
#: Start chip switches the app between them — 2026-09-05 frames spike).
#: t2v submits on ``YhhmEf``, i2v on ``eb1hJf``, and an Ingredients (r2v) run on
#: ``MZZa6b`` — measured 2026-09-05. Watching only the first is why r2v looked
#: for several rounds like it never submitted at all.
SUBMIT_RPCS = ("YhhmEf", "eb1hJf", "MZZa6b")
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
#: The Ingredients sub-mode holds references; Frames holds the i2v chips.
INGREDIENTS_LIGATURE = "chrome_extension"
#: The only duration at which this host offers reference-to-video. Measured 2026-09-06 at
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
FRAME_COMMIT_HIDDEN_S = 15.0
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
# Angular enables the arrow_forward button ~100 ms after `insert_text` lands in the
# composer (measured 2026-09-05, #670); checking it synchronously reads the stale state.
SUBMIT_ENABLE_BUDGET_S = 5.0
SUBMIT_ENABLE_POLL_S = 0.1
#: A ``jwpduf`` poll reports status 3 first; the record that carries the signed
#: URLs (``as29s``) followed 2–5 s later in every measured run. Wait that long
#: for it before settling for the URL-less record.
RESULT_URL_GRACE_S = 20.0
IMAGE_REPLY_BUDGET_S = 180.0

#: Product names read back verbatim from the live migrated menu (v0.62.1's refusal
#: diagnostic, corroborated by the 2026-09-05 spike). These are the tiers a *not yet
#: moved* account may be routed to the new host for — see :func:`migrated_can_serve`.
VIDEO_MODEL_MENU_LABELS: dict[VideoModel, str] = {
    VideoModel.OMNI_FLASH: "Omni 1.1 Flash",
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
#: Ligature per aspect. Only the four in :data:`IMAGE_ASPECT_LIGATURE_MEASURED`
#: were observed on the migrated host; ``crop_portrait`` is this driver's guess at
#: what a 3:4 radio WOULD be called, kept so that adding it later is a one-line
#: change, and deliberately not reachable until something measures it.
IMAGE_ASPECT_LIGATURE: dict[ImageAspect, str] = {
    ImageAspect.LANDSCAPE: "crop_16_9",
    ImageAspect.PORTRAIT: "crop_9_16",
    ImageAspect.SQUARE: "crop_square",
    ImageAspect.LANDSCAPE_FOUR_THREE: "crop_landscape",
    ImageAspect.PORTRAIT_THREE_FOUR: "crop_portrait",
}

#: The aspects actually enumerated in the migrated composer's radiogroup —
#: ``[crop_16_9*, crop_landscape, crop_square, crop_9_16]``, one account,
#: 2026-09-08 (docs/superpowers/spikes/2026-09-08-migrated-image-submit-wire.md).
IMAGE_ASPECT_LIGATURE_MEASURED: frozenset[ImageAspect] = frozenset(
    {
        ImageAspect.LANDSCAPE,
        ImageAspect.PORTRAIT,
        ImageAspect.SQUARE,
        ImageAspect.LANDSCAPE_FOUR_THREE,
    },
)
IMAGE_MODEL_MENU_MATCHERS: dict[ImageModel, ModelMenuMatcher] = {
    # Exact enough to exclude the separate "Nano Banana 2 Lite" entry without
    # depending on the decorative banana glyph that precedes both live labels.
    ImageModel.NARWHAL: ModelMenuMatcher("Nano Banana 2", excludes=("Lite",)),
    ImageModel.GEM_PIX_2: ModelMenuMatcher("Nano Banana Pro"),
}


def _unported_form(request: GenerateVideoRequest) -> str | None:
    """The noun for what this request asks of the new host that slice 1 does not
    drive, or ``None`` when the composer takes it. i2v is ported for a **local**
    start frame only: the Frames picker on this host lists assets by display name
    with no UUID in its DOM (2026-09-05 spike), so a frame given by media UUID or
    ``@Name`` has nothing to anchor on yet, and the End chip is unmeasured."""
    # Character entities are a different attach surface (a chip with an entity_id, in a
    # different wire slot) and unported. This check is MODE-INDEPENDENT and must stay
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
    if request.reference_entities:
        # STILL REFUSED — but NOT because the backend rejects it. The port is half done
        # in a narrower way than previously recorded here (#723).
        #
        # What works: `attach_character_entities` drives the `@` picker, commits the
        # chip, and verifies it carries data-reference-type="entity" with the requested
        # id. `migrated.character_entities_attached` fires and Flow loads the
        # character's voice sample. And the SUBMIT IS ACCEPTED: measured 2026-09-07,
        # entity-bound submissions appear in Flow's own gallery as **Queued** and
        # proceed. Nothing is refused server-side.
        #
        # What breaks is the OBSERVER, and the mechanism is exact:
        #
        #   MZZa6b -> [["wrb.fr","MZZa6b",null,null,null,[5],"generic"]]
        #
        # The submit reply carries a NULL payload, so it never names a media id and the
        # `submitted` future in submit_and_observe never resolves. That wait is bounded
        # by SUBMIT_REPLY_BUDGET_S (60 s), a value calibrated on runs where "the submit
        # reply arrived 4.0-4.6 s after the click" — i.e. a plate-based generation
        # against an idle queue. So the run times out (exit 9, TransportTimeoutError)
        # while the job sits in Flow's queue and completes on its own.
        #
        # This is also what the ORIGINAL note here described as "the submit never
        # produces a reply, three runs, 60 s each, cause unknown". Three timeouts
        # against a queue, read as a refusal. Two later comments (including one of
        # mine) hardened that misreading further; both were wrong.
        #
        # Queue latency is not incidental: Flow documents a limit of FIVE concurrent
        # generations, and rate-limits per-minute throughput after heavy daily use, so
        # 60 s is routinely too short in real production.
        #
        # THE FIX, when someone takes it: on a null submit payload, fall through to the
        # status poll (jwpduf/as29s) keyed on the project instead of requiring the
        # submit reply to name the media id, and make the budget configurable. The guard
        # stays only until that lands, because today the CLI would report a timeout on a
        # generation that is actually running — worse than an honest refusal.
        return "character references"
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
    if request.end_image or request.end_image_ref_id or request.end_image_ref_name:
        return "an end frame"
    if request.start_image_ref_id:
        return "a frame given by Flow media UUID"
    if request.start_image_ref_name:
        return "a frame given by @Name"
    if not isinstance(request.start_image, Path):
        return "image-to-video without a local start frame"
    return None


def migrated_can_serve(request: GenerateVideoRequest, project_id: str | None) -> bool:
    """Can the migrated composer take this request as it stands? Text-to-video, or
    image-to-video / reference-to-video from **local** files, in an existing project,
    with a model the new host offers (or none). Everything else — an end frame, a
    frame or reference by UUID or ``@Name``, character references, a fresh project,
    a labs-only model — is not ported yet, so an unmoved account keeps the labs
    driver for it.

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
    if request.refs:
        return "a reference given by Flow media UUID"
    if request.reference_entities:
        return "character references"
    if request.instructions:
        return "Agent instructions"
    if request.model not in IMAGE_MODEL_MENU_MATCHERS:
        return f"the {request.model.value} model"
    if request.aspect not in IMAGE_ASPECT_LIGATURE_MEASURED:
        # The aspect radiogroup was enumerated once on this host and carried four
        # radios — crop_16_9, crop_landscape, crop_square, crop_9_16 — with no
        # crop_portrait. Refusing here is the difference between exit 36 ("gflow
        # has not ported this") and exit 23 ("file a frontend-drift bug"), and the
        # second is a lie: nothing is drifting. If a later enumeration finds the
        # radio, move the aspect into the measured map rather than deleting this.
        return f"the {request.aspect.value} aspect ratio"
    return None


def _exact(label: str) -> re.Pattern[str]:
    return re.compile(r"^\s*" + re.escape(label) + r"\s*$")


def _ligature(page: Any, name: str) -> Any:
    """A ``mat-icon`` whose ligature text is exactly ``name`` — for ``filter(has=…)``."""
    return page.locator("mat-icon").filter(has_text=_exact(name))


async def _raise_if_out_of_credits(page: Any) -> None:
    """Raise :class:`InsufficientCreditsError` when Flow has swapped the submit control
    for its insufficient-credits warning.

    Called from every path that concludes "the submit anchor is unusable", because an
    credit shortfall and a moved frontend are indistinguishable at that point -- and only
    one of them is a bug in gflow. Silent when the warning is absent, so genuine
    selector drift still surfaces as drift.
    """
    if await page.locator(CREDITS_WARNING).count():
        log.info("migrated.submit_blocked_by_credits")
        raise InsufficientCreditsError(
            detail=(
                "migrated host: Flow replaced the submit control with its "
                "insufficient-credits warning, so this account cannot start a "
                "generation right now (host=migrated)"
            ),
        )


def _rpcid(url: str) -> str | None:
    m = re.search(r"[?&]rpcids=([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


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


def _post_data(request: Any) -> str:
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


def _i2v_body_problem(body: str, rpcid: str, media_id: str) -> str | None:
    """Why this submit body is NOT the image-to-video generation the user asked for,
    or ``None``. A t2v key means the chip was empty at submit time; a body without
    the uploaded id means the picker bound some other asset."""
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
        if _DURATION_IN_KEY.search(key_text):
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
            f"{len(media_ids)} uploaded reference(s) ({', '.join(missing[:3])}; ids in the "
            f"body: {', '.join(other[:4]) or 'none'}) — the picker bound different assets"
        )
    return None


def _image_body_problem(
    body: str,
    reference_ids: tuple[str, ...],
    model: ImageModel | None = None,
) -> str | None:
    """Why an ``ogiZ0b`` request is not the image run the caller asked for."""
    if not body:
        return (
            "migrated host: the image submit body could not be read, so the request "
            "could not be confirmed before Flow acted on it"
        )
    if model is not None and model.value not in body:
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
    return None


class MigratedComposer:
    """Settings → prompt → submit → observe, against the migrated editor."""

    # --- readiness ------------------------------------------------------------

    async def ensure_editor(self, page: Page, project_id: str, *, timeout_s: float = 30.0) -> None:
        """Land on ``flow.google.com/project/<id>`` (direct — no labs.google visit
        needed on either kind of account) and wait for the settings trigger."""
        target = MIGRATED_PROJECT_URL.format(project_id=project_id)
        current = str(getattr(page, "url", "") or "")
        if not current.startswith(target):
            log.info("migrated.navigate", url=target)
            await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        await self._dismiss_dialog(page)
        trigger = page.locator(READY_ANCHOR).first
        try:
            await trigger.wait_for(state="visible", timeout=int(timeout_s * 1000))
        except Exception as e:
            # Before blaming the anchor, ask the prior question: is this even the page
            # we asked for? Flow answers a project navigation with its public /about
            # landing when it will not open that project for this session (#756), and
            # `flow_host_kind` cannot see it — /about and /project/<id> share an origin.
            # Reaching this line on a landing page means the trigger was never going to
            # be here, so probing for the agent chip below is meaningless too.
            raise_if_known_landing(page, requested=target, at="migrated.ensure_editor")
            # Only now look for agent mode. Probing for the chip BEFORE this wait raced
            # the SPA: `goto` returns on `domcontentloaded` and Angular mounts the
            # composer seconds later, so the chip was reliably absent at that point, the
            # recovery no-opped, and the run failed exactly as it did before the fix
            # (caught by this change's own e2e, 2026-09-08). Waiting first also costs a
            # healthy run nothing — no extra query is issued unless the gate has failed.
            state, click_error = await self._exit_agent_mode(page)
            if state == "absent":
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: the settings trigger ({READY_ANCHOR}) did not "
                        f"become visible within {timeout_s:.0f}s on {page.url} "
                        f"(host=migrated): {e}"
                    ),
                ) from e
            if state == "blocked":
                # Nothing was toggled, so the trigger cannot have changed — spending
                # AGENT_RECOVERY_S here buys a verdict already in hand. The click's own
                # exception IS the message: a modal eating the click is a different bug
                # from a pinned mode, and only that exception says which one this is.
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: the account is in Flow's agent mode, which hides "
                        f"the settings trigger, and the chip ({AGENT_MODE_CHIP}) could not "
                        f"be clicked on {page.url} after {timeout_s:.0f}s (host=migrated) — "
                        f"something is covering it; turn the Agent chip off in a browser and "
                        f"re-run. Readiness gate: {e}. Chip click: {click_error}"
                    ),
                ) from click_error
            try:
                await trigger.wait_for(state="visible", timeout=int(AGENT_RECOVERY_S * 1000))
            except Exception as e2:
                # Which of these two it is decides who can fix it, so the chip is read
                # BACK rather than assumed. Reporting both as "the mode may be pinned"
                # hides genuine selector drift behind a mode the driver already left.
                pressed = await self._agent_chip_pressed(page)
                if pressed:
                    detail = (
                        f"migrated host: the account was in Flow's agent mode, the chip was "
                        f"clicked, and it is STILL pressed {AGENT_RECOVERY_S:.0f}s later on "
                        f"{page.url} (host=migrated) — the mode is pinned on this account; "
                        f"turn the Agent chip off in a browser and re-run: {e2}"
                    )
                elif pressed is None:
                    detail = (
                        f"migrated host: the account was in Flow's agent mode, the chip was "
                        f"clicked, and the settings trigger ({READY_ANCHOR}) did not come "
                        f"back within {AGENT_RECOVERY_S:.0f}s on {page.url} (host=migrated) "
                        f"— the chip could not be read back, so whether the mode is still "
                        f"on is unknown; check the Agent chip in a browser before filing "
                        f"this as drift: {e2}"
                    )
                else:
                    detail = (
                        f"migrated host: Flow's agent mode was left, but the settings "
                        f"trigger ({READY_ANCHOR}) still did not become visible within "
                        f"{AGENT_RECOVERY_S:.0f}s on {page.url} (host=migrated) — the mode "
                        f"is off, so this is ordinary selector drift: {e2}"
                    )
                raise UiSelectorDriftError(detail=detail) from e2
        # One count() on the happy path, for the cohort that would render the agent prompt
        # box while LEAVING the trigger visible: `send_prompt` would type into the agent
        # composer ([contenteditable='true'].first) and nothing downstream would notice.
        #
        # It OBSERVES and does not act, which is the whole point. Clicking here would
        # mutate a server-remembered account setting on a run that is otherwise healthy,
        # and it would do it in the one window where that is wrong: right after a
        # successful recovery, where a chip still reporting `aria-pressed='true'` for a
        # frame would toggle the account straight back INTO agent mode. `AGENT_MODE_CHIP`
        # being self-guarding only holds while the click is reserved for a trigger that
        # did NOT come up. No cohort has been measured here, so a log line is the honest
        # instrument — the next occurrence is then diagnosable from a run instead of a
        # re-run (the same reason #719 asks for telemetry before a fix).
        if await self._agent_chip_pressed(page):
            log.warning("migrated.agent_mode_chip_pressed_while_ready", issue_ref="#752")
        log.info("migrated.editor_ready", url=page.url)

    @staticmethod
    async def _agent_chip_pressed(page: Page) -> bool | None:
        """Is Flow's agent-mode chip pressed right now? One count, and never the failure.

        ``None`` — the query did not answer at all — is a third answer, not a quiet
        ``False``. Both of the claims this feeds are unsafe to make on an unanswered
        probe: "you were in agent mode" sends a user to fix a mode they may never have
        been in, and its negation, "the mode is off, so file a drift bug", sends a user
        whose account really is pinned to file a bug about a healthy frontend. A caller
        that only needs the safe direction can use the falsiness; one that reports on the
        absence has to look at ``None``.
        """
        try:
            return bool(await page.locator(AGENT_MODE_CHIP).first.count())
        except Exception as e:  # noqa: BLE001 - an unreadable page is not an answer
            log.warning("migrated.agent_mode_probe_failed", error=str(e)[:200])
            return None

    @classmethod
    async def _exit_agent_mode(cls, page: Page) -> tuple[AgentModeExit, Exception | None]:
        """Turn Flow's agent mode off if it is on.

        Agent mode `hidden`s the settings trigger this driver waits on (#749), and Flow
        remembers the chip per account — so without this, a single click in a browser
        leaves every later run failing as selector drift. :data:`AGENT_MODE_CHIP` matches
        only ``aria-pressed='true'``, so on a healthy composer this is one cheap count.

        The three outcomes are not interchangeable, which is why this is not a bool:
        ``"clicked"`` means the page was asked to change and may still be re-rendering,
        ``"blocked"`` means it was never asked — there is nothing to wait for — and
        ``"absent"`` means the mode was not the problem. ``"blocked"`` carries the click's
        own exception, so the caller can chain it instead of truncating it into a warning
        nobody reads.
        """
        if not await cls._agent_chip_pressed(page):
            return "absent", None

        # Some accounts leave the Agent panel expanded over the chip. Close only the
        # structural panel close button; the pressed-state chip has already been
        # confirmed above, so this cannot click a healthy composer into agent mode.
        panel_close = (
            page.locator("flow-agent-panel button").filter(has=_ligature(page, "close")).first
        )
        try:
            if await panel_close.count() and await panel_close.is_visible():
                await panel_close.click(timeout=5000)
                await asyncio.sleep(0.2)
        except Exception as e:  # noqa: BLE001 - best-effort panel cleanup
            log.warning("migrated.agent_panel_close_failed", error=str(e)[:200])

        try:
            await page.locator(AGENT_MODE_CHIP).first.click(timeout=5000)
        except Exception as e:  # noqa: BLE001 - the caller decides what it means
            log.warning("migrated.agent_mode_exit_failed", error=str(e)[:200])
            return "blocked", e
        log.info("migrated.agent_mode_exited", issue_ref="#749")
        return "clicked", None

    @staticmethod
    async def _dismiss_dialog(page: Page) -> None:
        """One attempt at the modal the host shows over a fresh editor (the "Get
        started" changelog on a first visit, #593's twin): its ``close`` icon button,
        else Escape. Best-effort — a dialog that stays is reported by whichever
        click it blocks next, with the overlay named there."""
        dialog = page.locator(DIALOG).first
        try:
            if not await dialog.is_visible():
                return
            close = page.locator(DIALOG_CLOSE).first
            via = "close" if await close.count() else "escape"
            if via == "close":
                await close.click(timeout=3000)
            else:
                await page.keyboard.press("Escape")
            log.info("migrated.dialog_dismissed", via=via)
        except Exception as e:  # noqa: BLE001 - best-effort, never the failure itself
            log.warning("migrated.dialog_not_dismissed", error=str(e)[:200])

    # --- clicking, and saying why a click did not land ---------------------------

    async def _click(self, page: Page, locator: Any, *, named: str, timeout: int) -> None:
        """Click, and when it expires report what was actually TRUE — never why.

        Playwright's actionability gate has four conditions — visible, stable, receives
        events, enabled — and a click that fails any of them expires as a bare
        ``TimeoutError`` carrying no locator, no exit code and no cause. That is #776:
        `editor_ready`, five seconds, exit 1, nothing to act on.

        **This reads; it does not diagnose.** Two candidate causes (#593, #752 finding #7)
        were unmeasurable on this host as of the 2026-09-10 spike, and a guard built on
        either would sometimes answer confidently and wrong (#770). So it states
        observations; "every reading was healthy" is one of them.

        Costs nothing on a healthy run — the read happens only in the except branch, which
        is also the rule :func:`_common.raise_if_known_landing` already states: a guard
        placed ahead of the probe deletes the evidence that would correct it.

        Only a Playwright timeout is reinterpreted. A closed page or a detached frame is a
        different failure and travels unchanged.
        """
        try:
            await locator.click(timeout=timeout)
        except PlaywrightTimeoutError as e:
            raise UiSelectorDriftError(
                detail=await self._why_the_click_missed(page, locator, named, timeout)
            ) from e

    async def _why_the_click_missed(
        self, page: Page, locator: Any, named: str, timeout: int
    ) -> str:
        """The message for a click that expired: locator first, observations after.

        Locator first is not cosmetic. ``redact_sensitive_text`` truncates to 500 chars
        at this raise site (``data/redaction.py``), so every surface sees the same cap and
        anything variable-length — the occluder's class list — has to sit behind the one
        part that must always survive it.
        """
        head = f"migrated host: {named} did not accept a click within {timeout} ms"
        try:
            # Through the LOCATOR, not a selector string. Half this driver's controls are
            # built by filtering on a ligature (`button` + `arrow_forward`), which no
            # `document.querySelector` can express — and the submit button, where losing
            # attribution costs the most, is one of them.
            state: dict[str, Any] = await locator.evaluate(_CLICK_POSTMORTEM_JS)
        except Exception as e:  # noqa: BLE001 - a diagnostic never replaces the failure
            # Detached, cross-origin, or the document replaced under us. All three are
            # "we could not look", and none of them may swallow the failure itself.
            return redact_sensitive_text(
                f"{head} — it could not be read back ({str(e)[:120]}) (host=migrated)"
            )

        seen: list[str] = []
        # Agent mode first: it is the one cause here with a user action attached, and
        # it hides the trigger with a bare `hidden` that touches neither the body's
        # pointer-events nor the hit-test — so nothing else below would notice it.
        if await self._agent_chip_pressed(page):
            seen.append(
                "the account is in Flow's agent mode, which hides it — turn the Agent "
                "chip off in a browser and re-run"
            )
        # One chain, because these are competing readings of the SAME question — can a
        # pointer reach it — ordered most specific first. The JS only hit-tests
        # `if (box.width && box.height)`, so an unrendered element always reports no hit
        # test too; as independent `if`s that said "it is not rendered" and "it answers no
        # hit test" about one fact.
        if state.get("hidden_attr"):
            seen.append("it carries a bare `hidden` attribute")
        elif not state.get("visible"):
            seen.append("it is not rendered (display, visibility, or a zero-sized box)")
        elif state.get("occluder"):
            seen.append(f"it is covered by {state['occluder']}")
        elif not state.get("hit_testable"):
            # Rendered, nothing named itself: whatever is on top is outside the document.
            # Not "healthy" — letting it fall through would claim hit-testable of an
            # element that had just failed the hit test.
            seen.append("it answers no hit test at its own centre")

        # Separate axes: an element can be disabled, or the whole page blocked, whatever
        # the chain above found.
        if not state.get("enabled"):
            seen.append("it is disabled")
        if state.get("body_blocked"):
            seen.append("the page is accepting no pointer events at all")

        if not seen:
            # Every readable condition is healthy. Saying so eliminates three of
            # Playwright's four checks instead of inventing one of them.
            seen.append(
                "it was visible, enabled and hit-testable at the moment the click "
                "expired, so nothing readable on the page explains it — Playwright also "
                "requires a stable bounding box, so the control was most likely still "
                "moving or being re-rendered"
            )
        # Belt and braces over the JS allowlist above.
        return redact_sensitive_text(f"{head} — {'; '.join(seen)} (host=migrated)")

    # --- settings ---------------------------------------------------------------

    async def apply_video_settings(self, page: Page, request: GenerateVideoRequest) -> None:
        """Mode, model, aspect, duration, count — through the radios, with read-back.

        Model goes first: like on labs.google the duration row is model-state.
        """
        pane = await self._open_pane(page)
        try:
            await self._select(page, pane, axis="mode", lig="videocam")
            if request.mode is Mode.I2V:
                # Frames renders the Start/End chips the attach stage binds to. Flow
                # remembers the last submode per account, so it is set, not assumed.
                await self._select(page, pane, axis="submode", lig=FRAMES_LIGATURE)
            if request.mode is Mode.R2V or request.reference_entities:
                # Ingredients is where references live, and the app derives the r2v model
                # key from this plus the picker choice — the same run sends
                # veo_3_1_r2v_lite_low_priority here and veo_3_1_lite_low_priority under
                # Frames — so nothing maps that by hand.
                #
                # A CHARACTER reference needs it too, whatever mode the caller asked for
                # (#723). Flow treats an attached character as reference-to-video: the
                # 2026-09-07 payload capture, taken in Ingredients, submitted
                # `abra_r2v_8s`. A t2v request that attached a character chip while the
                # composer sat under Frames clicked submit and got no reply at all —
                # measured, not inferred. So the submode follows the REFERENCE, not the
                # mode name.
                await self._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)
            model = request.model
            if model is None and request.mode is Mode.I2V:
                # #125: a queued MCP payload carries model=None, and without this bind
                # the editor submits on whatever tier it last remembered — possibly a
                # 100-credit one for a run the caller expected to cost 10.
                model = I2V_DEFAULT_MODEL
                log.info("migrated.i2v_model_defaulted", model=model.value, issue_ref="#125")
            if model is not None:
                await self._select_model(page, pane, model)
            await self._select(page, pane, axis="aspect", lig=ASPECT_LIGATURE[request.aspect])
            if request.duration is not None:
                if request.mode is Mode.R2V and request.duration != R2V_DURATION_S:
                    raise ConfigurationError(
                        detail=(
                            f"the migrated Flow host offers reference-to-video only at "
                            f"{R2V_DURATION_S}s; at {request.duration}s it does not refuse but "
                            "silently drops the references and submits a text-to-video run "
                            "with their file names typed into the prompt — a full-price clip "
                            "with none of the images on it"
                        ),
                        remediation_hint=(
                            f"Drop --duration (r2v pins {R2V_DURATION_S}s on its own), pass "
                            f"--duration {R2V_DURATION_S}, or run this length on labs with "
                            "GFLOW_CLI_FLOW_HOST=labs.google."
                        ),
                    )
                await self._select(page, pane, axis="duration", text=f"{request.duration}s")
            elif request.mode is Mode.R2V:
                await self._pin_r2v_duration(page, pane)
            await self._select(page, pane, axis="count", text=f"x{request.count}")
            log.info(
                "migrated.settings_applied",
                aspect=request.aspect.value,
                duration=request.duration,
                count=request.count,
                # The EFFECTIVE model — `request.model` is None on an i2v run that
                # took the #125 default, and logging that read as "no model bound".
                model=model.value if model else None,
            )
        except BaseException:
            # A close failure must never replace the error that is already travelling.
            # `_close_pane` in a bare `finally` did exactly that: a `--model` Flow does
            # not offer raises ConfigurationError (exit 11) naming the offered models,
            # and a stuck pane then overwrote it with UiSelectorDriftError (exit 23),
            # losing the list the user needed. On this path the pane is best-effort.
            await self._close_pane(page, strict=False)
            raise
        await self._close_pane(page, strict=True)

    async def apply_image_settings(self, page: Page, request: GenerateImageRequest) -> None:
        """Bind Image mode, model, aspect and count with read-back before submission."""
        pane = await self._open_pane(page)
        try:
            await self._select(page, pane, axis="mode", lig="image")
            await self._select_image_model(page, pane, request.model)
            await self._select(
                page,
                pane,
                axis="aspect",
                lig=IMAGE_ASPECT_LIGATURE[request.aspect],
            )
            await self._select(page, pane, axis="count", text=f"x{request.count}")
            log.info(
                "migrated.image_settings_applied",
                model=request.model.value,
                aspect=request.aspect.value,
                count=request.count,
            )
        except BaseException:
            await self._close_pane(page, strict=False)
            raise
        await self._close_pane(page, strict=True)

    async def _dismiss_cookie_bar(self, page: Page) -> None:
        """Clear Google's consent bar, which sits on top of the controls we click.

        Called from :meth:`_open_pane` — the one place both the image and the video path
        take their FIRST click, and early enough that consent is stored before anything
        else on the page is touched. The bar was already up 114 ms after
        ``domcontentloaded`` in every measured sample, so nothing here has to wait for it.

        **Best-effort on purpose.** A bar that refuses to go is not turned into a second
        error path: the click one line later fails through :meth:`_click`, whose
        post-mortem now names ``div.glue-cookie-notification-bar`` as the occluder. One
        attributed failure beats two competing ones, and it costs no code.
        """
        bar = page.locator(COOKIE_BAR).first
        try:
            if not await bar.is_visible():
                return
            await bar.locator(COOKIE_BAR_REJECT).first.click(timeout=3000)
            await bar.wait_for(state="hidden", timeout=3000)
        except Exception as e:  # noqa: BLE001 - the click post-mortem reports what is left
            log.warning("migrated.cookie_bar_not_dismissed", error=str(e)[:120])
            return
        log.info("migrated.cookie_bar_dismissed")

    async def _open_pane(self, page: Page) -> Any:
        await self._dismiss_cookie_bar(page)
        trigger = page.locator(READY_ANCHOR).first
        try:
            # Visibility, not `count()`. Agent mode leaves the trigger in the DOM under a
            # bare `hidden` (#749), and the mode can flip between `ensure_editor` and
            # here — a count-guard then walks into a click that expires as a bare
            # Playwright TimeoutError, with no exit code and no mention of the mode.
            await trigger.wait_for(state="visible", timeout=5000)
        except Exception as e:
            why = (
                " — the account is in Flow's agent mode, which hides it; turn the Agent "
                "chip off in a browser and re-run"
                if await self._agent_chip_pressed(page)
                else ""
            )
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the settings trigger ({READY_ANCHOR}) is not visible"
                    f"{why} (host=migrated)"
                ),
            ) from e
        # The other half of #752 finding #7: the guard above became a visibility wait,
        # this click stayed bare, and the comment above describes what it went on doing.
        await self._click(page, trigger, named=READY_ANCHOR, timeout=5000)
        # THE overlay that holds the option groups — not `.last`: once the model
        # menu (a second overlay) has opened and closed, a detached menu pane can
        # still be the last one in the DOM, and every axis after `--model` then
        # reads "0 option groups" (measured 2026-09-05, $0 run).
        pane = page.locator(OVERLAY).filter(has=page.locator(RADIOGROUP)).last
        try:
            await pane.locator(RADIOGROUP).first.wait_for(state="visible", timeout=8000)
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    "migrated host: the settings pane opened but rendered no option "
                    "groups ([role='radiogroup']) (host=migrated)"
                ),
            ) from e
        return pane

    def _blocking_overlays(self, page: Page) -> Any:
        """Visible overlays that are ours to dismiss — the settings pane and the model
        menu, identified by what they contain.

        Scoped rather than every ``.cdk-overlay-pane``: those classes are generic CDK
        and Flow mounts snackbars, tooltips and dialogs in the same container, none of
        which cover the composer or answer to Escape. An unrelated toast visible at the
        wrong moment would otherwise burn both escapes and abort a run that was fine.
        """
        return page.locator(VISIBLE_OVERLAY).filter(has=page.locator(f"{RADIOGROUP}, {MENU_ITEM}"))

    async def _close_pane(self, page: Page, *, strict: bool = True) -> None:
        """Dismiss every visible settings/menu overlay, and verify that none is left.

        ``strict=False`` downgrades a stuck pane to a warning. It is passed on the path
        where an exception is already in flight, so a close failure cannot mask the
        error the caller actually needs — see :meth:`apply_video_settings`.

        One Escape is not enough after ``--model``. Selecting from the menu leaves
        Angular with **two** stacked overlays — the settings pane and the menu opened
        over it — and each Escape dismisses exactly one, so a single press closed only
        the menu and left the settings pane covering the composer. ``send_prompt``'s
        click then failed Playwright's actionability check and surfaced ~5 s later as a
        bare ``TimeoutError`` naming ``[contenteditable='true']``, with nothing pointing
        at the pane. Field-reported as "switch the model and the run dies; re-run with
        the same model and it works" — a re-run binds the model at the button read-back,
        never opens the menu, and so never stacks the second overlay.

        Measured 2026-09-05 at $0 (spike
        ``2026-09-05-migrated-model-menu-lower-priority.md``): after a switch the
        composer's bounding box is **identical** for 12 s — it was never unstable — while
        ``.cdk-overlay-pane:visible`` stays at 1; one more Escape takes it to 0 and the
        prompt types.

        The old read-back *did* notice — ``migrated.pane_still_open`` fires on the
        pre-fix source, confirmed by replaying it against the live host. It was a
        warning: the run continued, the composer click timed out 5 s later, and the
        error it raised named ``[contenteditable='true']`` with no reference to the
        warning that had predicted it. The observation was there and only the
        consequence was missing, so it is now the failure itself.

        A pane that will not close is raised here, pre-submit and at $0, rather than left
        for the composer click to report as an unattributable timeout.
        """
        for _ in range(PANE_CLOSE_ESCAPES):
            # Re-queried every pass rather than held: the count has to be re-read after
            # each Escape, and a locator built once is only re-evaluated because
            # Playwright's are lazy — not a property worth depending on here.
            if not await self._blocking_overlays(page).count():
                return
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        remaining = await self._blocking_overlays(page).count()
        if not remaining:
            return
        log.warning("migrated.pane_still_open", visible_overlays=remaining, strict=strict)
        if not strict:
            return
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: {remaining} overlay(s) still visible after "
                f"{PANE_CLOSE_ESCAPES} Escape presses — the settings pane would cover the "
                f"composer and the prompt could not be typed (host=migrated)"
            ),
        )

    async def _pin_r2v_duration(self, page: Page, pane: Any) -> None:
        """Bind the base duration for a references run, when this pane offers durations.

        The editor REMEMBERS the last duration, so an r2v run that passes none inherits
        whatever the previous run left behind — and below :data:`R2V_DURATION_S` the app
        degrades an ingredients run to text-to-video instead of refusing it. That is the
        same shape as #125 one axis over: "no opinion" is not what the caller gets, the
        editor's memory is.

        Best-effort by construction. A cohort that renders no duration row already submits
        the base tier, so a missing row is the correct state, not an error — which is why
        this cannot go through :meth:`_select`, whose duration branch raises exit 11.
        """
        wanted = f"{R2V_DURATION_S}s"
        if not await pane.locator(RADIO).filter(has_text=_exact(wanted)).count():
            log.info("migrated.r2v_duration_row_absent", wanted=wanted)
            return
        await self._select(page, pane, axis="duration", text=wanted)
        log.info("migrated.r2v_duration_pinned", seconds=R2V_DURATION_S, issue_ref="#639")

    async def _select(
        self,
        page: Page,
        pane: Any,
        *,
        axis: str,
        lig: str | None = None,
        text: str | None = None,
    ) -> None:
        """Click one radio and read ``aria-checked`` back; re-query once on a stale node."""
        radios = pane.locator(RADIO)
        wanted = text if text is not None else str(lig)
        matches = (
            radios.filter(has=_ligature(page, lig))
            if lig
            else radios.filter(has_text=_exact(wanted))
        )
        target = matches.first
        if not await target.count():
            groups = await pane.locator(RADIOGROUP).count()
            # Only the duration row is a per-account/model capability (#650) — a missing
            # mode/aspect/count radio, or an empty pane, is the DOM having changed.
            if axis != "duration" or groups == 0:
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: no '{axis}' radio offering {wanted!r} in the settings "
                        f"pane ({groups} option groups rendered) (host=migrated)"
                    ),
                )
            raise ConfigurationError(
                detail=(
                    f"the migrated Flow host renders no duration control offering {wanted!r} "
                    f"for this account and model ({groups} option groups shown)"
                ),
                remediation_hint=(
                    "Drop --duration to accept Flow's default length, or pick a model whose "
                    "settings pane shows a duration row (on the maintainer cohort only "
                    "Omni 1.1 Flash does)."
                ),
            )
        if await target.get_attribute("aria-checked") == "true":
            return
        await target.click(timeout=4000)
        await asyncio.sleep(0.2)
        if await matches.first.get_attribute("aria-checked") == "true":
            return
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the '{axis}' radio {wanted!r} did not become aria-checked "
                f"after the click (host=migrated)"
            ),
        )

    async def _select_model(self, page: Page, pane: Any, model: VideoModel) -> None:
        matcher = VIDEO_MODEL_MENU_MATCHERS.get(model)
        if matcher is None:
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' is not available on the migrated Flow host; "
                    f"offered: {', '.join(VIDEO_MODEL_MENU_LABELS.values())}"
                ),
                remediation_hint="Pass --model with one of the offered names, or omit it.",
            )
        button = pane.locator("button").filter(has=_ligature(page, "arrow_drop_down")).first
        if not await button.count():
            raise UiSelectorDriftError(
                detail=(
                    "migrated host: model picker button (arrow_drop_down) not found in the "
                    "settings pane (host=migrated)"
                ),
            )
        current = (await button.text_content() or "").strip()
        if matcher.matches(current):
            # Logged, because otherwise this path is invisible: a run that short-circuits
            # here emits no model event at all, and a field timeline cannot tell "bound
            # the tier you asked for" from "never touched the picker". The 2026-09-05
            # live run needed a separate $0 probe to answer exactly that.
            log.info("migrated.model_already_selected", model=current, requested=model.value)
            return
        await button.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        try:
            await items.first.wait_for(state="visible", timeout=5000)
        except Exception as e:
            raise UiSelectorDriftError(
                detail="migrated host: model menu ([role='menuitem']) did not open (host=migrated)",
            ) from e
        # Matched in Python rather than through a `has_text` filter: the menu is read
        # back for the refusal diagnostic anyway, an *exclusion* is not expressible as
        # `has_text`, and more than one hit has to REFUSE instead of resolving `.first`
        # (#539 — the labs A/B that proved a `.first` on an ambiguous selector picks a
        # tier the user never asked for, and Flow bills for it).
        offered = [t.strip() for t in await items.all_text_contents()]
        hits = [i for i, text in enumerate(offered) if matcher.matches(text)]
        if not hits:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' is not offered on this account's migrated Flow "
                    f"host; offered: {', '.join(offered)}"
                ),
                remediation_hint="Pass --model with one of the offered names, or omit it.",
            )
        if len(hits) > 1:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' matched {len(hits)} entries in the migrated model "
                    f"menu ({', '.join(offered[i] for i in hits)}) — refusing rather than "
                    f"guessing which tier Flow would bill"
                ),
                remediation_hint=(
                    "Pass a --model that names one entry, or omit it to accept Flow's default."
                ),
            )
        await items.nth(hits[0]).click(timeout=4000)
        log.info("migrated.model_selected", model=offered[hits[0]], requested=model.value)

    async def _select_image_model(self, page: Page, pane: Any, model: ImageModel) -> None:
        matcher = IMAGE_MODEL_MENU_MATCHERS.get(model)
        if matcher is None:
            raise ConfigurationError(
                detail=f"image model '{model.value}' is not available on the migrated Flow host",
                remediation_hint="Use nano-banana-2 or nano-pro, or force the labs host.",
            )
        button = pane.locator("button").filter(has=_ligature(page, "arrow_drop_down")).first
        if not await button.count():
            raise UiSelectorDriftError(
                detail="migrated host: image model picker is missing (host=migrated)"
            )
        current = (await button.text_content() or "").strip()
        if matcher.matches(current):
            log.info("migrated.image_model_already_selected", model=current, requested=model.value)
            return
        await button.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        await items.first.wait_for(state="visible", timeout=5000)
        offered = [text.strip() for text in await items.all_text_contents()]
        hits = [index for index, text in enumerate(offered) if matcher.matches(text)]
        if len(hits) != 1:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"image model '{model.value}' matched {len(hits)} entries on the migrated "
                    f"host; offered: {', '.join(offered)}"
                ),
                remediation_hint="Choose one offered image model or omit --model.",
            )
        await items.nth(hits[0]).click(timeout=4000)
        log.info("migrated.image_model_selected", model=offered[hits[0]], requested=model.value)

    # --- start frame (i2v) ------------------------------------------------------

    async def attach_start_frame(self, page: Page, project_id: str, image_path: Path) -> str:
        """Upload ``image_path`` through the editor's own Upload entry, bind it on the
        Start chip by file name, and return the media id the app's ``maseQ`` reply
        named for it — the id the submit body is then asserted to carry.

        The upload is permanent in the Flow project (it lands in the library like any
        other asset). The picker is library-only and searched by display name; an
        upload is listed under its file name, and two uploads of one file list twice —
        the picker's default sort puts the newest first, and the submit-body check is
        what catches a wrong pick.
        """
        from gflow_cli.api.client import validate_image_file  # noqa: PLC0415 - cycle

        await validate_image_file(image_path)
        # No outer budget: each leg is bounded, and an outer one firing first would
        # replace the stage-named failure with a generic "attach timed out".
        media_id = await self._upload_via_toolbar(page, project_id, image_path)
        await self._pick_frame_by_name(page, image_path.name, media_id)
        return media_id

    async def _upload_via_toolbar(self, page: Page, project_id: str, image_path: Path) -> str:
        """Toolbar ``+`` → the ``upload`` menu item → the file chooser → the app's own
        ``maseQ`` upload, observed for the media id it returns. Nothing is replayed."""
        loop = asyncio.get_running_loop()
        reply: asyncio.Future[tuple[int, str]] = loop.create_future()
        route = f"batchexecute:{UPLOAD_RPC}"

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if "batchexecute" not in url or _rpcid(url) != UPLOAD_RPC or reply.done():
                return
            status = int(getattr(response, "status", 0) or 0)
            text = ""
            if status == 200:
                try:
                    text = await response.text()
                except Exception:  # noqa: BLE001 - an aborted body is a rejected upload
                    text = ""
            if not reply.done():
                reply.set_result((status, text))

        # The response listener alone cannot tell "the page never asked" from "Flow was
        # slow" — and those are different bugs with opposite fixes (#719: shape A is a
        # blocked send, shape B is a lost reply). One boolean splits them at the point of
        # failure instead of leaving both as one 60 s timeout.
        sent = False

        def on_request(request: Any) -> None:
            nonlocal sent
            url = str(getattr(request, "url", ""))
            if "batchexecute" in url and _rpcid(url) == UPLOAD_RPC:
                sent = True

        page.on("response", on_response)
        page.on("request", on_request)
        try:
            add = page.locator(TOOLBAR_ADD).first
            if not await add.count():
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the toolbar add button (mat-icon 'add' outside "
                        "flow-prompt-box) is missing (host=migrated)"
                    ),
                )
            await add.click(timeout=5000)
            item = page.locator(UPLOAD_MENU_ITEM).first
            try:
                await item.wait_for(state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000))
            except Exception as e:
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the toolbar menu rendered no upload entry "
                        f"({MENU_ITEM} with the 'upload' ligature) within "
                        f"{FRAME_PICKER_OPEN_S:.0f}s (host=migrated)"
                    ),
                ) from e
            try:
                async with page.expect_file_chooser(
                    timeout=int(FRAME_PICKER_OPEN_S * 1000)
                ) as fc_info:
                    await item.click(timeout=4000)
                chooser = await fc_info.value
            except Exception as e:
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the upload entry opened no file chooser within "
                        f"{FRAME_PICKER_OPEN_S:.0f}s (host=migrated)"
                    ),
                ) from e
            # Flow holds an account's FIRST upload behind a one-time "rights to use this
            # image" confirmation. It renders only once the chooser has handed the file
            # over — so `_dismiss_dialog`, back in `ensure_editor`, never sees it — and
            # Flow sends nothing until a human accepts, which is why the wait below used
            # to expire on a request the page had already declined to make (#719).
            #
            # Counted, never matched. The dialog's two buttons are
            # `button.flow-button-medium` with no ligature and no data attribute,
            # separable only by DOM order, and its copy is translated — no anchor there
            # satisfies this module's locale rule. "A dialog appeared between the file
            # being chosen and the wait expiring" is upload-related by construction: it
            # needs no selector and cannot rot when Angular renames a class. The baseline
            # is taken BEFORE `set_files` so an already-open modal is never blamed.
            #
            # gflow does not click it: accepting affirms that the ACCOUNT OWNER holds the
            # rights to the content, which is not a claim a script may make for someone.
            # Measured 2026-09-08 across 6 runs on ci-probe —
            # docs/superpowers/spikes/2026-09-08-migrated-upload-fails-two-ways.md
            dialogs_before = await page.locator(DIALOG).count()
            await chooser.set_files(str(image_path))
            try:
                status, text = await asyncio.wait_for(reply, timeout=FRAME_UPLOAD_S)
            except TimeoutError:
                try:
                    opened = await page.locator(DIALOG).count() > dialogs_before
                except Exception:  # noqa: BLE001 - a probe is never the failure it reports
                    # The likeliest reason no reply came is that the page died or navigated,
                    # in which case this count raises too. Letting that escape would replace
                    # a mapped exit 27 and its remediation with an unmapped traceback — the
                    # #752 lesson, one surface over.
                    opened = False
                if opened and not sent:
                    raise MediaUploadRejectedError(
                        detail=(
                            f"migrated host: a dialog opened after the file was chosen and no "
                            f"{UPLOAD_RPC} request left the page — most likely Flow's one-time "
                            f"upload-terms confirmation (host=migrated)"
                        ),
                        route=route,
                        remediation_hint=(
                            "Open the project on flow.google.com, upload any image by hand, "
                            "and accept the one-time 'rights to use this image' dialog. gflow "
                            "does not accept it for you: it affirms that YOU hold the rights "
                            "to what you upload. It appears once per account — after that, "
                            "uploads work unattended. If you see a different dialog instead "
                            "(an error, a quota notice, a re-login), that is the one blocking "
                            "the upload. Nothing was spent; re-run when done."
                        ),
                    ) from None
                # Say which half of #719 this is. `sent` is observed, not inferred: the
                # request listener above saw the upload leave the page, or it did not.
                went_out = (
                    "the request left the page and Flow did not answer in time"
                    if sent
                    else "no upload request ever left the page"
                )
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: no {UPLOAD_RPC} reply within {FRAME_UPLOAD_S:.0f}s "
                        f"of choosing the file — {went_out}"
                    ),
                    route=route,
                    remediation_hint=(
                        "If the request left the page, Flow accepted the upload and did not "
                        "answer in time — an intermittent fault on this host (#719), not "
                        "your file: re-run, and the same file usually succeeds. If nothing "
                        "left the page, something client-side stopped it — check for a modal "
                        "on flow.google.com. Either way nothing was spent. Re-encoding the "
                        "image does NOT help; three different files were ruled out in #719."
                    ),
                ) from None
            if status != 200:
                raise MediaUploadRejectedError(
                    detail=f"migrated host: the upload rpc {UPLOAD_RPC} answered HTTP {status}",
                    status=status,
                    route=route,
                )
            media_id = _first_uuid(text)
            if media_id is None:
                raise MediaUploadRejectedError(
                    detail=f"migrated host: {UPLOAD_RPC} answered 200 without a media id",
                    status=status,
                    route=route,
                )
            if media_id.lower() == project_id.lower():
                # The measured reply is ``[media_id, project_id, …]``. If the first
                # UUID is the project's, the shape moved under us — and the submit-body
                # assertion could not catch it, since the project id is in every body.
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: the first id in the {UPLOAD_RPC} reply is the "
                        f"project id ({project_id}), not a new media id — the reply "
                        "shape changed and the upload cannot be bound safely"
                    ),
                    status=status,
                    route=route,
                )
            log.info("migrated.frame_uploaded", media_id=media_id, status=status)
            return media_id
        finally:
            page.remove_listener("response", on_response)
            page.remove_listener("request", on_request)

    async def attach_references(
        self, page: Page, project_id: str, paths: tuple[Path, ...]
    ) -> tuple[str, ...]:
        """Upload each local reference, mention it in the prompt, return the media ids.

        Uploading reuses the i2v toolbar path, so the app's own ``maseQ`` reply names the
        media id — the id :func:`_r2v_body_problem` then asserts the submit carries.
        Binding is different from i2v though: a reference is not a chip slot but an ``@``
        mention in the prompt document, committed with **Enter**. A typed query alone
        leaves the picker open and inserts nothing (measured 2026-09-05), which is the
        failure that would otherwise generate a clip with no references on it.
        """
        media_ids: list[str] = []
        for path in paths:
            media_ids.append(await self._upload_via_toolbar(page, project_id, path))
        # Compose after every upload: the file chooser takes keyboard focus, so mentions
        # cannot be interleaved with uploading.
        await self.clear_composer(page)
        for i, path in enumerate(paths):
            await self._mention_by_name(page, path.name, expect_chips=i + 1)
        log.info("migrated.references_attached", count=len(paths), media_ids=media_ids)
        return tuple(media_ids)

    async def attach_character_entities(
        self,
        page: Page,
        *,
        entity_ids: tuple[str, ...],
        names: tuple[str, ...],
        clear: bool = True,
    ) -> None:
        """Mention each character by name, then prove the chip is that ENTITY (#723).

        The gesture is the same one references use — ``@``, the name, **Enter** — because
        the migrated composer has one picker for everything. What differs is the
        verification, and it is not optional:

        **Flow lists characters and media in that one picker and does not rank them.**
        Measured 2026-09-07: the same ``@Kael`` query committed
        ``data-reference-type="entity"`` with the real entity id on one gesture, and
        ``reference_type="media"`` — a JPEG that merely shared the name — on another.
        A media chip where a character was asked for produces a clip that looks right and
        drifts on the next cut, which is the failure characters exist to prevent. So every
        chip is read back and checked for BOTH its kind and its id before anything is
        submitted, exactly as the labs path asserts ``referenceEntities`` on the wire
        (``ui_automation_video.py`` ``_assert_entities_attached``).
        """
        if clear:
            await self.clear_composer(page)
        # An r2v run can carry media references AND characters, so judge only the chips
        # THIS call adds: media chips already on the prompt are legitimate, and clearing
        # them would silently drop references the caller asked for.
        base = len(await self.read_chips(page))
        for i, name in enumerate(names):
            await self._mention_by_name(page, name, expect_chips=base + i + 1)

        chips = (await self.read_chips(page))[base:]
        not_entities = [c for c in chips if c.get("reference_type") != "entity"]
        if not_entities:
            got = ", ".join(f"{c.get('text')!r} ({c.get('reference_type')})" for c in not_entities)
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: asked for character(s) {', '.join(names)} but the "
                    f"picker committed {got}. Flow offers characters and media under one "
                    f"search and does not rank them, so a file sharing the name can win. "
                    f"Refusing to spend credits on a generation that would carry the wrong "
                    f"reference"
                ),
            )
        attached = {c.get("entity_id", "") for c in chips}
        missing = [e for e in entity_ids if e not in attached]
        if missing:
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: the prompt carries entity chips {sorted(attached)} but "
                    f"{missing} was requested — a chip of the right kind is not proof it is "
                    f"the right character. Refusing to submit"
                ),
            )
        log.info("migrated.character_entities_attached", count=len(entity_ids), names=list(names))

    async def clear_composer(self, page: Page) -> None:
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(600)

    async def read_chips(self, page: Page) -> list[dict[str, str]]:
        """Every mention now in the prompt, with the kind that decides its wire slot."""
        return await page.evaluate(
            "(sel) => [...document.querySelectorAll(sel)].map(c => ({"
            "  text: (c.textContent || '').trim(),"
            "  entity_id: c.getAttribute('data-entity-id') || '',"
            "  reference_type: c.getAttribute('data-reference-type') || '',"
            "}))",
            MENTION_CHIP,
        )

    async def _mention_by_name(self, page: Page, name: str, *, expect_chips: int) -> None:
        """Insert one mention chip for *name*, and verify it landed.

        Retries like :meth:`_pick_frame_by_name` does, and for the same measured reason:
        the mention picker is backed by the same server-side asset search, which does not
        always index a fresh upload by the first query. A miss leaves the typed ``@name``
        in the composer as plain text, so each retry backspaces exactly what it typed
        before querying again — and refuses to continue if that clean-up ate a chip
        someone already attached.
        """
        offered: list[str] = []
        chips = await self.read_chips(page)
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            before = len(chips)
            await page.locator(COMPOSER).first.click(timeout=5000)
            # `keyboard.type`, never `insert_text`: the latter dispatches input events with
            # no key events, so the mention plugin opens a picker with no query behind it
            # and every later gesture is a no-op. `send_prompt` keeps insert_text on
            # purpose — a newline in prompt text must not submit — so they cannot share a
            # path.
            await page.keyboard.type("@", delay=120)
            await page.wait_for_timeout(2200)
            await page.keyboard.type(name, delay=100)
            await page.wait_for_timeout(2500)
            offered = [t.strip() for t in await page.locator(PICKER_OPTION).all_text_contents()]
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
            chips = await self.read_chips(page)
            if len(chips) == expect_chips:
                await page.keyboard.type(" ", delay=80)
                return
            log.info(
                "migrated.mention_miss",
                name=name,
                attempt=attempt,
                chips=len(chips),
                offered=len(offered),
            )
            if attempt == FRAME_SEARCH_ATTEMPTS:
                break
            for _ in range(len(name) + 1):  # the '@' and the query it opened
                await page.keyboard.press("Backspace")
            chips = await self.read_chips(page)
            if len(chips) < before:
                raise ReferenceNotFoundError(
                    detail=(
                        f"migrated host: clearing the failed {name!r} query removed an "
                        f"already-attached reference ({before} chip(s) before, "
                        f"{len(chips)} after) — the prompt is no longer the one that was "
                        "built, so this run is abandoned rather than submitted"
                    ),
                )
            await page.wait_for_timeout(FRAME_SEARCH_RETRY_PAUSE_S * 1000)
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: {name!r} did not attach as a reference in "
                f"{FRAME_SEARCH_ATTEMPTS} attempts ({len(chips)} chip(s), expected "
                f"{expect_chips}); the picker offered: {', '.join(offered[:6]) or '<nothing>'}"
            ),
        )

    @staticmethod
    async def _open_frame_picker(page: Page) -> Any:
        """Click the empty Start chip and wait for the library picker's search box."""
        chip = page.locator(EMPTY_CHIP).first
        if not await chip.count():
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: no empty Start chip ({EMPTY_CHIP}) to bind the frame "
                    "on — is the Frames submode selected? (host=migrated)"
                ),
            )
        await chip.click(timeout=4000)
        picker = page.locator(OVERLAY).filter(has=page.locator(PICKER)).last
        try:
            await picker.locator(PICKER_SEARCH).first.wait_for(
                state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000)
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the frame picker ({PICKER}) did not open within "
                    f"{FRAME_PICKER_OPEN_S:.0f}s of clicking the Start chip (host=migrated)"
                ),
            ) from e
        return picker

    async def _pick_frame_by_name(self, page: Page, name: str, media_id: str) -> None:
        """Start chip → the library picker → search by display name → first option →
        the chip must now hold a thumbnail. An unbound chip is refused here: an empty
        Frames submit goes out as text-to-video (the labs #125 shape on this host)."""
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            picker = await self._open_frame_picker(page)
            search = picker.locator(PICKER_SEARCH).first
            await search.click(timeout=4000)
            await page.keyboard.insert_text(name)
            options = picker.locator(PICKER_OPTION).filter(has_text=_exact(name))
            try:
                await options.first.wait_for(
                    state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000)
                )
            except Exception as e:
                # What the picker DID list is the diagnostic: the display name an upload
                # gets is an observation per account, not a contract.
                listed = [
                    t.strip() for t in await picker.locator(PICKER_OPTION).all_text_contents()
                ]
                await page.keyboard.press("Escape")
                if attempt < FRAME_SEARCH_ATTEMPTS:
                    log.info("migrated.frame_search_miss", attempt=attempt, listed=len(listed))
                    await asyncio.sleep(FRAME_SEARCH_RETRY_PAUSE_S)
                    continue
                raise ReferenceNotFoundError(
                    detail=(
                        f"migrated host: the frame picker lists no asset named {name!r} after "
                        f"{FRAME_SEARCH_ATTEMPTS} searches of {FRAME_PICKER_OPEN_S:.0f}s (media "
                        f"{media_id}) — uploads are expected under their file name; the "
                        "picker listed for that search: "
                        f"{', '.join(repr(t) for t in listed[:8]) or 'nothing'}"
                    ),
                ) from e
            else:
                # Outside the except above on purpose: a click that fails here is not
                # "the picker never listed it", and must not be reported as one.
                await options.first.click(timeout=4000)
                break
        try:
            # Re-queried, and `.last` like the open: the picker overlay is detached
            # after the pick, and a detached-but-hidden earlier pane would let a
            # `.first` hidden-wait pass while the live picker is still up.
            await (
                page.locator(OVERLAY)
                .filter(has=page.locator(PICKER))
                .last.wait_for(state="hidden", timeout=int(FRAME_COMMIT_HIDDEN_S * 1000))
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the frame picker stayed open {FRAME_COMMIT_HIDDEN_S:.0f}s "
                    f"after picking {name!r} (host=migrated)"
                ),
            ) from e
        try:
            await page.locator(BOUND_CHIP).first.wait_for(
                state="visible", timeout=int(FRAME_THUMB_VISIBLE_S * 1000)
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the Start chip did not bind {name!r} — no "
                    f"{BOUND_CHIP} within {FRAME_THUMB_VISIBLE_S:.0f}s of the pick; "
                    "refusing to submit what would go out as text-to-video (host=migrated)"
                ),
            ) from e
        log.info("migrated.frame_bound", media_id=media_id)

    # --- prompt + submit --------------------------------------------------------

    async def send_prompt(self, page: Page, prompt: str, *, append: bool = False) -> None:
        """Type the prompt. ``append=True`` keeps what is already in the composer — the
        mention chips an r2v run just attached — and leaves the caret where the last one
        put it, rather than clicking and moving it."""
        composer = page.locator(COMPOSER).first
        if not await composer.count():
            raise UiSelectorDriftError(
                detail=f"migrated host: composer ({COMPOSER}) not found (host=migrated)",
            )
        if not append:
            # _close_pane's docstring names THIS click as the one that surfaced a
            # stuck settings pane as a bare 5 s TimeoutError naming only the composer.
            await self._click(page, composer, named=COMPOSER, timeout=5000)
        # insert_text dispatches input events without key presses: a newline in the
        # prompt lands as text instead of an Enter that might submit early.
        await page.keyboard.insert_text(prompt)
        log.info("migrated.prompt_typed", chars=len(prompt))

    async def submit_and_observe(
        self,
        page: Page,
        *,
        poll_timeout_s: float,
        on_started: VideoStartedCallback | None,
        project_id: str | None,
        expect_media_id: str | None = None,
        expect_reference_ids: tuple[str, ...] = (),
    ) -> GenerationRecord:
        """Click submit, then read the page's own ``YhhmEf``/``eb1hJf`` / ``jwpduf`` /
        ``as29s`` replies until the record is terminal. Fires ``on_started`` as soon
        as the submit reply names the media id — before the poll, as the labs path does.

        ``expect_media_id`` (i2v) and ``expect_reference_ids`` (r2v) inspect the submit
        *request* the app sends: its body must carry those ids and the matching ``_i2v_``
        / ``_r2v_`` model key, else the run is a :class:`WireFormatError` — the generation
        the user asked for is not the one Flow is billing."""
        loop = asyncio.get_running_loop()
        submitted: asyncio.Future[GenerationRecord] = loop.create_future()
        route_error: asyncio.Future[WireFormatError] = loop.create_future()

        def on_request(request: Any) -> None:
            url = str(getattr(request, "url", ""))
            rpcid = _rpcid(url) if "batchexecute" in url else None
            if rpcid not in SUBMIT_RPCS or route_error.done():
                return
            if expect_reference_ids:
                problem = _r2v_body_problem(_post_data(request), rpcid, expect_reference_ids)
            elif expect_media_id is not None:
                problem = _i2v_body_problem(_post_data(request), rpcid, expect_media_id)
            else:
                return
            if problem is not None:
                route_error.set_result(
                    WireFormatError(detail=problem, route=f"batchexecute:{rpcid}")
                )

        # ``terminal``: failed, or done WITH the signed URL. ``done_no_url``: the
        # first status-3 record that has no URL yet (a poll beats the result RPC).
        terminal: asyncio.Future[GenerationRecord] = loop.create_future()
        done_no_url: asyncio.Future[GenerationRecord] = loop.create_future()
        workflow: dict[str, str] = {}

        def _settle(rec: GenerationRecord) -> None:
            if rec.is_failed or (rec.is_done and rec.video_url):
                if not terminal.done():
                    terminal.set_result(rec)
            elif rec.is_done and not done_no_url.done():
                done_no_url.set_result(rec)

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            rpcid = _rpcid(url) if "batchexecute" in url else None
            if rpcid not in SUBMIT_RPCS and rpcid not in STATUS_RPCS:
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - an aborted/streamed body is not our frame
                return
            for rid, payload in parse_frames(text):
                if rid in SUBMIT_RPCS and not submitted.done():
                    try:
                        rec = generation_record(rid, payload)
                    except WireFormatError as exc:
                        submitted.set_exception(exc)
                        return
                    workflow["id"] = rec.workflow_id
                    log.info(
                        "migrated.submit_observed",
                        rpc=rid,
                        workflow_id=rec.workflow_id,
                        media_id=rec.media_id,
                        status=rec.status,
                    )
                    submitted.set_result(rec)
                    _settle(rec)
                elif rid in STATUS_RPCS and workflow:
                    try:
                        rec = generation_record(rid, payload)
                    except WireFormatError:
                        continue
                    if rec.workflow_id != workflow["id"]:
                        continue
                    log.info("migrated.status", rpc=rid, status=rec.status, bytes=rec.size_bytes)
                    _settle(rec)

        page.on("response", on_response)
        # Both body assertions live in `on_request`, so the listener must be armed for
        # either. Gating it on `expect_media_id` alone left `_r2v_body_problem` unit-tested
        # but never reached in a live run — the check the r2v path was built around.
        if expect_media_id is not None or expect_reference_ids:
            page.on("request", on_request)
        try:
            submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            if not await submit.count():
                # A credit shortfall and a moved frontend look identical here: both are
                # "arrow_forward is gone". Ask which one BEFORE naming a culprit --
                # reporting a short balance as selector drift tells the user to file a
                # frontend bug that no code change can fix.
                await _raise_if_out_of_credits(page)
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the submit button (arrow_forward) is missing "
                        "after the prompt was typed (host=migrated)"
                    ),
                )
            enable_deadline = time.monotonic() + SUBMIT_ENABLE_BUDGET_S
            while not await submit.is_enabled():
                if time.monotonic() >= enable_deadline:
                    # Same question as above, on the other way of giving up on submit.
                    await _raise_if_out_of_credits(page)
                    raise UiSelectorDriftError(
                        detail=(
                            "migrated host: the submit button (arrow_forward) stayed disabled "
                            f"for {SUBMIT_ENABLE_BUDGET_S:.0f}s after the prompt was typed "
                            "(host=migrated)"
                        ),
                    )
                await asyncio.sleep(SUBMIT_ENABLE_POLL_S)
            deadline = time.monotonic() + poll_timeout_s
            # The credit-spending click: a bare timeout here leaves "did it submit?"
            # unanswerable, which is the worst place in this driver to lose attribution.
            await self._click(page, submit, named=SUBMIT_BUTTON, timeout=5000)
            log.info("migrated.submit_clicked")
            budget = min(SUBMIT_REPLY_BUDGET_S, poll_timeout_s)
            await asyncio.wait(
                {submitted, route_error}, timeout=budget, return_when=asyncio.FIRST_COMPLETED
            )
            if route_error.done():
                # The request is inspected before its reply lands, so a wrong body is
                # named as such and not as whatever the reply then says.
                raise route_error.result()
            if not submitted.done():
                raise TransportTimeoutError(
                    detail=(
                        f"migrated host: no {'/'.join(SUBMIT_RPCS)} reply within "
                        f"{budget:.0f}s of clicking submit"
                    ),
                )
            first = submitted.result()
            started = VideoStarted(
                media_id=first.media_id,
                project_id=project_id or first.project_id,
                flow_operation_id=first.workflow_id,
            )
            if on_started is not None:
                maybe = on_started(started)
                if asyncio.iscoroutine(maybe):
                    await maybe
            final = await self._await_terminal(
                terminal, done_no_url, deadline=deadline, workflow_id=first.workflow_id
            )
            log.info(
                "migrated.result",
                status=final.status,
                done=final.is_done,
                url_host=urlsplit(final.video_url).hostname if final.video_url else None,
                bytes=final.size_bytes,
            )
            return final
        finally:
            # A submit reply that failed to parse sets an exception on `submitted`;
            # when the body assertion raised first, nobody read it, and asyncio would
            # log "Future exception was never retrieved" at GC. Consume it.
            if submitted.done() and not submitted.cancelled():
                submitted.exception()
            page.remove_listener("response", on_response)
            if expect_media_id is not None or expect_reference_ids:
                page.remove_listener("request", on_request)

    async def submit_images_and_observe(
        self,
        page: Page,
        request: GenerateImageRequest,
        *,
        reference_ids: tuple[str, ...] = (),
    ) -> list[GeneratedImage]:
        """Submit Image mode and decode the completed ``ogiZ0b`` reply."""
        loop = asyncio.get_running_loop()
        result: asyncio.Future[list[GeneratedImage]] = loop.create_future()
        route_error: asyncio.Future[WireFormatError] = loop.create_future()

        def on_request(raw_request: Any) -> None:
            url = str(getattr(raw_request, "url", ""))
            if _rpcid(url) != IMAGE_SUBMIT_RPC or route_error.done():
                return
            problem = _image_body_problem(_post_data(raw_request), reference_ids, request.model)
            if problem is not None:
                route_error.set_result(
                    WireFormatError(detail=problem, route=f"batchexecute:{IMAGE_SUBMIT_RPC}")
                )

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if _rpcid(url) != IMAGE_SUBMIT_RPC or result.done():
                return
            status = int(getattr(response, "status", 0) or 0)
            if status != 200:
                result.set_exception(
                    WireFormatError(
                        detail=f"migrated image submit answered HTTP {status}",
                        status=status,
                        route=f"batchexecute:{IMAGE_SUBMIT_RPC}",
                    )
                )
                return
            try:
                text = await response.text()
                records = [
                    record
                    for rpcid, payload in parse_frames(text)
                    if rpcid == IMAGE_SUBMIT_RPC
                    for record in image_records(rpcid, payload)
                ]
                if not records:
                    raise WireFormatError(
                        detail="migrated image submit returned no ogiZ0b frame",
                        route=f"batchexecute:{IMAGE_SUBMIT_RPC}",
                    )
                images = [
                    GeneratedImage(
                        media_name=record.media_id,
                        workflow_id=record.workflow_id,
                        seed=record.seed,
                        prompt=record.prompt,
                        model_name_type=request.model.value,
                        aspect_ratio=request.aspect.value,
                        fife_url=record.image_url,
                        dimensions=record.dimensions,
                        display_name=record.display_name,
                    )
                    for record in records
                ]
            except Exception as exc:  # noqa: BLE001 - delivered through the waiting future
                result.set_exception(exc)
                return
            result.set_result(images)

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            if not await submit.count():
                await _raise_if_out_of_credits(page)
                raise UiSelectorDriftError(
                    detail="migrated host: image submit button is missing (host=migrated)"
                )
            enable_deadline = time.monotonic() + SUBMIT_ENABLE_BUDGET_S
            while not await submit.is_enabled():
                if time.monotonic() >= enable_deadline:
                    await _raise_if_out_of_credits(page)
                    raise UiSelectorDriftError(
                        detail="migrated host: image submit stayed disabled (host=migrated)"
                    )
                await asyncio.sleep(SUBMIT_ENABLE_POLL_S)
            await self._click(page, submit, named=SUBMIT_BUTTON, timeout=5000)
            done, _ = await asyncio.wait(
                {result, route_error},
                timeout=IMAGE_REPLY_BUDGET_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if route_error.done():
                raise route_error.result()
            if result not in done:
                raise TransportTimeoutError(
                    detail=(
                        f"migrated host: no {IMAGE_SUBMIT_RPC} image result within "
                        f"{IMAGE_REPLY_BUDGET_S:.0f}s of clicking submit"
                    )
                )
            return result.result()
        finally:
            # Consume the future's exception even on the paths that never read it
            # (a route error raised first, a timeout): otherwise asyncio logs
            # "exception was never retrieved" at GC, in a process that has already
            # reported a different, correct error. Not a no-op — do not delete.
            if result.done() and not result.cancelled():
                result.exception()
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)

    @staticmethod
    async def _await_terminal(
        terminal: asyncio.Future[GenerationRecord],
        done_no_url: asyncio.Future[GenerationRecord],
        *,
        deadline: float,
        workflow_id: str,
    ) -> GenerationRecord:
        """Wait for a terminal record; a done-without-URL record buys a short grace
        for the one that carries the URL, then stands on its own."""
        remaining = max(deadline - time.monotonic(), 0.01)
        done, _ = await asyncio.wait(
            {terminal, done_no_url}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if terminal.done():
            return terminal.result()
        if not done:
            raise TransportTimeoutError(
                detail=(
                    f"migrated host: generation {workflow_id} was not terminal within the "
                    f"poll timeout"
                ),
            )
        grace = min(RESULT_URL_GRACE_S, max(deadline - time.monotonic(), 0.01))
        try:
            return await asyncio.wait_for(terminal, timeout=grace)
        except TimeoutError:
            log.warning("migrated.result_url_not_observed", grace_s=grace)
            return done_no_url.result()

    # --- download ---------------------------------------------------------------

    @staticmethod
    async def _fetch_mp4(page: Page, record: GenerationRecord) -> bytes:
        """GET the signed URL and prove it is an MP4 (``ftyp`` at offset 4); if it is
        not — the record carries a poster JPEG next to the clip, and a 2026-09-05
        run downloaded that one — try the other URL before giving up."""
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415 - cycle
            _is_allowed_download_host,  # pyright: ignore[reportPrivateUsage]
        )

        seen: list[str] = []
        for url in (record.video_url, record.poster_url):
            if not url:
                continue
            if not _is_allowed_download_host(url):
                raise WireFormatError(
                    detail=(
                        "migrated host: refusing to download from "
                        f"{urlsplit(url).hostname!r} (not an allowed Google host)"
                    ),
                    route="batchexecute:as29s",
                )
            # No redirects: an open redirect on the CDN must not rebound the
            # request elsewhere (same posture as the labs image download).
            resp = await page.request.get(url, timeout=180_000, max_redirects=0)
            if resp.status >= 300:
                raise WireFormatError(
                    detail=f"migrated host: signed media URL returned HTTP {resp.status}",
                    status=resp.status,
                    route="flow-content.google",
                )
            body = await resp.body()
            if body[4:8] == b"ftyp":
                if record.size_bytes and len(body) != record.size_bytes:
                    log.warning(
                        "migrated.download_size_mismatch",
                        expected=record.size_bytes,
                        actual=len(body),
                    )
                return body
            seen.append(
                f"{urlsplit(url).path.rsplit('/', 1)[-1]}: {body[:4].hex()} ({len(body)} B)"
            )
        raise WireFormatError(
            detail=(
                "migrated host: no signed URL on the record returned an MP4 "
                f"(ftyp magic); saw {'; '.join(seen) or 'no URLs'}"
            ),
            route="batchexecute:as29s",
        )

    async def download(
        self,
        page: Page,
        record: GenerationRecord,
        out_dir: Path | None,
    ) -> Path | None:
        """The clip from its signed CDN URL. The labs ``media.getMediaUrlRedirect``
        route answers 404 for a migrated media id (measured 2026-09-05), so there is
        no second source: a record with no URL is a wire-format failure."""
        if not record.video_url and not record.poster_url:
            raise WireFormatError(
                detail=(
                    "migrated host: the generation finished but no signed media URL was "
                    f"observed within the {RESULT_URL_GRACE_S:.0f}s grace"
                ),
                route="batchexecute:as29s",
            )
        body = await self._fetch_mp4(page, record)
        target_dir = out_dir or Path.cwd()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{record.media_id}.mp4"
        path.write_bytes(body)
        log.info("migrated.download", path=str(path), bytes=len(body))
        return path


async def run_video(
    page: Page,
    request: GenerateVideoRequest,
    *,
    project_id: str | None,
    out_dir: Path | None,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    """The migrated-host twin of the labs ``_generate_video_locked`` tail: same
    inputs, same ``VideoResult``, so recorder, CLI, MCP and worker are untouched.

    t2v, i2v from a local start frame (uploaded through the editor and bound on the
    Start chip by file name), and r2v from local ``--ref`` files. An end frame and a
    frame or reference given by UUID / ``@Name`` are not ported yet; a fresh project
    can only be created through the labs gallery, so the caller must name one
    (``--project``).
    """
    unported = _unported_form(request)
    if unported is not None:
        raise FlowHostMigratedError(
            detail=(
                f"this account's Flow lives on flow.google.com, where gflow drives "
                f"text-to-video, image-to-video from a local start frame, and "
                f"reference-to-video from local files; {unported} is not ported yet "
                f"(#639) — pass --initial-frame / --ref as local files, without an "
                f"end frame"
            ),
        )
    pid = project_id or extract_project_id(page.url)
    if not pid:
        raise ConfigurationError(
            detail=(
                "generating on the migrated flow.google.com host needs an existing project: "
                "pass --project <id> (see `gflow project list` / `gflow project create`) — "
                "creating one from the editor is not ported to this host yet"
            ),
        )
    log.info("migrated.dispatch", project_id=pid, mode=request.mode.value)
    composer = MigratedComposer()
    await composer.ensure_editor(page, pid)
    await composer.apply_video_settings(page, request)
    media_id: str | None = None
    reference_ids: tuple[str, ...] = ()
    frame = request.start_image
    if request.mode is Mode.I2V and frame is not None:
        media_id = await composer.attach_start_frame(page, pid, frame)
    if request.mode is Mode.R2V and request.reference_images:
        reference_ids = await composer.attach_references(page, pid, request.reference_images)
    if request.reference_entities:
        # #723: characters attach through the SAME `@` picker as media, so they go on
        # after any uploads (whose file chooser steals keyboard focus) and must not clear
        # mentions those uploads already placed.
        await composer.attach_character_entities(
            page,
            entity_ids=tuple(request.reference_entities),
            names=tuple(request.reference_entity_names),
            clear=not reference_ids,
        )
    # The prompt is appended whenever mentions are already in the document: clicking the
    # composer would move the caret away from where the last one left it.
    has_mentions = bool(request.reference_entities) or request.mode is Mode.R2V
    await composer.send_prompt(page, request.prompt, append=has_mentions)
    if request.mode is Mode.R2V:
        attached = await composer.read_chips(page)
        if len(attached) != len(request.reference_images) + len(request.reference_entities):
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: {len(request.reference_images)} reference(s) requested "
                    f"but {len(attached)} on the prompt at submit time — refusing to spend "
                    f"credits on a run that would ignore them"
                ),
            )
    record = await composer.submit_and_observe(
        page,
        poll_timeout_s=poll_timeout_s,
        on_started=on_started,
        project_id=pid,
        expect_media_id=media_id,
        expect_reference_ids=reference_ids,
    )
    status = VideoStatus(
        media_id=record.media_id,
        status=(
            "MEDIA_GENERATION_STATUS_SUCCESSFUL"
            if record.is_done
            else "MEDIA_GENERATION_STATUS_FAILED"
        ),
        error_message=None if record.is_done else f"migrated host reported status {record.status}",
    )
    local_path: Path | None = None
    if download and record.is_done:
        local_path = await composer.download(page, record, out_dir)
    return VideoResult(
        status=status,
        local_path=Path(local_path) if local_path is not None else None,
        project_id=pid,
        flow_operation_id=record.workflow_id,
    )


async def run_images(
    page: Page,
    request: GenerateImageRequest,
    *,
    project_id: str | None,
) -> list[GeneratedImage]:
    """Drive supported image requests through the migrated project composer."""
    unported = _unported_image_form(request)
    if unported is not None:
        raise FlowHostMigratedError(
            detail=(
                "this account's Flow lives on flow.google.com, where gflow drives t2i "
                f"and i2i from local files; {unported} is not ported yet (#639)"
            )
        )
    pid = project_id or extract_project_id(page.url)
    if not pid:
        raise ConfigurationError(
            detail=(
                "image generation on flow.google.com needs an existing project; pass --project <id>"
            )
        )
    composer = MigratedComposer()
    await composer.ensure_editor(page, pid)
    await composer.apply_image_settings(page, request)
    reference_ids: tuple[str, ...] = ()
    if request.ref_paths:
        reference_ids = await composer.attach_references(page, pid, request.ref_paths)
        chips = await composer.read_chips(page)
        if len(chips) != len(reference_ids):
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: {len(reference_ids)} image reference(s) uploaded but "
                    f"{len(chips)} mention chip(s) were bound before submit"
                )
            )
    await composer.send_prompt(page, request.prompt, append=bool(reference_ids))
    return await composer.submit_images_and_observe(
        page,
        request,
        reference_ids=reference_ids,
    )
