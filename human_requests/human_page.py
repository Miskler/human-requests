from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, List, Literal, Optional, cast
from urllib.parse import urlsplit

from playwright.async_api import Cookie, Page
from playwright.async_api import Response as PWResponse
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from typing_extensions import override

from contextlib import suppress
import secrets as _secrets

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

        # НИЧЕГО не прячем: если goto упадёт, а затем ещё и unroute упадёт — поднимем обе ошибки как группу
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
        Выполняет реальный JS fetch и возвращает последний зафиксированный ответ,
        который достаточно похож на тот, который был отправлен (последний после завершения js ответ,
        request которого совпадает с нужным по URL и method).

        Таким образом гарантируется, что разные запросы не будут перепутаны.
        Даже одинаковые запросы, теоритически не будут перепутаны, т.к. мы орентируемся на завершения js логики,
        последний зарегистрированный в этот момент ответ с высокой вероятностью ему и пришел.

        Ответ НЕ извлекается напрямую из js, т.к. некоторые ответы могут быть заблокированны CORS.

        Принцип:
        1. Запускаем прослушку событий request/response.
        2. Выполняем page.evaluate(fetch(...)).
        3. После завершения JS промиса смотрим, какой последний response был образов от request с нужными параметрами.
        4. Возвращаем тело, статус и заголовки из этого последнего response.

        Пример:
            result = await page.fetch(
                "https://example.com/api/data",
                method=HttpMethod.POST,
                headers={"Content-Type": "application/json"},
                body={"user": "mike"}
            )
            print(result.status_code, result.json())
        """
        fid = _secrets.token_hex(6)
        start = time.perf_counter()
        _log = lambda msg: print(f"[fetch:{fid}][{(time.perf_counter()-start)*1000:7.1f} ms] {msg}")

        method_str = method.value.upper()
        norm_url = url.split("#")[0]

        js_headers = headers or {}
        js_body = body
        if isinstance(body, (dict, list)):
            js_body = json.dumps(body)
            js_headers.setdefault("Content-Type", "application/json")

        # будем собирать ответы пока жив JS fetch
        captured_responses = []

        # helper: walk request chain (request + its redirected_from ancestors)
        def _chain_matches(req) -> bool:
            cur = req
            while cur is not None:
                try:
                    rmethod = getattr(cur, "method", None)
                    rurl = getattr(cur, "url", None)
                except Exception as e:
                    _log("Error when try get method/url in response chain: "+e.__repr__)
                    rmethod = None
                    rurl = None
                if rmethod and rurl:
                    if rmethod.upper() == method_str and rurl.split("#")[0] == norm_url:
                        return True
                cur = getattr(cur, "redirected_from", None)
            return False

        async def on_response(resp):
            try:
                req = getattr(resp, "request", None)
                if req is None:
                    return
                if _chain_matches(req):
                    captured_responses.append(resp)
            except Exception as e:
                _log("Error when try on_response: "+e.__repr__)
                # никогда не ломаем основную логику сниффером
                return

        self.on("response", on_response)

        try:
            _log(f"START {method_str} {url}")

            js_code = """
            async ({ url, method, headers, body, credentials, mode, redirect, ref, timeoutMs }) => {
                const ctrl = new AbortController();
                const timer = setTimeout(() => ctrl.abort(), timeoutMs);
                try {

                    const init = { method, headers, credentials, mode, redirect, signal: ctrl.signal };
                    if (ref) init.referrer = ref;
                    if (body !== undefined && body !== null) init.body = body;

                    const res = await fetch(url, init);

                    clearTimeout(timer);
                    return { ok: true, status: res.status };
                } catch (err) {
                    clearTimeout(timer);
                    return { ok: false, errorName: err.name, error: err?.message || String(err) };
                }
            }
            """

            result = await asyncio.wait_for(
                self.evaluate(js_code, dict(
                    url=url,
                    method=method_str,
                    headers=js_headers,
                    body=js_body,
                    credentials=credentials,
                    mode=mode,
                    redirect=redirect,
                    ref=referrer,
                    timeoutMs=timeout_ms,
                )),
                timeout=timeout_ms / 1000 + 1,
            )

            if not captured_responses:
                _log("Raise")
                if not result.get("ok"):
                    raise RuntimeError(f"JS fetch failed: {result.get('error')}")
                raise RuntimeError("no matching responses captured")

            last_resp = captured_responses[-1]
            _log(f"using last response (of {len(captured_responses)}): {last_resp.url} ({last_resp.status})")

            body_bytes = await last_resp.body()
            headers = {k.lower(): v for k, v in (await last_resp.all_headers()).items()}

            req_model = FetchRequest(
                page=self,
                method=method,
                url=URL(full_url=url),
                headers=js_headers,
                body=body,
            )
            return FetchResponse(
                request=req_model,
                page=self,
                url=URL(full_url=last_resp.url),
                status_code=last_resp.status,
                headers=headers,
                raw=body_bytes,
                duration=(time.perf_counter() - start),
                end_time=time.time(),
            )

        finally:
            self.remove_listener("response", on_response)
            pass#self.off("response", on_response)


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
        return await self.wait_for_event(
            event="request", predicate=predicate, timeout=timeout, **kwargs
        )

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
        return await self.wait_for_event(
            event="response", predicate=predicate, timeout=timeout, **kwargs
        )

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
