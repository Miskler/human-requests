from __future__ import annotations

"""
helper_tools — вспомогательные утилиты, не зависящие от конкретного Session:
- сборка/слияние storage_state (cookies + localStorage)
- единый хендлер навигации с мягкими ретраями
"""

from typing import Awaitable, Callable, Literal, Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

# Зависящие типы простые и стабильные — импортируем прямо.
# CookieManager нужен только как протокол поведения (to_playwright/add_from_playwright).


def build_storage_state_for_context(
    *,
    local_storage: dict[str, dict[str, str]],
    cookie_manager,
) -> dict:
    """
    Собирает единый storage_state для new_context:
    - cookies — из CookieManager (как playwright-совместимые dict)
    - origins.localStorage — из local_storage (по origin)
    """
    cookie_list = cookie_manager.to_playwright()  # list[dict] совместимая с PW
    origins = []
    for origin, kv in local_storage.items():
        if not kv:
            continue
        origins.append(
            {
                "origin": origin,
                "localStorage": [{"name": k, "value": v} for k, v in kv.items()],
            }
        )
    return {"cookies": cookie_list, "origins": origins}


async def merge_storage_state_from_context(
    ctx: BrowserContext, *, cookie_manager
) -> dict[str, dict[str, str]]:
    """
    Читает storage_state из контекста и синхронизирует внутреннее состояние:
    - localStorage: ПОЛНАЯ перезапись и возвращается наружу
    - cookies: ДОБАВЛЕНИЕ/ОБНОВЛЕНИЕ в переданный CookieManager
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
    Единый хендлер навигации с мягкими повторами для goto/render.
    Ловит ТОЛЬКО PlaywrightTimeoutError. На повторах вызывает on_retry()
    (если задан), затем делает reload (мягкая перезагрузка).
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
