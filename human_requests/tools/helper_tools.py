"""
helper_tools — helper utilities independent of a specific Session:
- assembling/merging storage_state (cookies + localStorage)
- unified navigation handler with soft retries
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Optional

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

if TYPE_CHECKING:
    from playwright._impl._api_structures import LocalStorageEntry, OriginState
    from playwright.async_api import StorageState, StorageStateCookie

    from ..abstraction.cookies import CookieManager

# Зависящие типы простые и стабильные — импортируем прямо.
# CookieManager нужен только как протокол поведения (to_playwright/add_from_playwright).


def build_storage_state_for_context(
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


async def merge_storage_state_from_context(
    ctx: BrowserContext, *, cookie_manager: "CookieManager"
) -> dict[str, dict[str, str]]:
    """
    Reads storage_state from the context and synchronizes internal state:
    - localStorage: FULL overwrite and returned outward
    - cookies: ADD/UPDATE in the provided CookieManager
    """
    state = await ctx.storage_state()  # dict с 'cookies' и 'origins'

    # localStorage — точная перезапись
    new_ls: dict[str, dict[str, str]] = {}
    for o in state.get("origins", []) or []:
        origin = str(o.get("origin", ""))
        if not origin:
            continue
        kv: dict[str, str] = {}
        for pair in o.get("localStorage", []) or []:
            name = str(pair.get("name", ""))
            value = "" if pair.get("value") is None else str(pair.get("value"))
            if name:
                kv[name] = value
        new_ls[origin] = kv

    # cookies — пополняем CookieManager
    cookies_list = state.get("cookies", []) or []
    if cookies_list:
        cookie_manager.add_from_playwright(cookies_list)

    return new_ls


async def handle_nav_with_retries(
    page: Page,
    *,
    target_url: str,
    wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"],
    timeout_ms: int,
    attempts: int,
    on_retry: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    """
    Unified navigation handler with soft retries for goto/render.
    Catches ONLY PlaywrightTimeoutError. On retries calls on_retry()
    (if provided), then performs a reload (soft refresh).
    """
    try:
        await page.goto(target_url, wait_until=wait_until, timeout=timeout_ms)
    except PlaywrightTimeoutError as last_err:
        while attempts > 0:
            attempts -= 1
            if on_retry is not None:
                await on_retry()
            try:
                await page.reload(wait_until=wait_until, timeout=timeout_ms)
                last_err = None  # type: ignore[assignment]
                break
            except PlaywrightTimeoutError as e:
                last_err = e
        if last_err is not None:
            raise last_err
