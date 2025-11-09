from __future__ import annotations

from typing import Any, List, cast, override

from playwright.async_api import Browser

from .human_context import HumanContext
from .human_page import HumanPage


class HumanBrowser(Browser):

    @staticmethod
    def replace(playwright_browser: Browser) -> HumanBrowser:
        playwright_browser.__class__ = HumanBrowser
        return playwright_browser  # type: ignore[return-value]

    # ────── browser nav ──────
    async def new_page(
        self,
        **kwargs: Any,
    ) -> HumanPage:
        page = await super().new_page(**kwargs)
        HumanContext.replace(page.context)
        return HumanPage.replace(page)

    async def new_context(
        self,
        **kwargs: Any,
    ) -> HumanContext:
        ctx = await super().new_context(**kwargs)
        return HumanContext.replace(ctx)

    @property
    @override
    def contexts(self) -> List["HumanContext"]:  # type: ignore[override]
        ctxs = super().contexts
        for c in ctxs:
            HumanContext.replace(c)
        return cast(List["HumanContext"], ctxs)
