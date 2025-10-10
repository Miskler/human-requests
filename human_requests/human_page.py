from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, cast
from urllib.parse import urlsplit

from playwright.async_api import Page
from playwright.async_api import Response as PWResponse
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from typing_extensions import overload, override

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

    @property
    def origin(self) -> str:
        url_parts = urlsplit(self.url)
        return f"{url_parts.scheme}://{url_parts.netloc}"

    async def local_storage(self, **kwargs) -> dict[str, str]:
        ls = await self.context.local_storage(**kwargs)
        return ls.get(self.origin, {})

    def __repr__(self) -> str:
        return f"<HumanPage wrapping {super().__repr__()!r}>"
