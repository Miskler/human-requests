from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable, Mapping, Optional, Literal, ContextManager, Iterator
from urllib.parse import urlencode, urlsplit, urlunsplit
import time

# импорт строго по твоей структуре
from .abstraction.cookies import Cookie
from .abstraction.http import HttpMethod, URL
from .abstraction.request import Request
from .abstraction.response import Response
from .abstraction.response_content import HTMLContent

from curl_cffi import requests as cffi_requests
from playwright.sync_api import sync_playwright, Page, BrowserContext


# -------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -------------------------- #

def _build_url(url: str) -> URL:
    # URL датакласс сам заполнит base_url/path/domain/params в __post_init__
    return URL(full_url=url, base_url="", path="", domain="", params={})  # type: ignore[arg-type]


def _lower_headers(h: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in h.items()}


def _guess_encoding(headers: Mapping[str, str]) -> str:
    ctype = headers.get("content-type", "") or headers.get("Content-Type", "")
    if "charset=" in ctype:
        enc = ctype.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"').strip("'")
        return enc or "utf-8"
    return "utf-8"


def _cookies_to_pw(cookies: Iterable[Cookie]) -> list[dict]:
    out: list[dict] = []
    for c in cookies:
        d = asdict(c)
        out.append({
            "name": d.get("name"),
            "value": d.get("value"),
            "domain": d.get("domain") or None,
            "path": d.get("path") or "/",
            "expires": int(d.get("expires") or 0) or None,
            "httpOnly": bool(d.get("http_only") or False),
            "secure": bool(d.get("secure") or False),
            "sameSite": d.get("same_site") or None,
        })
    return out


def _cookie_from_pw(pw_cookie: Mapping[str, Any]) -> Cookie:
    # Поддержим только базовые поля; лишних не добавляем
    return Cookie(
        name=pw_cookie["name"],
        value=pw_cookie["value"],
        domain=pw_cookie.get("domain") or "",
        path=pw_cookie.get("path") or "/",
        expires=int(pw_cookie.get("expires") or 0),
        secure=bool(pw_cookie.get("secure") or False),
        http_only=bool(pw_cookie.get("httpOnly") or False),
    )


# ------------------------------- ГЛАВНЫЙ КЛАСС ------------------------------- #

class Session:
    """
    Минимальная сессия с двумя публичными методами:
      - requests(): прямой HTTP через curl_cffi
      - goto_page(): браузерный переход, возвращает КОНТЕКСТ-МЕНЕДЖЕР, который
                     отдаёт Page и при выходе тянет куки обратно в Session

    Плюс отдельный механизм рендера готового Response в Page БЕЗ сети:
      - Response._render_callable указывает на Session._render_response
      - Response.render() (в твоём классе) будет дергать этот коллбек
    """

    def __init__(self, *, timeout: float = 30.0, headless: bool = True,
                 browser: Literal["chromium", "firefox", "webkit"] = "chromium") -> None:
        self.timeout = timeout
        self.headless = headless
        self.browser_name = browser

        self.cookies: list[Cookie] = []

        # ленивые хендлы
        self._curl: Optional[cffi_requests.Session] = None
        self._pw = None
        self._browser = None
        self._context: Optional[BrowserContext] = None

    # ---------------------------- ЯВНАЯ ИНИЦИАЛИЗАЦИЯ ---------------------------- #

    def init_http(self) -> None:
        if self._curl is not None:
            return
        s = cffi_requests.Session()
        s.timeout = self.timeout
        s.verify = True
        s.http2 = True
        self._curl = s

    def init_browser(self) -> None:
        if self._context is not None:
            return
        self._pw = sync_playwright().start()
        browser = getattr(self._pw, self.browser_name).launch(headless=self.headless)
        self._browser = browser
        self._context = browser.new_context()
        if self.cookies:
            self._context.add_cookies(_cookies_to_pw(self.cookies))

    # --------------------------------- ПУБЛИЧНО --------------------------------- #

    def requests(
        self,
        method: HttpMethod | str,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        data: Optional[str | bytes | Mapping[str, Any]] = None,
        json_body: Optional[Any] = None,
        cookies: Optional[Iterable[Cookie]] = None,
        allow_redirects: bool = True,
    ) -> Response:
        """Прямой HTTP-запрос (curl_cffi) -> Response."""
        self.init_http()
        assert self._curl is not None

        # метод
        if isinstance(method, str):
            method = HttpMethod[method.upper()]

        # query
        if params:
            u = urlsplit(url)
            q = u.query
            url = urlunsplit((u.scheme, u.netloc, u.path,
                              (f"{q}&{urlencode(params, doseq=True)}" if q else urlencode(params, doseq=True)),
                              u.fragment))

        # куки, если передали явно
        if cookies is not None:
            self.cookies = list(cookies)

        # cookie header by domain (простой вариант)
        req_headers = dict(headers or {})
        domain = urlsplit(url).netloc
        cookie_kv: list[str] = []
        for c in self.cookies:
            if not c.domain:
                cookie_kv.append(f"{c.name}={c.value}")
            else:
                d = c.domain.lstrip(".")
                if domain.endswith(d) or d.endswith(domain):
                    cookie_kv.append(f"{c.name}={c.value}")
        if cookie_kv:
            req_headers["Cookie"] = "; ".join(cookie_kv)

        # payload
        body_bytes: Optional[bytes] = None
        if json_body is not None:
            import json as _json
            body_bytes = _json.dumps(json_body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, str):
            body_bytes = data.encode("utf-8")
        elif isinstance(data, bytes):
            body_bytes = data
        elif isinstance(data, Mapping):
            body_bytes = urlencode(data, doseq=True).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        # запрос
        started = time.perf_counter()
        r = self._curl.request(
            method.value,
            url,
            headers=req_headers,
            data=body_bytes,
            allow_redirects=allow_redirects,
        )
        duration = time.perf_counter() - started

        # заголовки + текст
        resp_headers = _lower_headers(dict(r.headers))
        raw = r.content or b""
        text = raw.decode(_guess_encoding(resp_headers), errors="replace")
        content = HTMLContent(raw=raw, html=text)  # type: ignore[arg-type]

        # модели
        req_model = Request(
            method=method,
            url=_build_url(url),
            headers=dict(req_headers),
            body=None,
            cookies=list(self.cookies),
        )
        resp = Response(
            request=req_model,
            url=_build_url(r.url),
            headers=resp_headers,  # type: ignore[arg-type]
            cookies=list(self.cookies),
            body=text,
            content=content,  # type: ignore[arg-type]
            status_code=r.status_code,
            duration=duration,
        )

        # дать Response возможность себя отрендерить без сети
        try:
            object.__setattr__(resp, "_render_callable", self._render_response)
        except Exception:
            pass
        return resp

    # --- Браузерный переход: возвращаем with-обёртку, отдающую Page --- #

    def goto_page(
        self,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        cookies: Optional[Iterable[Cookie]] = None,
        wait_until: Literal["load", "domcontentloaded", "networkidle"] = "load",
    ) -> ContextManager[Page]:
        """Перейти на URL реальным браузером. Возвращает контекст-менеджер Page."""
        if params:
            u = urlsplit(url)
            q = u.query
            url = urlunsplit((u.scheme, u.netloc, u.path,
                              (f"{q}&{urlencode(params, doseq=True)}" if q else urlencode(params, doseq=True)),
                              u.fragment))
        return _PageCtx(self, url, headers or {}, list(cookies) if cookies is not None else None, wait_until)

    # --- Внутренний коллбек для Response.render(): создаёт Page без сети --- #

    def _render_response(self, response: Response,
                         *, wait_until: Literal["load", "domcontentloaded", "networkidle"] = "load") -> ContextManager[Page]:
        """
        Построить Page, эмулируя сетевой ответ через route.fulfill.
        Не нужен внешний Интернет: мы сами подставим body/headers/URL из Response.
        Возврат — контекст-менеджер, который на выходе синхронизирует куки в Session.
        """
        target_url = response.url.full_url
        # Возьмём заголовки и тело из Response
        headers = dict(getattr(response, "headers", {}) or {})
        body_bytes = (getattr(response, "content", None) and getattr(response.content, "raw", None)) or response.body.encode("utf-8")
        status = int(getattr(response, "status_code", 200) or 200)
        # рендер с куками из самого Response, если есть; иначе — текущие Session
        render_cookies = list(getattr(response, "cookies", None) or self.cookies)
        return _FulfilledPageCtx(self, target_url, headers, body_bytes, status, render_cookies, wait_until)

    # ------------------------------ ЗАВЕРШЕНИЕ ЖИЗНИ ------------------------------ #

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
        finally:
            self._context = None
        try:
            if self._browser:
                self._browser.close()
        finally:
            self._browser = None
        try:
            if self._pw:
                self._pw.stop()
        finally:
            self._pw = None
        if self._curl is not None:
            try:
                self._curl.close()
            finally:
                self._curl = None


# --------------------------- КОНТЕКСТ-МЕНЕДЖЕРЫ PAGE --------------------------- #

class _PageCtx:
    """with Session.goto_page(...) as page: ..."""

    def __init__(self, session: Session, url: str, headers: Mapping[str, str],
                 cookies: Optional[list[Cookie]], wait_until: str) -> None:
        self.sess = session
        self.url = url
        self.headers = dict(headers)
        self.cookies = cookies
        self.wait_until = wait_until
        self.page: Optional[Page] = None

    def __enter__(self) -> Page:
        self.sess.init_browser()
        assert self.sess._context is not None
        page = self.sess._context.new_page()
        if self.headers:
            page.set_extra_http_headers(self.headers)
        # куки (если явно передали) — подменим на время этой страницы
        if self.cookies is not None:
            self.sess._context.add_cookies(_cookies_to_pw(self.cookies))
        page.goto(self.url, wait_until=self.wait_until)  # type: ignore[arg-type]
        self.page = page
        return page

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.sess._context is not None:
                # забираем куки из браузера в Session
                self.sess.cookies = [_cookie_from_pw(c) for c in self.sess._context.cookies()]
        finally:
            if self.page is not None:
                try:
                    self.page.close()
                except Exception:
                    pass


class _FulfilledPageCtx:
    """
    with session._render_response(resp) as page: ...
    Создаёт новую Page, перехватывает запрос на resp.url и отдаёт подготовленный ответ.
    """

    def __init__(self, session: Session, url: str, headers: Mapping[str, str], body: bytes,
                 status: int, cookies: list[Cookie], wait_until: str) -> None:
        self.sess = session
        self.url = url
        self.headers = dict(headers)
        self.body = body
        self.status = status
        self.cookies = cookies
        self.wait_until = wait_until
        self.page: Optional[Page] = None

    def __enter__(self) -> Page:
        self.sess.init_browser()
        assert self.sess._context is not None

        # применим куки, связанные с этим ответом
        if self.cookies:
            self.sess._context.add_cookies(_cookies_to_pw(self.cookies))

        # создаём страницу и перехватываем её навигацию на self.url
        ctx = self.sess._context
        page = ctx.new_page()

        def handler(route):
            route.fulfill(status=self.status, headers=self.headers, body=self.body)

        ctx.route(self.url, handler)
        page.goto(self.url, wait_until=self.wait_until)  # type: ignore[arg-type]
        # после загрузки можно снять роут, чтобы не мешать последующим запросам
        ctx.unroute(self.url, handler)

        self.page = page
        return page

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.sess._context is not None:
                self.sess.cookies = [_cookie_from_pw(c) for c in self.sess._context.cookies()]
        finally:
            if self.page is not None:
                try:
                    self.page.close()
                except Exception:
                    pass
