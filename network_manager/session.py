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
from typing import AsyncGenerator, Literal, Mapping, Optional
from urllib.parse import urlsplit

from curl_cffi import requests as cffi_requests
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

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
        page_retry: int = 2,
    ) -> None:
        """
        Args:
            timeout: стандартный таймаут для direct и goto запросов
            headless: запускать ли только движок, или еще и рендер?
            browser: "chromium", "firefox", "webkit" — стандартные браузеры, "camoufox" — спец. сборка firefox
            spoof: конфиг для direct-запросов
            playwright_stealth: прячет некоторые сигнатуры автоматизированного браузера
            page_retry: число «мягких» повторов навигации (после первичной). Используется по умолчанию.
        """

        self.timeout: float = timeout
        self.headless: bool = headless
        self.browser_name: Literal["chromium", "firefox", "webkit", "camoufox"] = browser
        self.spoof: ImpersonationConfig = spoof or ImpersonationConfig()
        self.playwright_stealth: bool = bool(playwright_stealth)
        self.page_retry: int = int(page_retry)

        # camoufox несовместим со stealth
        if self.browser_name == "camoufox" and self.playwright_stealth:
            raise RuntimeError(
                "playwright_stealth=True несовместим с browser='camoufox'. "
                "Выключите stealth или используйте chromium/firefox/webkit."
            )

        self.cookies: CookieManager = CookieManager([])
        """Хранилище-синхронизатор кук"""

        # localStorage теперь по origin
        # пример: {"https://example.com": {"k1": "v1", "k2": "v2"}}
        self.local_storage: dict[str, dict[str, str]] = {}
        """Хранилище-синхронизатор localStorage.
        
        sessionStorage сюда не входит, он удаляется сразу же после выхода из goto/render."""

        self._curl: Optional[cffi_requests.AsyncSession] = None
        self._pw = None  # Playwright instance (или stealth-обёртка)
        self._stealth_cm = None  # контекстный менеджер stealth
        self._camoufox_cm = None  # контекстный менеджер Camoufox
        self._browser = None

    # ────── lazy browser init (без контекста!) ──────
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

    # ────── storage_state helpers ──────
    def _build_storage_state_for_context(self) -> dict:
        """
        Собирает единый storage_state для new_context:
        - cookies — из CookieManager (как playwright-совместимые dict)
        - origins.localStorage — из self.local_storage
        """
        cookie_list = self.cookies.to_playwright()  # list[dict] совместимая с PW
        origins = []
        for origin, kv in self.local_storage.items():
            if not kv:
                continue
            origins.append(
                {
                    "origin": origin,
                    "localStorage": [{"name": k, "value": v} for k, v in kv.items()],
                }
            )
        return {"cookies": cookie_list, "origins": origins}

    async def _merge_storage_state_from_context(self, ctx: BrowserContext) -> None:
        """
        Читает storage_state из контекста и синхронизирует внутреннее состояние:
        - localStorage: ПОЛНАЯ перезапись self.local_storage из state["origins"]
        - cookies: ДОБАВЛЕНИЕ/ОБНОВЛЕНИЕ в CookieManager из state["cookies"]
        """
        state = await ctx.storage_state()  # dict с 'cookies' и 'origins'
        # localStorage — точная перезапись
        new_ls: dict[str, dict[str, str]] = {}
        for o in state.get("origins", []) or []:
            origin = str(o.get("origin", ""))
            if not origin:
                continue
            kv: dict[str, str] = {}
            for pair in o.get("localStorage", []) or []:
                name = str(pair.get("name", ""))
                value = "" if pair.get("value") is None else str(pair.get("value"))
                if name:
                    kv[name] = value
            new_ls[origin] = kv
        self.local_storage = new_ls

        # cookies — пополняем CookieManager
        cookies_list = state.get("cookies", []) or []
        if cookies_list:
            # add_from_playwright принимает список cookie-словари в формате PW
            self.cookies.add_from_playwright(cookies_list)

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
        retry: int | None = None,
    ) -> AsyncGenerator[Page, None]:
        """
        Открытие страницы в браузере.
        ВАЖНО: контекст создаётся на каждый вызов и закрывается при выходе.
        Повторы НЕ пересоздают контекст/страницу — используется «мягкий reload».
        """
        await self._ensure_browser()

        # создаём новый контекст с ЕДИНЫМ storage_state
        storage_state = self._build_storage_state_for_context()
        ctx = await self._browser.new_context(storage_state=storage_state)  # type: ignore[union-attr]
        page = await ctx.new_page()
        timeout_ms = int(self.timeout * 1000)
        attempts_left = self.page_retry if retry is None else int(retry)

        # первая попытка
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        except PlaywrightTimeoutError as last_err:
            # мягкие повторы
            while attempts_left > 0:
                attempts_left -= 1
                try:
                    # если уже частично перешли — reload; иначе повторный goto
                    if page.url and page.url != "about:blank":
                        await page.reload(wait_until=wait_until, timeout=timeout_ms)
                    else:
                        await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                    last_err = None  # type: ignore[assignment]
                    break
                except PlaywrightTimeoutError as e:
                    last_err = e
            if last_err is not None:
                # синхронизируем состояние ПЕРЕД пробросом (то, что успели получить)
                try:
                    await self._merge_storage_state_from_context(ctx)
                finally:
                    await page.close()
                    await ctx.close()
                raise last_err

        try:
            yield page
        finally:
            # обновляем внутреннее состояние из контекста
            await self._merge_storage_state_from_context(ctx)
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
        Повторы НЕ пересоздают контекст/страницу — делаем «мягкий reload».
        На каждый повтор перевешиваем route, чтобы снова отдать подготовленный ответ.
        """
        await self._ensure_browser()

        storage_state = self._build_storage_state_for_context()
        ctx = await self._browser.new_context(storage_state=storage_state)  # type: ignore[union-attr]

        timeout_ms = int(self.timeout * 1000)
        attempts_left = self.page_retry if retry is None else int(retry)

        async def _attach_route_once() -> None:
            # снимаем старые, чтобы гарантированно перевесить на повторе
            await ctx.unroute("**/*")
            async def handler(route, _req):  # noqa: ANN001
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
            await page.goto(
                response.url.full_url,
                wait_until=wait_until,
                timeout=timeout_ms,
            )
        except PlaywrightTimeoutError as last_err:
            while attempts_left > 0:
                attempts_left -= 1
                try:
                    await _attach_route_once()
                    if page.url and page.url != "about:blank":
                        await page.reload(wait_until=wait_until, timeout=timeout_ms)
                    else:
                        await page.goto(response.url.full_url, wait_until=wait_until, timeout=timeout_ms)
                    last_err = None  # type: ignore[assignment]
                    break
                except PlaywrightTimeoutError as e:
                    last_err = e
            if last_err is not None:
                try:
                    await self._merge_storage_state_from_context(ctx)
                finally:
                    await page.close()
                    await ctx.close()
                raise last_err

        try:
            yield page
        finally:
            await self._merge_storage_state_from_context(ctx)
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
