"""Direct HTTP transport for image generation on flow.google.com."""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api._engine import mint_evaluate_kwargs
from gflow_cli.api.image import ImageRef, Model
from gflow_cli.api.recaptcha import TokenMinter
from gflow_cli.api.transports._common import flow_host_kind
from gflow_cli.api.transports._mc_common import _first_uuid  # pyright: ignore[reportPrivateUsage]
from gflow_cli.api.transports._mc_submit_observe import ogiz0b_reply_images
from gflow_cli.api.transports.frame_builder import build_maseq_body, build_ogiz0b_body
from gflow_cli.api.transports.migrated_character import read_project
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.auth.cookies import sent_to_flow_host
from gflow_cli.config import UiMode, get_settings, resolve_ui_mode
from gflow_cli.errors import NetworkError, RecaptchaError, WireFormatError
from gflow_cli.paths import extension_from_magic

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.image import GenerateImageRequest

log = structlog.get_logger(__name__)

# The model name an ogiZ0b body carries on flow.google.com. Only BELUGA is measured (the
# page sends it for "Nano Banana 2.1", which replaced NARWHAL in Flow's catalogue, #958);
# every other model routes to the UI transport until its wire name is captured.
_WIRE_MODEL: dict[Model, str] = {Model.NARWHAL: "BELUGA"}

_ORIGIN = "https://flow.google.com"
_MINT_BUDGET_S = 10.0  # how long a freshly navigated page gets to load reCAPTCHA
_MINT_RETRY_S = 0.5
_NAVIGATE_MS = 45_000  # the composer's own budget for reaching a project
_CHARACTER_READ_S = 15.0  # a bare @Name's project read; past it, the UI picker decides
_RECONCILE_HINT = (
    "Flow may have accepted this image request before the error, so gflow did not resend "
    "it. Check the project in Flow for the new image before running the command again."
)
_UPLOAD_RECONCILE_HINT = (
    "Flow may have stored this upload before the error, so gflow did not resend it. Check "
    "the project in Flow for the uploaded image before running the command again."
)
_SUBMIT = ("ogiZ0b", "IMAGE_GENERATION", "image submit", _RECONCILE_HINT)
_UPLOAD = ("maseQ", "UPLOAD_IMAGE", "image upload", _UPLOAD_RECONCILE_HINT)
_RpcSpec = tuple[str, str, str, str]  # rpcid, reCAPTCHA action, noun, reconcile hint
# The MIME type sent with an upload comes from the bytes, not the file name: a JPEG named
# without an extension must not go out as image/png. The caller's validator already refused
# anything that is not one of these.
_UPLOAD_MIME = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def direct_can_serve(request: GenerateImageRequest) -> bool:
    """True only for a request whose every wire slot is measured.

    T2I with a measured model, any aspect, any count Flow offers (1-4: N entries in one
    POST returned N images live), or one in-project reference or one named character with
    one image, on the classic composer. No Agent instructions and no raw ``@`` left in the
    prompt (a mention reaches here already resolved to a name).
    """
    return (
        request.model in _WIRE_MODEL
        and _references_measured(request)
        and _characters_measured(request)
        and not request.ref_paths
        and not request.instructions
        and request.thinking_level is None
        and "@" not in request.prompt
        and resolve_ui_mode(request.ui_mode) is not UiMode.AGENTIC
    )


def _references_measured(request: GenerateImageRequest) -> bool:
    """No refs, or the one measured I2I shape: a single in-project image with a display
    name (the chip needs it), one output image, made or uploaded in this run.

    Only a ref this run made (a ``batch:N`` row's image) or uploaded carries no
    ``local_path``; one found in the local catalog always does (the CLI's lookup and the
    #772 reuse fill it). The catalog can be stale: Flow may have deleted or renamed the
    image. The UI path proves the ref still exists before it submits and refuses at $0; a
    direct POST cannot, and what Flow does with a dead id is unmeasured. So catalogued
    refs stay on the UI path."""
    if not request.refs:
        return True
    (ref, *more) = request.refs
    return (
        not more
        and ref.in_project
        and bool(ref.display_name)
        and not ref.local_path
        and request.count == 1
    )


def _characters_measured(request: GenerateImageRequest) -> bool:
    """No character, or the one measured shape: a single named character, no image
    reference, one output image. A bare ``@Name`` mention arrives as the name alone; the
    direct path looks its id up in the project before sending (:func:`_resolve_character`)."""
    ids, names = request.reference_entities, request.reference_entity_names
    if not ids and not names:
        return True
    return (
        len(ids) <= 1
        and len(names) == 1
        and bool(names[0])
        and not request.refs
        and request.count == 1
    )


async def _resolve_character(
    page: Page, project_id: str, request: GenerateImageRequest
) -> tuple[str, str]:
    """Read path: the ``(entity_id, name)`` pair the frame carries, as the project holds it.

    Both forms are checked against Flow's own project reply (``read_project``, which also
    lands the page on the project for the mint), as the UI does when it attaches one:
    - an id (``--reference-entity``) must be one the project holds; the chip uses Flow's
      name for it. A stale or foreign id falls back before any POST.
    - a bare mention carries only the name: exactly one character must match it by the
      picker's rule (trimmed, case-insensitive), or the UI picker decides.
    The project read also measured the difference live: with it the request was accepted,
    without it Flow refused an id-carrying request twice (exit 10, 2026-11-23).
    """
    names = request.reference_entity_names
    try:
        # Bounded: a slow read must fall back, not hold the generate lock for minutes.
        # One outer deadline: read_project's own budget is per attempt, and it retries.
        project = await asyncio.wait_for(
            read_project(page, project_id, timeout_s=_CHARACTER_READ_S), _CHARACTER_READ_S
        )
    except Exception as exc:
        raise _DirectFallbackError(f"character_read:{type(exc).__name__}") from exc
    if request.reference_entities:
        wanted_id = request.reference_entities[0]
        matches = [c for c in project.characters if c.entity_id == wanted_id]
    else:  # the picker's own rule (_mc_mention.py): trimmed, case-insensitive, unique
        wanted = names[0].strip().casefold()
        matches = [c for c in project.characters if c.display_name.strip().casefold() == wanted]
    if len(matches) != 1:
        raise _DirectFallbackError(f"character_matches:{len(matches)}")
    log.info("direct.character_resolved", source="project_read")
    return (matches[0].entity_id, matches[0].display_name)


class _DirectFallbackError(Exception):
    """Internal sentinel: nothing was accepted, so the UI transport may run instead."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _reconcile_error(detail: str, spec: _RpcSpec, *, status: int | None = None) -> NetworkError:
    """A failure after the POST was sent: never resent, the caller reconciles."""
    return NetworkError(
        detail=f"{detail}; not resent",
        status=status,
        route=f"batchexecute:{spec[0]}",
        next_action="reconcile",
        remediation_hint=spec[3],
    )


def _proxy_configured() -> bool:
    """A proxy anywhere means the browser and this request could leave by different routes.
    ``NO_PROXY`` alone shows up as the ``no`` key and configures no proxy."""
    return bool(get_settings().browser_proxy) or any(k != "no" for k in flow_wire.getproxies())


async def _session_headers(page: Page, profile_dir: Path) -> dict[str, str]:
    """Read path: the live browser's cookies (the on-disk store is locked while Chrome
    holds the profile) and the profile's own user agent. Read without a URL filter and
    matched by domain: a URL filter also drops path-scoped cookies (#222)."""
    try:
        cookies = await page.context.cookies()
    except Exception as exc:
        raise _DirectFallbackError(f"cookie_error:{type(exc).__name__}") from exc
    cookie_header = "; ".join(
        f"{name}={value}"
        for c in cookies
        if (name := c.get("name"))
        and (value := c.get("value"))
        and sent_to_flow_host(c.get("domain", ""))
    )
    if not cookie_header:
        raise _DirectFallbackError("cookies_empty")
    headers = {"accept-language": "en-US,en;q=0.9", "cookie": cookie_header}
    if ua := flow_wire.user_agent(profile_dir):
        headers["user-agent"] = ua
    return headers


async def _shell(
    client: httpx.AsyncClient, profile_dir: Path, headers: dict[str, str]
) -> flow_wire.Tokens:
    """Read path: ``at`` / ``f.sid`` / ``bl`` from flow_wire's shell GET."""
    try:
        return await flow_wire.shell_tokens(client, profile_dir, headers)
    except flow_wire.WireReadUnavailable as exc:
        raise _DirectFallbackError(exc.reason.value) from exc
    except Exception as exc:
        raise _DirectFallbackError(f"shell_tokens:{type(exc).__name__}") from exc


async def _mint(page: Page, project_id: str, action: str) -> str:
    """The single browser step: a reCAPTCHA token for ``action``, minted on the project page.

    The page must end up on flow.google.com: an account Flow serves on labs.google is
    redirected there, and the direct path does not serve it. Right after a navigation the
    enterprise script is not loaded yet and the mint raises ``RecaptchaError`` (measured
    2026-11-23 on an i2i run with ``--project``). A mint spends nothing, so it is retried,
    every attempt bounded, until the script answers or the budget runs out.
    """
    target = f"{_ORIGIN}/project/{project_id}"
    try:
        if not page.url.startswith(target):
            await page.goto(target, wait_until="domcontentloaded", timeout=_NAVIGATE_MS)
    except Exception as exc:
        raise _DirectFallbackError(f"navigation_failed:{type(exc).__name__}") from exc
    if flow_host_kind(page.url) != "migrated":
        raise _DirectFallbackError("served_labs")
    minter = TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs())
    deadline = time.monotonic() + _MINT_BUDGET_S
    while True:
        try:
            remaining = max(deadline - time.monotonic(), 0.1)
            return await asyncio.wait_for(minter.mint(action), remaining)
        except RecaptchaError as exc:
            if time.monotonic() >= deadline:
                raise _DirectFallbackError("mint_failure:RecaptchaError") from exc
            await asyncio.sleep(_MINT_RETRY_S)
        except Exception as exc:
            raise _DirectFallbackError(f"mint_failure:{type(exc).__name__}") from exc


def _post_request(
    rpcid: str, project_id: str, tokens: flow_wire.Tokens, headers: dict[str, str], body: str
) -> httpx.Request:
    query = {
        "rpcids": rpcid,
        "source-path": f"/project/{project_id}",
        "bl": tokens.bl,
        "f.sid": tokens.fsid,
        "hl": "en",
        "_reqid": str(1000 + secrets.randbelow(9000)),
        "rt": "c",
    }
    return httpx.Request(
        "POST",
        f"{_ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute",
        params={k: v for k, v in query.items() if v is not None},
        content=body,
        headers={
            **headers,
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1",
            "origin": _ORIGIN,
            "referer": f"{_ORIGIN}/project/{project_id}",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        },
    )


async def _send_once(
    client: httpx.AsyncClient, request: httpx.Request, profile_dir: Path, spec: _RpcSpec
) -> httpx.Response:
    """Write path: exactly one send.

    Nothing was accepted when the connection never opened, or when Flow redirected (3xx),
    refused the session (401) or the token (403): the UI transport may run instead.
    Anything else after the send may have been billed and is never resent. Every rejected
    reply drops the cached ``at`` so a stale one cannot stick in a long-lived process.
    """
    try:
        response = await client.send(request)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise _DirectFallbackError(f"connect:{type(exc).__name__}") from exc
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        cause = (str(exc).strip().splitlines() or [""])[0][:160]
        raise _reconcile_error(f"{type(exc).__name__} on an {spec[2]} ({cause})", spec) from exc
    status = response.status_code
    if status == 200:
        return response
    flow_wire.forget_tokens(profile_dir)
    if 300 <= status < 400 or status in (401, 403):
        raise _DirectFallbackError(f"http_{status}")
    raise _reconcile_error(f"HTTP {status} on an {spec[2]}", spec, status=status)


def _client(http_transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,  # nothing env-derived (proxies, netrc) reaches this client
        timeout=httpx.Timeout(120.0, connect=10.0),  # a generation takes 16-24 s
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        transport=http_transport,  # None = httpx's default; a mock under test
    )


async def run_direct_images(
    *,
    page: Page,
    profile_dir: Path,
    project_id: str,
    request: GenerateImageRequest,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> list[GeneratedImage]:
    """One direct image generation: read the session, mint, build, send once, parse."""
    started = time.perf_counter()
    headers = await _session_headers(page, profile_dir)
    async with _client(http_transport) as client:
        tokens = await _shell(client, profile_dir, headers)
        characters = (
            (await _resolve_character(page, project_id, request),)
            if request.reference_entity_names
            else ()
        )
        mint_started = time.perf_counter()
        token = await _mint(page, project_id, _SUBMIT[1])
        mint_ms = int((time.perf_counter() - mint_started) * 1000)
        body = build_ogiz0b_body(
            prompt=request.prompt,
            model=_WIRE_MODEL[request.model],
            project_id=project_id,
            recaptcha_token=token,
            at_token=tokens.at,
            batch_id=str(uuid.uuid4()).upper(),
            entry_seeds=tuple(1 + secrets.randbelow(2**31 - 1) for _ in range(request.count)),
            aspect=request.aspect,
            references=tuple((ref.name, ref.display_name) for ref in request.refs),
            characters=characters,
        )
        log.info(
            "direct.frame_built",
            model=request.model.value,
            prompt_length=len(request.prompt),
            count=request.count,
        )
        post_started = time.perf_counter()
        response = await _send_once(
            client, _post_request("ogiZ0b", project_id, tokens, headers, body), profile_dir, _SUBMIT
        )
    log.info(
        "direct.image_post",
        status=response.status_code,
        mint_ms=mint_ms,
        post_ms=int((time.perf_counter() - post_started) * 1000),
        total_ms=int((time.perf_counter() - started) * 1000),
    )
    images = ogiz0b_reply_images(response.text, request)
    return [replace(img, transport="direct_http") for img in images]


async def run_direct_upload(
    *,
    page: Page,
    profile_dir: Path,
    project_id: str,
    path: Path,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> ImageRef:
    """One direct ``maseQ`` upload: read the session, mint ``UPLOAD_IMAGE``, send once.

    The caller has validated the file (size, image header). The display name gets a run-
    unique suffix, as the UI's own upload does, so a later chip names this image only.
    """
    started = time.perf_counter()
    headers = await _session_headers(page, profile_dir)
    async with _client(http_transport) as client:
        tokens = await _shell(client, profile_dir, headers)
        token = await _mint(page, project_id, _UPLOAD[1])
        image = await asyncio.to_thread(path.read_bytes)
        display_name = f"{path.stem}-{secrets.token_hex(4)}{path.suffix}"
        body = build_maseq_body(
            project_id=project_id,
            recaptcha_token=token,
            at_token=tokens.at,
            image_bytes=image,
            mime=_UPLOAD_MIME[extension_from_magic(image[:12]) or ".png"],
            display_name=display_name,
            request_ids=(str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()),
        )
        response = await _send_once(
            client, _post_request("maseQ", project_id, tokens, headers, body), profile_dir, _UPLOAD
        )
    media_id = _first_uuid(response.text)
    if media_id is None:
        raise WireFormatError(
            detail="direct maseQ upload returned no media id", route="batchexecute:maseQ"
        )
    log.info("direct.upload", total_ms=int((time.perf_counter() - started) * 1000))
    return ImageRef(name=media_id, display_name=display_name, in_project=True)


class DirectHttpTransport(UiAutomationTransport):
    """The UI transport, with measured image requests sent as one direct ``ogiZ0b`` POST."""

    name = "direct_http"
    _profile_dir: Path | None = None

    def __init__(self, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__()
        self._http_transport = http_transport

    async def setup(self, profile_dir: Path, *, page: Page | None = None) -> None:
        self._profile_dir = profile_dir
        await super().setup(profile_dir, page=page)

    async def generate_images(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]:
        """Generate via one direct POST when the request is measured; otherwise, or when
        nothing was accepted, through the inherited UI path."""
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before generate_images()"
            raise RuntimeError(msg)

        async with self._generate_lock:
            await self.park_deferred_page()
            page, profile_dir = self._page, self._profile_dir
            reason = self._why_not_direct(request, project_id)
            if reason is None and project_id is not None and profile_dir is not None:
                try:
                    images = await run_direct_images(
                        page=page,
                        profile_dir=profile_dir,
                        project_id=project_id,
                        request=request,
                        http_transport=self._http_transport,
                    )
                except _DirectFallbackError as fb:
                    reason = fb.reason
                else:
                    self.last_image_route = "migrated"
                    return images
                finally:
                    # Never leave a mounted composer for the next request (_park_composer_page).
                    await self._park_composer_page(page, event="direct.page_park_failed")
            log.info("direct.fallback_to_ui", reason=reason)
            return await self._generate_images_locked(
                request, project_id=project_id, name_resolver=name_resolver
            )

    async def upload_reference(self, *, project_id: str, path: Path) -> ImageRef | None:
        """Upload ``path`` with one direct ``maseQ`` POST; the UI upload when nothing was
        sent (no session, no token, not flow.google.com, a proxy, a refused session)."""
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before upload_reference()"
            raise RuntimeError(msg)
        page, profile_dir = self._page, self._profile_dir
        reason = self._why_not_direct_upload()
        if reason is None and profile_dir is not None:
            async with self._generate_lock:
                await self.park_deferred_page()
                try:
                    return await run_direct_upload(
                        page=page,
                        profile_dir=profile_dir,
                        project_id=project_id,
                        path=path,
                        http_transport=self._http_transport,
                    )
                except _DirectFallbackError as fb:
                    reason = fb.reason
                finally:
                    await self._park_composer_page(page, event="direct.page_park_failed")
        log.info("direct.upload_fallback_to_ui", reason=reason)
        return await super().upload_reference(project_id=project_id, path=path)

    def _why_not_direct_upload(self) -> str | None:
        if self._profile_dir is None:
            return "no_profile_dir"
        if get_settings().flow_host == "labs.google":
            return "flow_host_labs"
        if _proxy_configured():
            return "proxy_configured"
        return None

    def _why_not_direct(self, request: GenerateImageRequest, project_id: str | None) -> str | None:
        if not direct_can_serve(request):
            return "unsupported_shape"
        if project_id is None:
            return "no_project"
        if self._profile_dir is None:
            return "no_profile_dir"
        if get_settings().flow_host == "labs.google":
            return "flow_host_labs"
        if _proxy_configured():
            return "proxy_configured"
        return None
