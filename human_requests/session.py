"""
core.session — unified stateful session for *curl_cffi* and *Playwright*-compatible engines.

Main Methods
============
* ``Session.request``   — low-level HTTP request (curl_cffi) with cookie jar.
* ``Session.make_page`` — opens a new page in the browser, returns a HumanPage inside
  a context manager; upon exit synchronizes cookies + localStorage.
* ``Response.render``   — offline render of a pre-fetched Response.

Optional Dependencies
=====================
- playwright-stealth: enabled via `playwright_stealth=True`.
  If the package is not installed and the flag is set — raises RuntimeError
  with installation instructions.
- camoufox: selected with `browser='camoufox'`.
- patchright: selected with `browser='patchright'`.
- Incompatibility: camoufox/patchright + playwright_stealth cannot be used together.
  Raises RuntimeError.


Additional
==========
- Browser launch arguments are assembled via `make_browser_launch_opts()` from:
  - `browser_launch_opts` (arbitrary dict)
  - `headless` (always overrides the key of the same name)
  - `proxy` (string URL or dict) → adapted for Playwright/Patchright/Camoufox
- Proxy is also applied to curl_cffi (if no custom `proxy` is passed in .request()).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, AsyncGenerator, Literal, Mapping, Optional

from playwright.async_api import Request as PWRequest
from playwright.async_api import Route

from .abstraction.http import URL, HttpMethod
from .abstraction.proxy_manager import ParsedProxy
from .abstraction.request import Request
from .abstraction.response import Response
from .browsers import BrowserMaster, Engine
from .fingerprint import Fingerprint
from .human_context import HumanContext
from .human_page import HumanPage

__all__ = ["Session"]


class Session:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        headless: bool = True,
        browser: Engine = "chromium",
        playwright_stealth: bool = True,
        page_retry: int = 3,
        direct_retry: int = 2,
        browser_launch_opts: Mapping[str, Any] = {},
        proxy: str | None = None,
    ) -> None:
        """
        Args:
            timeout: default timeout for both direct and goto requests
            headless: launch mode (passed into browser launch arguments)
            browser: chromium/firefox/webkit — standard; camoufox/patchright — special builds
            spoof: configuration for direct requests
            playwright_stealth: hides certain automation browser signatures
            page_retry: number of "soft" retries for page navigation (after the initial attempt)
            direct_retry: retries for direct requests on curl_cffi Timeout (after first attempt)
        """
        self.timeout: float = timeout
        """Timeout for goto/direct requests."""

        self.headless: bool = bool(headless)
        """Whether to run the browser in headless mode."""

        self.browser_name: Engine = browser
        """Current browser (chromium/firefox/webkit/camoufox/patchright)."""

        self.playwright_stealth: bool = bool(playwright_stealth)
        """Hide certain automation signatures?
        Implemented via JS injection. Some sites may detect this."""

        self.page_retry: int = int(page_retry)
        """If a timeout occurs after N seconds — retry with page.reload()."""

        self.direct_retry: int = int(direct_retry)
        """If a timeout occurs after N seconds — retry the direct request."""

        if self.browser_name in ("camoufox", "patchright") and self.playwright_stealth:
            raise RuntimeError(
                "playwright_stealth=True is incompatible with browser='camoufox'/'patchright'. "
                "Disable stealth or use chromium/firefox/webkit."
            )

        # Custom browser launch parameters + proxy
        self.browser_launch_opts: Mapping[str, Any] = browser_launch_opts
        """Browser launch arguments (arbitrary keys)."""

        self.proxy: str | dict[str, str] | None = proxy
        """
        Proxy server, one of:

        a. URL string in the form: `schema://user:pass@host:port`

        b. playwright-like dict
        """

        self.fingerprint: Optional[Fingerprint] = None
        """Fingerprint of the browser."""

        # Браузерный движок — через мастер (всегда отдаёт Browser)
        self._bm: BrowserMaster = BrowserMaster(
            engine=self.browser_name,
            stealth=self.playwright_stealth,
            launch_opts=self._make_browser_launch_opts(),  # первичный снапшот
        )

    async def new_context(self, **kwargs) -> HumanContext:
        self._bm.launch_opts = self._make_browser_launch_opts()
        await self._bm.start()

        return await HumanContext.create(session=self, **kwargs)

    async def start(
        self,
        *,
        origin: str = "https://example.com",
        wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"] = "load",
    ) -> None:
        HTML_PATH = Path(__file__).parent / "fingerprint" / "fingerprint_gen.html"
        _HTML_FINGERPRINT = HTML_PATH.read_text(encoding="utf-8")

        headers = {}

        async def handler(route: Route, _req: PWRequest) -> None:
            headers.update(_req.headers)
            await route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=_HTML_FINGERPRINT
            )

        ctx: HumanContext = await self.make_context()

        await ctx.route(f"{origin}/**", handler)

        async with await ctx.new_page() as page:
            await page.goto(origin, wait_until=wait_until, timeout=self.timeout * 1000)

            try:
                storage = await page.localStorage()
                raw = storage.get("fingerprint", "")
                data = json.loads(raw)
            except Exception as e:
                raise RuntimeError("fingerprint отсутствует или битый JSON") from e

        await ctx.close()

        self.fingerprint = Fingerprint(
            user_agent=data.get("user_agent"),
            user_agent_client_hints=data.get("user_agent_client_hints"),
            headers=headers,
            platform=data.get("platform"),
            vendor=data.get("vendor"),
            languages=data.get("languages"),
            timezone=data.get("timezone"),
        )

    # ──────────────── Launch args & proxy helpers ────────────────
    def _make_browser_launch_opts(self) -> dict[str, Any]:
        """
        Merges launch arguments for BrowserMaster from Session settings.

        Sources:
          - self.browser_launch_opts (arbitrary keys)
          - self.headless (overrides the key of the same name)
          - self.proxy (URL string or dict) → converted to Playwright-style proxy
        """
        opts = dict(self.browser_launch_opts)
        opts["headless"] = bool(self.headless)

        pw_proxy = ParsedProxy.from_any(self.proxy)
        if pw_proxy is not None:
            opts["proxy"] = pw_proxy.for_playwright()

        return opts

    # ────── browser nav ──────
    @asynccontextmanager
    async def new_page(
        self,
        **kwargs: Any,
    ) -> AsyncGenerator[HumanPage, None]:
        ctx: HumanContext = await self.new_context(**kwargs)
        page: HumanPage = await ctx.new_page()

        try:
            yield page
        finally:
            await page.close()
            await ctx.close()

    @asynccontextmanager
    async def _render_response(
        self,
        response: Response,
        *,
        wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded",
        retry: int | None = None,
        context: Optional[HumanContext] = None,
    ) -> AsyncGenerator[HumanPage, None]:
        """
        Offline render of a Response:
          - Intercepts the first request and fulfills it with `response`.
          - Retries re-attach the route without recreating the context/page.

        Context handling is the same as in `make_page()`:
          - Provided `context` is reused and NOT closed.
          - Otherwise, a one-shot context is created and closed with auto-sync.
        """
        external_ctx = context is not None
        ctx: HumanContext = context or await self.new_context()

        async def _attach_route_once() -> None:
            await ctx.unroute("**/*")

            async def handler(route: Route, _req: PWRequest) -> None:
                await route.fulfill(
                    status=response.status_code,
                    headers=dict(response.headers),
                    body=response.body.encode("utf-8"),
                )

            await ctx.route("**/*", handler, times=1)

        await _attach_route_once()
        page: HumanPage = await ctx.new_page()

        try:

            async def _on_retry() -> None:
                await _attach_route_once()

            await page.goto(
                url=response.url.full_url,
                retry=retry,
                on_retry=_on_retry,
                wait_until=wait_until,
            )

            yield page
        finally:
            await page.close()
            if not external_ctx:
                await ctx.close()

    # ────── cleanup ──────
    async def close(self) -> None:
        await self._bm.close()

    # поддержка «async with»
    async def __aenter__(self) -> "Session":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()
