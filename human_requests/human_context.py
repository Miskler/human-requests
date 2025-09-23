from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Any

from playwright.async_api import BrowserContext, Page

from .human_page import HumanPage

if TYPE_CHECKING:
    from playwright._impl._api_structures import LocalStorageEntry, OriginState
    from playwright.async_api import StorageState, StorageStateCookie

    from .abstraction.cookies import CookieManager
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

    Responsibilities:
      - Bootstrap from Session (cookies + localStorage) on creation.
      - Provide stable HumanPage wrappers via `pages` and `new_page()`.
      - Synchronize context state back to Session (`synchronize()`).
      - On `close()`, synchronize then close the underlying context.

    Implementation:
      - Inherits from BrowserContext for isinstance-compatibility.
      - Stores the underlying context in `_raw` (does not call BrowserContext.__init__).
      - Proxies unknown attributes to `_raw` via __getattribute__/__setattr__.
      - Internal storage helpers (_build_storage_state / _merge_from_context) live here.
    """

    __slots__ = ("_raw", "_session", "_wrappers")

    def __init__(self, *, raw_ctx: BrowserContext, session: "Session") -> None:
        object.__setattr__(self, "_raw", raw_ctx)
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_wrappers", _WrapperCache(self))

    # ---------- factory ----------

    @classmethod
    async def create(cls, *, session: "Session") -> "HumanContext":
        storage_state = cls._build_storage_state(
            local_storage=session.local_storage,
            cookie_manager=session.cookies,
        )
        raw_ctx: BrowserContext = await session._bm.new_context(storage_state=storage_state)
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

    async def synchronize(self) -> None:
        """
        Pull storage_state from underlying context and push it to Session:
        - cookies: add/update in Session CookieManager
        - localStorage: exact overwrite in Session.local_storage
        """
        new_ls = await self._merge_from_context(cookie_manager=self.session.cookies)
        self.session.local_storage = new_ls

    async def close(self) -> None:
        try:
            await self.synchronize()
        finally:
            await self.raw.close()

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

    # ---------- internals ----------

    @staticmethod
    def _build_storage_state(
        *,
        local_storage: dict[str, dict[str, str]],
        cookie_manager: "CookieManager",
    ) -> "StorageState":
        cookie_list: list["StorageStateCookie"] = cookie_manager.to_playwright()
        origins: list["OriginState"] = []
        for origin, kv in local_storage.items():
            if not kv:
                continue
            entries: list["LocalStorageEntry"] = [{"name": k, "value": v} for k, v in kv.items()]
            origins.append({"origin": origin, "localStorage": entries})
        return {"cookies": cookie_list, "origins": origins}

    async def _merge_from_context(
        self,
        *,
        cookie_manager: "CookieManager",
    ) -> dict[str, dict[str, str]]:
        state = await self.raw.storage_state()

        new_ls: dict[str, dict[str, str]] = {}
        for o in state.get("origins", []) or []:
            origin = str(o.get("origin", "")) or ""
            if not origin:
                continue
            kv: dict[str, str] = {}
            for pair in o.get("localStorage", []) or []:
                name = str(pair.get("name", "")) or ""
                value = "" if pair.get("value") is None else str(pair.get("value"))
                if name:
                    kv[name] = value
            new_ls[origin] = kv

        cookies_list = state.get("cookies", []) or []
        if cookies_list:
            cookie_manager.add_from_playwright(cookies_list)

        return new_ls
