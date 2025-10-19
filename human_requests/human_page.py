from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, cast, List, Literal
from urllib.parse import urlsplit
import json
import time
import asyncio

import base64

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

        result = await self.evaluate(
            """
            async ({ url, method, headers, body, credentials, mode, redirect, ref, timeoutMs }) => {
            const ctrl = new AbortController();
            const id = setTimeout(() => ctrl.abort("timeout"), timeoutMs);
            try {
                const init = { method, headers, credentials, mode, redirect, signal: ctrl.signal };
                if (ref) init.referrer = ref;
                if (body !== undefined && body !== null) init.body = body;

                const r = await fetch(url, init);

                // Заголовки (если CORS позволит)
                const headersObj = {};
                try { r.headers.forEach((v, k) => headersObj[k.toLowerCase()] = v); } catch {}

                // Тело читаем как ArrayBuffer (это уже РАСПАКОВАННЫЕ байты), кодируем в base64
                let bodyB64 = null;
                try {
                const ab = await r.arrayBuffer();
                const u8 = new Uint8Array(ab);
                const chunk = 0x8000;
                let binary = "";
                for (let i = 0; i < u8.length; i += chunk) {
                    binary += String.fromCharCode.apply(null, u8.subarray(i, i + chunk));
                }
                bodyB64 = btoa(binary);
                } catch { bodyB64 = null; }

                return {
                ok: true,
                finalUrl: r.url,
                status: r.status,
                type: r.type,        // basic | cors | opaque | opaque-redirect
                redirected: r.redirected,
                headers: headersObj, // может быть пустым при CORS
                bodyB64,             // base64 распакованных байтов или null
                };
            } catch (e) {
                return { ok: false, error: String(e) };
            } finally {
                clearTimeout(id);
            }
            }
            """,
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

        duration = time.perf_counter() - start_t
        end_epoch = time.time()

        if not result.get("ok"):
            raise RuntimeError(f"fetch failed: {result.get('error')}")

        # bytes в raw: распакованные (если body доступен)
        b64 = result.get("bodyB64")
        raw = base64.b64decode(b64) if isinstance(b64, str) else b""

        # Нормализуем заголовки: если raw есть, уберём transport-атрибуты, чтобы не путать потребителя
        resp_headers = {k.lower(): v for k, v in (result.get("headers") or {}).items()}
        if raw:
            resp_headers.pop("content-encoding", None)
            resp_headers.pop("content-length", None)

        req_model = FetchRequest(
            method=method,
            url=URL(full_url=url),
            headers=declared_headers,
            body=body,
        )

        resp_model = FetchResponse(
            request=req_model,
            url=URL(full_url=result.get("finalUrl") or url),
            headers=resp_headers,
            raw=raw,     # всегда bytes; пусто если CORS не дал читать тело
            status_code=int(result.get("status", 0)),
            duration=duration,
            end_time=end_epoch,
        )
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

    async def _collect_web_storage(
        self,
        store: Literal["localStorage", "sessionStorage"],
    ) -> dict[str, dict[str, str]]:
        """
        Сбор содержимого выбранного Web Storage по всем фреймам страницы.
        Возвращает: { origin: { key: value, ... }, ... }
        """
        # Собираем список фреймов один раз
        frames = list(getattr(self, "frames", []))

        async def _from_frame(frame) -> Optional[tuple[str, dict[str, str]]]:
            url: str = getattr(frame, "url", "") or ""
            if not (url.startswith("http://") or url.startswith("https://")):
                return None  # about:blank/data:/chrome-*, и т.п. — пропускаем
            u = urlsplit(url)
            origin = f"{u.scheme}://{u.netloc}"

            try:
                data = await frame.evaluate(
                    """
                    (which) => {
                    try {
                        const s = (which in window) ? window[which] : null;
                        if (!s) return null;
                        const out = {};
                        for (let i = 0; i < s.length; i++) {
                        const k = s.key(i);
                        out[k] = s.getItem(k);
                        }
                        return out;
                    } catch (_) {
                        return null;
                    }
                    }
                    """,
                    store,
                )
            except Exception:
                data = None

            if not data:
                return None
            return (origin, data)

        # Параллельно обходим все фреймы
        results = await asyncio.gather(*[_from_frame(f) for f in frames], return_exceptions=True)

        # Аггрегируем по origin
        aggregated: dict[str, dict[str, str]] = {}
        for res in results:
            if isinstance(res, tuple):
                origin, data = res
                if origin in aggregated:
                    aggregated[origin].update(data)   # несколько фреймов одного origin
                else:
                    aggregated[origin] = dict(data)
        return aggregated


    async def local_storage(self) -> dict[str, dict[str, str]]:
        """
        Прочитать localStorage во всех видимых фреймах (<iframe></<iframe> элементы, в т.ч. main).
        Возвращает: { origin: { key: value, ... }, ... }
        """
        return await self._collect_web_storage("localStorage")


    async def session_storage(self) -> dict[str, dict[str, str]]:
        """
        Прочитать sessionStorage во всех видимых фреймах (<iframe></iframe> элементы, в т.ч. main).
        Возвращает: { origin: { key: value, ... }, ... }
        """
        return await self._collect_web_storage("sessionStorage")


    def __repr__(self) -> str:
        return f"<HumanPage wrapping {super().__repr__()!r}>"
