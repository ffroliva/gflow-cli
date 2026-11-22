"""Direct HTTP transport for image generation on flow.google.com."""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx
import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api._engine import mint_evaluate_kwargs
from gflow_cli.api.image import Model
from gflow_cli.api.recaptcha import TokenMinter
from gflow_cli.api.transports._common import migrated_route
from gflow_cli.api.transports._mc_submit_observe import ogiz0b_reply_images
from gflow_cli.api.transports.frame_builder import build_ogiz0b_body
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings
from gflow_cli.errors import NetworkError

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.image import GenerateImageRequest

log = structlog.get_logger(__name__)

# The model name an ogiZ0b body carries on flow.google.com. Only BELUGA is measured (the
# page sends it for "Nano Banana 2.1", which replaced NARWHAL in Flow's catalogue, #958);
# every other model routes to the UI transport until its wire name is captured.
_WIRE_MODEL: dict[Model, str] = {Model.NARWHAL: "BELUGA"}


# Counts measured on the direct path (one POST with two entries returned two images).
_MAX_DIRECT_COUNT = 2
_ORIGIN = "https://flow.google.com"
_RECONCILE_HINT = (
    "Flow may have accepted this image request before the error, so gflow did not resend "
    "it. Check the project in Flow for the new image before running the command again."
)


def direct_can_serve(request: GenerateImageRequest) -> bool:
    """True only for a request whose every wire slot is measured.

    T2I with a measured model, any aspect, and up to ``_MAX_DIRECT_COUNT`` images. No
    reference images, character entities, Agent instructions or ``@`` mentions yet.
    """
    return (
        request.model in _WIRE_MODEL
        and request.count <= _MAX_DIRECT_COUNT
        and not (request.refs or request.ref_paths or request.reference_entities)
        and not request.instructions
        and "@" not in request.prompt
    )


class _DirectFallbackError(Exception):
    """Internal sentinel: nothing was sent, so the UI transport may run instead."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _reconcile_error(detail: str, *, status: int | None = None) -> NetworkError:
    """A failure after the POST was sent: never resent, the caller reconciles."""
    return NetworkError(
        detail=f"{detail}; not resent",
        status=status,
        route="batchexecute:ogiZ0b",
        next_action="reconcile",
        remediation_hint=_RECONCILE_HINT,
    )


async def _session_headers(page: Page, profile_dir: Path) -> dict[str, str]:
    """Read path: the live browser's cookies (the on-disk store is locked while Chrome
    holds the profile) and the profile's own user agent."""
    try:
        cookies = await page.context.cookies([_ORIGIN])
    except Exception as exc:
        raise _DirectFallbackError(f"cookie_error:{type(exc).__name__}") from exc
    cookie_header = "; ".join(
        f"{name}={value}" for c in cookies if (name := c.get("name")) and (value := c.get("value"))
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


async def _mint(page: Page, project_id: str) -> str:
    """The single browser step: an IMAGE_GENERATION token minted on the project page."""
    target = f"{_ORIGIN}/project/{project_id}"
    try:
        if not page.url.startswith(target):
            await page.goto(target)
        minter = TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs())
        return await minter.mint("IMAGE_GENERATION")
    except Exception as exc:
        raise _DirectFallbackError(f"mint_failure:{type(exc).__name__}") from exc


def _post_request(
    project_id: str, tokens: flow_wire.Tokens, headers: dict[str, str], body: str
) -> httpx.Request:
    query = {
        "rpcids": "ogiZ0b",
        "source-path": f"/project/{project_id}",
        "bl": tokens.bl,
        "f.sid": tokens.fsid,
        "hl": "en",
        "_reqid": str(1000 + secrets.randbelow(9000)),
        "rt": "c",
    }
    return httpx.Request(
        "POST",
        f"{_ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute?"
        f"{urlencode({k: v for k, v in query.items() if v is not None})}",
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


async def _send_once(client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
    """Write path: exactly one send. A connect error reached nothing; anything after it may
    have been billed and is never resent."""
    try:
        response = await client.send(request)
    except httpx.ConnectError as exc:
        raise _DirectFallbackError("connect_error") from exc
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        cause = (str(exc).strip().splitlines() or [""])[0][:160]
        raise _reconcile_error(f"{type(exc).__name__} on an image submit ({cause})") from exc
    if response.status_code == 403:
        raise _DirectFallbackError("http_403")
    if response.status_code != 200:
        raise _reconcile_error(
            f"HTTP {response.status_code} on an image submit", status=response.status_code
        )
    return response


class DirectImageHttpRunner:
    """Runs one direct image generation: read the session, mint, build, send once, parse."""

    def __init__(self, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http_transport = http_transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,  # nothing env-derived (proxies, netrc) reaches this client
            timeout=httpx.Timeout(120.0, connect=10.0),  # a generation takes 16-24 s
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
            transport=self._http_transport,  # None = httpx's default; a mock under test
        )

    async def run(
        self,
        *,
        page: Page,
        profile_dir: Path,
        project_id: str,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        started = time.perf_counter()
        headers = await _session_headers(page, profile_dir)
        async with self._client() as client:
            tokens = await _shell(client, profile_dir, headers)
            mint_started = time.perf_counter()
            token = await _mint(page, project_id)
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
            )
            log.info(
                "direct.frame_built",
                model=request.model.value,
                prompt_length=len(request.prompt),
                count=request.count,
            )
            post_started = time.perf_counter()
            response = await _send_once(client, _post_request(project_id, tokens, headers, body))
        log.info(
            "direct.image_post",
            status=response.status_code,
            mint_ms=mint_ms,
            post_ms=int((time.perf_counter() - post_started) * 1000),
            total_ms=int((time.perf_counter() - started) * 1000),
        )
        images = ogiz0b_reply_images(response.text, request)
        return [replace(img, transport="direct_http") for img in images]


class DirectHttpTransport(UiAutomationTransport):
    """Direct HTTP transport for image generation with reCAPTCHA minting from Flow page."""

    name = "direct_http"
    _profile_dir: Path | None = None

    def __init__(
        self,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
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
        """Generate images via direct HTTP when eligible, falling back to UI automation."""
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before generate_images()"
            raise RuntimeError(msg)

        async with self._generate_lock:
            await self.park_deferred_page()

            if not direct_can_serve(request):
                log.info("direct.fallback_to_ui", reason="unsupported_shape")
                return await self._generate_images_locked(
                    request, project_id=project_id, name_resolver=name_resolver
                )

            if project_id is None:
                log.info("direct.fallback_to_ui", reason="no_project")
                return await self._generate_images_locked(
                    request, project_id=project_id, name_resolver=name_resolver
                )

            page = self._page
            profile_dir = self._profile_dir
            if profile_dir is None:
                log.info("direct.fallback_to_ui", reason="no_profile_dir")
                return await self._generate_images_locked(
                    request, project_id=project_id, name_resolver=name_resolver
                )

            flow_host = get_settings().flow_host
            route = migrated_route(page.url, flow_host, prefer_migrated=True)
            if route != "migrated":
                log.info("direct.fallback_to_ui", reason="route_not_migrated")
                return await self._generate_images_locked(
                    request, project_id=project_id, name_resolver=name_resolver
                )

            if get_settings().browser_proxy or flow_wire.getproxies():
                log.info("direct.fallback_to_ui", reason="proxy_configured")
                return await self._generate_images_locked(
                    request, project_id=project_id, name_resolver=name_resolver
                )

            runner = DirectImageHttpRunner(http_transport=self._http_transport)

            try:
                images = await runner.run(
                    page=page,
                    profile_dir=profile_dir,
                    project_id=project_id,
                    request=request,
                )
                self.last_image_route = "migrated"
                return images
            except _DirectFallbackError as fb:
                log.info("direct.fallback_to_ui", reason=fb.reason)
                return await self._generate_images_locked(
                    request, project_id=project_id, name_resolver=name_resolver
                )
