from __future__ import annotations

import asyncio
import os
import pytest
from sannysoft_parser import parse_sannysoft_bot

from network_manager import Session, ImpersonationConfig

# ---------------------------------------------------------  settings
SANNY_URL   = os.getenv("SANNYSOFT_URL", "https://bot.sannysoft.com/")
BROWSERS    = ("chromium", "firefox", "webkit", "camoufox")
STEALTH_OPS = ("stealth", "base")          # включён playwright-stealth или нет
HEADLESS    = True
SLEEP_SEC   = 1.0
ANTI_ERROR  = {
    "webkit": {
        "all": ["Chrome(New)"],
        "base": ["WebDriver(New)"],
        "stealth": [],
    },
    "firefox": {
        "all": ["Chrome(New)"],
        "base": ["WebDriver(New)"],
        "stealth": [],
    },
    "chromium": {
        "all": [],
        "base": ["WebDriver(New)"],
        "stealth": [],
    },
    "camoufox": {
        "all": ["Chrome(New)",
                "WebGL Vendor",
                "WebGL Renderer"],
        "base": [],
        "stealth": [],
    },
}
# ---------------------------------------------------------

def _collect_failures(browser: str, stealth: str, tree: dict, prefix: str = "") -> list[str]:
    """
    Возвращает список путей внутри JSON, где `"passed": false`.
    """
    fails: list[str] = []
    for k, v in tree.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            shold_fail = k in ANTI_ERROR[browser][stealth] or k in ANTI_ERROR[browser]["all"]
            if v.get("passed") == False and not shold_fail:
                fails.append(path)
            elif v.get("passed") == True and shold_fail:
                fails.append(f"{path} (should fail, but not fail)")
            fails += _collect_failures(browser, stealth, v, prefix=f"{path} → ")
    return fails


async def _html_via_goto(session: Session) -> str:
    async with session.goto_page(SANNY_URL, wait_until="load") as p:
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
async def test_antibot_matrix(browser: str, stealth: str, mode: str):
    """
    Один элемент матрицы.  Формат имени теста в отчёте Py-test:
        test_antibot_matrix[chromium-True-goto]   (к примеру)
    """
    if browser == "camoufox" and stealth == "stealth":
        pytest.skip("playwright_stealth=True is incompatible with browser='camoufox'")

    cfg = ImpersonationConfig(sync_with_engine=True)
    session = Session(
        timeout=10,
        headless=HEADLESS,
        browser=browser,
        playwright_stealth=stealth == "stealth",
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
    fails  = _collect_failures(browser, stealth, result)

    if fails:        # форматируем красивое сообщение
        if browser != "camoufox":
            matrix_tag = f"{browser}/{stealth}/{mode}"
        else:
            matrix_tag = f"{browser}/{mode}"
        fail_list  = ", ".join(fails)
        pytest.fail(f"[{matrix_tag}] не прошли проверки: {fail_list}", pytrace=False)
