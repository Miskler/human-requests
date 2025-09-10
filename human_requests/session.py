from __future__ import annotations

"""
core.session — единая state-ful-сессия для *curl_cffi* и *Playwright*.

Главные методы
==============
* ``Session.request``   — низкоуровневый HTTP-запрос (curl_cffi) с cookie-jar.
* ``Session.goto_page`` — открывает URL в браузере, возвращает Page внутри
  контекст-менеджера; по выходу синхронизирует cookies + localStorage.
* ``Response.render``   — офлайн-рендер заранее полученного Response.

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
from typing import Any, AsyncGenerator, Literal, Mapping, Optional, cast
from urllib.parse import urlsplit

from curl_cffi import requests as cffi_requests
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
)
from playwright.async_api import Request as PWRequest
from playwright.async_api import (
    Route,
    async_playwright,
)
from playwright.async_api._context_manager import PlaywrightContextManager

# ── опциональные импорты ──────────────────────────────────────────────────────
try:
    from playwright_stealth import Stealth  # type: ignore[import-untyped]
    from playwright_stealth.context_managers import (
        AsyncWrappingContextManager as StealthContextManager,  # type: ignore[import-untyped]
    )
except ImportError:  # пакет не установлен — используем заглушки типов/значений
    Stealth = None

    class StealthContextManager:  # type: ignore[no-redef]
        pass


try:
    from camoufox.async_api import AsyncCamoufox
except ImportError:
    AsyncCamoufox = None  # type: ignore[assignment]
# ──────────────────────────────────────────────────────────────────────────────

from .abstraction.cookies import CookieManager
from .abstraction.http import URL, HttpMethod
from .abstraction.request import Request
from .abstraction.response import Response

# Новые вынесенные утилиты
from .helper_tools import (
    build_storage_state_for_context,
    handle_nav_with_retries,
    merge_storage_state_from_context,
)
from .impersonation import ImpersonationConfig
from .tools.http_utils import (
    collect_set_cookie_headers,
    compose_cookie_header,
    guess_encoding,
    parse_set_cookie,
)

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
        page_retry: int = 2,
        direct_retry: int = 1,
    ) -> None:
        """
        Args:
            timeout: стандартный таймаут для direct и goto запросов
            headless: запускать ли только движок, или еще и рендер?
            browser: chromium/firefox/webkit — стандарт. браузеры, camoufox — спец. сборка firefox
            spoof: конфиг для direct-запросов
            playwright_stealth: прячет некоторые сигнатуры автоматизированного браузера
            page_retry: число «мягких» повторов навигации страницы (после первичной)
            direct_retry: число повторов direct-запроса при curl_cffi Timeout (после первичной)
        """

        self.timeout: float = timeout
        self.headless: bool = headless
        self.browser_name: Literal["chromium", "firefox", "webkit", "camoufox"] = browser
        self.spoof: ImpersonationConfig = spoof or ImpersonationConfig()
        self.playwright_stealth: bool = bool(playwright_stealth)
        self.page_retry: int = int(page_retry)
        self.direct_retry: int = int(direct_retry)

        # camoufox несовместим со stealth
        if self.browser_name == "camoufox" and self.playwright_stealth:
            raise RuntimeError(
                "playwright_stealth=True несовместим с browser='camoufox'. "
                "Выключите stealth или используйте chromium/firefox/webkit."
            )

        self.cookies: CookieManager = CookieManager([])
        """Хранилище-синхронизатор кук"""

        # localStorage теперь по origin
        self.local_storage: dict[str, dict[str, str]] = {}
        """Хранилище-синхронизатор localStorage.

        sessionStorage сюда не входит, он удаляется сразу же после выхода из goto/render."""

        self._curl: Optional[cffi_requests.AsyncSession] = None
        self._pw: Optional[Playwright] = None
        self._stealth_cm: Optional[StealthContextManager] = None
        self._camoufox_cm: Optional[PlaywrightContextManager] = None
        self._browser: Optional[Browser] = None  # ← только Browser (не BrowserContext)

    # ────── lazy browser init (без контекста!) ──────
    async def _ensure_browser(self) -> None:
        # Playwright (с Stealth, если включён)
        if self._pw is None:
            if self.playwright_stealth:
                if Stealth is None:
                    raise RuntimeError(
                        "Запрошен playwright_stealth=True, "
                        "но пакет 'playwright-stealth' не установлен.\n"
                        "Установите дополнительную зависимость, например:\n"
                        "  pip install 'human-requests[stealth]'\n"
                        "или напрямую: pip install playwright-stealth"
                    )
                self._stealth_cm = Stealth().use_async(async_playwright())
                self._pw = await self._stealth_cm.__aenter__()
            else:
                self._pw = await async_playwright().__aenter__()

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
                    self._camoufox_cm = AsyncCamoufox(
                        headless=self.headless, persistent_context=False
                    )
                browser = await self._camoufox_cm.__aenter__()
                assert isinstance(browser, Browser)
                self._browser = browser
            else:
                assert self._pw is not None
                self._browser = await getattr(self._pw, self.browser_name).launch(
                    headless=self.headless
                )

    # ────── public: async HTTP ──────
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
        Обычный быстрый запрос через curl_cffi.
        Обязательно нужно передать HttpMethod или его строковое представление а так же url.

        Опционально можно передать дополнительные заголовки.

        Через **kwargs можно передать дополнительные параметры curl_cffi.AsyncSession.request
        (см. их документацию для подробностей).
        Повторяем ТОЛЬКО при cffi Timeout: ``curl_cffi.requests.exceptions.Timeout``.
        """
        method_enum = method if isinstance(method, HttpMethod) else HttpMethod[str(method).upper()]
        base_headers = {k.lower(): v for k, v in (headers or {}).items()}

        # lazy curl session
        if self._curl is None:
            self._curl = cffi_requests.AsyncSession()

        curl = self._curl
        assert curl is not None  # для mypy: ниже уже не union

        # spoof UA / headers
        imper_profile = self.spoof.choose(self.browser_name)
        base_headers.update(self.spoof.forge_headers(imper_profile))

        # Cookie header (фиксируем один раз на первую попытку)
        url_parts = urlsplit(url)
        cookie_header, sent_cookies = compose_cookie_header(
            url_parts, base_headers, list(self.cookies)
        )
        if cookie_header:
            base_headers["cookie"] = cookie_header

        attempts_left = self.direct_retry if retry is None else int(retry)
        last_err: Exception | None = None

        async def _do_request() -> tuple[Any, float]:
            # Возвращаем (r, duration)
            req_headers = dict(base_headers)  # копия на попытку
            t0 = perf_counter()
            r = await curl.request(
                method_enum.value,
                url,
                headers=req_headers,
                impersonate=cast(  # сузить тип до Literal набора curl_cffi
                    "cffi_requests.impersonate.BrowserTypeLiteral", imper_profile
                ),
                timeout=self.timeout,
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
        # ── success ────────────────────────────────────────────────────────────

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
            headers=dict(base_headers),
            body=data or json_body or files or None,
            cookies=sent_cookies,
        )
        resp_model = Response(
            request=req_model,
            url=URL(full_url=str(r.url)),
            headers=resp_headers,
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
        retry: int | None = None,
    ) -> AsyncGenerator[Page, None]:
        """
        Открытие страницы в браузере.
        Контекст одноразовый; повторы НЕ пересоздают контекст/страницу — «мягкий reload».
        """
        await self._ensure_browser()
        assert self._browser is not None

        # создаём новый контекст с ЕДИНЫМ storage_state
        storage_state = build_storage_state_for_context(
            local_storage=self.local_storage,
            cookie_manager=self.cookies,
        )
        ctx = await self._browser.new_context(storage_state=storage_state)
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
            # обновляем внутреннее состояние из контекста
            self.local_storage = await merge_storage_state_from_context(
                ctx, cookie_manager=self.cookies
            )
            await page.close()
            await ctx.close()

    # ────── offline render ──────
    @asynccontextmanager
    async def _render_response(
        self,
        response: Response,
        *,
        wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded",
        retry: int | None = None,
    ) -> AsyncGenerator[Page, None]:
        """
        Офлайн-рендер Response: создаём временный контекст (с нашим storage_state),
        перехватываем первый запрос и отвечаем подготовленным телом.
        Повторы НЕ пересоздают контекст/страницу — «мягкий reload», при этом
        на каждый повтор перевешиваем одноразовый route.
        """
        await self._ensure_browser()
        assert self._browser is not None

        storage_state = build_storage_state_for_context(
            local_storage=self.local_storage,
            cookie_manager=self.cookies,
        )
        ctx: BrowserContext = await self._browser.new_context(
            storage_state=cast(Any, storage_state)
        )

        timeout_ms = int(self.timeout * 1000)
        attempts_left = self.page_retry if retry is None else int(retry)

        async def _attach_route_once() -> None:
            # снимаем старые, чтобы гарантированно перевесить на повторе
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

        # первая попытка
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
        # Закрываем браузер/движки (контекстов к этому моменту нет — они одноразовые)
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

            await self._pw.stop()
            self._pw = None

        if self._curl:
            await self._curl.close()
            self._curl = None

    # поддержка «async with»
    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
