from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, cast, List, Literal
from urllib.parse import urlsplit
import json
import time
from pathlib import Path

import base64
import asyncio

from playwright.async_api import Page, Cookie
from playwright.async_api import Response as PWResponse
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from typing_extensions import override

from .abstraction.http import HttpMethod, URL
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
            Per-request `Referer` header (overrides headers set via `page.set_extra_http_headers()`).
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
            return data if isinstance(data, bytes) else bytes(data) if isinstance(data, (bytearray, memoryview)) else data.encode("utf-8", "replace")

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
            if req.frame is not main_frame or not req.is_navigation_request() or req.resource_type != "document":
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

        # НИЧЕГО не прячем: если goto упадёт, а затем ещё и unroute упадёт — поднимем обе ошибки как группу
        nav_exc: Exception | None = None
        res: Optional[PWResponse] = None
        try:
            res = await page.goto(target_url, retry=retry, on_retry=_on_retry_wrapper, **goto_kwargs)
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
        if isinstance(body, (dict, list)) and declared_headers.get("content-type", "").lower().startswith("application/json"):
            js_body = json.dumps(body, ensure_ascii=False)

        start_t = time.perf_counter()

        # Подготовка тела для JS (JSON -> строка при нужном content-type)
        js_body: Any = body
        if isinstance(body, (dict, list)) and declared_headers.get("content-type", "").lower().startswith("application/json"):
            js_body = json.dumps(body, ensure_ascii=False)

        # --- одноразовый наблюдатель (ничего не меняет, сразу continue_()) ---
        captured_first_req = None  # type: ignore[assignment]

        async def _route_handler(route, request):
            nonlocal captured_first_req
            if (
                captured_first_req is None
                and request.frame is self.main_frame
                and request.method.lower() == method.value.lower()
                and urlsplit(request.url)._replace(fragment="").geturl()
                   == urlsplit(url)._replace(fragment="").geturl()
            ):
                captured_first_req = request
            await route.continue_()

        await self.route("**/*", _route_handler)

        # Запускаем JS fetch как триггер сети (тело/заголовки возьмём из протокола)
        _JS_PATH = Path(__file__).parent / "fetch.js"
        JS_FETCH = _JS_PATH.read_text(encoding="utf-8")
        eval_task = asyncio.create_task(
            self.evaluate(
                JS_FETCH,
                dict(
                    url=url,
                    method=method.value,
                    headers={k: v for k, v in declared_headers.items() if k != "referer"} or {},
                    body=js_body,
                    credentials=credentials,
                    mode=mode,
                    redirect=redirect,
                    ref=referrer or declared_headers.get("referer"),
                    timeoutMs=timeout_ms,
                ),
            )
        )

        # Ждём наш первый Request
        first_req = await self.wait_for_event(
            "request",
            predicate=lambda r: (r.frame is self.main_frame)
                                and (r.method.lower() == method.value.lower())
                                and urlsplit(r.url)._replace(fragment="").geturl()
                                    == urlsplit(url)._replace(fragment="").geturl(),
            timeout=timeout_ms,
        )

        # Финальный запрос: по цепочке redirected_to
        cur = first_req
        while getattr(cur, "redirected_to", None) is not None:
            cur = cur.redirected_to  # type: ignore[assignment]

        # Ждём завершения финального запроса
        evt_finished = asyncio.create_task(
            self.wait_for_event("requestfinished", predicate=lambda r: r is cur, timeout=timeout_ms)
        )
        evt_failed = asyncio.create_task(
            self.wait_for_event("requestfailed", predicate=lambda r: r is cur, timeout=timeout_ms)
        )
        done, pending = await asyncio.wait({evt_finished, evt_failed}, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()

        duration = time.perf_counter() - start_t
        end_epoch = time.time()

        # Снимаем наблюдатель (если тут бросит — ты увидишь ошибку сразу)
        await self.unroute("**/*", _route_handler)

        # Если сеть упала — поднимем понятную ошибку
        if evt_failed in done:
            failure = cur.failure  # dict | None
            msg = (failure or {}).get("errorText") if isinstance(failure, dict) else None
            raise RuntimeError(str(failure))
            raise RuntimeError(f"network error: {msg or 'unknown'} | {method.value} {url}")

        # Успех сети: читаем ответ целиком
        resp = await cur.response()
        if resp is None:
            raise RuntimeError("no response object")

        raw = await resp.body()
        resp_headers = {k.lower(): v for k, v in (await resp.all_headers()).items()}

        req_model = FetchRequest(
            method=method,
            url=URL(full_url=url),
            headers=declared_headers,
            body=body,
        )
        resp_model = FetchResponse(
            request=req_model,
            url=URL(full_url=resp.url),
            headers=resp_headers,
            raw=raw,
            status_code=int(resp.status),
            duration=duration,
            end_time=end_epoch,
        )

        # Дожимаем JS-фетч (если он упадёт — упадёт явно, без подавления)
        await eval_task

        return resp_model



    async def wait_for_request(
        self,
        predicate: Callable[[Any], bool],
        *,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Wait for a request event to be emitted.
        """
        return await self.wait_for_event(event="request", predicate=predicate, timeout=timeout, **kwargs)

    async def wait_for_response(
        self,
        predicate: Callable[[Any], bool],
        *,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Wait for a response event to be emitted.
        """
        return await self.wait_for_event(event="response", predicate=predicate, timeout=timeout, **kwargs)

    @property
    def origin(self) -> str:
        url_parts = urlsplit(self.url)
        return f"{url_parts.scheme}://{url_parts.netloc}"

    async def cookies(self) -> List[Cookie]:
        """BrowserContext.cookies

        Cookies for the current page URL. Alias for `page.context.cookies([page.url])`.

        Returns
        -------
        List[{name: str, value: str, domain: str, path: str, expires: float, httpOnly: bool, secure: bool, sameSite: Union["Lax", "None", "Strict"], partitionKey: Union[str, None]}]
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

    def __repr__(self) -> str:
        return f"<HumanPage wrapping {super().__repr__()!r}>"
