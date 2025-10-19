from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, cast, List, Literal
from urllib.parse import urlsplit

from playwright.async_api import Page, Cookie
from playwright.async_api import Response as PWResponse
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from typing_extensions import overload, override

from .human_context import HumanContext
from .abstraction import HttpMethod, FetchResponse

class HumanPage(Page):
    @property
    def context(self) -> "HumanContext": ...
    @staticmethod
    def replace(playwright_page: Page) -> "HumanPage": ...

    @override
    async def goto(
        self,
        url: str,
        *,
        retry: Optional[int] = ...,
        on_retry: Optional[Callable[[], Awaitable[None]]] = ...,
        timeout: Optional[float] = ...,
        wait_until: Optional[Literal["commit", "domcontentloaded", "load", "networkidle"]] = ...,
        referer: Optional[str] = ...,
        **kwargs: Any,
    ) -> PWResponse | None: ...


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
        ...

    @property
    def origin(self) -> str: ...

    async def cookies(self) -> List[Cookie]: ...

    async def local_storage(self, **kwargs: Any) -> Dict[str, str]: ...

    def __repr__(self) -> str: ...
