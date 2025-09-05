from __future__ import annotations

import asyncio
import os
import pytest
from sannysoft_parser import parse_sannysoft_bot

from human_requests.core.session import Session
from human_requests.core.impersonation import ImpersonationConfig

# ---------------------------------------------------------  settings
SANNY_URL   = os.getenv("SANNYSOFT_URL", "https://bot.sannysoft.com/")
BROWSERS    = ("chromium", "firefox", "webkit")
STEALTH_OPS = (True, False)          # включён playwright-stealth или нет
SLEEP_SEC   = 1.0                    # как требовалось в ТЗ
# ---------------------------------------------------------

def _collect_failures(tree: dict, prefix: str = "") -> list[str]:
    """
    Возвращает список путей внутри JSON, где `"passed": false`.
    """
    fails: list[str] = []
    for k, v in tree.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            if v.get("passed") is False:
                fails.append(path)
            fails += _collect_failures(v, prefix=f"{path} → ")
    return fails


async def _html_via_goto(session: Session) -> str:
    async with session.goto_page(SANNY_URL) as p:
        await asyncio.sleep(SLEEP_SEC)
        return await p.content()


async def _html_via_render(session: Session) -> str:
    resp = await session.request("GET", SANNY_URL)
    async with resp.render() as p:
        await asyncio.sleep(SLEEP_SEC)
        return await p.content()


# ————————————————————————————————————————————————————————————————————————
#  Parametrизация: browser × stealth × mode
# ————————————————————————————————————————————————————————————————————————
@pytest.mark.parametrize("browser",    BROWSERS)
@pytest.mark.parametrize("stealth",    STEALTH_OPS)
@pytest.mark.parametrize("mode",       ("goto", "render"))
@pytest.mark.asyncio
async def test_antibot_matrix(browser: str, stealth: bool, mode: str):
    """
    Один элемент матрицы.  Формат имени теста в отчёте Py-test:
        test_antibot_matrix[chromium-True-goto]   (к примеру)
    """
    cfg = ImpersonationConfig(sync_with_engine=True)
    session = Session(
        browser=browser,
        playwright_stealth=stealth,
        spoof=cfg,
    )

    # --- получаем HTML ------------------------------------------------
    try:
        html = (
            await _html_via_goto(session)
            if mode == "goto" else
            await _html_via_render(session)
        )
    finally:
        await session.close()

    # --- разбираем ----------------------------------------------------------------
    result = parse_sannysoft_bot(html)
    fails  = _collect_failures(result)

    if fails:        # форматируем красивое сообщение
        matrix_tag = f"{browser}/{ 'stealth' if stealth else 'plain' }/{mode}"
        fail_list  = ", ".join(fails)
        pytest.fail(f"[{matrix_tag}] не прошли проверки: {fail_list}", pytrace=False)
