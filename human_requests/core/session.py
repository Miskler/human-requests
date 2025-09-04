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

from contextlib import AbstractContextManager
from http.cookies import SimpleCookie
from time import perf_counter
from typing import Any, Iterable, Literal, Mapping, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from curl_cffi import requests as cffi_requests
from playwright.sync_api import BrowserContext, Page, sync_playwright

from .abstraction.cookies import Cookie
from .abstraction.http import HttpMethod, URL
from .abstraction.request import Request
from .abstraction.response import Response
from .abstraction.response_content import HTMLContent

__all__ = ["Session"]

# ───────────────────────── helpers ──────────────────────────


def _domain_match(host: str, cookie_domain: str | None) -> bool:
    """RFC 6265 §5.1.3 — host-only vs. domain cookie match (порт игнорируем)."""
    if not cookie_domain:
        return True
    host = host.split(":", 1)[0].lower()
    cd = cookie_domain.lstrip(".").lower()
    return host == cd or host.endswith("." + cd)


def _path_match(req_path: str, cookie_path: str | None) -> bool:
    if not cookie_path:
        return True
    if not req_path.endswith("/"):
        req_path += "/"
    cp = cookie_path if cookie_path.endswith("/") else cookie_path + "/"
    return req_path.startswith(cp)


def _guess_encoding(headers: Mapping[str, str]) -> str:
    ctype = headers.get("content-type", "")
    if "charset=" in ctype:
        return (
            ctype.split("charset=", 1)[1].split(";", 1)[0].strip(" \"'") or "utf-8"
        )
    return "utf-8"


def _cookies_to_pw(cookies: Iterable[Cookie]) -> list[dict[str, Any]]:
    return [c.to_playwright_like_dict() for c in cookies]


def _cookie_from_pw(data: Mapping[str, Any]) -> Cookie:
    return Cookie(
        name=data["name"],
        value=data["value"],
        domain=data.get("domain") or "",
        path=data.get("path") or "/",
        expires=int(data.get("expires") or 0),
        secure=bool(data.get("secure")),
        http_only=bool(data.get("httpOnly")),
    )


def _parse_set_cookie(raw_headers: list[str], default_domain: str) -> list[Cookie]:
    out: list[Cookie] = []
    for raw in raw_headers:
        jar = SimpleCookie()
        jar.load(raw)
        for m in jar.values():
            out.append(
                Cookie(
                    name=m.key,
                    value=m.value,
                    domain=(m["domain"] or default_domain).lower(),
                    path=m["path"] or "/",
                    secure=bool(m["secure"]),
                    http_only=bool(m["httponly"]),
                )
            )
    return out


# ───────────────────────── Session ──────────────────────────


class Session(AbstractContextManager):
    """curl + Playwright + единый cookie-jar."""

    # construction / teardown ────────────────────────────────────────
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        headless: bool = True,
        browser: Literal["chromium", "firefox", "webkit"] = "chromium",
    ) -> None:
        self.timeout = timeout
        self.headless = headless
        self.browser_name = browser

        self.cookies: list[Cookie] = []

        self._curl: Optional[cffi_requests.Session] = None
        self._pw = None
        self._browser = None
        self._context: Optional[BrowserContext] = None

    # lazy init ───────────────────────────────────────────────────────
    def _ensure_curl(self) -> None:
        if self._curl is None:
            self._curl = cffi_requests.Session()

    def _ensure_browser(self) -> None:
        if self._pw is None:
            self._pw = sync_playwright().start()
        if self._browser is None:
            self._browser = getattr(self._pw, self.browser_name).launch(
                headless=self.headless
            )
        if self._context is None:
            self._context = self._browser.new_context()
            if self.cookies:
                self._context.add_cookies(_cookies_to_pw(self.cookies))

    # cookie-jar internals ────────────────────────────────────────────
    def _cookie_matches(self, url_parts, c: Cookie) -> bool:  # noqa: ANN001
        return (
            _domain_match(url_parts.hostname or "", c.domain)
            and _path_match(url_parts.path or "/", c.path)
            and (not c.secure or url_parts.scheme == "https")
        )

    def _compose_cookie_header(
        self, url_parts, extra_headers: Mapping[str, str]
    ) -> tuple[str, list[Cookie]]:  # noqa: ANN001
        if "cookie" in extra_headers:
            return extra_headers["cookie"], []
        kv: list[str] = []
        sent: list[Cookie] = []
        for c in self.cookies:
            if self._cookie_matches(url_parts, c):
                kv.append(f"{c.name}={c.value}")
                sent.append(c)
        return ("; ".join(kv) if kv else "", sent)

    def _merge_cookies(self, fresh: Iterable[Cookie]) -> None:
        if not fresh:
            return
        jar: list[Cookie] = []
        for old in self.cookies:
            if any(
                old.name == n.name and old.domain == n.domain and old.path == n.path
                for n in fresh
            ):
                continue  # будет заменён
            jar.append(old)
        jar.extend(fresh)
        self.cookies = jar

    # public: low-level HTTP ──────────────────────────────────────────
    def requests(
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
        """HTTP-запрос через curl_cffi + cookie-jar."""

        # URL + query params
        if params:
            u = urlsplit(url)
            merged = urlencode(params, doseq=True)
            qs = u.query
            new_qs = f"{qs}&{merged}" if qs else merged
            url = urlunsplit((u.scheme, u.netloc, u.path, new_qs, u.fragment))

        # method enum
        if isinstance(method, HttpMethod):
            method_enum = method
        else:
            method_str = str(method).upper()
            try:
                method_enum = HttpMethod[method_str]
            except KeyError:  # value-based lookup fallback
                method_enum = HttpMethod(method_str)

        req_headers = {k.lower(): v for k, v in (headers or {}).items()}

        url_parts = urlsplit(url)
        cookie_header, sent_cookies = self._compose_cookie_header(url_parts, req_headers)
        if cookie_header:
            req_headers["cookie"] = cookie_header

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
            req_headers.setdefault(
                "content-type", "application/x-www-form-urlencoded"
            )

        # perform request
        self._ensure_curl()
        assert self._curl is not None
        t0 = perf_counter()
        r = self._curl.request(
            method_enum.value,
            url,
            headers=req_headers,
            data=body_bytes,
            allow_redirects=allow_redirects,
            timeout=self.timeout,
        )
        duration = perf_counter() - t0

        # response processing
        resp_headers = {k.lower(): v for k, v in r.headers.items()}
        set_cookie_raw: list[str] = []
        for k, v in r.headers.items():
            if k.lower() == "set-cookie":
                if isinstance(v, (list, tuple)):
                    set_cookie_raw.extend(v)
                else:
                    parts = [p.strip() for p in str(v).split(",") if p.strip()]
                    set_cookie_raw.extend(parts)
        resp_cookies = _parse_set_cookie(
            set_cookie_raw, url_parts.hostname or ""
        )
        self._merge_cookies(resp_cookies)

        charset = _guess_encoding(resp_headers)
        body_text = r.content.decode(charset, errors="replace")
        content = HTMLContent(body_text, url)  # type: ignore[arg-type]

        # models
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
            content=content,  # type: ignore[arg-type]
            status_code=r.status_code,
            duration=duration,
            _render_callable=self._render_response
        )
        return resp_model

    # public: browser navigation ───────────────────────────────────────
    def goto_page(
        self,
        url: str,
        *,
        wait_until: Literal["load", "domcontentloaded", "networkidle"] = "load",
    ) -> AbstractContextManager[Page]:
        """Контекст-менеджер для :class:`Page` с автосинхронизацией cookie-jar."""

        sess = self

        class _PageCtx(AbstractContextManager):
            def __init__(self, target: str, wait: str) -> None:
                self._target = target
                self._wait = wait
                self._page: Optional[Page] = None

            def __enter__(self) -> Page:  # noqa: D401
                sess._ensure_browser()
                ctx = sess._context
                assert ctx is not None
                if sess.cookies:
                    ctx.add_cookies(_cookies_to_pw(sess.cookies))
                self._page = ctx.new_page()
                self._page.goto(
                    self._target,
                    wait_until=self._wait,
                    timeout=sess.timeout * 1000,
                )
                return self._page

            def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401, ANN001
                ctx = sess._context
                if ctx is not None:
                    sess._merge_cookies(_cookie_from_pw(c) for c in ctx.cookies())
                if self._page is not None:
                    try:
                        self._page.close()
                    except Exception:
                        pass

        return _PageCtx(url, wait_until)

    # backend for Response.render() ────────────────────────────────────
    def _render_response(
        self,
        response: Response,
        *,
        wait_until: Literal[
            "load", "domcontentloaded", "networkidle"
        ] = "domcontentloaded",
    ) -> AbstractContextManager[Page]:
        """Создать Page, отвечающий подготовленным Response полностью офлайн."""

        sess = self

        class _OfflineCtx(AbstractContextManager):
            def __init__(self) -> None:
                self._page: Optional[Page] = None

            def __enter__(self) -> Page:  # noqa: D401
                sess._ensure_browser()
                ctx = sess._context
                assert ctx is not None

                # передаём куки в контекст
                if response.cookies:
                    ctx.add_cookies(_cookies_to_pw(response.cookies))

                # перехватываем первый запрос
                def handler(route, _req):  # noqa: ANN001
                    route.fulfill(
                        status=response.status_code,
                        headers=dict(response.headers),
                        body=response.body.encode("utf-8"),
                    )

                ctx.route("**/*", handler, times=1)
                self._page = ctx.new_page()
                self._page.goto(
                    response.url.full_url,
                    wait_until=wait_until,
                    timeout=sess.timeout * 1000,
                )
                return self._page

            def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401, ANN001
                ctx = sess._context
                if ctx is not None:
                    sess._merge_cookies(_cookie_from_pw(c) for c in ctx.cookies())
                if self._page is not None:
                    try:
                        self._page.close()
                    except Exception:
                        pass

        return _OfflineCtx()

    # cleanup ──────────────────────────────────────────────────────────
    def close(self) -> None:
        if self._context:
            try:
                self._context.close()
            finally:
                self._context = None
        if self._browser:
            try:
                self._browser.close()
            finally:
                self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            finally:
                self._pw = None
        if self._curl:
            try:
                self._curl.close()
            finally:
                self._curl = None

    # поддержка ``with Session() as s:``
    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()
