"""
core.session — unified stateful session for *curl_cffi* and *Playwright*-compatible engines.

Main Methods
============
* ``Session.request``   — low-level HTTP request (curl_cffi) with cookie jar.
* ``Session.goto_page`` — opens a URL in the browser, returns a Page inside
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
from time import perf_counter, time
from types import TracebackType
from typing import Any, AsyncGenerator, Literal, Mapping, Optional, cast
from urllib.parse import urlsplit

from curl_cffi import requests as cffi_requests
from playwright.async_api import BrowserContext, Page
from playwright.async_api import Request as PWRequest
from playwright.async_api import Route

from .abstraction.cookies import CookieManager
from .abstraction.http import URL, HttpMethod
from .abstraction.proxy_manager import ParsedProxy
from .abstraction.request import Request
from .abstraction.response import Response
from .browsers import BrowserMaster, Engine
from .fingerprint import Fingerprint
from .impersonation.impersonation import ImpersonationConfig
from .tools.helper_tools import (
    build_storage_state_for_context,
    handle_nav_with_retries,
    merge_storage_state_from_context,
)
from .tools.http_utils import (
    collect_set_cookie_headers,
    compose_cookie_header,
    parse_set_cookie,
)

__all__ = ["Session"]


class Session:
    """curl_cffi.AsyncSession + BrowserMaster + CookieManager."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        headless: bool = True,
        browser: Engine = "chromium",
        spoof: ImpersonationConfig | None = None,
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

        self.spoof: ImpersonationConfig = spoof or ImpersonationConfig()
        """Impersonation settings (user-agent, TLS, client-hello)."""

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

        # Cookie/localStorage state
        self.cookies: CookieManager = CookieManager([])
        """Storage of all active cookies."""

        self.fingerprint: Optional[Fingerprint] = None
        """Fingerprint of the browser."""

        self.local_storage: dict[str, dict[str, str]] = {}
        """localStorage from the last browser context (goto run)."""

        # Низкоуровневый HTTP
        self._curl: Optional[cffi_requests.AsyncSession] = None

        # Браузерный движок — через мастер (всегда отдаёт Browser)
        self._bm: BrowserMaster = BrowserMaster(
            engine=self.browser_name,
            stealth=self.playwright_stealth,
            launch_opts=self._make_browser_launch_opts(),  # первичный снапшот
        )

    async def _make_context(self) -> BrowserContext:
        self._bm.launch_opts = self._make_browser_launch_opts()
        await self._bm.start()

        storage_state = build_storage_state_for_context(
            local_storage=self.local_storage,
            cookie_manager=self.cookies,
        )
        return await self._bm.new_context(storage_state=storage_state)

    async def start(self, *, origin: str = "https://example.com", wait_until: str = "load") -> None:
        HTML_PATH = Path(__file__).parent / "fingerprint" / "fingerprint_gen.html"
        _HTML_FINGERPRINT = HTML_PATH.read_text(encoding="utf-8")
        ctx: BrowserContext = await self._make_context()

        headers = {}

        async def handler(route: Route, _req: PWRequest) -> None:
            headers.update(_req.headers)
            await route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=_HTML_FINGERPRINT
            )

        await ctx.route(f"{origin}/**", handler)

        async with await ctx.new_page() as page:
            await page.goto(origin, wait_until="load", timeout=self.timeout * 1000)

        self.local_storage = await merge_storage_state_from_context(
            ctx, cookie_manager=self.cookies
        )

        try:
            raw = self.local_storage[origin]["fingerprint"]  # читаем только ПОСЛЕ закрытия
            data = json.loads(raw)
        except Exception as e:
            raise RuntimeError("fingerprint отсутствует или битый JSON") from e
        self.local_storage[origin].pop("fingerprint", None)

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

    # ────── HTTP через curl_cffi ──────
    async def request(
        self,
        method: HttpMethod | str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        retry: int | None = None,
        **kwargs: Any,
    ) -> Response:
        """
        Standard fast request via curl_cffi.
        You must provide either an HttpMethod or its string representation, as well as a URL.

        Optionally, you can pass additional headers.
        Always adds a standard browsers headers.

        Extra parameters can be passed through **kwargs to curl_cffi.AsyncSession.request
        (see their documentation for details).
        Retries are performed ONLY on cffi Timeout: ``curl_cffi.requests.exceptions.Timeout``.
        """
        method_enum = method if isinstance(method, HttpMethod) else HttpMethod[str(method).upper()]
        base_headers = {k.lower(): v for k, v in (headers or {}).items()}

        # lazy curl session
        if self._curl is None:
            self._curl = cffi_requests.AsyncSession()
        curl = self._curl
        assert curl is not None  # для mypy: ниже уже не union

        # spoof UA / headers
        assert isinstance(
            self.fingerprint, Fingerprint
        ), "fingerprint must be initialized in start()"

        imper_profile, hdrs = self.spoof.choose(self.fingerprint)

        req_url = URL(full_url=url)
        hdrs["host"] = req_url.domain_with_port
        hdrs.update(base_headers)

        # Cookie header (фиксируем один раз на первую попытку)
        url_parts = urlsplit(url)
        cookie_header, sent_cookies = compose_cookie_header(
            url_parts, base_headers, list(self.cookies)
        )
        if cookie_header:
            hdrs["cookie"] = cookie_header

        # proxies по умолчанию из Session.proxy, если пользователь не передал свои
        pp_user_proxies = ParsedProxy.from_any(kwargs.pop("proxy", None))
        user_proxies = None
        if pp_user_proxies:
            user_proxies = pp_user_proxies.for_curl()

        pp_default_proxies = ParsedProxy.from_any(self.proxy)
        default_proxies = None
        if pp_default_proxies:
            default_proxies = pp_default_proxies.for_curl()

        attempts_left = self.direct_retry if retry is None else int(retry)
        last_err: Exception | None = None

        async def _do_request() -> tuple[Any, float]:
            t0 = perf_counter()
            r = await curl.request(
                method_enum.value,
                url,
                headers=hdrs,
                impersonate=cast(  # сузить тип до Literal набора curl_cffi
                    "cffi_requests.impersonate.BrowserTypeLiteral", imper_profile
                ),
                timeout=self.timeout,
                proxy=user_proxies if user_proxies is not None else default_proxies,
                **kwargs,
            )
            duration = perf_counter() - t0
            return r, duration

        # первая попытка + мягкие повторы на Timeout
        try:
            r, duration = await _do_request()
        except cffi_requests.exceptions.Timeout as e:
            last_err = e
            while attempts_left > 0:
                attempts_left -= 1
                try:
                    r, duration = await _do_request()
                    last_err = None
                    break
                except cffi_requests.exceptions.Timeout as e2:
                    last_err = e2
            if last_err is not None:
                raise last_err

        # response → cookies
        resp_headers = {k.lower(): v for k, v in r.headers.items()}
        raw_sc = collect_set_cookie_headers(r.headers)
        resp_cookies = parse_set_cookie(raw_sc, url_parts.hostname or "")
        self.cookies.add(resp_cookies)

        data = kwargs.get("data")
        json_body = kwargs.get("json")
        files = kwargs.get("files")

        # models
        req_model = Request(
            method=method_enum,
            url=req_url,
            headers=dict(base_headers),
            impersonate=imper_profile,
            body=data or json_body or files or None,
            cookies=sent_cookies,
        )
        resp_model = Response(
            request=req_model,
            url=URL(full_url=str(r.url)),
            headers=resp_headers,
            cookies=resp_cookies,
            raw=r.content,
            status_code=r.status_code,
            duration=duration,
            end_time=time(),
            _render_callable=self._render_response,
        )
        return resp_model

    # ────── browser nav ──────
    @asynccontextmanager
    async def goto_page(
        self,
        url: str,
        *,
        wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"] = "commit",
        retry: int | None = None,
    ) -> AsyncGenerator[Page, None]:
        """
        Opens a page in the browser using a one-time context.
        Retries perform a "soft reload" without recreating the context.
        """
        ctx = await self._make_context()
        page = await ctx.new_page()
        timeout_ms = int(self.timeout * 1000)
        attempts_left = self.page_retry if retry is None else int(retry)

        try:
            await handle_nav_with_retries(
                page,
                target_url=url,
                wait_until=wait_until,
                timeout_ms=timeout_ms,
                attempts=attempts_left,
                on_retry=None,
            )
            yield page
        finally:
            self.local_storage = await merge_storage_state_from_context(
                ctx, cookie_manager=self.cookies
            )
            await page.close()
            await ctx.close()

    # ────── Offline render ──────
    @asynccontextmanager
    async def _render_response(
        self,
        response: Response,
        *,
        wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded",
        retry: int | None = None,
    ) -> AsyncGenerator[Page, None]:
        """
        Offline render of a Response: creates a temporary context (with our storage_state),
        intercepts the first request and responds with the prepared body.
        Retries do not recreate the context/page — instead a "soft reload" is performed,
        reattaching the route on retry.
        """
        ctx: BrowserContext = await self._make_context()
        timeout_ms = int(self.timeout * 1000)
        attempts_left = self.page_retry if retry is None else int(retry)

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
        page = await ctx.new_page()

        try:

            async def _on_retry() -> None:
                await _attach_route_once()

            await handle_nav_with_retries(
                page,
                target_url=response.url.full_url,
                wait_until=wait_until,
                timeout_ms=timeout_ms,
                attempts=attempts_left,
                on_retry=_on_retry,
            )
            yield page
        finally:
            self.local_storage = await merge_storage_state_from_context(
                ctx, cookie_manager=self.cookies
            )
            await page.close()
            await ctx.close()

    # ────── cleanup ──────
    async def close(self) -> None:
        # Закрываем браузерные движки
        await self._bm.close()
        # Закрываем HTTP-сессию
        if self._curl:
            await self._curl.close()
            self._curl = None

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
