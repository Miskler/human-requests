"""
core.session — единая state-ful-сессия для *curl_cffi* и *Playwright*-совместимых движков.

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
- patchright: выбирается `browser='patchright'`.
- Несовместимость: camoufox/patchright + playwright_stealth одновременно запрещены (RuntimeError).

Дополнительно
=============
- Аргументы запуска браузера собираются через `make_browser_launch_opts()` из:
  - `browser_launch_opts` (произвольный dict)
  - `headless` (всегда переопределяет одноимённый ключ)
  - `proxy` (строка URL или dict) → адаптация под Playwright/Patchright/Camoufox
- Прокси также применяется к curl_cffi (если в .request() не передан свой `proxy`).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter
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
from .impersonation import ImpersonationConfig
from .tools.helper_tools import (
    build_storage_state_for_context,
    handle_nav_with_retries,
    merge_storage_state_from_context,
)
from .tools.http_utils import (
    collect_set_cookie_headers,
    compose_cookie_header,
    guess_encoding,
    parse_set_cookie,
)

__all__ = ["Session"]


class Session:
    """curl_cffi.AsyncSession + BrowserMaster + CookieManager."""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        headless: bool = True,
        browser: Engine = "chromium",
        spoof: ImpersonationConfig | None = None,
        playwright_stealth: bool = True,
        page_retry: int = 2,
        direct_retry: int = 1,
        browser_launch_opts: Mapping[str, Any] = {},
        proxy: str | None = None,
    ) -> None:
        """
        Args:
            timeout: стандартный таймаут для direct и goto запросов
            headless: режим запуска (идёт в launch-аргументы браузера)
            browser: chromium/firefox/webkit — стандартные; camoufox/patchright — спец. сборки
            spoof: конфиг для direct-запросов
            playwright_stealth: прячет некоторые сигнатуры автоматизированного браузера
            page_retry: число «мягких» повторов навигации страницы (после первичной)
            direct_retry: число повторов direct-запроса при curl_cffi Timeout (после первичной)
        """
        self.timeout: float = timeout
        """Таймаут для запросов goto/direct"""
        self.headless: bool = bool(headless)
        """Запускать ли браузер в headless-режиме?"""
        self.browser_name: Engine = browser
        """Текущий браузер (chromium/firefox/webkit/camoufox/patchright)"""
        self.spoof: ImpersonationConfig = spoof or ImpersonationConfig()
        """Настройки имперсонации (user-agent, tlc, client-hello)"""
        self.playwright_stealth: bool = bool(playwright_stealth)
        """Прятать ли некоторые сигнатуры автоматизированного браузера?
        Реализовано через js-инъекцию. Некоторые сайты могут это обнаружить."""
        self.page_retry: int = int(page_retry)
        """Если после N секунд наступил таймаут - page.reload()"""
        self.direct_retry: int = int(direct_retry)
        """Если после N секунд наступил таймаут - direct-запрос повторится"""

        if self.browser_name in ("camoufox", "patchright") and self.playwright_stealth:
            raise RuntimeError(
                "playwright_stealth=True несовместим с browser='camoufox'/'patchright'. "
                "Отключите stealth или используйте chromium/firefox/webkit."
            )

        # Пользовательские launch-параметры браузера + прокси
        self.browser_launch_opts: Mapping[str, Any] = browser_launch_opts
        """launch-аргументы для браузера (произвольные ключи)"""
        self.proxy: str | dict[str, str] | None = proxy
        """
        Прокси-сервер вида:

        a. строка URL вида - `schema://user:pass@host:port`

        b. playwright-like-dict
        """

        # Состояние cookie/localStorage
        self.cookies: CookieManager = CookieManager([])
        """Хранилище всех актуальных кук."""
        self.local_storage: dict[str, dict[str, str]] = {}
        """localStorage из последнего контекста (запуска goto) браузера."""

        # Низкоуровневый HTTP
        self._curl: Optional[cffi_requests.AsyncSession] = None

        # Браузерный движок — через мастер (всегда отдаёт Browser)
        self._bm: BrowserMaster = BrowserMaster(
            engine=self.browser_name,
            stealth=self.playwright_stealth,
            launch_opts=self._make_browser_launch_opts(),  # первичный снапшот
        )

    # ──────────────── Launch args & proxy helpers ────────────────
    def _make_browser_launch_opts(self) -> dict[str, Any]:
        """
        Склеивает launch-аргументы для BrowserMaster из настроек Session.

        Источники:
          - self.browser_launch_opts (произвольные ключи)
          - self.headless (перекрывает одноимённый ключ)
          - self.proxy (строка URL или dict) → playwright-style proxy
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
        Обычный быстрый запрос через curl_cffi.
        Обязательно нужно передать HttpMethod или его строковое представление, а также url.

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
        Открытие страницы в браузере через одноразовый контекст.
        Повторы делают «мягкий reload» без пересоздания контекста.
        """
        # Обновляем launch-аргументы в мастере перед стартом
        self._bm.launch_opts = self._make_browser_launch_opts()
        await self._bm.start()

        storage_state = build_storage_state_for_context(
            local_storage=self.local_storage,
            cookie_manager=self.cookies,
        )
        ctx = await self._bm.new_context(storage_state=storage_state)
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
        Офлайн-рендер Response: создаём временный контекст (с нашим storage_state),
        перехватываем первый запрос и отвечаем подготовленным телом.
        Повторы не пересоздают контекст/страницу — «мягкий reload», на повторе перевешиваем route.
        """
        # Обновляем launch-аргументы в мастере перед стартом
        self._bm.launch_opts = self._make_browser_launch_opts()
        await self._bm.start()

        storage_state = build_storage_state_for_context(
            local_storage=self.local_storage,
            cookie_manager=self.cookies,
        )
        ctx: BrowserContext = await self._bm.new_context(storage_state=cast(Any, storage_state))
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
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()
