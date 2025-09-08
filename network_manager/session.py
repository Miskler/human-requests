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

Опциональные зависимости
========================
- playwright-stealth: включается флагом `playwright_stealth=True`.
  Если пакет не установлен и флаг включён — бросаем RuntimeError с инструкцией по установке.
- camoufox: выбирается `browser='camoufox'`.
  Если пакет не установлен — бросаем RuntimeError с инструкцией по установке.
- Несовместимость: camoufox + playwright_stealth одновременно запрещены (RuntimeError).
"""

from contextlib import asynccontextmanager
from time import perf_counter
from typing import Literal, Mapping, Optional
from urllib.parse import urlsplit

from curl_cffi import requests as cffi_requests
from playwright.async_api import BrowserContext, Page, async_playwright

# ── опциональные импорты ──────────────────────────────────────────────────────
try:
    from playwright_stealth import Stealth
except Exception:
    Stealth = None  # type: ignore[assignment]

try:
    from camoufox.async_api import AsyncCamoufox
except Exception:
    AsyncCamoufox = None  # type: ignore[assignment]
# ──────────────────────────────────────────────────────────────────────────────

from .tools.http_utils import (
    compose_cookie_header,
    collect_set_cookie_headers,
    guess_encoding,
    parse_set_cookie,
)
from .impersonation import ImpersonationConfig
from .abstraction.cookies import CookieManager
from .abstraction.http import HttpMethod, URL
from .abstraction.request import Request
from .abstraction.response import Response

__all__ = ["Session"]


class Session:
    """curl_cffi.AsyncSession + Playwright (+опц. Stealth/Camoufox) + CookieManager."""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        headless: bool = True,
        browser: Literal["chromium", "firefox", "webkit", "camoufox"] = "chromium",
        spoof: ImpersonationConfig | None = None,
        playwright_stealth: bool = True,
    ) -> None:
        self.timeout: float = timeout
        self.headless: bool = headless
        self.browser_name: Literal["chromium", "firefox", "webkit", "camoufox"] = browser
        self.spoof: ImpersonationConfig = spoof or ImpersonationConfig()
        self.playwright_stealth: bool = bool(playwright_stealth)

        # camoufox несовместим со stealth (по вашим требованиям)
        if self.browser_name == "camoufox" and self.playwright_stealth:
            raise RuntimeError(
                "playwright_stealth=True несовместим с browser='camoufox'. "
                "Выключите stealth или используйте chromium/firefox/webkit."
            )

        self.cookies: CookieManager = CookieManager([])

        self._curl: Optional[cffi_requests.AsyncSession] = None
        self._pw = None  # Playwright instance (или stealth-обёртка)
        self._stealth_cm = None  # контекстный менеджер stealth, если используется
        self._camoufox_cm = None  # контекстный менеджер Camoufox, если используется
        self._browser = None
        self._context: Optional[BrowserContext] = None

    # ────── lazy browser init ──────
    async def _ensure_browser(self) -> None:
        # Playwright (с Stealth, если включён)
        if self._pw is None:
            if self.playwright_stealth:
                # Явно ругаемся, если попросили stealth, а пакета нет
                if self.playwright_stealth and Stealth is None:
                    raise RuntimeError(
                        "Запрошен playwright_stealth=True, но пакет 'playwright-stealth' не установлен.\n"
                        "Установите дополнительную зависимость, например:\n"
                        "  pip install 'human-requests[stealth]'\n"
                        "или напрямую: pip install playwright-stealth"
                )
                # тут гарантировано, что Stealth установлен (см. __init__)
                self._stealth_cm = Stealth().use_async(async_playwright())  # type: ignore[operator]
                self._pw = await self._stealth_cm.__aenter__()
            else:
                self._pw = await async_playwright().start()

        # Запуск браузера
        if self._browser is None:
            if self.browser_name == "camoufox":
                if AsyncCamoufox is None:
                    raise RuntimeError(
                        "Браузер 'camoufox' запрошен, но пакет 'camoufox' не установлен.\n"
                        "Установите дополнительную зависимость, например:\n"
                        "  pip install 'human-requests[camoufox]'\n"
                        "или напрямую: pip install camoufox"
                    )
                if self._camoufox_cm is None:
                    self._camoufox_cm = AsyncCamoufox(headless=self.headless)
                    self._browser = await self._camoufox_cm.__aenter__()
            else:
                self._browser = await getattr(self._pw, self.browser_name).launch(
                    headless=self.headless
                )

        # Контекст
        if self._context is None:
            self._context = await self._browser.new_context()
            if self.cookies:
                await self._context.add_cookies(self.cookies.to_playwright())

    # ────── public: async HTTP ──────
    async def request(
        self,
        method: HttpMethod | str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        **kwargs,
    ) -> Response:
        """
        Обычный быстрый запрос через curl_cffi. Обязательно нужно передать HttpMethod или его строковое представление а так же url.

        Опционально можно передать дополнительные заголовки.

        Через **kwargs можно передать дополнительные параметры curl_cffi.AsyncSession.request (см. их документацию для подробностей).
        """

        method_enum = method if isinstance(method, HttpMethod) else HttpMethod[str(method).upper()]
        req_headers = {k.lower(): v for k, v in (headers or {}).items()}

        # lazy curl session
        if self._curl is None:
            self._curl = cffi_requests.AsyncSession()

        # spoof UA / headers
        imper_profile = self.spoof.choose(self.browser_name)
        req_headers.update(self.spoof.forge_headers(imper_profile))

        # Cookie header
        url_parts = urlsplit(url)
        cookie_header, sent_cookies = compose_cookie_header(
            url_parts, req_headers, list(self.cookies)
        )
        if cookie_header:
            req_headers["cookie"] = cookie_header

        # perform
        t0 = perf_counter()
        r = await self._curl.request(
            method_enum.value,
            url,
            headers=req_headers,
            impersonate=imper_profile,
            timeout=self.timeout,
            **kwargs,
        )
        duration = perf_counter() - t0

        # response → cookies
        resp_headers = {k.lower(): v for k, v in r.headers.items()}
        raw_sc = collect_set_cookie_headers(r.headers)
        resp_cookies = parse_set_cookie(raw_sc, url_parts.hostname or "")
        self.cookies.add(resp_cookies)

        charset = guess_encoding(resp_headers)
        body_text = r.content.decode(charset, errors="replace")

        data = kwargs.get("data")
        json_body = kwargs.get("json")
        files = kwargs.get("files")

        # models
        req_model = Request(
            method=method_enum,
            url=URL(full_url=url),
            headers=dict(req_headers),
            body=data or json_body or files or None,
            cookies=sent_cookies,
        )
        resp_model = Response(
            request=req_model,
            url=URL(full_url=str(r.url)),
            headers=resp_headers,  # type: ignore[arg-type]
            cookies=resp_cookies,
            body=body_text,
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
        """Открытие страницы в браузере. Возвращается Playwright Page."""

        await self._ensure_browser()
        ctx = self._context
        assert ctx is not None

        if self.cookies:
            await ctx.add_cookies(self.cookies.to_playwright())

        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=self.timeout * 1000)
            yield page
        finally:
            self.cookies.add_from_playwright(await ctx.cookies())
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
            await ctx.add_cookies([c.to_playwright_like_dict() for c in response.cookies])

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
            self.cookies.add_from_playwright(await ctx.cookies())
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

    # поддержка «async with»
    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.close()
