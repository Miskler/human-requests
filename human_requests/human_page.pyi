from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import Page as _PWPage
from playwright.async_api import Response as PWResponse
from typing_extensions import Literal, override

class HumanPage(_PWPage):
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
