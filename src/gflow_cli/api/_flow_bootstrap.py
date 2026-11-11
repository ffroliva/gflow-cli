"""Account bootstrap on session entry: Google's account chooser and the account locale.

Split out of ``client.py`` (#1249); ``FlowApiClient`` composes it. Method
bodies are unchanged.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

import structlog
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api import routes
from gflow_cli.api.transports._common import (
    await_url_settled,
    flow_host_kind,
    safe_page_url,
)
from gflow_cli.auth.internal_chromium import GOOGLE_REJECTED_BROWSER_ROUTE
from gflow_cli.errors import (
    ConfigurationError,
    FlowAccountChooserError,
)
from gflow_cli.profile_store import (
    NOT_REDIRECTED,
    next_locale_state,
    read_account_locale,
    write_account_locale,
)
from gflow_cli.redaction import redact_sensitive_text

if TYPE_CHECKING:
    pass
from gflow_cli.api._flow_session import FlowSession

logger = structlog.get_logger(__name__)


#: Chromium's net errors for a proxy it cannot use (#1036) — dead port, refused tunnel.
_PROXY_FAILURES = (
    "net::ERR_PROXY_CONNECTION_FAILED",
    "net::ERR_TUNNEL_CONNECTION_FAILED",
)

# How long a clicked account-chooser row has to land on a Flow host before it is
# reported as exit 38. A module constant so the hermetic e2e can shorten it.
_CHOOSER_LANDING_TIMEOUT_MS = 30_000


class AccountBootstrap(FlowSession):
    """Account bootstrap on session entry: Google's account chooser and the account locale."""

    async def _handle_account_chooser(self, page: Page) -> bool:
        """Select the recorded Google account on accountchooser if encountered (#763).

        Returns True if an account was clicked, False if not on chooser.
        Raises FlowAccountChooserError if on chooser but account is missing/not selectable.

        Matching is exact on the account row only: the chooser's loose surfaces
        ("Remove <email>", "Sign out of <email>", signed-in-as subtitle) would
        otherwise win a substring match and click the wrong account, billing it.
        """
        from gflow_cli.profile_store import read_account_file

        url = getattr(page, "url", "") or ""
        # Exact host match, never a substring test: a Flow URL merely carrying
        # accounts.google.com in a ?continue= param must not read as a chooser.
        # The rejected-browser hop is not a chooser either and must surface as
        # its own error rather than a missing account.
        # Total by construction (same discipline as flow_host_kind): a probe
        # error must never displace the real bootstrap failure, and suites
        # drive this path with mocked pages whose url is not a string.
        if not isinstance(url, str):
            return False
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
        except ValueError:
            return False
        is_accounts_host = parts.scheme == "https" and host == "accounts.google.com"
        if not is_accounts_host or GOOGLE_REJECTED_BROWSER_ROUTE in url:
            return False

        # Identify a chooser POSITIVELY. The host gate accepts every
        # accounts.google.com landing, and most are not choosers — the email form, a
        # password challenge, a consent interstitial — where there is nothing to pick.
        # Reporting those as a chooser misdirects the operator and changes an exit code
        # callers branch on: they otherwise reach the transport's 401 and classify as
        # AuthExpiredError (exit 3). Two independent signals, because each covers the
        # other's blind spot — Google renaming the path, or a chooser whose rows carry
        # no data-email. Neither matching means we return False, which is exactly how
        # this path behaved before the feature existed.
        on_chooser_path = parts.path.rstrip("/").endswith("accountchooser")
        if not on_chooser_path and await page.locator("[data-email]").count() == 0:
            return False

        email = read_account_file(self.profile_dir)
        if not email:
            raise FlowAccountChooserError(
                detail=(
                    f"Google sign-in/chooser displayed at {safe_page_url(url)} but no "
                    f"account is recorded in this profile to auto-select."
                )
            )

        # Exact row match only (D3): data-email is the chooser's stable per-account
        # anchor. A substring/text-engine fallback would match "Remove <email>"
        # or "Sign out of <email>" and click a DOM-order-first wrong account.
        # Case-insensitive on BOTH tiers, because `gflow auth login --account`
        # already compares with `.lower()` and `read_account_file` normalises
        # nothing: an address recorded in one case and rendered by Google in
        # another otherwise passes the --account assertion and then misses the
        # row, raising "not found among selectable accounts" while the account
        # sits on the chooser. CSS attribute matching is case-sensitive unless
        # the `i` flag is given; the text fallback stays ANCHORED so relaxing
        # case does not start matching "Remove <email>" / "Sign out of <email>"
        # — clicking those signs the operator out instead of in.
        row = page.locator(f'[data-email="{email}" i]')
        count = await row.count()
        if count == 0:
            row = page.get_by_text(re.compile(rf"^{re.escape(email)}$", re.IGNORECASE))
            count = await row.count()

        if count == 0:
            raise FlowAccountChooserError(
                detail=(
                    f"Account chooser displayed at {safe_page_url(url)} but recorded "
                    f"account '{email}' was not found among selectable accounts."
                )
            )

        # The row is the account's entry; verify we actually leave the chooser.
        await row.first.click()
        # `wait_for_url` returns None and signals a miss by RAISING, so its return value
        # is falsy on success as well as failure — testing it inverted the check and made
        # every successful click raise. Catch the raise instead.
        #
        # The landing predicate is "on any Flow host", not a `**/project/**` glob: the
        # bootstrap URL is `labs.google/fx/tools/flow` with no /project/ segment, and only
        # the migrated origin serves /project/<id>. `flow_host_kind` is the codebase's
        # exact-host classifier (a substring test matches any URL merely mentioning the
        # host in a ?continue= param), and it answers for both cohorts.
        try:
            await page.wait_for_url(
                lambda u: flow_host_kind(u) is not None, timeout=_CHOOSER_LANDING_TIMEOUT_MS
            )
        except PlaywrightTimeoutError as exc:
            # Where the click left us IS the diagnosis, so the detail has to carry it
            # (its sibling raise above interpolates the chooser URL for the same
            # reason). `flow_host_kind` is a host-only match that accepts every Flow
            # landing this codebase knows, `/about` included, so a timeout here is
            # never the predicate being too narrow — the session is still on a Google
            # surface. WHICH surface is the whole question: a challenge needs a human,
            # a consent screen needs a click, and a URL still equal to `url` above
            # means the click never navigated at all. Shipped without this, the branch
            # fired live on 2026-09-09 and said only "did not reach Flow within 30s".
            landed = page.url
            raise FlowAccountChooserError(
                detail=(
                    f"Clicked recorded account '{email}' on the chooser but the session "
                    f"did not reach Flow within 30s — it is at {safe_page_url(landed)}."
                )
            ) from exc
        logger.info(
            "client.account_chooser_autoselected",
            account=redact_sensitive_text(email),
        )
        return True

    async def _bootstrap_and_resolve_locale(self) -> None:
        """Navigate the bootstrap page and settle the account locale (#580, #587).

        The probe (#580) costs the full ``URL_SETTLE_TIMEOUT_MS`` on any account
        Flow does **not** redirect: ``wait_for_url`` never matches, so it runs to
        timeout on every command. That single case is the entire cost, and the
        cache answers exactly one question: *does this account redirect?*

        The navigation stays **bare, always** — never to a cached segment. Flow
        serves whatever segment it is asked for, so only a bare navigation makes
        it state the account's own answer; see the CHANGELOG entry for #587 and
        ``scripts/dev/spike_locale_poison.py``.

        The settle is skipped only on :data:`NOT_REDIRECTED`, which
        :func:`next_locale_state` reaches only after two runs agree — so a single
        transient timeout cannot disable it.
        """
        assert self._page is not None
        cached = read_account_locale(self.profile_dir)
        try:
            await self._page.goto(
                routes.EDITOR_BOOTSTRAP_URL,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
        except Exception as exc:
            # #1036: the first navigation is where an unreachable proxy shows up.
            code = next((c for c in _PROXY_FAILURES if c in str(exc)), None)
            if code is not None:
                raise ConfigurationError(
                    f"the browser could not reach Flow through its proxy ({code})",
                    remediation_hint=(
                        "Check that the proxy in GFLOW_CLI_BROWSER_PROXY is running and "
                        "reachable, or unset it (with it unset, the browser uses the "
                        "system proxy)."
                    ),
                ) from exc
            raise
        # #639: NOT_REDIRECTED means "there is no redirect to wait for". It must not
        # ALSO mean "do not read the locale" — which is what returning here made it
        # mean, and that made the state ABSORBING: `_resolve_account_locale` is the
        # only site of the <html lang> recovery (#643) AND the only caller of
        # `next_locale_state`, so nothing could ever move a latched profile off it.
        # Every profile that saw two migrated loads on <=0.66.0 latched this way,
        # which is exactly the population #643 was written for. Measured 2026-09-03
        # on `ffroliva`: latched, and the page declares lang="en" on every load.
        settle = cached != NOT_REDIRECTED
        self._account_locale, from_url = await self._resolve_account_locale(
            self._page, settle=settle
        )
        # #763: the chooser hop lands through the same post-goto redirect chain as
        # the locale hop, so it is observable only after the settle above.
        # BOTH outputs of the first resolve are the chooser's, and both must be
        # replaced. `self._account_locale` would otherwise carry
        # accounts.google.com's <html lang> for the rest of the run. `from_url`
        # is subtler and was wrong: a chooser yields None, and
        # `next_locale_state(cached="pt", observed=None)` returns PROVISIONAL, so
        # the fold below wrote a DEMOTION of a committed locale on every chooser
        # hop (#643's bug class). The post-click resolve holds the editor's real
        # segment — fold that.
        if await self._handle_account_chooser(self._page):
            self._account_locale, from_url = await self._resolve_account_locale(
                self._page, settle=False
            )
        if not settle:
            # Kept (not merged into account_locale_state) because field reports key
            # on this event to tell "the settle was skipped" from "it timed out".
            logger.info(
                "client.account_locale_cached",
                locale=self._account_locale,
                settle_skipped=True,
            )
            # Do NOT fold this observation into the cache. The cached state answers
            # ONE question — "does Flow redirect this account?" — and NOT_REDIRECTED
            # is a true answer here. Writing the <html lang> locale into it would
            # make `cached != NOT_REDIRECTED` on the next run, turning the settle
            # back on for good and reintroducing the 4 s URL_SETTLE_TIMEOUT_MS that
            # #587 removed. Measured on `ffroliva` when this return was missing:
            # two `transport.url_settle_gave_up` timeouts and a warm bootstrap of
            # 7.41 s, which `scripts/dev/measure_locale_probe.py` flagged as "warm
            # arm slower than cold". Re-deriving the locale costs one sub-ms
            # `page.evaluate` per run, which is strictly cheaper than persisting it.
            return
        # #639: fold ONLY the URL-derived segment. The cached state answers "does
        # Flow redirect this account?", and a locale read from `<html lang>` is no
        # evidence of a redirect — every account declares one. Folding it in made
        # `next_locale_state` record a segment for an account Flow serves bare,
        # which switched the settle on permanently and cost the full 4 s
        # URL_SETTLE_TIMEOUT_MS on every bootstrap thereafter. That shipped in
        # v0.66.1 with #643's `<html lang>` fallback and is measurable on any
        # non-redirecting account: `scripts/dev/measure_locale_probe.py` reports
        # two `transport.url_settle_gave_up` timeouts per run.
        state = next_locale_state(cached, from_url)
        if state != cached:
            logger.info("client.account_locale_state", was=cached, now=state)
        write_account_locale(self.profile_dir, state)

    async def _resolve_account_locale(
        self, page: Any, *, settle: bool = True
    ) -> tuple[str | None, str | None]:
        """Settle the bootstrap navigation and read the account's locale (#580).

        Flow redirects the editor to the ACCOUNT's locale, but that redirect
        lands after ``goto`` returns — settling is what makes it observable.
        ``None`` means "build bare URLs", which is never worse than the
        hardcoded ``en-US`` this replaces.

        Returns ``(locale, from_url)`` — the same segment twice when the URL
        answered, and ``(lang_segment, None)`` when only ``<html lang>`` did.
        **The two are not interchangeable** (#639): the caller uses ``locale`` to
        build URLs, but folds only ``from_url`` into the cached state, because
        that cache answers "does Flow redirect this account?" and a ``lang``
        attribute is no evidence of a redirect — every account has one.

        ``settle=False`` (#639) skips only the wait, not the read. A profile
        cached :data:`NOT_REDIRECTED` has nothing to wait for — measured 60/60 on
        `ffroliva` — but its page still declares a locale in ``<html lang>``, and
        skipping the whole function to save the wait is what made that cache an
        absorbing state.
        """
        settled = await await_url_settled(page) if settle else None
        segment = routes.locale_segment_from_url(settled or "")
        if segment is not None:
            logger.info("client.account_locale_resolved", locale=segment, url=settled)
            return segment, segment
        # #643: the migrated flow.google.com origin serves /project/<id> with no
        # locale segment, so the URL can never answer there — but Flow still
        # renders the account locale into <html lang>. Without this the resolver
        # returns None and `next_locale_state` DEMOTES an already-learned locale
        # to PROVISIONAL (measured: a pt account lost its "pt" on every migrated
        # load). Best-effort: a probe failure must never break navigation.
        lang = await self._settled_lang(page)
        segment = routes.locale_segment_from_lang_attr(lang)
        if segment is not None:
            logger.info("client.account_locale_resolved", locale=segment, source="html_lang")
            return segment, None
        logger.info("client.account_locale_unresolved", last_url=settled)
        return None, None

    #: How long to wait for `<html lang>` to leave the server-default shell value
    #: (#651). Measured on a pt account, two consecutive loads: the attribute reads
    #: `en` until ~1.9 s and flips to `pt` at 2.26 / 2.49 s. 4 s leaves margin
    #: without being open-ended; a slower network could still exceed it, which the
    #: `lang_unchanged` event makes visible rather than silent.
    _LANG_SETTLE_TIMEOUT_MS: ClassVar[float] = 4_000.0

    async def _settled_lang(self, page: Any) -> str | None:
        """Read ``<html lang>`` only after hydration has had a chance to set it (#651).

        Flow serves an **`en` shell** and the app rewrites ``lang`` during
        hydration, so a single early read returns ``en`` for every account. That is
        not a migrated-origin quirk — it was measured on the OLD host:

        ===========  ==========  ==============
        t (ms)       ``lang``    ``readyState``
        ===========  ==========  ==============
        887          ``en``      interactive
        1510         ``en``      **complete**
        2092         ``en``      complete
        **2488**     **``pt``**  complete
        ===========  ==========  ==============

        **``readyState`` is not a usable signal** — it reaches ``complete`` a full
        second *before* the flip, so the obvious "wait for complete, then read" is
        wrong. The DOM node count oscillates (136 → 300 → 249 → 251) and does not
        discriminate either. Nothing cheap predicts the flip, so this observes it.

        Cost: an account whose real locale IS the shell default never changes the
        attribute and pays the full timeout once per process. That is the price of
        not guessing, and it is bounded; the alternative shipped a wrong locale for
        every account whose URL could not answer.
        """
        try:
            first = await page.evaluate("() => document.documentElement.lang || ''")
        except Exception as exc:  # noqa: BLE001 - observation only
            logger.info("client.account_locale_lang_probe_failed", error=type(exc).__name__)
            return None
        try:
            await page.wait_for_function(
                "initial => (document.documentElement.lang || '') !== initial",
                arg=first,
                timeout=self._LANG_SETTLE_TIMEOUT_MS,
            )
            after = await page.evaluate("() => document.documentElement.lang || ''")
        except Exception as exc:  # noqa: BLE001 - a timeout is an ANSWER, not a failure
            # Either the account's locale genuinely equals the shell default, or the
            # page never hydrated within the window. Both leave the first read as the
            # best available answer — which is exactly the pre-#651 behaviour, so this
            # branch can never be worse than what it replaces.
            logger.info(
                "client.account_locale_lang_unchanged",
                lang=first,
                waited_ms=self._LANG_SETTLE_TIMEOUT_MS,
                reason=type(exc).__name__,
            )
            return first
        logger.info("client.account_locale_lang_settled", was=first, now=after)
        return after
