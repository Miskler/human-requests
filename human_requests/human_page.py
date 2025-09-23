from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Optional

from playwright.async_api import Page
from playwright.async_api import Response as PWResponse
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

if TYPE_CHECKING:
    from .human_context import HumanContext
    from .session import Session


class HumanPage(Page):
    """
    A thin, type-compatible wrapper over Playwright's Page.
    """

    __slots__ = ("_raw", "_hc")

    def __init__(self, *, raw_page: Page, human_context: "HumanContext") -> None:
        # store to slots directly to avoid proxy logic and slot errors
        object.__setattr__(self, "_raw", raw_page)
        object.__setattr__(self, "_hc", human_context)

    # ---------- core identity ----------

    @property
    def raw(self) -> Page:
        return object.__getattribute__(self, "_raw")

    @property
    def context(self) -> "HumanContext":
        return object.__getattribute__(self, "_hc")

    @property
    def session(self) -> "Session":
        return self.context.session

    # ---------- lifecycle / sync ----------

    async def synchronize(self) -> None:
        await self.context.synchronize()

    async def goto(
        self,
        url: str,
        *,
        # our extras (optional):
        retry: Optional[int] = None,
        on_retry: Optional[Callable[[], Awaitable[None]]] = None,
        # standard Playwright kwargs (not exhaustive; forwarded via **kwargs):
        wait_until: Optional[Literal["commit", "load", "domcontentloaded", "networkidle"]] = None,
        timeout: Optional[float] = None,
        referer: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[PWResponse]:
        """
        Navigate to `url`. On PlaywrightTimeoutError, performs up to `retry`
        soft reloads using the same `wait_until`/`timeout`.

        Returns whatever the underlying Page.goto() returns.
        """
        # Build the kwargs for the underlying goto/reload calls:

        base_kwargs: dict[str, Any] = {"timeout": int(self.session.timeout * 1000)}
        if wait_until is not None:
            base_kwargs["wait_until"] = wait_until
        if timeout is not None:
            base_kwargs["timeout"] = timeout
        if referer is not None:
            base_kwargs["referer"] = referer
        if kwargs:
            base_kwargs.update(kwargs)

        try:
            return await self.raw.goto(url, **base_kwargs)
        except PlaywrightTimeoutError as last_err:
            attempts_left = int(retry) if retry is not None else self.session.page_retry
            while attempts_left > 0:
                attempts_left -= 1
                if on_retry is not None:
                    await on_retry()
                try:
                    # Soft refresh with the SAME wait_until/timeout
                    await self.raw.reload(
                        **{k: base_kwargs[k] for k in ("wait_until", "timeout") if k in base_kwargs}
                    )
                    last_err = None
                    break
                except PlaywrightTimeoutError as e:
                    last_err = e
            if last_err is not None:
                raise last_err

    async def close(self, *args: Any, **kwargs: Any) -> None:
        await self.raw.close(*args, **kwargs)

    # ---------- transparent proxying ----------

    def __getattribute__(self, name: str) -> Any:
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            # иначе — прокси на raw Page
            raw = object.__getattribute__(self, "_raw")
            return getattr(raw, name)

    def __setattr__(self, name: str, value):
        # Критично: системные поля всегда ставим локально
        if name in self.__slots__:
            object.__setattr__(self, name, value)
            return

        # Если у класса/обёртки есть такой атрибут (поле/свойство с setter’ом) — пишем в неё
        if hasattr(type(self), name):
            object.__setattr__(self, name, value)
            return

        # Если _raw ещё не инициализирован, ставим локально (этап __init__)
        try:
            raw = object.__getattribute__(self, "_raw")
        except AttributeError:
            object.__setattr__(self, name, value)
            return

        # По умолчанию — пробрасываем установку на оригинальную Page
        setattr(raw, name, value)

    def __delattr__(self, name: str):
        # Удаление — та же логика
        if name in self.__slots__ or hasattr(type(self), name):
            object.__delattr__(self, name)
            return
        try:
            raw = object.__getattribute__(self, "_raw")
        except AttributeError:
            object.__delattr__(self, name)
            return
        delattr(raw, name)

    def __repr__(self) -> str:
        return f"<HumanPage wrapping {self.raw!r}>"
