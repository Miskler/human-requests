from __future__ import annotations

import asyncio
import os
import pytest
from tests.sannysoft.sannysoft_parser import parse_sannysoft_bot

from network_manager import Session, ImpersonationConfig

# ---------------------------------------------------------  settings
SANNY_URL   = os.getenv("SANNYSOFT_URL", "https://bot.sannysoft.com/")
BROWSERS    = ("firefox",)# "chromium", , "webkit", "camoufox")
STEALTH_OPS = ("stealth", "base")          # включён playwright-stealth или нет
HEADLESS    = False
SLEEP_SEC   = 1.0

# Структура:
#   [browser][type][stable|unstable]
# Стабильность/нестабильность означает, насколько часто встречается проблема
# высокая вероятность что нестабильные значения зависят от системы на которой запущены
ANTI_ERROR = {
    "webkit": {
        "all": {"stable": ["Chrome(New)"],
                "unstable": []},
        "base": {"stable": ["WebDriver(New)"],
                 "unstable": []},
        "stealth": {"stable": [],
                    "unstable": []}
    },
    "firefox": {
        "all": {"stable": ["Chrome(New)",
                           "WebGL Vendor",
                           "WebGL Renderer"],
                "unstable": []},
        "base": {"stable": ["WebDriver(New)"],
                 "unstable": []},
        "stealth": {"stable": [],
                    "unstable": []}
    }, "chromium": {
        "all": {"stable": [], # "VIDEO_CODECS"
                "unstable": []},
        "base": {"stable": ["WebDriver(New)",
                            "Plugins is of type PluginArray",
                            "WebGL Renderer",
                            "HEADCHR_UA",
                            "HEADCHR_CHROME_OBJ",
                            "HEADCHR_PERMISSIONS",
                            "HEADCHR_PLUGINS",
                            "HEADCHR_IFRAME",
                            "CHR_MEMORY"],
                 "unstable": []},
        "stealth": {"stable": [],
                    "unstable": []}
    },
    "camoufox": {
        "all":  {"stable": ["Chrome(New)",
                            "WebGL Vendor",
                            "WebGL Renderer"],
                 "unstable": []},
        "base": {"stable": [],
                 "unstable": []},
        "stealth": {"stable": [],
                    "unstable": []}
    }
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
            shold_fail = k in ANTI_ERROR[browser][stealth]["stable"] or k in ANTI_ERROR[browser]["all"]["stable"]
            maybe_fail = k in ANTI_ERROR[browser][stealth]["unstable"] or k in ANTI_ERROR[browser]["all"]["unstable"]
            if v.get("passed") == False and not (shold_fail or maybe_fail):
                fails.append(path)
            elif v.get("passed") == True and shold_fail:
                fails.append(f"{path} (shld, bt not f)")
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
