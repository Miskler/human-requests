from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, cast, List, Literal
from urllib.parse import urlsplit
import json
import time
from pathlib import Path

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
    
    async def goto_render(self, first, /, **goto_kwargs: Any) -> Optional[PWResponse]:
        """
        Одноразово перехватывает *первый* навигационный запрос main-frame к `target_url`
        и отдает синтетический ответ (status/headers/body). Затем выполняет обычный
        `page.goto(target_url, **kwargs)` и возвращает его результат (PWResponse | None).

        Формы вызова:
          • page.goto_render(FetchResponse, **goto_kwargs)
          • page.goto_render(url, body=..., status_code=..., headers=..., **goto_kwargs)
        """
        # ---- нормализация входа -------------------------------------------------
        def _to_bytes(data: bytes | bytearray | memoryview | str) -> bytes:
            if isinstance(data, (bytes, bytearray, memoryview)):
                return bytes(data)
            # str -> bytes
            return data.encode("utf-8", "replace")

        def _looks_like_html(b: bytes) -> bool:
            sample = b[:512].lstrip().lower()
            return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or b"<body" in sample

        retry = goto_kwargs.pop("retry", None)
        on_retry = goto_kwargs.pop("on_retry", None)

        if isinstance(first, FetchResponse):
            target_url = first.url.full_url
            raw = _to_bytes(first.raw or b"")
            status_code = int(first.status_code)
            headers = dict(first.headers or {})
        else:
            target_url = str(first)
            if "body" not in goto_kwargs:
                raise TypeError("goto_render(url=..., *, body=...) is required")
            raw = _to_bytes(goto_kwargs.pop("body"))
            status_code = int(goto_kwargs.pop("status_code", 200))
            headers = dict(goto_kwargs.pop("headers", {}) or {})

        # Заголовки: убрать транспортные, подставить content-type при необходимости
        clean_headers: dict[str, str] = {}
        for k, v in headers.items():
            kl = str(k).strip()
            if kl.lower() in {"content-length", "content-encoding", "transfer-encoding", "connection"}:
                continue
            clean_headers[kl] = v
        if not any(k.lower() == "content-type" for k in clean_headers):
            if raw and _looks_like_html(raw):
                clean_headers["content-type"] = "text/html; charset=utf-8"

        # ---- безопасный one-shot роутинг на уровне страницы ---------------------
        page = self
        main_frame = page.main_frame
        target_wo_hash = urlsplit(target_url)._replace(fragment="").geturl()

        handled = False
        installed = False
        unroute_exc_from_handler: Optional[Exception] = None

        async def handler(route, request):
            nonlocal handled, installed, unroute_exc_from_handler

            def _is_target_nav(req) -> bool:
                if req.frame != main_frame or \
                not req.is_navigation_request() or \
                req.resource_type != "document":
                    return False
                req_wo_hash = urlsplit(req.url)._replace(fragment="").geturl()
                return req_wo_hash == target_wo_hash

            if handled or not _is_target_nav(request):
                return await route.continue_()
            handled = True
            await route.fulfill(status=status_code, headers=clean_headers, body=raw)
            # Сразу отвяжемся (не скрываем возможную ошибку — сохраним и поднимем позже)
            try:
                await page.unroute("**/*", handler)
                installed = False
            except Exception as e:
                unroute_exc_from_handler = e  # поднимем после навигации

        async def _install():
            nonlocal installed
            if installed:
                # переустановка: сначала снять, потом повесить; ошибки — наружу
                await page.unroute("**/*", handler)
                installed = False
            await page.route("**/*", handler)
            installed = True

        # Ставим перехват до навигации
        await _install()

        # Обертка над on_retry: при ретрае надо заново установить маршрут
        async def _on_retry_wrapper():
            await _install()
            if on_retry:
                await on_retry()

        nav_exc: Optional[Exception] = None
        result: Optional[PWResponse] = None

        try:
            result = await page.goto(
                target_url,
                retry=retry,
                on_retry=_on_retry_wrapper,
                **goto_kwargs,  # пробросить все родные параметры goto
            )
        except Exception as e:
            nav_exc = e
        finally:
            # Финальная попытка снять перехват, если он ещё висит
            final_unroute_exc: Optional[Exception] = None
            if installed:
                try:
                    await page.unroute("**/*", handler)
                except Exception as e:
                    final_unroute_exc = e
                finally:
                    installed = False

            # Не скрываем ошибки: собираем и поднимаем максимально честно.
            errors: list[Exception] = []
            if nav_exc:
                errors.append(nav_exc)
            if unroute_exc_from_handler:
                errors.append(unroute_exc_from_handler)
            if final_unroute_exc:
                errors.append(final_unroute_exc)

            if errors:
                if len(errors) == 1:
                    raise errors[0]
                # Python 3.11+: вернём ExceptionGroup, чтобы ничего не потерять.
                raise ExceptionGroup("goto_render failed with multiple errors", errors)

        return result
    
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
        print(f"FETCH RESULT: {result}", flush=True)

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
