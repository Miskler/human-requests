from __future__ import annotations

import asyncio
from typing import Any
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

# Параметры ожиданий/ретраев (можно переопределять из вызывающего кода)
DEFAULT_TIMEOUT_MS = 10_000
DEFAULT_MAX_ATTEMPTS = 3
RETRY_DELAY_SEC = 1.0  # пауза между попытками при таймауте селектора


# ----------------------------- DOM ready helpers -----------------------------
async def wait_fp_ready(page_like, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
    """
    Ждём «готовность» страницы: у таблицы table#fp2 появились строки <tr>.
    Это признак, что клиентские скрипты отработали и можно парсить.
    """
    await page_like.wait_for_selector("table#fp2", state="attached", timeout=timeout_ms)
    await page_like.wait_for_selector("table#fp2 tr", state="attached", timeout=timeout_ms)


async def _load_with_retry(load_fn, *, max_attempts: int, timeout_ms: int) -> str:
    """
    Универсальная обёртка с ретраями по таймауту селектора.
    load_fn(timeout_ms) должен внутри вызвать wait_fp_ready(...) и вернуть HTML (str).
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await load_fn(timeout_ms)
        except PlaywrightTimeoutError as e:
            last_exc = e
            # даём сайту шанс «проснуться» и пробуем снова
            await asyncio.sleep(RETRY_DELAY_SEC)
            continue
    assert last_exc is not None
    raise last_exc


# ------------------------------ HTML loaders ---------------------------------
async def html_via_goto(session, url: str,
                        *, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                        max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> str:
    """
    Открыть страницу обычной навигацией и вернуть HTML.
    С ретраями: при таймауте селектора — перезагрузка страницы.
    """
    async with session.goto_page(url, wait_until="load") as p:
        async def _do(tmo: int) -> str:
            try:
                # можно добавить networkidle перед селектором, если нужно:
                # await p.wait_for_load_state("networkidle")
                await wait_fp_ready(p, timeout_ms=tmo)
                return await p.content()
            except PlaywrightTimeoutError:
                await p.reload(wait_until="load")
                await wait_fp_ready(p, timeout_ms=tmo)
                return await p.content()
        return await _load_with_retry(_do, max_attempts=max_attempts, timeout_ms=timeout_ms)


async def html_via_render(session, url: str,
                          *, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                          max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> str:
    """
    Открыть страницу через .request(...).render() и вернуть HTML.
    С ретраями: при таймауте селектора — новый render-контекст.
    """
    resp = await session.request("GET", url)
    async with resp.render() as p:
        async def _do(tmo: int) -> str:
            try:
                await wait_fp_ready(p, timeout_ms=tmo)
                return await p.content()
            except PlaywrightTimeoutError:
                async def reopen() -> str:
                    resp2 = await session.request("GET", url)
                    async with resp2.render() as p2:
                        await wait_fp_ready(p2, timeout_ms=tmo)
                        return await p2.content()
                return await reopen()
        return await _load_with_retry(_do, max_attempts=max_attempts, timeout_ms=timeout_ms)


# ------------------------------ Parse helpers --------------------------------
def collect_failed_props(tree: dict[str, Any]) -> set[str]:
    """
    Собирает множество имён свойств (без префиксов), у которых passed == False.
    """
    failed: set[str] = set()

    def walk(node: Any):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict):
                    if "passed" in v and v.get("passed") is False:
                        failed.add(k)
                    walk(v)

    walk(tree)
    return failed


def select_unexpected_failures(
    browser: str,
    stealth: str,
    tree: dict[str, Any],
    anti_error: dict[str, dict[str, dict[str, list[str]]]],
    *,
    prefix: str = "",
) -> list[str]:
    """
    Полностью повторяет логику старого теста:
    - идём по дереву parse_sannysoft_bot,
    - если v.passed == False, но этот ключ НЕ входит в declared stable/unstable (для данного stealth или для all),
      то добавляем в список как «неожиданный фейл»;
    - если v.passed == True, а ключ объявлен «stable», то добавляем маркер, что ожидался фейл («(shld, bt not f)»).

    Возвращает список строковых «путей» (с префиксами через " → ") для красивого сообщения об ошибке.
    """
    fails: list[str] = []

    def rec(node: dict[str, Any], cur_prefix: str):
        for k, v in node.items():
            path = f"{cur_prefix}{k}"
            if not isinstance(v, dict):
                continue

            declared_stable = k in anti_error[browser][stealth]["stable"] or k in anti_error[browser]["all"]["stable"]
            declared_unst   = k in anti_error[browser][stealth]["unstable"] or k in anti_error[browser]["all"]["unstable"]

            if "passed" in v:
                val = v.get("passed")
                if val is False and not (declared_stable or declared_unst):
                    fails.append(path)
                elif val is True and declared_stable:
                    fails.append(f"{path} (shld, bt not f)")

            # глубже по дереву
            rec({nk: nv for nk, nv in v.items() if isinstance(nv, dict)}, cur_prefix=f"{path} → ")

    rec(tree, prefix)
    return fails
