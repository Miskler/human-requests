from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.async_api import BrowserContext, Page

from .human_page import HumanPage

if TYPE_CHECKING:
    from .human_page import HumanPage


# ---- tiny helper to avoid repeating "get-or-create" for page wrappers ----

class HumanContext(BrowserContext):
    """
    A type-compatible wrapper over Playwright's BrowserContext.
    """
    
    @staticmethod
    def replace(playwright_context: BrowserContext) -> HumanContext:
        playwright_context.__class__ = HumanContext
        return playwright_context  # type: ignore[return-value]

    @property
    def pages(self) -> list["HumanPage"]:
        return [HumanPage.replace(p) for p in super().pages]

    async def new_page(self) -> "HumanPage":
        p = await super().new_page()
        return HumanPage.replace(p)

    # ---------- new funcs ----------

    async def local_storage(self, **kwargs) -> dict[str, dict[str, str]]:
        ls = await self.storage_state(**kwargs)
        return {o["origin"]: {e["name"]: e["value"] for e in o.get("localStorage", [])} for o in ls.get("origins", [])}

    def __repr__(self) -> str:
        return f"<HumanContext wrapping {super().__repr__()!r}>"
