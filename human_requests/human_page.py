from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, List, Literal, Optional, cast
from urllib.parse import urlsplit

from playwright.async_api import Cookie, Page
from playwright.async_api import Response as PWResponse
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from selectolax.parser import HTMLParser
from typing_extensions import override

from .abstraction.http import URL, HttpMethod
from .abstraction.request import FetchRequest
from .abstraction.response import FetchResponse

if TYPE_CHECKING:
    from .human_context import HumanContext


class HumanPage(Page):
    """
    A thin, type-compatible wrapper over Playwright's Page.
    """

    # ---------- core identity ----------

    @property
    @override
    def context(self) -> "HumanContext":
        # рантайм остаётся прежним; только уточняем тип
        return cast("HumanContext", super().context)

    @staticmethod
    def replace(playwright_page: Page) -> HumanPage:
        """Подменяет стандартный Playwright класс с сохранением содержимого."""
        from .human_context import HumanContext  # avoid circular import

        if isinstance(playwright_page.context, HumanContext) is False:
            raise TypeError("The provided Page's context is not a HumanContext")

        playwright_page.__class__ = HumanPage
        return playwright_page  # type: ignore[return-value]

    # ---------- lifecycle / sync ----------

    @override
    async def goto(
        self,
        url: str,
        *,
        retry: Optional[int] = None,
        on_retry: Optional[Callable[[], Awaitable[None]]] = None,
        # standard Playwright kwargs (not exhaustive; forwarded via **kwargs):
        **kwargs: Any,
    ) -> Optional[PWResponse]:
        """
        Navigate to `url` with optional retry-on-timeout.

        If the initial navigation raises a Playwright `TimeoutError`, this method performs up to
        `retry` *soft* reloads (`Page.reload`) using the same `wait_until`/`timeout` settings.
        Before each retry, the optional `on_retry` hook is awaited so you can (re)attach
        one-shot listeners, route handlers, subscriptions, etc., that would otherwise be spent.

        Parameters
        ----------
        url : str
            Absolute URL to navigate to.
        retry : int | None, optional
            Number of soft reload attempts after a timeout (0 means no retries).
            If None, defaults to `session.page_retry`.
        on_retry : Callable[[], Awaitable[None]] | None, optional
            Async hook called before each retry; use it to re-register any one-shot
            event handlers or routes needed for the next attempt.
        timeout : float | None, optional
            Navigation timeout in milliseconds. If None, falls back to `session.timeout * 1000`.
        wait_until : {"commit", "domcontentloaded", "load", "networkidle"} | None, optional
            When to consider the navigation successful (forwarded to Playwright).
        referer : str | None, optional
            Per-request `Referer` header
            (overrides headers set via `page.set_extra_http_headers()`).
        **kwargs : Any
            Any additional keyword arguments are forwarded to Playwright's `Page.goto`.

        Returns
        -------
        playwright.async_api.Response | None
            The main resource `Response`, or `None` for `about:blank` and same-URL hash navigations.

        Raises
        ------
        playwright.async_api.TimeoutError
            If the initial navigation and all retries time out.
        Any other exceptions from `Page.goto` / `Page.reload` may also propagate.

        Notes
        -----
        - Soft reloads reuse the same `wait_until`/`timeout` pair to keep behavior consistent
        across attempts.
        - Because one-shot handlers are consumed after a failed attempt, always re-attach them
        inside `on_retry` if the navigation logic depends on them.
        """
        # Build the kwargs for the underlying goto/reload calls:
        try:
            return await super().goto(url, **kwargs)
        except PlaywrightTimeoutError as last_err:
            attempts_left = (
                int(retry) + 1 if retry is not None else 1
            )  # +1 т.к. первый запрос базис
            while attempts_left > 0:
                attempts_left -= 1
                if on_retry is not None:
                    await on_retry()
                try:
                    # Soft refresh with the SAME wait_until/timeout
                    await super().reload(
                        **{k: kwargs[k] for k in ("wait_until", "timeout") if k in kwargs}
                    )
                    last_err = None
                    break
                except PlaywrightTimeoutError as e:
                    last_err = e
            if last_err is not None:
                raise last_err

    async def goto_render(self, first, /, **goto_kwargs) -> Optional[PWResponse]:
        """
        Перехватывает первый навигационный запрос main-frame к target_url и
        отдаёт синтетический ответ, затем делает обычный page.goto(...).
        Возвращает Optional[PWResponse] как и goto.
        """

        # -------- helpers (локально и коротко) ---------------------------------
        def _to_bytes(data: bytes | bytearray | memoryview | str) -> bytes:
            return (
                data
                if isinstance(data, bytes)
                else (
                    bytes(data)
                    if isinstance(data, (bytearray, memoryview))
                    else data.encode("utf-8", "replace")
                )
            )

        def _is_html(b: bytes) -> bool:
            s = b[:512].lstrip().lower()
            return s.startswith(b"<!doctype html") or s.startswith(b"<html") or b"<body" in s

        def _norm_args() -> tuple[str, bytes, int, dict[str, str]]:
            if isinstance(first, FetchResponse):
                url = first.url.full_url
                body = _to_bytes(first.raw or b"")
                code = int(first.status_code)
                hdrs = dict(first.headers or {})
            else:
                url = str(first)
                if "body" not in goto_kwargs:
                    raise TypeError("goto_render(url=..., *, body=...) is required")
                body = _to_bytes(goto_kwargs.pop("body"))
                code = int(goto_kwargs.pop("status_code", 200))
                hdrs = dict(goto_kwargs.pop("headers", {}) or {})
            # убрать транспортные, поставить content-type при html
            drop = {"content-length", "content-encoding", "transfer-encoding", "connection"}
            clean = {k: v for k, v in hdrs.items() if k.lower() not in drop}
            if body and not any(k.lower() == "content-type" for k in clean) and _is_html(body):
                clean["content-type"] = "text/html; charset=utf-8"
            return url, body, code, clean

        # Переназначим ретраи до того, как их прочитает goto
        retry = goto_kwargs.pop("retry", None)
        on_retry = goto_kwargs.pop("on_retry", None)

        target_url, raw, status_code, headers = _norm_args()
        page = self
        main_frame = page.main_frame
        target_wo_hash = urlsplit(target_url)._replace(fragment="").geturl()

        handled = False
        installed = False

        def _match(req) -> bool:
            if (
                req.frame is not main_frame
                or not req.is_navigation_request()
                or req.resource_type != "document"
            ):
                return False
            return urlsplit(req.url)._replace(fragment="").geturl() == target_wo_hash

        async def handler(route, request):
            nonlocal handled, installed
            if handled or not _match(request):
                return await route.continue_()
            handled = True
            await route.fulfill(status=status_code, headers=headers, body=raw)
            # Снимем маршрут сразу; если упадёт — не скрываем: пусть всплывёт позже.
            await page.unroute(target_url, handler)
            installed = False

        async def _install():
            nonlocal installed
            if installed:
                await page.unroute(target_url, handler)
            await page.route(target_url, handler)
            installed = True

        await _install()

        async def _on_retry_wrapper():
            await _install()
            if on_retry:
                await on_retry()

        # НИЧЕГО не прячем: если goto упадёт, а затем ещё и unroute упадёт
        # — поднимем обе ошибки как группу
        nav_exc: Exception | None = None
        res: Optional[PWResponse] = None
        try:
            res = await page.goto(
                target_url, retry=retry, on_retry=_on_retry_wrapper, **goto_kwargs
            )
        except Exception as e:
            nav_exc = e
        finally:
            unroute_exc: Exception | None = None
            if installed:
                try:
                    await page.unroute(target_url, handler)
                except Exception as e:
                    unroute_exc = e
            if nav_exc and unroute_exc:
                raise ExceptionGroup("goto_render failed", (nav_exc, unroute_exc))
            if nav_exc:
                raise nav_exc
            if unroute_exc:
                raise unroute_exc

        return res

    async def fetch(
        self,
        url: str,
        *,
        method: HttpMethod = HttpMethod.GET,
        headers: Optional[dict[str, str]] = None,
        body: Optional[str | list | dict] = None,
        credentials: Literal["omit", "same-origin", "include"] = "include",
        mode: Literal["cors", "no-cors", "same-origin"] = "cors",
        redirect: Literal["follow", "error", "manual"] = "follow",
        referrer: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> FetchResponse:
        """
        Тонкая прослойка над JS fetch: выполняет запрос внутри страницы и возвращает ResponseModel.
        • Без route / wait_for_event.
        • raw — ВСЕГДА распакованные байты (если тело доступно JS).
        • При opaque-ответе тело/заголовки могут быть недоступны — это ограничение CORS.
        """
        declared_headers = {k.lower(): v for k, v in (headers or {}).items()}
        js_headers = {k: v for k, v in declared_headers.items() if k != "referer"}
        js_ref = referrer or declared_headers.get("referer")

        js_body: Any = body
        if isinstance(body, (dict, list)):
            js_body = json.dumps(body, ensure_ascii=False)
            js_headers["content-type"] = "application/json"

        start_t = time.perf_counter()

        _JS_PATH = Path(__file__).parent / "fetch.js"
        JS_FETCH = _JS_PATH.read_text(encoding="utf-8")

        result = await self.evaluate(
            JS_FETCH,
            dict(
                url=url,
                method=method.value,
                headers=js_headers or {},
                body=js_body,
                credentials=credentials,
                mode=mode,
                redirect=redirect,
                ref=js_ref,
                timeoutMs=timeout_ms,
            ),
        )

        if not result.get("ok"):
            raise RuntimeError(f"fetch failed: {result.get('error')}")

        # bytes в raw: распакованные (если body доступен)
        b64 = result.get("bodyB64")
        raw = base64.b64decode(b64) if isinstance(b64, str) else b""

        # Нормализуем заголовки: если raw есть,
        # уберём transport-атрибуты, чтобы не путать потребителя
        resp_headers = {k.lower(): v for k, v in (result.get("headers") or {}).items()}
        if raw:
            resp_headers.pop("content-encoding", None)
            resp_headers.pop("content-length", None)

        req_model = FetchRequest(
            page=self,
            method=method,
            url=URL(full_url=url),
            headers=declared_headers,
            body=body,
        )

        duration = time.perf_counter() - start_t
        end_epoch = time.time()

        resp_model = FetchResponse(
            page=self,
            request=req_model,
            url=URL(full_url=result.get("finalUrl") or url),
            headers=resp_headers,
            raw=raw,  # всегда bytes; пусто если CORS не дал читать тело
            status_code=int(result.get("status", 0)),
            status_text=str(result.get("statusText", "STATUS TEXT NOT AVAILABLE")),
            redirected=bool(result.get("redirected", False)),
            type=result.get("type", False),
            duration=duration,
            end_time=end_epoch,
        )
        return resp_model

    @property
    def origin(self) -> str:
        url_parts = urlsplit(self.url)
        return f"{url_parts.scheme}://{url_parts.netloc}"

    async def cookies(self) -> List[Cookie]:
        """BrowserContext.cookies

        Cookies for the current page URL. Alias for `page.context.cookies([page.url])`.

        Returns
        -------
        List[{
            name: str,
            value: str,
            domain: str,
            path: str,
            expires: float,
            httpOnly: bool,
            secure: bool,
            sameSite: Union["Lax", "None", "Strict"],
            partitionKey: Union[str, None]
        }]
        """
        return await self.context.cookies([self.url])

    async def local_storage(self, **kwargs) -> dict[str, str]:
        ls = await self.context.local_storage(**kwargs)
        return ls.get(self.origin, {})

    async def session_storage(self) -> dict[str, str]:
        return await self.evaluate(
            """
            (which) => {
            try {
                const s = (which in window) ? window[which] : null;
                if (!s) return null;
                return s;
            } catch (_) {
                return null;
            }
            }
            """,
            "sessionStorage",
        )

    async def json(self) -> list | dict:
        """
        Если контент страницы это json - парсит (браузер всегда оборачивает его в body->pre),
        сереализует и выдает его.
        """
        body = await self.content()
        tree = HTMLParser(body)

        node = tree.css_first("body > pre")  # точный селектор "body > pre"
        if node is None:
            raise RuntimeError("Содержимое страницы не является json-контейнером")

        return json.loads(node.text())

    def __repr__(self) -> str:
        return f"<HumanPage wrapping {super().__repr__()!r}>"
