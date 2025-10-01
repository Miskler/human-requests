from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Any

from playwright.async_api import BrowserContext, Page

from .human_page import HumanPage

if TYPE_CHECKING:
    from .human_page import HumanPage
    from .session import Session


# ---- tiny helper to avoid repeating "get-or-create" for page wrappers ----
class _WrapperCache(weakref.WeakKeyDictionary[Page, "HumanPage"]):
    def __init__(self, owner: "HumanContext") -> None:
        super().__init__()
        self._owner = owner

    def __call__(self, raw_page: Page) -> "HumanPage":
        hp = super().get(raw_page)
        if hp is None:
            hp = HumanPage(raw_page=raw_page, human_context=self._owner)
            super().__setitem__(raw_page, hp)
        return hp


class HumanContext(BrowserContext):
    """
    A type-compatible wrapper over Playwright's BrowserContext.
    """

    __slots__ = ("_raw", "_session", "_wrappers")

    def __init__(self, *, raw_ctx: BrowserContext, session: "Session") -> None:
        object.__setattr__(self, "_raw", raw_ctx)
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_wrappers", _WrapperCache(self))

    # ---------- factory ----------

    @classmethod
    async def create(cls, *, session: "Session", **kwargs) -> "HumanContext":
        raw_ctx: BrowserContext = await session._bm.new_context(**kwargs)
        return cls(raw_ctx=raw_ctx, session=session)

    # ---------- core props ----------

    @property
    def raw(self) -> BrowserContext:
        return object.__getattribute__(self, "_raw")

    @property
    def session(self) -> "Session":
        return object.__getattribute__(self, "_session")

    @property
    def pages(self) -> list["HumanPage"]:
        wrappers: _WrapperCache = object.__getattribute__(self, "_wrappers")
        return [wrappers(p) for p in self.raw.pages]

    # ---------- page creation ----------

    async def new_page(self) -> "HumanPage":
        p = await self.raw.new_page()
        return object.__getattribute__(self, "_wrappers")(p)  # get-or-create

    # ---------- sync lifecycle ----------

    async def localStorage(self, **kwargs) -> dict[str, dict[str, str]]:
        ls = await self.storage_state(**kwargs)
        return {o["origin"]: {e["name"]: e["value"] for e in o.get("localStorage", [])} for o in ls.get("origins", [])}

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
        return f"<HumanContext wrapping {self.raw!r}>"
