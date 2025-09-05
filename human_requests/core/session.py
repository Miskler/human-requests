from __future__ import annotations

"""
core.session — единая state-ful-сессия для *curl_cffi* и *Playwright*.

Главные методы
==============
* ``Session.requests``  — низкоуровневый HTTP-запрос (curl_cffi) с cookie-jar.
* ``Session.goto_page`` — открывает URL в браузере, возвращает
  :class:`playwright.sync_api.Page` внутри контекст-менеджера и после выхода
  подтягивает новые куки в сессию.
* ``Response.render``   — офлайн-рендер заранее полученного Response через
  приватный ``Session._render_response``.

Cookie-jar (упрощённый RFC 6265): домен, путь, secure-флаг.
Один объект ``Session`` = один набор куков, поэтому под каждый тест создавайте
свежий экземпляр.
"""

from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any, Literal, Mapping, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from curl_cffi import requests as cffi_requests
from playwright.async_api import BrowserContext, Page, async_playwright

from .tools.http_utils import (
    cookies_to_pw,
    cookie_from_pw,
    compose_cookie_header,
    collect_set_cookie_headers,
    guess_encoding,
    merge_cookies,
    parse_set_cookie,
)
from .impersonation import ImpersonationConfig
from .abstraction.cookies import Cookie
from .abstraction.http import HttpMethod, URL
from .abstraction.request import Request
from .abstraction.response import Response
from .abstraction.response_content import HTMLContent

from playwright_stealth import Stealth  # лёгкая зависимость (~20 КБ)

__all__ = ["Session"]


class Session:
    """curl_cffi.AsyncSession + Playwright + единый cookie-jar."""

    # ────── ctor ──────
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        headless: bool = False,
        browser: Literal["chromium", "firefox", "webkit", "camoufox"] = "chromium",
        spoof: ImpersonationConfig | None = None,
        playwright_stealth: bool = True,
    ) -> None:
        self.timeout = timeout
        self.headless = headless
        self.browser_name = browser
        self.spoof = spoof or ImpersonationConfig()
        self.playwright_stealth = playwright_stealth and Stealth is not None

        if self.browser_name == "camoufox" and self.playwright_stealth:
            raise RuntimeError("playwright_stealth=True is incompatible with browser='camoufox'")

        self.cookies: list[Cookie] = []

        self._curl: Optional[cffi_requests.AsyncSession] = None
        self._pw = None  # Playwright object
        self._stealth_cm = None  # контекст-менеджер Stealth
        self._camoufox_cm = None  # контекст-менеджер Camoufox
        self._browser = None
        self._context: Optional[BrowserContext] = None

    # ────── lazy init ──────
    async def _ensure_browser(self) -> None:
        if self._pw is None:
            if self.playwright_stealth:
                self._stealth_cm = Stealth().use_async(async_playwright())
                self._pw = await self._stealth_cm.__aenter__()
            else:
                self._pw = await async_playwright().start()

        if self._browser is None:
            if self.browser_name == "camoufox":
                if self._camoufox_cm is None:
                    from camoufox.async_api import AsyncCamoufox  # type: ignore

                    self._camoufox_cm = AsyncCamoufox(headless=self.headless)
                    self._browser = await self._camoufox_cm.__aenter__()
            else:
                self._browser = await getattr(self._pw, self.browser_name).launch(
                    headless=self.headless
                )

        if self._context is None:
            self._context = await self._browser.new_context()
            if self.cookies:
                await self._context.add_cookies(cookies_to_pw(self.cookies))

    # ────── public: async HTTP ──────
    async def request(
        self,
        method: HttpMethod | str,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        data: Any = None,
        json_body: Any = None,
        allow_redirects: bool = True,
    ) -> Response:
        # query-string merge
        if params:
            u = urlsplit(url)
            merged = urlencode(params, doseq=True)
            url = urlunsplit(
                (u.scheme, u.netloc, u.path, f"{u.query}&{merged}" if u.query else merged, u.fragment)
            )

        method_enum = (
            method if isinstance(method, HttpMethod) else HttpMethod[str(method).upper()]
        )

        req_headers = {k.lower(): v for k, v in (headers or {}).items()}

        # spoof --------------------
        if self._curl is None:
            self._curl = cffi_requests.AsyncSession()

        imper_profile = self.spoof.choose(self.browser_name)
        req_headers.update(self.spoof.forge_headers(imper_profile))

        # cookies header
        url_parts = urlsplit(url)
        cookie_header, sent_cookies = compose_cookie_header(
            url_parts, req_headers, self.cookies
        )
        if cookie_header:
            req_headers["cookie"] = cookie_header

        # body encoding
        body_bytes: Optional[bytes] = None
        if json_body is not None:
            import json as _json

            body_bytes = _json.dumps(json_body).encode()
            req_headers.setdefault("content-type", "application/json")
        elif isinstance(data, str):
            body_bytes = data.encode()
        elif isinstance(data, bytes):
            body_bytes = data
        elif isinstance(data, Mapping):
            body_bytes = urlencode(data, doseq=True).encode()
            req_headers.setdefault("content-type", "application/x-www-form-urlencoded")

        # perform
        t0 = perf_counter()
        r = await self._curl.request(
            method_enum.value,
            url,
            headers=req_headers,
            impersonate=imper_profile,
            data=body_bytes,
            allow_redirects=allow_redirects,
            timeout=self.timeout,
        )
        duration = perf_counter() - t0

        # response → cookies
        resp_headers = {k.lower(): v for k, v in r.headers.items()}
        raw_sc = collect_set_cookie_headers(r.headers)
        resp_cookies = parse_set_cookie(raw_sc, url_parts.hostname or "")
        merge_cookies(self.cookies, resp_cookies)

        charset = guess_encoding(resp_headers)
        body_text = r.content.decode(charset, errors="replace")

        req_model = Request(
            method=method_enum,
            url=URL(full_url=url),
            headers=dict(req_headers),
            body=data if data is not None else json_body,
            cookies=sent_cookies,
        )
        resp_model = Response(
            request=req_model,
            url=URL(full_url=str(r.url)),
            headers=resp_headers,  # type: ignore[arg-type]
            cookies=resp_cookies,
            body=body_text,
            content=HTMLContent(body_text, url),  # type: ignore[arg-type]
            status_code=r.status_code,
            duration=duration,
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
    ) -> Page:
        await self._ensure_browser()
        ctx = self._context
        assert ctx is not None

        if self.cookies:
            await ctx.add_cookies(cookies_to_pw(self.cookies))

        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=self.timeout * 1000)
            yield page
        finally:
            merge_cookies(self.cookies, (cookie_from_pw(c) for c in await ctx.cookies()))
            await page.close()

    # ────── offline render ──────
    @asynccontextmanager
    async def _render_response(
        self,
        response: Response,
        *,
        wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded",
    ) -> Page:
        await self._ensure_browser()
        ctx = self._context
        assert ctx is not None

        if response.cookies:
            await ctx.add_cookies(cookies_to_pw(response.cookies))

        async def handler(route, _req):  # noqa: ANN001
            await route.fulfill(
                status=response.status_code,
                headers=dict(response.headers),
                body=response.body.encode("utf-8"),
            )

        await ctx.route("**/*", handler, times=1)
        page = await ctx.new_page()
        try:
            await page.goto(
                response.url.full_url,
                wait_until=wait_until,
                timeout=self.timeout * 1000,
            )
            yield page
        finally:
            merge_cookies(self.cookies, (cookie_from_pw(c) for c in await ctx.cookies()))
            await page.close()

    # ────── cleanup ──────
    async def close(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None

        # Handle Camoufox context separately
        if self.browser_name == "camoufox" and self._camoufox_cm is not None:
            await self._camoufox_cm.__aexit__(None, None, None)
            self._camoufox_cm = None
            self._browser = None
        elif self._browser:
            await self._browser.close()
            self._browser = None

        if self._pw:
            if self._stealth_cm:  # использовали playwright-stealth
                await self._stealth_cm.__aexit__(None, None, None)
                self._stealth_cm = None
            else:
                await self._pw.stop()
            self._pw = None

        if self._curl:
            await self._curl.close()
            self._curl = None

    # поддержка «async with AsyncSession() as s»
    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.close()
