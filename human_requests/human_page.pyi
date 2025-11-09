from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from playwright.async_api import Cookie, Page
from playwright.async_api import Response as PWResponse
from typing_extensions import overload, override

from .abstraction import FetchResponse, HttpMethod
from .human_context import HumanContext

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
    @overload
    async def goto_render(
        self,
        response: "FetchResponse",
        *,
        retry: Optional[int] = ...,
        on_retry: Optional[Callable[[], Awaitable[None]]] = ...,
        timeout: Optional[float] = ...,
        wait_until: Optional[Literal["commit", "domcontentloaded", "load", "networkidle"]] = ...,
        referer: Optional[str] = ...,
        **kwargs: Any,
    ) -> Optional[PWResponse]: ...
    @overload
    async def goto_render(
        self,
        url: str,
        *,
        body: bytes | str,
        status_code: int = 200,
        headers: Optional[dict[str, str]] = None,
        retry: Optional[int] = ...,
        on_retry: Optional[Callable[[], Awaitable[None]]] = ...,
        timeout: Optional[float] = ...,
        wait_until: Optional[Literal["commit", "domcontentloaded", "load", "networkidle"]] = ...,
        referer: Optional[str] = ...,
        **kwargs: Any,
    ) -> Optional[PWResponse]: ...
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
    ) -> FetchResponse: ...
    @property
    def origin(self) -> str: ...
    async def cookies(self) -> List[Cookie]: ...
    async def local_storage(self, **kwargs: Any) -> Dict[str, str]: ...
    def __repr__(self) -> str: ...
