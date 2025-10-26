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
        Выполняет HTTP-запрос «изнутри» страницы и возвращает *протокольный* ответ как `FetchResponse`.

        Как это работает
        ----------------
        Метод запускает короткий JS-`fetch(...)` лишь как **триггер сети**, а сами данные ответа
        (статус, заголовки, «сырые» байты) читает через сетевые события браузера (Playwright API).
        Благодаря этому:
          • игнорируются CORS-ограничения JS: `resp.raw` — это всегда байты тела (если сервер их прислал);
          • корректно ведётся цепочка редиректов; возвращается финальный ответ;
          • не используются `page.route(...)` — отсутствуют конфликты с чужими перехватчиками и накладные расходы.

        Поведение и особенности
        -----------------------
        • `headers`: передаются в запрос; заголовок `Referer` брать из аргумента `referrer`
          (если вы передали `'referer'` в `headers`, он будет автоматически использован как `referrer`).
        • `body`: если это `dict`/`list` **и** `Content-Type: application/json`,
          тело будет сериализовано `json.dumps(..., ensure_ascii=False)`. В остальных случаях передайте готовую строку.
        • `mode="no-cors"`: JS-уровень увидит «opaque», но метод всё равно вернёт тело и заголовки,
          т.к. они считываются из протокола.
        • Редиректы: цепочка отслеживается по объектам `Request`. Типичные FF-аборты на редиректе
          (`NS_BINDING_ABORTED`) не считаются ошибкой, если сразу появляется следующий hop.
        • Параллельность: корреляция первого запроса идёт по URL+методу в *том же фрейме*, где выполняется `evaluate`.
          Если на странице в этот же момент стартует другой запрос с **тем же** URL/методом из того же фрейма,
          возможна коллизия. Избегайте одновременных идентичных запросов или различайте их заголовками/квери.

        Параметры
        ---------
        url : str
            Адрес запроса (фрагмент `#...` игнорируется при сопоставлении).
        method : HttpMethod, по умолчанию GET
            HTTP-метод.
        headers : dict[str, str] | None
            Заголовки запроса. `Referer` берите из `referrer` (или положите сюда — мы переложим).
        body : str | list | dict | None
            Тело запроса. Для JSON (см. выше) сериализуется автоматически.
        credentials : {"omit","same-origin","include"}
            Политика отправки куков/креденшлов (как в fetch()).
        mode : {"cors","no-cors","same-origin"}
            Режим CORS (как в fetch()).
        redirect : {"follow","error","manual"}
            Политика редиректов JS-уровня (на протокол не влияет; финальный ответ мы определяем сами).
        referrer : str | None
            Явный реферер (если не задан — возьмём из `headers["referer"]`, если он там есть).
        timeout_ms : int
            Общий таймаут ожидания сетевых событий, миллисекунды.

        Возвращает
        ----------
        FetchResponse
            Объект с полями:
              • `request: FetchRequest` (метод, исходный URL, исходные заголовки/тело);
              • `url: URL` — финальный URL после редиректов;
              • `headers: dict[str,str]` — **полные** заголовки ответа (в нижнем регистре);
              • `raw: bytes` — «сырое» тело ответа;
              • `status_code: int`;
              • `duration: float` — длительность запроса (секунды);
              • `end_time: float` — UNIX-время окончания.

        Исключения
        ----------
        RuntimeError
            • `"network error: … | <METHOD> <URL>"` — реальная ошибка сети/соединения без следующего hop;
            • `"no response object"` — завершение без объекта ответа (аномалия).
        Также метод завершится исключением, если страница/контекст закрыты во время выполнения
        (например, `TargetClosedError` из Playwright при `evaluate`).

        Пример
        ------
        >>> from camoufox.async_api import AsyncCamoufox
        >>> from human_requests import HumanBrowser
        >>> from human_requests.abstraction import HttpMethod
        >>> import asyncio, json
        >>>
        >>> async def demo():
        ...     async with AsyncCamoufox() as b:
        ...         b = HumanBrowser.replace(b)
        ...         ctx = await b.new_context()
        ...         page = await ctx.new_page()
        ...         # GET с обходом CORS на протокольном уровне:
        ...         resp = await page.fetch(
        ...             url="https://httpbin.org/get",
        ...             method=HttpMethod.GET,
        ...             mode="no-cors",
        ...         )
        ...         print(resp.status_code, len(resp.raw), resp.headers.get("content-type"))
        ...
        ...         # POST JSON: тело сериализуется автоматически при Content-Type: application/json
        ...         resp2 = await page.fetch(
        ...             url="https://httpbin.org/post",
        ...             method=HttpMethod.POST,
        ...             headers={"Content-Type": "application/json"},
        ...             body={"hello": "world"},
        ...         )
        ...         data = json.loads(resp2.raw.decode("utf-8", "replace"))
        ...         print(data["json"])
        ...         await b.close()
        >>>
        >>> asyncio.run(demo())
        """

        start_t = time.perf_counter()
        declared_headers = {k.lower(): v for k, v in (headers or {}).items()}

        js_body: Any = body
        if isinstance(body, (dict, list)) and declared_headers.get(
            "content-type", ""
        ).lower().startswith("application/json"):
            js_body = json.dumps(body, ensure_ascii=False)

        # Подготовка тела для JS (JSON -> строка при нужном content-type)
        js_body: Any = body
        if isinstance(body, (dict, list)) and declared_headers.get(
            "content-type", ""
        ).lower().startswith("application/json"):
            js_body = json.dumps(body, ensure_ascii=False)

        # 1) Запускаем JS fetch (только триггер сети; тело/хедеры читаем с протокола)
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

        # Нормализуем URL без фрагмента
        def _norm(u: str) -> str:
            return urlsplit(u)._replace(fragment="").geturl()

        target_url_norm = _norm(url)
        target_method = method.value.lower()

        # 2) Ждём первый request к нужному URL/методу
        first_req = await self.wait_for_event(
            "request",
            predicate=lambda r: r.method.lower() == target_method
            and _norm(r.url) == target_url_norm,
            timeout=timeout_ms,
        )

        cur = first_req
        redirect_grace = 1  # 1000ms на появление следующего hop при abort/3xx

        while True:
            t_next = asyncio.create_task(
                self.wait_for_event(
                    "request",
                    predicate=lambda r, _cur=cur: getattr(r, "redirected_from", None) is _cur,
                    timeout=timeout_ms,
                )
            )
            t_fin = asyncio.create_task(
                self.wait_for_event(
                    "requestfinished", predicate=lambda r, _cur=cur: r is _cur, timeout=timeout_ms
                )
            )
            t_fail = asyncio.create_task(
                self.wait_for_event(
                    "requestfailed", predicate=lambda r, _cur=cur: r is _cur, timeout=timeout_ms
                )
            )
            done, pending = await asyncio.wait(
                {t_next, t_fin, t_fail}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()

            # 2.1 redirect: есть следующий hop
            if t_next in done and t_next.exception() is None:
                cur = t_next.result()
                continue

            # 2.2 fail: возможно abort из-за редиректа
            if t_fail in done and t_fail.exception() is None:
                failure_req = t_fail.result()
                failure = getattr(failure_req, "failure", None) or {}
                err_text = failure.get("errorText") if isinstance(failure, dict) else None

                # даём короткую фразу на появление next hop после abort
                t_quick = asyncio.create_task(
                    self.wait_for_event(
                        "request",
                        predicate=lambda r, _cur=cur: getattr(r, "redirected_from", None) is _cur,
                        timeout=int(redirect_grace * 1000),
                    )
                )
                await asyncio.wait({t_quick}, timeout=redirect_grace)
                if t_quick.done() and t_quick.exception() is None:
                    cur = t_quick.result()
                    continue

                # реальный провал сети
                await eval_task
                raise RuntimeError(f"network error: {err_text or 'unknown'} | {method.value} {url}")

            # 2.3 finished: возможно 3xx -> ждём hop
            if t_fin in done and t_fin.exception() is None:
                resp_try = await cur.response()
                status = int(getattr(resp_try, "status", 0)) if resp_try is not None else 0
                if 300 <= status < 400:
                    t_quick = asyncio.create_task(
                        self.wait_for_event(
                            "request",
                            predicate=lambda r, _cur=cur: getattr(r, "redirected_from", None)
                            is _cur,
                            timeout=int(redirect_grace * 1000),
                        )
                    )
                    await asyncio.wait({t_quick}, timeout=redirect_grace)
                    if t_quick.done() and t_quick.exception() is None:
                        cur = t_quick.result()
                        continue
                # финал
                resp = resp_try
                break

        duration = time.perf_counter() - start_t
        end_epoch = time.time()

        if resp is None:
            await eval_task
            raise RuntimeError("no response object")

        raw = await resp.body()
        resp_headers = {k.lower(): v for k, v in (await resp.all_headers()).items()}

        req_model = FetchRequest(
            page=self,
            method=method,
            url=URL(full_url=url),
            headers=declared_headers,
            body=body,
        )
        resp_model = FetchResponse(
            request=req_model,
            page=self,
            url=URL(full_url=resp.url),
            headers=resp_headers,
            raw=raw,
            status_code=int(resp.status),
            duration=duration,
            end_time=end_epoch,
        )

        # 4) Дожимаем JS-fetch (если он упал из-за CORS — это уже не критично)
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
