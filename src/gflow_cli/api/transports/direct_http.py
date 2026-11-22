"""Direct HTTP transport for image generation on flow.google.com."""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api._engine import mint_evaluate_kwargs
from gflow_cli.api.image import Aspect
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


def direct_can_serve(request: GenerateImageRequest) -> bool:
    """Return True only for plain T2I requests supported by direct HTTP.

    Excludes reference images (refs, ref_paths), character entities,
    non-portrait aspect ratios, multi-image counts, Agent instructions,
    and character mentions (@).
    """
    if request.aspect is not Aspect.PORTRAIT:
        return False
    if request.count != 1:
        return False
    if bool(request.refs) or bool(request.ref_paths) or bool(request.reference_entities):
        return False
    if bool(request.instructions):
        return False
    if "@" in request.prompt:
        return False
    return True


class _DirectFallbackError(Exception):
    """Internal sentinel indicating the direct path should fall back to UI automation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DirectImageHttpRunner:
    """Collaborator executing the direct HTTP sequence for image generation.

    Coordinates live cookies, shell tokens, page minting, ogiZ0b frame building,
    batchexecute POST, and response parsing.
    """

    def __init__(self, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http_transport = http_transport

    async def run(
        self,
        *,
        page: Page,
        profile_dir: Path,
        project_id: str,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        t_start = time.perf_counter()

        # 1. Read live cookies from browser context
        try:
            cookie_list = await page.context.cookies(["https://flow.google.com"])
            cookie_parts = [
                f"{name}={value}"
                for c in cookie_list
                if (name := c.get("name")) and (value := c.get("value"))
            ]
            cookie_header = "; ".join(cookie_parts)
            if not cookie_header:
                raise _DirectFallbackError("cookies_empty")
        except _DirectFallbackError:
            raise
        except Exception as exc:
            raise _DirectFallbackError(f"cookie_error:{type(exc).__name__}") from exc

        # 2. Shell headers and user agent
        ua = flow_wire.user_agent(profile_dir)
        headers = {
            "accept-language": "en-US,en;q=0.9",
            "cookie": cookie_header,
        }
        if ua:
            headers["user-agent"] = ua

        client_kwargs: dict[str, Any] = {
            "follow_redirects": False,
            "trust_env": False,
            "timeout": httpx.Timeout(120.0, connect=10.0),
            "cookies": CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        }
        if self._http_transport is not None:
            client_kwargs["transport"] = self._http_transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            # 3. Shell tokens
            try:
                tokens = await flow_wire.shell_tokens(client, profile_dir, headers)
            except flow_wire.WireReadUnavailable as exc:
                raise _DirectFallbackError(exc.reason.value) from exc
            except Exception as exc:
                raise _DirectFallbackError(f"shell_tokens:{type(exc).__name__}") from exc

            # 4. Page navigation check & reCAPTCHA token minting
            target_url = f"https://flow.google.com/project/{project_id}"
            if not page.url.startswith(target_url):
                try:
                    await page.goto(target_url)
                except Exception as exc:
                    raise _DirectFallbackError(f"navigation_failed:{type(exc).__name__}") from exc

            mint_start = time.perf_counter()
            try:
                token = await TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs()).mint(
                    "IMAGE_GENERATION"
                )
            except Exception as exc:
                raise _DirectFallbackError("mint_failure") from exc
            mint_ms = int((time.perf_counter() - mint_start) * 1000)

            # 5. Build ogiZ0b body
            batch_id = str(uuid.uuid4()).upper()
            session_counter = 1 + secrets.randbelow(2**31 - 1)
            body = build_ogiz0b_body(
                prompt=request.prompt,
                model=request.model.value,
                project_id=project_id,
                recaptcha_token=token,
                at_token=tokens.at,
                batch_id=batch_id,
                session_counter=session_counter,
            )
            log.info(
                "direct.frame_built",
                model=request.model.value,
                prompt_length=len(request.prompt),
            )

            # 6. POST
            query = {
                "rpcids": "ogiZ0b",
                "source-path": f"/project/{project_id}",
                "bl": tokens.bl,
                "f.sid": tokens.fsid,
                "hl": "en",
                "_reqid": str(1000 + secrets.randbelow(9000)),
                "rt": "c",
            }
            post_url = (
                f"https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?"
                f"{urlencode({k: v for k, v in query.items() if v is not None})}"
            )
            post_headers = {
                **headers,
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                "x-same-domain": "1",
                "origin": "https://flow.google.com",
                "referer": f"https://flow.google.com/project/{project_id}",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            }

            post_start = time.perf_counter()
            try:
                response = await client.post(post_url, content=body, headers=post_headers)
            except httpx.ConnectError as exc:
                raise _DirectFallbackError("connect_error") from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                cause = (str(exc).strip().splitlines() or [""])[0][:160]
                raise NetworkError(
                    detail=f"{type(exc).__name__} on an image submit ({cause}); not resent",
                    route="batchexecute:ogiZ0b",
                    next_action="reconcile",
                    remediation_hint=(
                        "Flow may have accepted this image request before the error, so gflow "
                        "did not resend it. Check the project in Flow for the new image before "
                        "running the command again."
                    ),
                ) from exc

            post_ms = int((time.perf_counter() - post_start) * 1000)
            total_ms = int((time.perf_counter() - t_start) * 1000)
            log.info(
                "direct.image_post",
                status=response.status_code,
                mint_ms=mint_ms,
                post_ms=post_ms,
                total_ms=total_ms,
            )

            if response.status_code == 403:
                raise _DirectFallbackError("http_403")

            if response.status_code != 200:
                raise NetworkError(
                    detail=f"HTTP {response.status_code} on an image submit; not resent",
                    status=response.status_code,
                    route="batchexecute:ogiZ0b",
                    next_action="reconcile",
                    remediation_hint=(
                        "Flow may have accepted this image request before the error, so gflow "
                        "did not resend it. Check the project in Flow for the new image before "
                        "running the command again."
                    ),
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
        self._runner = DirectImageHttpRunner(http_transport=http_transport)

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
