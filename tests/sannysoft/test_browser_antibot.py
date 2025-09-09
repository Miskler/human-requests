from __future__ import annotations

import json
import os

import pytest

from human_requests import ImpersonationConfig, Session
from tests.sannysoft.sannysoft_parser import parse_sannysoft_bot
from tests.sannysoft.tool import (
    html_via_goto,
    html_via_render,
    select_unexpected_failures,
)

# ---------------------------------------------------------  settings
SANNY_URL = os.getenv("SANNYSOFT_URL", "https://bot.sannysoft.com/")
BROWSERS = ("firefox", "chromium", "webkit", "camoufox")
STEALTH_OPS = ("stealth", "base")  # включён playwright-stealth или нет
HEADLESS = False

# Структура:
#   [browser][type][stable|unstable]
# Стабильность/нестабильность означает, насколько часто встречается проблема
# высокая вероятность что нестабильные значения зависят от системы на которой запущены
ANTI_ERROR = json.load(open("tests/sannysoft/browser_antibot_sannysoft.json"))
# ---------------------------------------------------------


# ————————————————————————————————————————————————————————————————————————
#  Parametrизация: browser × stealth × mode
# ————————————————————————————————————————————————————————————————————————
@pytest.mark.parametrize("browser", BROWSERS)
@pytest.mark.parametrize("stealth", STEALTH_OPS)
@pytest.mark.parametrize("mode", ("goto", "render"))
@pytest.mark.asyncio
async def test_antibot_matrix(browser: str, stealth: str, mode: str):
    """
    Один элемент матрицы.  Формат имени теста в отчёте Py-test:
        test_antibot_matrix[chromium-stealth-goto], к примеру.
    """
    if browser == "camoufox" and stealth == "stealth":
        pytest.skip("playwright_stealth=True is incompatible with browser='camoufox'")

    cfg = ImpersonationConfig(sync_with_engine=True)
    session = Session(
        timeout=15,
        headless=HEADLESS,
        browser=browser,
        playwright_stealth=(stealth == "stealth"),
        spoof=cfg,
    )

    # --- получаем HTML (устойчиво к «скрипт не стартовал») ---
    try:
        html = await (
            html_via_goto(session, SANNY_URL)
            if mode == "goto"
            else html_via_render(session, SANNY_URL)
        )
    finally:
        await session.close()

    # --- разбираем и фильтруем «неожиданные» сбои ---
    result = parse_sannysoft_bot(html)
    fails = select_unexpected_failures(browser, stealth, result, ANTI_ERROR)

    if fails:
        matrix_tag = f"{browser}/{mode}" if browser == "camoufox" else f"{browser}/{stealth}/{mode}"
        fail_list = ", ".join(fails)
        pytest.fail(f"[{matrix_tag}] не прошли проверки: {fail_list}", pytrace=False)
